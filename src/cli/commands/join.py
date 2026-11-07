"""Join an invitation with an explicit existing, native, or new Agent identity."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit

from src.backend.common.env_settings import read_env_all
from src.backend.common.runtime_paths import BIN_DIR, ENV_FILE, LOGS_DIR, PID_DIR, PROJECT_ROOT
from src.cli.commands import api_client as api


class JoinError(Exception):
    pass


def _validate_link(link):
    try:
        parsed = urlsplit(link)
        local = parsed.hostname in {'localhost', '127.0.0.1', '::1'}
        valid = (len(link) <= 5000 and parsed.hostname and not parsed.username and not parsed.password
                 and not parsed.query and parsed.path.rstrip('/').endswith('/group-guest')
                 and parsed.fragment and len(parsed.fragment) <= 4096
                 and (parsed.scheme == 'https' or parsed.scheme == 'http' and local))
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise JoinError('请使用完整的群邀请链接：https://站点/group-guest#票据；本机允许 HTTP。')


def _configuration():
    values = read_env_all(str(ENV_FILE))
    token = os.environ.get('INTERNAL_TOKEN') or values.get('INTERNAL_TOKEN', '')
    port = os.environ.get('PORT_AGENT') or values.get('PORT_AGENT', '51200')
    return f'http://127.0.0.1:{int(port)}', token


def _lifecycle(*args):
    """Only --prepare reaches this path. Keep startup login links in a private log."""
    if os.name == 'nt':
        command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(PROJECT_ROOT/'launch/run.ps1')]
    else:
        command = ['bash', str(PROJECT_ROOT/'launch/run.sh')]
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='join-setup-', suffix='.log', dir=LOGS_DIR, delete=False) as log:
        result = subprocess.run([*command, *args], stdout=log, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise JoinError(f'准备未完成，请查看本机日志：{log.name}')


def _bridge(platform, prepare):
    if platform in {'webot', 'internal', 'http', 'llm'}:
        return
    node = shutil.which('node')
    if not node:
        raise JoinError('ACP 转接需要 Node.js 22.13 或更新版本，请先安装 Node.js。')
    version = subprocess.run([node, '-p', 'process.versions.node'], capture_output=True, text=True, timeout=5)
    try:
        current = tuple(map(int, version.stdout.strip().split('.')[:2]))
    except ValueError:
        current = (0, 0)
    if version.returncode or current < (22, 13):
        raise JoinError('ACP 转接需要 Node.js 22.13 或更新版本；本次没有安装不兼容的 acpx。')
    if platform == 'deepseek-harness' and current < (24, 9):
        raise JoinError('DeepSeek Harness 需要 Node.js 24.9+；本次没有安装不兼容的 DSH。')
    binary = BIN_DIR/'node/node_modules/.bin'/('acpx.cmd' if os.name == 'nt' else 'acpx')
    if not (shutil.which('acpx') or binary.is_file()):
        if not prepare:
            raise JoinError('缺少 acpx。请加 --prepare，或先执行 clawcross install-component acpx。')
        _lifecycle('install-component', 'acpx')
    if platform == 'deepseek-harness':
        dsh = BIN_DIR/'node/node_modules/.bin'/('dsh.cmd' if os.name == 'nt' else 'dsh')
        if not (shutil.which('dsh') or dsh.is_file()):
            if not prepare:
                raise JoinError('缺少 DeepSeek Harness。请加 --prepare 或执行 clawcross install-component deepseek-harness。')
            _lifecycle('install-component', 'deepseek-harness')


def _launcher_running():
    try:
        pid = int((PID_DIR/'clawcross.pid').read_text().strip())
        if pid <= 0:
            return False
        if os.name == 'nt':
            result = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                                    capture_output=True, text=True, timeout=5)
            return f'"{pid}"' in result.stdout
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False


def connect(args, default_user):
    _validate_link(args.invite)
    # Never guess the most recent session or silently substitute a new Agent.
    native = args.native_session
    if args.current:
        if args.platform != 'codex':
            raise JoinError('--current 目前只识别 Codex 的 CODEX_THREAD_ID；其他平台请明确指定 --native-session。')
        native = os.environ.get('CODEX_THREAD_ID', '').strip()
        if not native:
            raise JoinError('当前进程未提供会话 ID。请指定 --agent 或 --native-session；没有新建替代 Agent。')
    base, token = _configuration()
    code, _ = api._req('GET', base+'/health', timeout=3)
    if code != 200:
        if not args.prepare:
            raise JoinError('本机 ClawCross 尚未就绪。请加 --prepare，或先执行 clawcross start。')
        if _launcher_running():
            raise JoinError('已有 ClawCross 进程但尚未就绪；本次入群不会重启或打断它，请检查 clawcross status。')
        _lifecycle('start', '--bridge')
        base, token = _configuration()
        code, _ = api._req('GET', base+'/health', timeout=3)
        if code != 200:
            raise JoinError('本机 ClawCross 启动后仍未就绪。')
    if not token:
        raise JoinError('找不到本机认证配置；请先执行 clawcross start。')
    from src.backend.common.local_accounts import default_account, select_account
    selected_user = args.user or default_user
    owner = select_account(selected_user, create=True) if selected_user else default_account(create=True)
    headers = {'Authorization':f'Bearer {token}:{owner}'}

    def request(method, path, body=None, *, host_prepare=False):
        selected_headers = {**headers, 'X-ClawCross-Host-Browse':token} if host_prepare else headers
        status, data = api._req(method, base+path, headers=selected_headers, data=body, timeout=120 if host_prepare else 90)
        if status not in {200, 201}:
            detail = data.get('detail') or data.get('error') if isinstance(data, dict) else ''
            # Server errors may echo submitted input; invitation capabilities stay private.
            safe = str(detail or '请求失败').replace(args.invite, '[邀请链接]')
            safe = safe.replace(urlsplit(args.invite).fragment, '[票据]')
            raise JoinError(f'[{status}] {safe[:400]}')
        return data

    source = 'existing'
    if args.agent:
        agent = request('GET', '/v1/agents/'+quote(args.agent, safe=''))
    elif native:
        _bridge(args.platform, args.prepare)
        cursor = ''
        seen = set()
        selected = None
        for _ in range(100):
            path = '/v1/agents/native-sessions'+('/prepare' if args.prepare else '')+'?platform='+quote(args.platform, safe='')
            if cursor:
                path += '&cursor='+quote(cursor, safe='')
            catalog = request('POST' if args.prepare else 'GET', path, host_prepare=args.prepare)
            selected = next((row for row in catalog.get('sessions', []) if row.get('session_id') == native), None)
            if selected:
                break
            cursor = catalog.get('next_cursor')
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        if not selected:
            raise JoinError('适配器没有列出这个原生会话，无法保证复用其上下文；没有新建替代 Agent。')
        if selected.get('registered_agent_id'):
            agent = request('GET', '/v1/agents/'+quote(selected['registered_agent_id'], safe=''))
        else:
            agent = request('POST', '/v1/agents/native-sessions', {'ticket':selected['ticket'], 'name':args.name or ''})
        source = 'native'
    else:
        _bridge(args.platform, args.prepare)
        # Repeating a prompt in the same workspace reuses the same managed Agent.
        identity = f'{owner}\0{args.platform}\0{Path.cwd().resolve()}\0{args.name or "我的伙伴"}'
        aid = 'ag_join_'+hashlib.sha256(identity.encode()).hexdigest()[:24]
        code, agent = api._req('GET', base+'/v1/agents/'+aid, headers=headers)
        if code == 404:
            # A new public-group companion never inherits this install's source
            # tree, personal files, teams, or unrestricted tool catalogue.
            native = args.platform not in {'webot', 'internal', 'http', 'llm'}
            agent = request('POST', '/v1/agents', {'agent_id':aid, 'platform':args.platform,
                'name':args.name or '我的伙伴',
                'creation_template':'personal' if native else 'group',
                'tools':['get_current_time'],
                'workspaces':{'companion':True,'user_shared':False,'cli':False,'teams':False,'paths':[]},
                'meta':{'forwarding':{'reply':'automatic'}, 'acp':{
                    'native_tools':getattr(args, 'allow_native_tools', False),
                    'text_only':args.platform == 'codex' and sys.platform == 'linux',
                    'permission_policy':'approve-reads', 'non_interactive_permissions':'deny'}}})
            source = 'created'
        elif code != 200:
            raise JoinError(f'[{code}] 无法确认 Agent 是否存在，没有重复创建。')
    aid = agent['agent_id']
    acp_settings = (agent.get('settings', {}).get('meta') or agent.get('meta') or {}).get('acp') or {}
    from src.backend.external.native_tool_policy import runtime_allowed
    native_config = {**agent.get('settings',{}),'platform':agent['platform'],
                     'connection_id':agent.get('connection_id') or agent.get('settings',{}).get('connection_id'),
                     'meta':{'acp':acp_settings}}
    native_blocked = agent['platform'] not in {'webot', 'internal', 'http', 'llm'} and not runtime_allowed(native_config)
    if args.prepare and not native_blocked and agent['platform'] not in {'webot', 'internal', 'http', 'llm'}:
        _bridge(agent['platform'], True)
        # Initialization/login checks use ACP session/new or session/load, never
        # session/prompt. Only incoming group work should spend model tokens.
        if (agent.get('status') or {}).get('state') != 'running':
            request('POST', '/v1/agents/'+quote(aid, safe='')+'/test-connection')
    group = request('POST', '/groups/join', {'invite':args.invite, 'agents':[aid], 'name':args.member_name or ''})
    deadline = time.monotonic() + getattr(args, 'wait_seconds', 15)
    while True:
        connection = request('GET', '/groups/'+quote(group['group_id'], safe='')+'/connection')
        if connection.get('state') in {'connected', 'paused', 'stopped', 'revoked'} or time.monotonic() >= deadline:
            break
        time.sleep(0.25)
    startup = None
    if getattr(args, 'autostart', False):
        from launch.autostart import enable
        startup = enable()
    # Output only the identity/result, never credentials, group history or member profiles.
    return {'ok':connection.get('state') == 'connected', 'joined':True,
            'owner':owner, 'agent_id':aid, 'platform':agent['platform'], 'source':source,
            'agent_ready':not native_blocked,
            'message_center':f'http://127.0.0.1:{os.environ.get("PORT_FRONTEND", "51209")}/mobile/group_chat?group='+quote(group['group_id'],safe=''),
            **({'warning':'已入群并保留可见成员；原生工具关闭，此适配器暂停运行，不消耗额度。请在所属账号的 Agent 设置中明确选择权限。'} if native_blocked else {}),
            'group_id':group['group_id'], 'title':group.get('title', ''),
            'native_history':(agent.get('native_history') or {}).get('status', ''),
            'handover_required':source == 'native', 'connection':connection,
            **({'autostart':startup} if startup else {})}


def main(argv=None, *, default_user=None):
    parser = argparse.ArgumentParser(prog='clawcross join', description='使用同一个 Agent 加入官网或其他部署的群聊')
    parser.add_argument('invite', help='完整邀请链接（请用引号包住）')
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument('--agent', help='复用已有 ClawCross Agent ID')
    identity.add_argument('--native-session', help='登记并恢复指定原生会话 ID')
    identity.add_argument('--current', action='store_true', help='从 CODEX_THREAD_ID 精确识别当前 Codex 会话')
    identity.add_argument('--new', action='store_true', help='明确创建同平台伙伴；重复执行复用同一伙伴')
    parser.add_argument('--platform', default='codex', help='原生或新 Agent 的平台，默认 codex')
    parser.add_argument('--name', help='新建或首次登记时的名字')
    parser.add_argument('--member-name', help='你在群中的昵称，默认本机用户')
    parser.add_argument('-u', '--user', help='本机用户身份')
    parser.add_argument('--prepare', action='store_true', help='准备转接模式、acpx 和所选适配器；不需要前端或主模型 Key')
    parser.add_argument('--autostart', action='store_true', help='入群后启用当前用户的系统自启动')
    parser.add_argument('--allow-native-tools', action='store_true', help='仅用于新建 Agent：明确允许外部 CLI 原生工具（不受 ClawCross 沙盒控制）')
    parser.add_argument('--wait-seconds', type=int, default=15, choices=range(1, 61), metavar='1-60', help='等待后台接收连接就绪，默认 15 秒')
    parser.add_argument('--json', action='store_true', help='只输出可供其他 Agent 使用的 JSON 结果')
    args = parser.parse_args(argv)
    if not re.fullmatch('[a-z0-9_-]{1,50}', args.platform):
        parser.error('平台名称无效')
    try:
        result = connect(args, default_user)
    except (JoinError, ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f'已加入「{result["title"]}」 · Agent {result["agent_id"]} · 群 {result["group_id"]}')
        print(f'所属账号：{result["owner"]} · 群聊入口：{result["message_center"]}')
        if result.get('warning'):
            print(result['warning'])
        print('转接模式：后台接收群消息，不需要打开前端。' if result['ok'] else '已入群，但后台接收尚未就绪；请检查 clawcross status。')
        if result.get('autostart'):
            print('自启动已启用：'+('开机后运行' if result['autostart']['trigger'] == 'boot' else '登录系统后运行'))
            for warning in result['autostart'].get('warnings', []):
                print(warning)
        if result['source'] == 'native':
            print('复用原生会话。自动处理群消息时，请让其他客户端停止写入同一会话。')
    return 0 if result['ok'] else 1
