#!/usr/bin/env python3
"""
Clawcross CLI — 命令行控制工具

功能：
- 像人操作前端一样，通过命令行控制 Agent 的各项功能
- 直接调用后端 API（绕过 front.py session），使用 INTERNAL_TOKEN 认证

用法: python scripts/cli.py <command> [options]
"""
import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def _configure_stdio():
    """配置标准输出的编码，避免 Windows 控制台非 UTF-8 编码导致帮助信息崩溃"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                kwargs = {"errors": "replace"}
                if os.name == "nt" and (getattr(stream, "encoding", "") or "").lower() != "utf-8":
                    kwargs["encoding"] = "utf-8"
                stream.reconfigure(**kwargs)
            except Exception:
                pass


_configure_stdio()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# ── 加载 .env 配置 ────────────────────────────────────────────────────────
def _load_env():
    """从 config/.env 加载环境变量到 os.environ"""
    env_path = os.path.join(PROJECT_ROOT, "config", ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and k not in os.environ:
                os.environ[k] = v


_load_env()

# 服务端口配置
PORT_AGENT = int(os.getenv("PORT_AGENT", "51200"))
PORT_OASIS = int(os.getenv("PORT_OASIS", "51202"))
PORT_FRONTEND = int(os.getenv("PORT_FRONTEND", "51209"))
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")

# API 基础 URL
AGENT_BASE = f"http://127.0.0.1:{PORT_AGENT}"
OASIS_BASE = f"http://127.0.0.1:{PORT_OASIS}"
FRONT_BASE = f"http://127.0.0.1:{PORT_FRONTEND}"

DEFAULT_USER = os.getenv("CLI_USER", "admin")


def _workflow_yaml_dir(user_id: str, team: str = "") -> str:
    user_root = os.path.join(PROJECT_ROOT, "data", "user_files", user_id)
    if team:
        return os.path.join(user_root, "teams", team, "oasis", "yaml")
    return os.path.join(user_root, "oasis", "yaml")


def _workflow_python_dir(user_id: str, team: str = "") -> str:
    user_root = os.path.join(PROJECT_ROOT, "data", "user_files", user_id)
    if team:
        return os.path.join(user_root, "teams", team, "oasis", "python")
    return os.path.join(user_root, "oasis", "python")


def _iter_yaml_workflow_dirs(user_id: str, team: str = ""):
    if not user_id:
        return []
    user_root = os.path.join(PROJECT_ROOT, "data", "user_files", user_id)
    if team:
        return [("team", team, _workflow_yaml_dir(user_id, team))]
    dirs = [("personal", "", _workflow_yaml_dir(user_id, ""))]
    teams_root = os.path.join(user_root, "teams")
    if os.path.isdir(teams_root):
        for team_name in sorted(os.listdir(teams_root)):
            team_dir = os.path.join(teams_root, team_name)
            if os.path.isdir(team_dir):
                dirs.append(("team", team_name, _workflow_yaml_dir(user_id, team_name)))
    return dirs


def _iter_python_workflow_dirs(user_id: str, team: str = ""):
    if not user_id:
        return []
    user_root = os.path.join(PROJECT_ROOT, "data", "user_files", user_id)
    if team:
        return [("team", team, _workflow_python_dir(user_id, team))]
    dirs = [("personal", "", _workflow_python_dir(user_id, ""))]
    teams_root = os.path.join(user_root, "teams")
    if os.path.isdir(teams_root):
        for team_name in sorted(os.listdir(teams_root)):
            team_dir = os.path.join(teams_root, team_name)
            if os.path.isdir(team_dir):
                dirs.append(("team", team_name, _workflow_python_dir(user_id, team_name)))
    return dirs


def _resolve_yaml_workflow_path(user_id: str, name: str, team: str = ""):
    if not name:
        return None, "未提供 workflow 文件名"
    target = name if name.endswith((".yaml", ".yml")) else f"{name}.yaml"
    matches = []
    for scope, team_name, yaml_dir in _iter_yaml_workflow_dirs(user_id, team):
        path = os.path.join(yaml_dir, target)
        if os.path.isfile(path):
            label = f"team:{team_name}" if scope == "team" else "personal"
            matches.append((label, path))
    if not matches:
        return None, f"未找到 YAML workflow: {target}"
    if len(matches) > 1:
        where = ", ".join(label for label, _ in matches)
        return None, f"找到多个同名 YAML workflow: {target}（{where}），请指定 --team"
    return matches[0][1], None


def _resolve_python_workflow_path(user_id: str, name: str, team: str = ""):
    if not name:
        return None, "未提供 python workflow 文件名"
    target = name if name.endswith(".py") else f"{name}.py"
    matches = []
    for scope, team_name, py_dir in _iter_python_workflow_dirs(user_id, team):
        path = os.path.join(py_dir, target)
        if os.path.isfile(path):
            label = f"team:{team_name}" if scope == "team" else "personal"
            matches.append((label, path))
    if not matches:
        return None, f"未找到 Python workflow: {target}"
    if len(matches) > 1:
        where = ", ".join(label for label, _ in matches)
        return None, f"找到多个同名 Python workflow: {target}（{where}），请指定 --team"
    return matches[0][1], None


def _python_runs_dir() -> str:
    return os.path.join(PROJECT_ROOT, "data", "python_workflow_runs")


def _spawn_standalone_python_workflow(*, user_id: str, python_file: str, question: str, team: str = ""):
    runs_dir = _python_runs_dir()
    os.makedirs(runs_dir, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    log_path = os.path.join(runs_dir, f"{run_id}.log")
    result_path = os.path.join(runs_dir, f"{run_id}.json")
    meta_path = os.path.join(runs_dir, f"{run_id}.meta.json")
    python_executable = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
    if not os.path.isfile(python_executable):
        python_executable = sys.executable
    cmd = [
        python_executable,
        python_file,
        "--user-id",
        user_id or DEFAULT_USER,
        "--question",
        question or "",
        "--result-file",
        result_path,
    ]
    if team:
        cmd.extend(["--team", team])
    log_file = open(log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "CLAWCROSS_PROJECT_ROOT": PROJECT_ROOT,
            "CLAWCROSS_PYTHONPATH": PROJECT_ROOT,
        },
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log_file.close()
    meta = {
        "run_id": run_id,
        "pid": proc.pid,
        "log_file": log_path,
        "result_file": result_path,
        "python_file": python_file,
        "python_executable": python_executable,
        "user_id": user_id,
        "team": team,
        "question": question,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return meta


# ═══════════════════════════════════════════════════════════════════════
#  HTTP 工具函数
# ═══════════════════════════════════════════════════════════════════════

def _req(method, url, headers=None, data=None, params=None, timeout=30):
    """发送 HTTP 请求

    参数：
        method: HTTP 方法（GET/POST/PUT/DELETE）
        url: 请求 URL
        headers: 请求头
        data: 请求体数据（dict，会被 JSON 序列化）
        params: URL 查询参数
        timeout: 超时时间（秒）

    返回：
        tuple: (status_code, response_body)
    """
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body_bytes = None
    if data is not None:
        body_bytes = json.dumps(data).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body_bytes, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            raw = resp.read()
            if "json" in ct:
                return resp.status, json.loads(raw)
            return resp.status, raw
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
        except Exception:
            err = {"error": e.reason}
        return e.code, err
    except (socket.timeout, TimeoutError):
        return 0, {"error": "请求超时"}
    except urllib.error.URLError as e:
        return 0, {"error": f"连接失败: {e.reason}"}


def _stream_req(url, headers=None, data=None, params=None, timeout=300):
    """发送 SSE 流式请求，yield 每行数据

    参数：
        url: 请求 URL
        headers: 请求头
        data: 请求体数据
        params: URL 查询参数
        timeout: 超时时间（秒）

    yeilds:
        str: 每行响应数据
    """
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body_bytes = json.dumps(data).encode("utf-8") if data else None
    hdrs = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body_bytes, headers=hdrs, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        for raw_line in resp:
            yield raw_line.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        print(f"❌ HTTP {e.code}: {e.reason}", file=sys.stderr)
    except urllib.error.URLError as e:
        print(f"❌ 连接失败: {e.reason}", file=sys.stderr)


def _agent_headers():
    """返回 Agent API 的认证请求头"""
    return {"X-Internal-Token": INTERNAL_TOKEN}


def _group_headers(user_id):
    """返回群组 API 的认证请求头

    参数：
        user_id: 用户 ID

    返回：
        dict: 包含 Authorization 的请求头
    """
    return {"Authorization": f"Bearer {INTERNAL_TOKEN}:{user_id}"}


def _quote_group_id(gid: str) -> str:
    """Path segment for /groups/{group_id}/...（#、中文、:: 等须编码，否则 # 会截断路径）。"""
    return urllib.parse.quote((gid or "").strip(), safe="")


def _check_token():
    """检查 INTERNAL_TOKEN 是否已配置，未配置则退出"""
    if not INTERNAL_TOKEN:
        print("❌ INTERNAL_TOKEN 未配置，请先启动服务或在 config/.env 中设置", file=sys.stderr)
        sys.exit(1)


def _pp(obj):
    """美化打印 JSON 对象"""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ── 文档提示 ────────────────────────────────────────────────────────────────
# 不同场景的文档提示映射（提醒用户在操作前先阅读相关文档）
_DOC_HINTS = {
    "team": (
        "\n⚠️  【必读】在创建或修改 Team 之前，请务必先阅读以下文档：\n"
        "  📖 docs/build_team.md       — Team 创建/配置完整指南 (成员、人设、JSON 文件)\n"
        "  📖 docs/example_team.md     — 示例 Team 文件结构和内容\n"
        "  📖 docs/cli.md              — 完整 CLI 命令参考和示例\n"
        "  ❗ 不阅读文档直接操作可能导致配置错误或功能异常！\n"
    ),
    "workflow": (
        "\n⚠️  【必读】在创建或运行 Workflow 之前，请务必先阅读以下文档：\n"
        "  📖 docs/create_workflow.md  — OASIS 工作流 YAML 完整指南 (图格式、人设类型、示例)\n"
        "  📖 docs/cli.md              — 完整 CLI 命令参考和示例\n"
        "  ❗ 不阅读文档直接操作可能导致配置错误或功能异常！\n"
    ),
    "persona": (
        "\n⚠️  【必读】在添加或修改人设之前，请务必先阅读以下文档：\n"
        "  📖 docs/build_team.md       — 人设配置详解 (内部/外部 Agent 人设)\n"
        "  📖 docs/create_workflow.md  — 工作流中的人设类型说明\n"
        "  ❗ 不阅读文档直接操作可能导致配置错误或功能异常！\n"
    ),
    "openclaw": (
        "\n⚠️  【必读】在操作 OpenClaw Agent 之前，请务必先阅读以下文档：\n"
        "  📖 docs/openclaw-commands.md — OpenClaw agent 集成命令详解\n"
        "  📖 docs/build_team.md        — 将 OpenClaw agent 加入 Team\n"
        "  ❗ 不阅读文档直接操作可能导致配置错误或功能异常！\n"
    ),
    "internal_agent": (
        "\n⚠️  【必读】在操作内部 Agent 之前，请务必先阅读以下文档：\n"
        "  📖 docs/build_team.md       — 内部 Agent 配置详解\n"
        "  📖 docs/example_team.md     — 示例 Agent 配置\n"
        "  ❗ 不阅读文档直接操作可能导致配置错误或功能异常！\n"
    ),
    "status": (
        "\n⚠️  【必读】如需进一步配置或操作，请务必先阅读对应文档：\n"
        "  📖 docs/build_team.md       — 创建/配置 Team (成员、人设、JSON 文件)\n"
        "  📖 docs/create_workflow.md  — 创建 OASIS 工作流 YAML\n"
        "  📖 docs/cli.md              — 完整 CLI 命令参考和示例\n"
        "  📖 docs/openclaw-commands.md — OpenClaw agent 集成命令\n"
        "  📖 docs/ports.md            — 端口配置和冲突处理\n"
        "  ❗ 执行操作前务必先阅读相关文档，否则可能导致配置错误！\n"
    ),
}


def _print_doc_hint(hint_key: str):
    """输出文档阅读提示

    参数：
        hint_key: 提示类型键名
    """
    hint = _DOC_HINTS.get(hint_key, "")
    if hint:
        print(hint)


def _err(code, body):
    """格式化输出错误信息

    参数：
        code: HTTP 状态码或错误码
        body: 错误响应体
    """
    msg = body.get("error", body) if isinstance(body, dict) else body
    print(f"❌ [{code}] {msg}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════
#  子命令实现
# ═══════════════════════════════════════════════════════════════════════

# ── chat: 发送消息 ─────────────────────────────────────────────────────────
def cmd_chat(args):
    """通过 OpenAI 兼容接口发送消息（流式输出）

    参数：
        args: 命令行参数对象
    """
    if not args.user:
        print("❌ 请指定 -u/--user 用户名", file=sys.stderr)
        return
    _check_token()
    url = f"{AGENT_BASE}/v1/chat/completions"
    payload = {
        "model": args.model or "default",
        "messages": [{"role": "user", "content": args.message}],
        "stream": True,
        "user": args.user,
    }
    payload["session_id"] = args.session
    hdrs = {"Authorization": f"Bearer {INTERNAL_TOKEN}:{args.user}"}

    collected = []
    for line in _stream_req(url, headers=hdrs, data=payload):
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            text = delta.get("content", "")
            if text:
                print(text, end="", flush=True)
                collected.append(text)
        except json.JSONDecodeError:
            pass
    if collected:
        print()  # 换行


# ── sessions: 会话管理 ─────────────────────────────────────────────────────
def cmd_sessions(args):
    """查看会话列表

    参数：
        args: 命令行参数对象
    """
    _check_token()
    code, body = _req("POST", f"{AGENT_BASE}/sessions",
                       headers=_agent_headers(),
                       data={"user_id": args.user})
    if code == 200:
        if isinstance(body, dict) and "sessions" in body:
            sessions = body["sessions"]
        elif isinstance(body, list):
            sessions = body
        else:
            _pp(body)
            return
        if not sessions:
            print("📭 暂无会话")
            return
        print(f"📋 会话列表 ({len(sessions)} 个):\n")
        for session in sessions:
            sid = session.get("session_id", session.get("id", "?"))
            title = session.get("title", session.get("name", ""))
            status = session.get("status", "")
            updated = session.get("updated_at", session.get("last_active", ""))
            flag = "🟢" if status == "active" else "⚪"
            line = f"  {flag} {sid}"
            if title:
                line += f"  {title}"
            if updated:
                line += f"  ({updated})"
            print(line)
    else:
        _err(code, body)


def cmd_history(args):
    """查看会话历史

    参数：
        args: 命令行参数对象
    """
    _check_token()
    data = {"user_id": args.user, "session_id": args.session or "default"}
    code, body = _req("POST", f"{AGENT_BASE}/session_history",
                       headers=_agent_headers(), data=data)
    if code == 200:
        messages = body if isinstance(body, list) else body.get("messages", body.get("history", [body]))
        if not messages:
            print("📭 暂无历史记录")
            return
        limit = args.limit or len(messages)
        for msg in messages[-limit:]:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            icon = {"user": "👤", "assistant": "🤖", "system": "⚙️", "tool": "🔧"}.get(role, "❓")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
                ) or str(content)
            # 截断过长内容
            if len(content) > 500 and not args.full:
                content = content[:500] + "..."
            print(f"{icon} [{role}]: {content}\n")
    else:
        _err(code, body)


def cmd_delete_session(args):
    """删除会话

    参数：
        args: 命令行参数对象
    """
    _check_token()
    data = {"user_id": args.user, "session_id": args.session}
    code, body = _req("POST", f"{AGENT_BASE}/delete_session",
                       headers=_agent_headers(), data=data)
    if code == 200:
        print(f"✅ 会话 '{args.session}' 已删除")
    else:
        _err(code, body)


# ── settings: 设置 ─────────────────────────────────────────────────────────
def cmd_settings(args):
    """查看或修改设置

    参数：
        args: 命令行参数对象
    """
    _check_token()
    if args.set_key:
        # 修改设置
        data = {"user_id": args.user, args.set_key: args.set_value}
        code, body = _req("POST", f"{AGENT_BASE}/settings",
                           headers=_agent_headers(), data=data)
        if code == 200:
            print(f"✅ 设置已更新: {args.set_key} = {args.set_value}")
        else:
            _err(code, body)
    else:
        # 查看设置
        endpoint = "/settings/full" if args.full else "/settings"
        code, body = _req("GET", f"{AGENT_BASE}{endpoint}",
                           headers=_agent_headers(),
                           params={"user_id": args.user})
        if code == 200:
            _pp(body)
        else:
            _err(code, body)


# ── tools: 工具列表 ────────────────────────────────────────────────────────
def cmd_tools(args):
    """查看可用工具

    参数：
        args: 命令行参数对象
    """
    _check_token()
    code, body = _req("GET", f"{AGENT_BASE}/tools",
                       headers=_agent_headers(),
                       params={"user_id": args.user})
    if code == 200:
        tools = body if isinstance(body, list) else body.get("tools", [body])
        if not tools:
            print("📭 无可用工具")
            return
        print(f"🔧 可用工具 ({len(tools)} 个):\n")
        for tool in tools:
            name = tool.get("name", tool.get("function", {}).get("name", "?"))
            desc = tool.get("description", tool.get("function", {}).get("description", ""))
            print(f"  • {name}")
            if desc and not args.brief:
                print(f"    {desc[:100]}")
    else:
        _err(code, body)


# ── tts: 语音合成 ──────────────────────────────────────────────────────────
def cmd_tts(args):
    """文字转语音

    参数：
        args: 命令行参数对象
    """
    _check_token()
    data = {"user_id": args.user, "text": args.text}
    if args.voice:
        data["voice"] = args.voice
    code, body = _req("POST", f"{AGENT_BASE}/tts",
                       headers=_agent_headers(), data=data, timeout=60)
    if code == 200:
        if isinstance(body, bytes):
            out = args.output or "tts_output.mp3"
            with open(out, "wb") as f:
                f.write(body)
            print(f"✅ 音频已保存: {out} ({len(body)} bytes)")
        else:
            _pp(body)
    else:
        _err(code, body)


# ── cancel: 取消生成 ────────────────────────────────────────────────────────
def cmd_cancel(args):
    """取消当前生成

    参数：
        args: 命令行参数对象
    """
    _check_token()
    data = {"user_id": args.user, "session_id": args.session or "default"}
    code, body = _req("POST", f"{AGENT_BASE}/cancel",
                       headers=_agent_headers(), data=data)
    if code == 200:
        print("✅ 已取消")
    else:
        _err(code, body)


# ── restart: 重启 Agent ─────────────────────────────────────────────────────
def cmd_restart(args):
    """重启 Agent 服务（通过写入重启标记文件）

    参数：
        args: 命令行参数对象
    """
    flag = os.path.join(PROJECT_ROOT, ".restart_flag")
    with open(flag, "w") as f:
        f.write("restart")
    print("✅ 重启信号已发送（等待 launcher 处理）")


# ── groups: 群组管理 ────────────────────────────────────────────────────────
def cmd_groups(args):
    """群组管理

    参数：
        args: 命令行参数对象
    """
    hdrs = _group_headers(args.user)
    base = f"{AGENT_BASE}/groups"

    if args.action == "list":
        # 列出所有群组
        code, body = _req("GET", base, headers=hdrs)
        if code == 200:
            groups = body if isinstance(body, list) else body.get("groups", [body])
            if not groups:
                print("📭 暂无群组")
                return
            print(f"👥 群组列表 ({len(groups)} 个):\n")
            for group in groups:
                gid = group.get("id", group.get("group_id", "?"))
                name = group.get("name", "")
                print(f"  • [{gid}] {name}")
        else:
            _err(code, body)

    elif args.action == "create":
        # 创建群组
        if not args.team_name:
            print("❌ 请指定 --team-name", file=sys.stderr)
            return
        data = json.loads(args.data) if args.data else {
            "name": args.name or "新群组",
            "team_name": args.team_name,
        }
        code, body = _req("POST", base, headers=hdrs, data=data)
        if code in (200, 201):
            print("✅ 群组已创建")
            _pp(body)
        else:
            _err(code, body)

    elif args.action == "messages":
        # 获取群组消息
        if not args.group_id:
            print("❌ 请指定 --group-id", file=sys.stderr)
            return
        gid = _quote_group_id(args.group_id)
        url = f"{base}/{gid}/messages"
        if args.after_id:
            url += f"?after_id={args.after_id}"
        code, body = _req("GET", url, headers=hdrs)
        if code == 200:
            msgs = body if isinstance(body, list) else body.get("messages", [body])
            for msg in msgs[-20:]:
                sender = msg.get("sender", msg.get("user_id", "?"))
                content = msg.get("content", "")
                print(f"  [{sender}]: {content}")
        else:
            _err(code, body)

    elif args.action in {"send", "private-send"}:
        # 发送消息到群组
        if not args.group_id:
            print("❌ 请指定 --group-id", file=sys.stderr)
            return
        gid = _quote_group_id(args.group_id)
        url = f"{base}/{gid}/messages"
        data = {"content": args.message or ""}
        if args.sender:
            data["sender"] = args.sender
            # sender 格式: tag#type#short_name，作为发送者显示名
            data["sender_display"] = args.sender
        code, body = _req("POST", url, headers=hdrs, data=data)
        if code in (200, 201):
            if args.action == "private-send":
                print("✅ 私聊消息已发送")
            else:
                print("✅ 消息已发送")
        else:
            _err(code, body)

    elif args.action == "get":
        # 获取群组详情
        if not args.group_id:
            print("❌ 请指定 --group-id", file=sys.stderr)
            return
        gid = _quote_group_id(args.group_id)
        code, body = _req("GET", f"{base}/{gid}", headers=hdrs)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif args.action == "update":
        # 更新群组信息
        if not args.group_id:
            print("❌ 请指定 --group-id", file=sys.stderr)
            return
        data = json.loads(args.data) if args.data else {}
        gid = _quote_group_id(args.group_id)
        code, body = _req("PUT", f"{base}/{gid}", headers=hdrs, data=data)
        if code == 200:
            print("✅ 群组已更新")
            _pp(body)
        else:
            _err(code, body)

    elif args.action == "delete":
        # 删除群组
        if not args.group_id:
            print("❌ 请指定 --group-id", file=sys.stderr)
            return
        gid = _quote_group_id(args.group_id)
        code, body = _req("DELETE", f"{base}/{gid}", headers=hdrs)
        if code == 200:
            print(f"✅ 群组 {args.group_id} 已删除")
        else:
            _err(code, body)

    elif args.action == "mute":
        # 静音群组
        if not args.group_id:
            print("❌ 请指定 --group-id", file=sys.stderr)
            return
        gid = _quote_group_id(args.group_id)
        code, body = _req("POST", f"{base}/{gid}/mute", headers=hdrs)
        if code == 200:
            print("✅ 群组已静音")
        else:
            _err(code, body)

    elif args.action == "unmute":
        # 取消静音群组
        if not args.group_id:
            print("❌ 请指定 --group-id", file=sys.stderr)
            return
        gid = _quote_group_id(args.group_id)
        code, body = _req("POST", f"{base}/{gid}/unmute", headers=hdrs)
        if code == 200:
            print("✅ 群组已取消静音")
        else:
            _err(code, body)

    elif args.action == "mute-status":
        # 查看群组静音状态
        if not args.group_id:
            print("❌ 请指定 --group-id", file=sys.stderr)
            return
        gid = _quote_group_id(args.group_id)
        code, body = _req("GET", f"{base}/{gid}/mute_status", headers=hdrs)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif args.action == "sessions":
        # 查看群组会话
        if not args.group_id:
            print("❌ 请指定 --group-id", file=sys.stderr)
            return
        gid = _quote_group_id(args.group_id)
        code, body = _req("GET", f"{base}/{gid}/sessions", headers=hdrs)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif args.action == "sync-members":
        # 同步群组成员
        if not args.group_id:
            print("❌ 请指定 --group-id", file=sys.stderr)
            return
        gid = _quote_group_id(args.group_id)
        code, body = _req("POST", f"{base}/{gid}/sync_members", headers=hdrs)
        if code == 200:
            print("✅ 群成员已同步")
            _pp(body)
        else:
            _err(code, body)


# ── profile: 用户画像 ──────────────────────────────────────────────────────
def cmd_profile(args):
    """用户画像管理：读取或写入用户画像

    参数：
        args: 命令行参数对象

    用法：
        profile get   - 读取当前用户画像
        profile set   - 写入用户画像（支持 -c/--content 或 stdin）
        profile path  - 显示用户画像文件路径
    """
    user_id = args.user
    act = args.action

    # 构建用户画像文件路径
    profile_path = os.path.join(PROJECT_ROOT, "data", "user_files", user_id, "user_profile.txt")

    if act == "path":
        print(f"用户画像路径: {profile_path}")
        return

    if act == "get":
        if not os.path.isfile(profile_path):
            print("（暂无用户画像）")
            return
        with open(profile_path, "r", encoding="utf-8") as f:
            content = f.read()
        print(content or "（空）")
        return

    if act == "set":
        if args.content:
            text = args.content
        elif args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            print("❌ 请通过 -c/--content 指定内容，或 --file <文件路径>", file=sys.stderr)
            return
        os.makedirs(os.path.dirname(profile_path), exist_ok=True)
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"✅ 用户画像已保存（{len(text)} 字符）")
        return

    print(f"❌ 未知操作: {act}", file=sys.stderr)


# ── sessions-status: 所有会话忙碌状态 ──────────────────────────────────────
def cmd_sessions_status(args):
    """查看所有会话的忙碌状态

    参数：
        args: 命令行参数对象
    """
    _check_token()
    code, body = _req("POST", f"{AGENT_BASE}/sessions_status",
                       headers=_agent_headers(),
                       data={"user_id": args.user})
    if code == 200:
        _pp(body)
    else:
        _err(code, body)


# ── session-status: 单个会话状态 ───────────────────────────────────────────
def cmd_session_status(args):
    """查看单个会话是否有新消息

    参数：
        args: 命令行参数对象
    """
    _check_token()
    data = {"user_id": args.user, "session_id": args.session or "default"}
    code, body = _req("POST", f"{AGENT_BASE}/session_status",
                       headers=_agent_headers(), data=data)
    if code == 200:
        _pp(body)
    else:
        _err(code, body)


# ── topics: OASIS 话题 ─────────────────────────────────────────────────────
def cmd_topics(args):
    """OASIS 话题管理

    参数：
        args: 命令行参数对象
    """
    params = {"user_id": args.user}

    if args.action == "list":
        # 列出所有话题
        code, body = _req("GET", f"{OASIS_BASE}/topics", params=params)
        if code == 200:
            topics = body if isinstance(body, list) else body.get("topics", [body])
            if not topics:
                print("📭 暂无话题")
                return
            print(f"💬 OASIS 话题 ({len(topics)} 个):\n")
            for topic in topics:
                tid = topic.get("id", topic.get("topic_id", "?"))
                title = topic.get("title", topic.get("question", ""))
                status = topic.get("status", "")
                print(f"  • [{tid}] {title}  ({status})")
        else:
            _err(code, body)

    elif args.action == "show":
        # 显示话题详情
        if not args.topic_id:
            print("❌ 请指定 --topic-id", file=sys.stderr)
            return
        code, body = _req("GET", f"{OASIS_BASE}/topics/{args.topic_id}", params=params)
        if code == 200:
            if args.raw:
                _pp(body)
                return
            # 美化输出
            question = body.get("question", "")
            status = body.get("status", "?")
            current_round = body.get("current_round", "?")
            max_rounds = body.get("max_rounds", "?")
            is_discussion = body.get("discussion", True)
            status_icon = {"pending": "⏳", "discussing": "🔄", "concluded": "✅",
                           "error": "❌"}.get(status, "❓")
            print(f"{'─' * 60}")
            print(f"📋 话题: {question}")
            print(f"   状态: {status_icon} {status}  |  轮次: {current_round}/{max_rounds}  |  {'讨论模式' if is_discussion else '执行模式'}")
            print(f"{'─' * 60}")

            pending_human = body.get("pending_human")
            if pending_human:
                print("\n🙋 等待人类节点:")
                print(f"  节点: {pending_human.get('node_id', '?')}")
                print(f"  轮次: {pending_human.get('round_num', '?')}")
                print(f"  提示: {pending_human.get('prompt', '')}")
            # 时间线（执行模式下更有意义）
            timeline = body.get("timeline", [])
            if timeline:
                print(f"\n⏱️  时间线 ({len(timeline)} 事件):")
                for event in timeline:
                    elapsed = event.get("elapsed", 0)
                    event_type = event.get("event", "")
                    agent = event.get("agent", "")
                    detail = event.get("detail", "")
                    ev_icon = {"start": "🚀", "round": "📢", "agent_call": "⏳",
                               "agent_done": "✅", "conclude": "🏁"}.get(event_type, "•")
                    parts = [f"  {ev_icon} [{elapsed:.1f}s] {event_type}"]
                    if agent:
                        parts.append(agent)
                    if detail:
                        parts.append(f"— {detail}")
                    print(" ".join(parts))

            # 帖子/发言
            posts = body.get("posts", [])
            if posts:
                print(f"\n💬 发言记录 ({len(posts)} 条):\n")
                for post in posts:
                    author = post.get("author", "?")
                    content = post.get("content", "")
                    reply_to = post.get("reply_to")
                    upvotes = post.get("upvotes", 0)
                    elapsed = post.get("elapsed", 0)
                    pid = post.get("id", "?")

                    header = f"  ┌─ #{pid} [{author}]"
                    if reply_to:
                        header += f" ↳回复#{reply_to}"
                    header += f"  ({elapsed:.1f}s)"
                    if upvotes:
                        header += f"  👍{upvotes}"
                    print(header)

                    # 内容缩进显示，限制过长内容
                    lines = content.strip().split("\n")
                    max_lines = 30 if not args.full else len(lines)
                    for i, line in enumerate(lines[:max_lines]):
                        print(f"  │ {line}")
                    if len(lines) > max_lines:
                        print(f"  │ ... (共 {len(lines)} 行，用 --full 查看完整)")
                    print(f"  └{'─' * 40}")
            else:
                print("\n📭 暂无发言")

            # 结论
            conclusion = body.get("conclusion")
            if conclusion:
                print(f"\n{'═' * 60}")
                print(f"🏆 结论:\n")
                print(conclusion)
                print(f"{'═' * 60}")
        else:
            _err(code, body)

    elif args.action == "watch":
        # 实时跟踪话题
        if not args.topic_id:
            print("❌ 请指定 --topic-id", file=sys.stderr)
            return
        print(f"👀 实时跟踪话题 {args.topic_id}（Ctrl+C 退出）...\n")
        stream_url = f"{OASIS_BASE}/topics/{args.topic_id}/stream"
        if params:
            stream_url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(stream_url, method="GET",
                                      headers={"Accept": "text/event-stream"})
        try:
            resp = urllib.request.urlopen(req, timeout=600)
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        print("\n✅ 讨论结束")
                        break
                    print(data_str)
        except KeyboardInterrupt:
            print("\n⏹️ 已停止跟踪")
        except urllib.error.HTTPError as e:
            print(f"❌ HTTP {e.code}: {e.reason}", file=sys.stderr)
        except urllib.error.URLError as e:
            print(f"❌ 连接失败: {e.reason}", file=sys.stderr)

    elif args.action == "cancel":
        # 取消话题
        if not args.topic_id:
            print("❌ 请指定 --topic-id", file=sys.stderr)
            return
        code, body = _req("DELETE", f"{OASIS_BASE}/topics/{args.topic_id}", params=params)
        if code == 200:
            print(f"✅ 话题 {args.topic_id} 已取消")
        else:
            _err(code, body)

    elif args.action == "purge":
        # 清除话题
        if not args.topic_id:
            print("❌ 请指定 --topic-id", file=sys.stderr)
            return
        code, body = _req("POST", f"{OASIS_BASE}/topics/{args.topic_id}/purge", params=params)
        if code == 200:
            print(f"✅ 话题 {args.topic_id} 已清除")
        else:
            _err(code, body)

    elif args.action == "callback":
        if not args.topic_id:
            print("❌ 请指定 --topic-id", file=sys.stderr)
            return
        if not args.author:
            print("❌ 请指定 --author", file=sys.stderr)
            return
        if args.round_num is None:
            print("❌ 请指定 --round-num", file=sys.stderr)
            return
        if not args.data:
            print("❌ 请指定 --data <JSON对象>", file=sys.stderr)
            return
        try:
            result = json.loads(args.data)
        except json.JSONDecodeError as e:
            print(f"❌ --data 不是合法 JSON: {e}", file=sys.stderr)
            return
        if not isinstance(result, dict):
            print("❌ --data 必须是 JSON 对象", file=sys.stderr)
            return
        data = {
            "user_id": args.user,
            "author": args.author,
            "round_num": args.round_num,
            "result": result,
        }
        code, body = _req("POST", f"{OASIS_BASE}/topics/{args.topic_id}/callback", data=data)
        if code == 200:
            print(f"✅ Agent callback 已提交到话题 {args.topic_id}")
            _pp(body)
        else:
            _err(code, body)

    elif args.action == "human-reply":
        if not args.topic_id:
            print("❌ 请指定 --topic-id", file=sys.stderr)
            return
        if not args.node_id:
            print("❌ 请指定 --node-id", file=sys.stderr)
            return
        if args.round_num is None:
            print("❌ 请指定 --round-num", file=sys.stderr)
            return
        if not args.message:
            print("❌ 请指定 --message", file=sys.stderr)
            return
        data = {
            "user_id": args.user,
            "node_id": args.node_id,
            "round_num": args.round_num,
            "content": args.message,
            "author": args.author or args.user,
        }
        code, body = _req("POST", f"{OASIS_BASE}/topics/{args.topic_id}/human-reply", data=data)
        if code == 200:
            print(f"✅ 人类回复已提交到话题 {args.topic_id}")
            _pp(body)
        else:
            _err(code, body)
    elif args.action == "delete-all":
        # 删除所有话题
        code, body = _req("DELETE", f"{OASIS_BASE}/topics", params=params)
        if code == 200:
            print("✅ 所有话题已删除")
        else:
            _err(code, body)


# ── experts: OASIS 人设 ───────────────────────────────────────────────────
def cmd_experts(args):
    """OASIS 人设管理

    参数：
        args: 命令行参数对象
    """
    act = args.action

    if act == "list":
        # 列出所有人设
        params = {"user_id": args.user}
        if args.team:
            params["team"] = args.team
        code, body = _req("GET", f"{OASIS_BASE}/experts", params=params)
        if code == 200:
            experts = body if isinstance(body, list) else body.get("experts", [body])
            if not experts:
                print("📭 暂无人设")
                return
            print(f"🧑‍🏫 人设列表 ({len(experts)} 个):\n")
            for expert in experts:
                tag = expert.get("tag", expert.get("id", "?"))
                name = expert.get("name", tag)
                role = expert.get("role", "")
                print(f"  • [{tag}] {name}")
                if role:
                    print(f"    {role[:80]}")
            _print_doc_hint("persona")
        else:
            _err(code, body)

    elif act == "add":
        # 添加人设
        if not args.tag:
            print("❌ 请指定 --tag <人设标签>", file=sys.stderr)
            return
        if not args.persona_name:
            print("❌ 请指定 --persona-name <人设名称>", file=sys.stderr)
            return
        data = {
            "user_id": args.user,
            "tag": args.tag,
            "name": args.persona_name,
            "team": args.team or "",
        }
        if args.persona:
            data["persona"] = args.persona
        if args.temperature is not None:
            data["temperature"] = args.temperature
        code, body = _req("POST", f"{OASIS_BASE}/experts/user", data=data)
        if code == 200:
            print(f"✅ 人设已添加: [{args.tag}] {args.persona_name}")
            _pp(body)
        else:
            _err(code, body)

    elif act == "update":
        # 更新人设
        if not args.tag:
            print("❌ 请指定 --tag <人设标签>", file=sys.stderr)
            return
        data = {
            "user_id": args.user,
            "tag": args.tag,
            "team": args.team or "",
        }
        if args.persona_name:
            data["name"] = args.persona_name
        if args.persona:
            data["persona"] = args.persona
        if args.temperature is not None:
            data["temperature"] = args.temperature
        code, body = _req("PUT", f"{OASIS_BASE}/experts/user/{args.tag}", data=data)
        if code == 200:
            print(f"✅ 人设已更新: [{args.tag}]")
            _pp(body)
        else:
            _err(code, body)

    elif act == "delete":
        # 删除人设
        if not args.tag:
            print("❌ 请指定 --tag <人设标签>", file=sys.stderr)
            return
        params = {"user_id": args.user}
        if args.team:
            params["team"] = args.team
        code, body = _req("DELETE", f"{OASIS_BASE}/experts/user/{args.tag}", params=params)
        if code == 200:
            print(f"✅ 人设已删除: [{args.tag}]")
        else:
            _err(code, body)

    else:
        print(f"❌ 未知操作: {act}", file=sys.stderr)


# ── workflows: OASIS Workflow 管理 ────────────────────────────────────────
def cmd_workflows(args):
    """OASIS Workflow 管理

    参数：
        args: 命令行参数对象
    """
    act = args.action
    workflow_type = getattr(args, "type", "all") or "all"

    if act == "list":
        items = []
        if workflow_type in ("all", "yaml"):
            for scope, team_name, yaml_dir in _iter_yaml_workflow_dirs(args.user, args.team or ""):
                if not os.path.isdir(yaml_dir):
                    continue
                files = sorted(f for f in os.listdir(yaml_dir) if f.endswith((".yaml", ".yml")))
                for fname in files:
                    desc = ""
                    fpath = os.path.join(yaml_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            first = f.readline().strip()
                        if first.startswith("#"):
                            desc = first.lstrip("# ").strip()
                    except Exception:
                        pass
                    items.append({
                        "kind": "yaml",
                        "file": fname,
                        "description": desc,
                        "scope": scope,
                        "team": team_name,
                    })
        if workflow_type in ("all", "python"):
            for scope, team_name, py_dir in _iter_python_workflow_dirs(args.user, args.team or ""):
                if not os.path.isdir(py_dir):
                    continue
                files = sorted(f for f in os.listdir(py_dir) if f.endswith(".py"))
                for fname in files:
                    preview = ""
                    fpath = os.path.join(py_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            preview = f.readline().strip()
                    except Exception:
                        pass
                    items.append({
                        "kind": "python",
                        "file": fname,
                        "description": preview[:100],
                        "scope": scope,
                        "team": team_name,
                    })
        if not items:
            print("📭 暂无 workflow")
            return
        items.sort(key=lambda it: (it["kind"], it["scope"], it["team"], it["file"]))
        print(f"📋 Workflows ({len(items)} 个):\n")
        for workflow in items:
            location = f"[team:{workflow['team']}]" if workflow["scope"] == "team" else "[personal]"
            desc = workflow.get("description", "")
            print(f"  • [{workflow['kind']}] {location} {workflow['file']}")
            if desc:
                print(f"    {desc}")
        if args.team:
            print(f"\n💡 当前只显示 team=\"{args.team}\" 下的 workflow。")
        else:
            print("\n💡 未指定 --team，已展示 personal 和全部 team 的 workflow。")
        _print_doc_hint("workflow")

    elif act == "show":
        # 显示 workflow 文件内容
        if not args.name:
            print("❌ 请指定 --name <workflow文件名>", file=sys.stderr)
            return
        selected_type = workflow_type
        if selected_type == "all":
            if args.name.endswith(".py"):
                selected_type = "python"
            else:
                selected_type = "yaml"
        if selected_type == "python":
            path, err = _resolve_python_workflow_path(args.user, args.name, args.team or "")
        else:
            path, err = _resolve_yaml_workflow_path(args.user, args.name, args.team or "")
        if err:
            print(f"❌ {err}", file=sys.stderr)
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                print(f.read())
        except Exception as e:
            print(f"❌ 读取文件失败: {e}", file=sys.stderr)

    elif act == "save":
        # 保存 workflow
        if not args.name:
            print("❌ 请指定 --name <workflow名称>", file=sys.stderr)
            return
        # 从 --yaml-file 读取 YAML 内容，或从 --yaml 直接传入
        yaml_content = None
        if args.yaml_file:
            try:
                with open(args.yaml_file, "r", encoding="utf-8") as f:
                    yaml_content = f.read()
            except Exception as e:
                print(f"❌ 读取文件失败: {e}", file=sys.stderr)
                return
        elif args.yaml:
            yaml_content = args.yaml
        else:
            print("❌ 请指定 --yaml <YAML内容> 或 --yaml-file <YAML文件路径>", file=sys.stderr)
            return
        data = {
            "user_id": args.user,
            "name": args.name,
            "schedule_yaml": yaml_content,
            "description": args.description or "",
            "team": args.team or "",
        }
        code, body = _req("POST", f"{OASIS_BASE}/workflows", data=data)
        if code == 200:
            print(f"✅ Workflow 已保存: {body.get('file', args.name)}")
        else:
            _err(code, body)

    elif act == "run":
        # 运行 workflow
        if not args.question:
            print("❌ 请指定 --question <讨论问题/任务>", file=sys.stderr)
            return
        selected_type = workflow_type
        if selected_type == "all":
            if args.name and args.name.endswith(".py"):
                selected_type = "python"
            elif args.python_file:
                selected_type = "python"
            else:
                selected_type = "yaml"

        if selected_type == "python":
            if args.yaml or args.yaml_file:
                print("❌ Python workflow 不支持 --yaml / --yaml-file，请使用 --name 或 --python-file", file=sys.stderr)
                return
            if args.python_file and args.name:
                print("❌ 请只使用 --name 或 --python-file 其中一种", file=sys.stderr)
                return
            python_target = args.python_file or args.name
            if not python_target:
                print("❌ 请指定 --name <已保存的python workflow名> 或 --python-file <Python文件路径>", file=sys.stderr)
                return
            if os.path.isabs(python_target) or os.path.isfile(python_target):
                python_path = python_target
                if not os.path.isfile(python_path):
                    print(f"❌ 文件不存在: {python_path}", file=sys.stderr)
                    return
            else:
                python_path, err = _resolve_python_workflow_path(args.user, python_target, args.team or "")
                if err:
                    print(f"❌ {err}", file=sys.stderr)
                    return
            payload = _spawn_standalone_python_workflow(
                user_id=args.user,
                python_file=python_path,
                question=args.question,
                team=args.team or "",
            )
            print("🐍 Python workflow 已启动（standalone）")
            print(f"   Run ID: {payload['run_id']}")
            print(f"   PID: {payload['pid']}")
            print(f"   Log: {payload['log_file']}")
            print(f"   Result: {payload['result_file']}")
            return

        data = {"user_id": args.user, "question": args.question, "team": args.team or ""}
        if args.name:
            yaml_path, err = _resolve_yaml_workflow_path(args.user, args.name, args.team or "")
            if err:
                print(f"❌ {err}", file=sys.stderr)
                return
            data["schedule_file"] = yaml_path
        elif args.yaml_file:
            try:
                with open(args.yaml_file, "r", encoding="utf-8") as f:
                    data["schedule_yaml"] = f.read()
            except Exception as e:
                print(f"❌ 读取文件失败: {e}", file=sys.stderr)
                return
        elif args.yaml:
            data["schedule_yaml"] = args.yaml
        else:
            print("❌ 请指定 --name <已保存的workflow名> 或 --yaml-file <YAML文件> 或 --yaml <YAML内容>", file=sys.stderr)
            return

        if args.max_rounds:
            data["max_rounds"] = args.max_rounds
        if args.discussion is not None:
            data["discussion"] = args.discussion
        if args.early_stop:
            data["early_stop"] = True

        code, body = _req("POST", f"{OASIS_BASE}/topics", data=data, timeout=30)
        if code == 200:
            tid = body.get("topic_id", "?")
            msg = body.get("message", "")
            print("🚀 YAML workflow 已启动!")
            print(f"   Topic ID: {tid}")
            print(f"   {msg}")
            print(f"\n   查看详情: uv run scripts/cli.py -u {args.user} topics show --topic-id {tid}")
            print(f"   实时跟踪: uv run scripts/cli.py -u {args.user} topics watch --topic-id {tid}")
            print(f"   等待结论: uv run scripts/cli.py -u {args.user} workflows conclusion --topic-id {tid}")
        else:
            _err(code, body)

    elif act == "conclusion":
        # 获取 workflow 结论
        if not args.topic_id:
            print("❌ 请指定 --topic-id <话题ID>", file=sys.stderr)
            return
        params = {"user_id": args.user}
        timeout = args.timeout or 300
        params["timeout"] = timeout
        print(f"⏳ 等待话题 {args.topic_id} 结论 (最多 {timeout}s)...")
        code, body = _req("GET", f"{OASIS_BASE}/topics/{args.topic_id}/conclusion",
                           params=params, timeout=timeout + 10)
        if code == 200:
            status = body.get("status", "")
            if status == "running":
                print(f"⏳ 话题仍在运行中 (第 {body.get('current_round', '?')} 轮, {body.get('total_posts', 0)} 条发言)")
                print("   稍后再试")
            else:
                print(f"✅ 话题已结束 ({body.get('rounds', '?')} 轮, {body.get('total_posts', 0)} 条发言)\n")
                print("📋 结论:")
                print(body.get("conclusion", "(无)"))
        else:
            _err(code, body)

    else:
        print(f"❌ 未知操作: {act}", file=sys.stderr)


# ── tunnel: Tunnel 管理 ───────────────────────────────────────────────────
def cmd_tunnel(args):
    """Cloudflare Tunnel 管理

    参数：
        args: 命令行参数对象
    """
    pidfile = os.path.join(PROJECT_ROOT, ".tunnel.pid")

    def _is_running():
        """检查 tunnel 是否正在运行"""
        if not os.path.exists(pidfile):
            return False, 0
        with open(pidfile) as f:
            pid = int(f.read().strip())
        try:
            os.kill(pid, 0)
            return True, pid
        except OSError:
            return False, pid

    def _get_public_domain():
        """获取公网域名"""
        env_path = os.path.join(PROJECT_ROOT, "config", ".env")
        if not os.path.exists(env_path):
            return None
        with open(env_path) as f:
            for line in f:
                if line.strip().startswith("PUBLIC_DOMAIN="):
                    v = line.strip().split("=", 1)[1].strip()
                    if v and v != "wait to set":
                        return v
        return None

    if args.action == "status":
        # 查看 tunnel 状态
        ok, pid = _is_running()
        if ok:
            domain = _get_public_domain()
            print(f"✅ Tunnel 运行中 (PID: {pid})")
            if domain:
                print(f"🌍 公网地址: {domain}")
            else:
                print("⏳ 公网地址尚未就绪")
        else:
            print("❌ Tunnel 未运行")

    elif args.action == "start":
        # 启动 tunnel
        ok, pid = _is_running()
        if ok:
            print(f"⚠️ Tunnel 已在运行 (PID: {pid})")
            return
        print("🌐 启动 Tunnel...")
        log = os.path.join(PROJECT_ROOT, "logs", "tunnel.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        proc = subprocess.Popen(
            [sys.executable, os.path.join(PROJECT_ROOT, "scripts", "tunnel.py")],
            stdout=open(log, "w"), stderr=subprocess.STDOUT,
            cwd=PROJECT_ROOT, start_new_session=True
        )
        with open(pidfile, "w") as f:
            f.write(str(proc.pid))
        print(f"✅ Tunnel 已启动 (PID: {proc.pid})")
        print(f"   日志: {log}")
        # 等待公网地址
        for _ in range(30):
            time.sleep(2)
            domain = _get_public_domain()
            if domain:
                print(f"🌍 公网地址: {domain}")
                return
        print("⏳ 公网地址尚未就绪，请查看日志")

    elif args.action == "stop":
        # 停止 tunnel
        ok, pid = _is_running()
        if not ok:
            print("Tunnel 未运行")
            return
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            os.kill(pid, signal.SIGKILL)
        if os.path.exists(pidfile):
            os.remove(pidfile)
        print("✅ Tunnel 已停止")


# ── openclaw: OpenClaw Agent 管理 ─────────────────────────────────────────
def cmd_openclaw(args):
    """OpenClaw Agent 管理

    参数：
        args: 命令行参数对象
    """
    _check_token()
    act = args.action

    if act == "sessions":
        # 查看 OpenClaw 会话列表
        params = {}
        if args.filter:
            params["filter"] = args.filter
        code, body = _req("GET", f"{OASIS_BASE}/sessions/openclaw",
                           params=params)
        if code == 200:
            _pp(body)
            _print_doc_hint("openclaw")
        else:
            _err(code, body)

    elif act == "add":
        # 添加 OpenClaw Agent
        data = json.loads(args.data) if args.data else {}
        code, body = _req("POST", f"{OASIS_BASE}/sessions/openclaw/add",
                           data=data, timeout=35)
        if code == 200:
            print("✅ Agent 已添加")
            _pp(body)
        else:
            _err(code, body)

    elif act == "default-workspace":
        # 获取默认工作区
        code, body = _req("GET", f"{OASIS_BASE}/sessions/openclaw/default-workspace")
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "workspace-files":
        # 列出工作区文件
        code, body = _req("GET", f"{OASIS_BASE}/sessions/openclaw/workspace-files",
                           params={"workspace": args.workspace or ""})
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "workspace-file-read":
        # 读取工作区文件
        code, body = _req("GET", f"{OASIS_BASE}/sessions/openclaw/workspace-file",
                           params={"workspace": args.workspace or "",
                                   "filename": args.filename or ""})
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "workspace-file-save":
        # 保存工作区文件
        data = json.loads(args.data) if args.data else {}
        code, body = _req("POST", f"{OASIS_BASE}/sessions/openclaw/workspace-file",
                           data=data, timeout=15)
        if code == 200:
            print("✅ 文件已保存")
            _pp(body)
        else:
            _err(code, body)

    elif act == "detail":
        # 获取 Agent 详情
        code, body = _req("GET", f"{OASIS_BASE}/sessions/openclaw/agent-detail",
                           params={"name": args.name or ""}, timeout=15)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "skills":
        # 查看 Agent 技能
        params = {}
        if args.agent:
            params["name"] = args.agent
        code, body = _req("GET", f"{OASIS_BASE}/sessions/openclaw/skills",
                           params=params, timeout=20)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "tool-groups":
        # 查看工具组
        code, body = _req("GET", f"{OASIS_BASE}/sessions/openclaw/tool-groups")
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "update-config":
        # 更新配置
        data = json.loads(args.data) if args.data else {}
        code, body = _req("POST", f"{OASIS_BASE}/sessions/openclaw/update-config",
                           data=data, timeout=15)
        if code == 200:
            print("✅ 配置已更新")
            _pp(body)
        else:
            _err(code, body)

    elif act == "channels":
        # 查看频道
        code, body = _req("GET", f"{OASIS_BASE}/sessions/openclaw/channels",
                           timeout=45)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "bindings":
        # 查看绑定
        code, body = _req("GET", f"{OASIS_BASE}/sessions/openclaw/agent-bindings",
                           params={"agent": args.agent or ""}, timeout=45)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "bind":
        # 绑定 Agent
        data = json.loads(args.data) if args.data else {}
        code, body = _req("POST", f"{OASIS_BASE}/sessions/openclaw/agent-bind",
                           data=data, timeout=45)
        if code == 200:
            print("✅ 绑定成功")
            _pp(body)
        else:
            _err(code, body)

    elif act == "remove":
        # 删除 Agent
        data = {"name": args.name or ""}
        code, body = _req("DELETE", f"{OASIS_BASE}/sessions/openclaw/remove",
                           data=data, timeout=15)
        if code == 200:
            print("✅ Agent 已删除")
            _pp(body)
        else:
            _err(code, body)

    else:
        print(f"❌ 未知操作: {act}", file=sys.stderr)


# ── openclaw-snapshot: OpenClaw 快照管理 ────────────────────────────────────
def _front_headers(args=None):
    """前端接口的请求头（带 session cookie 模拟 + 用户身份）

    参数：
        args: 命令行参数对象

    返回：
        dict: 请求头字典
    """
    h = {"X-Internal-Token": INTERNAL_TOKEN}
    # 将 CLI 的 -u/--user 通过 X-User-Id 传给 front.py
    uid = getattr(args, "user", None) if args else None
    if not uid:
        uid = _cli_user  # 回退到全局缓存的用户名
    if uid:
        h["X-User-Id"] = uid
    return h


def cmd_openclaw_snapshot(args):
    """OpenClaw 快照管理 (通过 front.py 接口)

    参数：
        args: 命令行参数对象
    """
    _check_token()
    act = args.action

    if act == "get":
        # 获取快照
        code, body = _req("GET", f"{FRONT_BASE}/team_openclaw_snapshot",
                           headers=_front_headers(),
                           params={"team": args.team or ""})
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "export":
        # 导出快照
        data = {"team": args.team or "", "agent_name": args.agent_name or "",
                "short_name": args.short_name or ""}
        code, body = _req("POST", f"{FRONT_BASE}/team_openclaw_snapshot/export",
                           headers=_front_headers(), data=data, timeout=30)
        if code == 200:
            print("✅ 导出成功")
            _pp(body)
        else:
            _err(code, body)

    elif act == "sync-all":
        # 同步所有快照
        data = {"team": args.team or ""}
        code, body = _req("POST", f"{FRONT_BASE}/team_openclaw_snapshot/sync_all",
                           headers=_front_headers(), data=data, timeout=60)
        if code == 200:
            print("✅ 同步完成")
            _pp(body)
        else:
            _err(code, body)

    elif act == "restore":
        # 恢复快照
        data = {"team": args.team or "", "short_name": args.short_name or ""}
        if args.target_name:
            data["target_agent_name"] = args.target_name
        code, body = _req("POST", f"{FRONT_BASE}/team_openclaw_snapshot/restore",
                           headers=_front_headers(), data=data, timeout=60)
        if code == 200:
            print("✅ 恢复成功")
            _pp(body)
        else:
            _err(code, body)

    elif act == "export-all":
        # 导出所有快照
        data = {"team": args.team or ""}
        code, body = _req("POST", f"{FRONT_BASE}/team_openclaw_snapshot/export_all",
                           headers=_front_headers(), data=data, timeout=120)
        if code == 200:
            print("✅ 全部导出完成")
            _pp(body)
        else:
            _err(code, body)

    elif act == "restore-all":
        # 恢复所有快照
        data = {"team": args.team or ""}
        code, body = _req("POST", f"{FRONT_BASE}/team_openclaw_snapshot/restore_all",
                           headers=_front_headers(), data=data, timeout=120)
        if code == 200:
            print("✅ 全部恢复完成")
            _pp(body)
        else:
            _err(code, body)

    else:
        print(f"❌ 未知操作: {act}", file=sys.stderr)


# ── visual: 可视化编排 ─────────────────────────────────────────────────────
def cmd_visual(args):
    """可视化编排管理

    参数：
        args: 命令行参数对象
    """
    _check_token()
    act = args.action

    if act == "personas":
        # 查看自定义人设
        params = {}
        if args.team:
            params["team"] = args.team
        code, body = _req("GET", f"{FRONT_BASE}/proxy_visual/experts",
                           headers=_front_headers(), params=params)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "add-persona":
        # 添加自定义人设
        data = json.loads(args.data) if args.data else {}
        if args.team:
            data["team"] = args.team
        code, body = _req("POST", f"{FRONT_BASE}/proxy_visual/experts/custom",
                           headers=_front_headers(), data=data)
        if code == 200:
            print("✅ 自定义人设已添加")
            _pp(body)
        else:
            _err(code, body)

    elif act == "delete-persona":
        # 删除自定义人设
        params = {}
        if args.team:
            params["team"] = args.team
        code, body = _req("DELETE",
                           f"{FRONT_BASE}/proxy_visual/experts/custom/{args.tag}",
                           headers=_front_headers(), params=params)
        if code == 200:
            print("✅ 人设已删除")
        else:
            _err(code, body)

    elif act == "generate-yaml":
        # 生成 YAML
        data = json.loads(args.data) if args.data else {}
        code, body = _req("POST", f"{FRONT_BASE}/proxy_visual/generate-yaml",
                           headers=_front_headers(), data=data)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "agent-generate-yaml":
        # Agent 生成 YAML
        data = json.loads(args.data) if args.data else {}
        if args.team:
            data["team"] = args.team
        code, body = _req("POST", f"{FRONT_BASE}/proxy_visual/agent-generate-yaml",
                           headers=_front_headers(), data=data, timeout=60)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "save-layout":
        # 保存布局
        data = json.loads(args.data) if args.data else {}
        if args.team:
            data["team"] = args.team
        code, body = _req("POST", f"{FRONT_BASE}/proxy_visual/save-layout",
                           headers=_front_headers(), data=data)
        if code == 200:
            print("✅ 布局已保存")
        else:
            _err(code, body)

    elif act == "load-layouts":
        # 加载所有布局
        params = {}
        if args.team:
            params["team"] = args.team
        code, body = _req("GET", f"{FRONT_BASE}/proxy_visual/load-layouts",
                           headers=_front_headers(), params=params)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "load-layout":
        # 加载指定布局
        params = {}
        if args.team:
            params["team"] = args.team
        code, body = _req("GET",
                           f"{FRONT_BASE}/proxy_visual/load-layout/{args.name}",
                           headers=_front_headers(), params=params)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "load-yaml-raw":
        # 原始加载 YAML
        params = {}
        if args.team:
            params["team"] = args.team
        code, body = _req("GET",
                           f"{FRONT_BASE}/proxy_visual/load-yaml-raw/{args.name}",
                           headers=_front_headers(), params=params)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "delete-layout":
        # 删除布局
        params = {}
        if args.team:
            params["team"] = args.team
        code, body = _req("DELETE",
                           f"{FRONT_BASE}/proxy_visual/delete-layout/{args.name}",
                           headers=_front_headers(), params=params)
        if code == 200:
            print("✅ 布局已删除")
        else:
            _err(code, body)

    elif act == "upload-yaml":
        # 上传 YAML
        data = json.loads(args.data) if args.data else {}
        if args.team:
            data["team"] = args.team
        code, body = _req("POST", f"{FRONT_BASE}/proxy_visual/upload-yaml",
                           headers=_front_headers(), data=data)
        if code == 200:
            print("✅ YAML 已上传")
            _pp(body)
        else:
            _err(code, body)

    elif act == "sessions-status":
        # 查看会话状态
        code, body = _req("GET", f"{FRONT_BASE}/proxy_visual/sessions-status",
                           headers=_front_headers())
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    else:
        print(f"❌ 未知操作: {act}", file=sys.stderr)


# ── internal-agents: 内部 Agent CRUD ──────────────────────────────────────
def cmd_internal_agents(args):
    """内部 Agent 管理

    参数：
        args: 命令行参数对象
    """
    _check_token()
    act = args.action
    params = {}
    if args.team:
        params["team"] = args.team

    if act == "list":
        # 列出内部 Agent
        code, body = _req("GET", f"{FRONT_BASE}/internal_agents",
                           headers=_front_headers(), params=params)
        if code == 200:
            _pp(body)
            _print_doc_hint("internal_agent")
        else:
            _err(code, body)

    elif act == "add":
        # 添加内部 Agent
        data = json.loads(args.data) if args.data else {}
        if "session" not in data and args.session:
            data["session"] = args.session
        code, body = _req("POST", f"{FRONT_BASE}/internal_agents",
                           headers=_front_headers(), data=data,
                           params=params)
        if code == 200:
            print("✅ Agent 已添加")
            _pp(body)
        else:
            _err(code, body)

    elif act == "update":
        # 更新内部 Agent
        if not args.sid:
            print("❌ 请指定 --sid", file=sys.stderr)
            return
        data = json.loads(args.data) if args.data else {}
        code, body = _req("PUT", f"{FRONT_BASE}/internal_agents/{args.sid}",
                           headers=_front_headers(), data=data,
                           params=params)
        if code == 200:
            print("✅ Agent 已更新")
            _pp(body)
        else:
            _err(code, body)

    elif act == "delete":
        # 删除内部 Agent
        if not args.sid:
            print("❌ 请指定 --sid", file=sys.stderr)
            return
        code, body = _req("DELETE", f"{FRONT_BASE}/internal_agents/{args.sid}",
                           headers=_front_headers(), params=params)
        if code == 200:
            print(f"✅ Agent {args.sid} 已删除")
        else:
            _err(code, body)

    else:
        print(f"❌ 未知操作: {act}", file=sys.stderr)


# ── teams: Team 管理 ───────────────────────────────────────────────────────
def cmd_teams(args):
    """Team 管理

    参数：
        args: 命令行参数对象
    """
    _check_token()
    act = args.action

    if act == "list":
        # 列出所有 Team
        code, body = _req("GET", f"{FRONT_BASE}/teams",
                           headers=_front_headers())
        if code == 200:
            _pp(body)
            _print_doc_hint("team")
        else:
            _err(code, body)

    elif act == "create":
        # 创建 Team
        data = {"team": args.team_name or ""}
        code, body = _req("POST", f"{FRONT_BASE}/teams",
                           headers=_front_headers(), data=data)
        if code == 200:
            print("✅ Team 已创建")
            _pp(body)
            _print_doc_hint("team")
        else:
            _err(code, body)

    elif act == "delete":
        # 删除 Team
        if not args.team_name:
            print("❌ 请指定 --team-name", file=sys.stderr)
            return
        code, body = _req("DELETE", f"{FRONT_BASE}/teams/{args.team_name}",
                           headers=_front_headers(), timeout=30)
        if code == 200:
            print(f"✅ Team {args.team_name} 已删除")
            _pp(body)
        else:
            _err(code, body)

    elif act == "rename":
        if not args.team_name or not getattr(args, "new_name", None):
            print("❌ rename 需要 --team-name 与 --new-name（仅重命名 teams 下文件夹）", file=sys.stderr)
            return
        new_name = (args.new_name or "").strip()
        if not new_name:
            print("❌ --new-name 不能为空", file=sys.stderr)
            return
        from urllib.parse import quote as _quote
        path = _quote(args.team_name, safe="")
        code, body = _req(
            "PATCH",
            f"{FRONT_BASE}/teams/{path}",
            headers=_front_headers(),
            data={"new_name": new_name},
        )
        if code == 200:
            print(f"✅ Team 文件夹已重命名: {args.team_name} → {new_name}")
            _pp(body)
        else:
            _err(code, body)

    elif act == "info":
        # 查看 Team 详细信息
        if not args.team_name:
            print("❌ 请指定 --team-name", file=sys.stderr)
            return
        team_name = args.team_name
        hdrs = _front_headers()
        print(f"{'═' * 60}")
        print(f"📋 Team: {team_name}")
        print(f"{'═' * 60}")

        # 1. 成员信息
        code, body = _req("GET", f"{FRONT_BASE}/teams/{team_name}/members", headers=hdrs)
        if code == 200:
            members = body.get("members", [])
            oasis_members = [m for m in members if m.get("type") == "oasis"]
            ext_members = [m for m in members if m.get("type") != "oasis"]
            print(f"\n👥 成员 ({len(members)} 个):")
            if oasis_members:
                print(f"\n  内部 Agent ({len(oasis_members)}):")
                for m in oasis_members:
                    name = m.get("name", "?")
                    tag = m.get("tag", "")
                    gn = m.get("global_name", "")
                    parts = [f"    • {name}"]
                    if tag:
                        parts.append(f"[{tag}]")
                    if gn:
                        parts.append(f"(session: {gn})")
                    print(" ".join(parts))
            if ext_members:
                print(f"\n  外部 Agent ({len(ext_members)}):")
                for m in ext_members:
                    name = m.get("name", "?")
                    tag = m.get("tag", "")
                    gn = m.get("global_name", "")
                    meta = m.get("meta", {})
                    parts = [f"    • {name}"]
                    if tag:
                        parts.append(f"[{tag}]")
                    if gn:
                        parts.append(f"(global: {gn})")
                    print(" ".join(parts))
                    if meta:
                        model = meta.get("model", "")
                        if model:
                            print(f"      model: {model}")
            if not members:
                print("  📭 暂无成员")
        else:
            print(f"  ⚠️ 获取成员失败: [{code}]", file=sys.stderr)

        # 2. 人设信息
        code2, body2 = _req("GET", f"{FRONT_BASE}/teams/{team_name}/experts", headers=hdrs)
        if code2 == 200:
            experts = body2.get("experts", [])
            print(f"\n🧑‍🏫 自定义人设 ({len(experts)} 个):")
            if experts:
                for expert in experts:
                    tag = expert.get("tag", "?")
                    name = expert.get("name", tag)
                    prompt = expert.get("prompt", expert.get("persona", ""))
                    print(f"  • [{tag}] {name}")
                    if prompt:
                        preview = prompt[:80].replace("\n", " ")
                        if len(prompt) > 80:
                            preview += "..."
                        print(f"    {preview}")
            else:
                print("  📭 暂无自定义人设")
        else:
            print(f"  ⚠️ 获取人设失败: [{code2}]", file=sys.stderr)

        # 3. Workflows
        params_wf = {"user_id": args.user, "team": team_name}
        code3, body3 = _req("GET", f"{OASIS_BASE}/workflows", params=params_wf)
        if code3 == 200:
            workflows = body3.get("workflows", []) if isinstance(body3, dict) else body3
            print(f"\n📐 Workflows ({len(workflows)} 个):")
            if workflows:
                for workflow in workflows:
                    fname = workflow.get("file", "?")
                    desc = workflow.get("description", "")
                    line = f"  • {fname}"
                    if desc:
                        line += f"  — {desc}"
                    print(line)
            else:
                print("  📭 暂无 workflow")
        else:
            print(f"  ⚠️ 获取 workflows 失败: [{code3}]", file=sys.stderr)

        # 4. 最近话题
        params_tp = {"user_id": args.user}
        code4, body4 = _req("GET", f"{OASIS_BASE}/topics", params=params_tp)
        if code4 == 200:
            all_topics = body4 if isinstance(body4, list) else body4.get("topics", [])
            # 过滤属于当前 team 的话题
            team_topics = []
            for t in all_topics:
                t_team = t.get("team", "")
                if t_team == team_name:
                    team_topics.append(t)
            print(f"\n💬 话题 ({len(team_topics)} 个):")
            if team_topics:
                status_icon = {"pending": "⏳", "discussing": "🔄", "concluded": "✅",
                               "error": "❌"}
                for topic in team_topics[-10:]:  # 最多展示最近 10 个
                    tid = topic.get("id", topic.get("topic_id", "?"))
                    q = topic.get("title", topic.get("question", ""))
                    st = topic.get("status", "?")
                    icon = status_icon.get(st, "❓")
                    # 截断过长标题
                    if len(q) > 60:
                        q = q[:60] + "..."
                    print(f"  {icon} [{tid}] {q}  ({st})")
                if len(team_topics) > 10:
                    print(f"  ... 共 {len(team_topics)} 个话题，仅展示最近 10 个")
            else:
                print("  📭 暂无话题")
        else:
            print(f"  ⚠️ 获取话题失败: [{code4}]", file=sys.stderr)

        # 5. OpenClaw 快照
        code5, body5 = _req("GET", f"{FRONT_BASE}/team_openclaw_snapshot",
                             headers=hdrs, params={"team": team_name})
        if code5 == 200:
            snapshots = body5.get("snapshots", body5.get("agents", []))
            if isinstance(body5, dict) and not snapshots:
                # 尝试其他可能的字段
                for k, v in body5.items():
                    if isinstance(v, list) and v:
                        snapshots = v
                        break
            if snapshots:
                print(f"\n📸 OpenClaw 快照 ({len(snapshots)} 个):")
                for snapshot in snapshots:
                    sname = snapshot.get("short_name", snapshot.get("name", "?"))
                    agent_name = snapshot.get("agent_name", "")
                    line = f"  • {sname}"
                    if agent_name:
                        line += f"  → {agent_name}"
                    print(line)

        print(f"\n{'═' * 60}")
        _print_doc_hint("team")

    elif act == "members":
        # 查看 Team 成员
        if not args.team_name:
            print("❌ 请指定 --team-name", file=sys.stderr)
            return
        code, body = _req("GET", f"{FRONT_BASE}/teams/{args.team_name}/members",
                           headers=_front_headers())
        if code == 200:
            _pp(body)
            _print_doc_hint("team")
        else:
            _err(code, body)

    elif act == "add-ext-member":
        # 添加外部成员
        if not args.team_name:
            print("❌ 请指定 --team-name", file=sys.stderr)
            return
        data = json.loads(args.data) if args.data else {}
        code, body = _req("POST",
                           f"{FRONT_BASE}/teams/{args.team_name}/members/external",
                           headers=_front_headers(), data=data)
        if code == 200:
            print("✅ 外部成员已添加")
            _pp(body)
            _print_doc_hint("team")
        else:
            _err(code, body)

    elif act == "delete-ext-member":
        # 删除外部成员
        if not args.team_name:
            print("❌ 请指定 --team-name", file=sys.stderr)
            return
        data = json.loads(args.data) if args.data else {}
        code, body = _req("DELETE",
                           f"{FRONT_BASE}/teams/{args.team_name}/members/external",
                           headers=_front_headers(), data=data)
        if code == 200:
            print("✅ 外部成员已删除")
            _pp(body)
        else:
            _err(code, body)

    elif act == "update-ext-member":
        # 更新外部成员
        if not args.team_name:
            print("❌ 请指定 --team-name", file=sys.stderr)
            return
        data = json.loads(args.data) if args.data else {}
        code, body = _req("PUT",
                           f"{FRONT_BASE}/teams/{args.team_name}/members/external",
                           headers=_front_headers(), data=data)
        if code == 200:
            print("✅ 外部成员已更新")
            _pp(body)
        else:
            _err(code, body)

    elif act == "personas":
        # 查看 Team 人设
        if not args.team_name:
            print("❌ 请指定 --team-name", file=sys.stderr)
            return
        code, body = _req("GET",
                           f"{FRONT_BASE}/teams/{args.team_name}/experts",
                           headers=_front_headers())
        if code == 200:
            _pp(body)
            _print_doc_hint("persona")
        else:
            _err(code, body)

    elif act == "add-persona":
        # 添加 Team 人设
        if not args.team_name:
            print("❌ 请指定 --team-name", file=sys.stderr)
            return
        data = json.loads(args.data) if args.data else {}
        code, body = _req("POST",
                           f"{FRONT_BASE}/teams/{args.team_name}/experts",
                           headers=_front_headers(), data=data)
        if code == 200:
            print("✅ 人设已添加")
            _pp(body)
        else:
            _err(code, body)

    elif act == "update-persona":
        # 更新 Team 人设
        if not args.team_name or not args.tag:
            print("❌ 请指定 --team-name 和 --tag", file=sys.stderr)
            return
        data = json.loads(args.data) if args.data else {}
        code, body = _req("PUT",
                           f"{FRONT_BASE}/teams/{args.team_name}/experts/{args.tag}",
                           headers=_front_headers(), data=data)
        if code == 200:
            print("✅ 人设已更新")
            _pp(body)
        else:
            _err(code, body)

    elif act == "delete-persona":
        # 删除 Team 人设
        if not args.team_name or not args.tag:
            print("❌ 请指定 --team-name 和 --tag", file=sys.stderr)
            return
        code, body = _req("DELETE",
                           f"{FRONT_BASE}/teams/{args.team_name}/experts/{args.tag}",
                           headers=_front_headers())
        if code == 200:
            print("✅ 人设已删除")
            _pp(body)
        else:
            _err(code, body)

    elif act == "snapshot-preview":
        # 快照预览
        if not args.team_name:
            print("❌ 请指定 --team-name", file=sys.stderr)
            return
        data = {"team": args.team_name}
        code, body = _req("POST", f"{FRONT_BASE}/teams/snapshot/preview",
                           headers=_front_headers(), data=data, timeout=60)
        if code == 200:
            _pp(body)
        else:
            _err(code, body)

    elif act == "snapshot-download":
        # 下载快照
        data = {"team": args.team_name or ""}
        # 解析 --include JSON 实现选择性导出
        if args.include:
            try:
                include_filter = json.loads(args.include)
                data["include"] = include_filter
            except json.JSONDecodeError:
                print("❌ --include 参数必须是有效的 JSON，例如: '{\"agents\":true,\"personas\":true}'", file=sys.stderr)
                return
        code, body = _req("POST", f"{FRONT_BASE}/teams/snapshot/download",
                           headers=_front_headers(), data=data, timeout=60)
        if code == 200:
            if isinstance(body, bytes):
                out = args.output or f"team_{args.team_name}_snapshot.zip"
                with open(out, "wb") as f:
                    f.write(body)
                print(f"✅ 快照已保存: {out} ({len(body)} bytes)")
                if args.include:
                    print(f"   导出选项: {args.include}")
            else:
                _pp(body)
        else:
            _err(code, body)

    elif act == "snapshot-upload":
        # 上传快照
        if not args.team_name:
            print("❌ 请指定 --team-name", file=sys.stderr)
            return
        if not args.file:
            print("❌ 请指定 --file (zip 文件路径)", file=sys.stderr)
            return
        # 使用 multipart/form-data 上传
        import mimetypes
        boundary = "----CLIUploadBoundary"
        body_parts = []
        # team 字段
        body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"team\"\r\n\r\n{args.team_name}")
        # file 字段
        fname = os.path.basename(args.file)
        ct = mimetypes.guess_type(args.file)[0] or "application/zip"
        with open(args.file, "rb") as f:
            file_data = f.read()
        body_parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\nContent-Type: {ct}\r\n\r\n"
        )
        # 手动拼装 multipart body
        encoded = b""
        for i, part in enumerate(body_parts):
            encoded += part.encode("utf-8")
            if i == len(body_parts) - 1:
                encoded += file_data
            encoded += b"\r\n"
        encoded += f"--{boundary}--\r\n".encode("utf-8")
        req_obj = urllib.request.Request(
            f"{FRONT_BASE}/teams/snapshot/upload",
            data=encoded,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "X-Internal-Token": INTERNAL_TOKEN,
                "X-User-Id": args.user or "",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req_obj, timeout=120) as resp:
                result = json.loads(resp.read())
                _pp(result)
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode())
            except Exception:
                err = {"error": e.reason}
            _err(e.code, err)
        except urllib.error.URLError as e:
            print(f"❌ 连接失败: {e.reason}", file=sys.stderr)

    else:
        print(f"❌ 未知操作: {act}", file=sys.stderr)


# ── token: Token 生成与验证 ────────────────────────────────────────────────
import hashlib
import hmac
import secrets
import base64


def _generate_login_token(user_id: str, internal_token: str, valid_hours: int = 24) -> str:
    """生成 HMAC 签名的登录 Token

    参数：
        user_id: 用户 ID
        internal_token: 内部认证 Token
        valid_hours: 有效期（小时）

    返回：
        str: 生成的登录 Token，格式：base64(user_id:expire_ts:random:signature)
    """
    expire_ts = int(time.time()) + valid_hours * 3600
    random_str = secrets.token_urlsafe(8)
    payload = f"{user_id}:{expire_ts}:{random_str}"
    signature = hmac.new(
        internal_token.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    token = base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode().rstrip('=')
    return token


def _verify_login_token(token: str, internal_token: str) -> str | None:
    """验证 HMAC 签名的登录 Token

    参数：
        token: 待验证的 Token
        internal_token: 内部认证 Token

    返回：
        str or None: 验证成功返回 user_id，失败返回 None
    """
    try:
        padded = token + '=' * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode()
        parts = decoded.rsplit(':', 1)
        if len(parts) != 2:
            return None
        payload, signature = parts
        user_id, expire_ts, _ = payload.split(':')
        expire_ts = int(expire_ts)

        if time.time() > expire_ts:
            return None

        expected = hmac.new(
            internal_token.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()[:16]

        if not hmac.compare_digest(signature, expected):
            return None

        return user_id
    except Exception:
        return None


def cmd_token(args):
    """Token 生成与验证

    参数：
        args: 命令行参数对象
    """
    if args.action == "generate":
        # 生成 Token
        if not INTERNAL_TOKEN:
            print("❌ INTERNAL_TOKEN 未配置", file=sys.stderr)
            return
        # 支持多用户
        users = []
        if args.user:
            users = [u.strip() for u in args.user.split(',')]
        elif args.users:
            users = [u.strip() for u in args.users.split(',')]
        else:
            print("❌ 请指定用户: --user 或 --users", file=sys.stderr)
            return

        valid_hours = args.valid_hours or 24

        # 获取本机 IP 用于生成链接
        local_ip = _get_local_ip() or "127.0.0.1"
        port = PORT_FRONTEND

        print(f"{'═' * 60}")
        print(f"🔑 Login Tokens ({len(users)} user(s), valid for {valid_hours}h)")
        print(f"{'═' * 60}\n")

        for user_id in users:
            token = _generate_login_token(user_id, INTERNAL_TOKEN, valid_hours)

            # 生成访问链接
            local_link = f"http://127.0.0.1:{port}/login-link/{token}"
            lan_link = f"http://{local_ip}:{port}/login-link/{token}"

            print(f"👤 User: {user_id}")
            print(f"   Token: {token}")
            print(f"   Local: {local_link}")
            print(f"   LAN:   {lan_link}")
            print()

    elif args.action == "verify":
        # 验证 Token
        token = args.token.strip() if args.token else ""
        if not token:
            print("❌ 请提供 token: --token <token>", file=sys.stderr)
            return
        if not INTERNAL_TOKEN:
            print("❌ INTERNAL_TOKEN 未配置", file=sys.stderr)
            return

        result = _verify_login_token(token, INTERNAL_TOKEN)
        if result:
            print(f"✅ Token 有效")
            print(f"   User ID: {result}")
        else:
            print("❌ Token 无效或已过期")

    elif args.action == "decode":
        # 解码 Token
        token = args.token.strip() if args.token else ""
        if not token:
            print("❌ 请提供 token: --token <token>", file=sys.stderr)
            return
        try:
            padded = token + '=' * (-len(token) % 4)
            decoded = base64.urlsafe_b64decode(padded).decode()
            parts = decoded.rsplit(':', 1)
            if len(parts) == 2:
                payload, signature = parts
                user_id, expire_ts, random_str = payload.split(':')
                expire_ts = int(expire_ts)
                expire_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(expire_ts))
                is_expired = "已过期" if time.time() > expire_ts else "有效"

                print(f"{'═' * 60}")
                print(f"🔍 Token 解析")
                print(f"{'═' * 60}")
                print(f"  User ID:   {user_id}")
                print(f"  Expire:    {expire_time} ({is_expired})")
                print(f"  Random:    {random_str}")
                print(f"  Signature: {signature}")
            else:
                print("❌ Token 格式无效")
        except Exception as e:
            print(f"❌ 解析失败: {e}")


def _get_local_ip() -> str | None:
    """获取本机局域网 IP 地址

    返回：
        str or None: 本机 IP 地址，获取失败返回 None
    """
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        try:
            s.connect(('10.254.254.254', 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = '127.0.0.1'
        finally:
            s.close()
        return ip
    except Exception:
        return None


# ── status: 服务状态 ───────────────────────────────────────────────────────
def cmd_status(args):
    """检查各服务状态、外部平台、API Key 等

    参数：
        args: 命令行参数对象
    """
    import shutil

    # 1. 服务在线状态
    services = [
        ("Agent",     f"http://127.0.0.1:{PORT_AGENT}/v1/models"),
        ("OASIS",     f"http://127.0.0.1:{PORT_OASIS}/experts"),
        ("Frontend",  f"http://127.0.0.1:{PORT_FRONTEND}/"),
    ]
    print("📊 服务状态:\n")
    for name, url in services:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3):
                print(f"  ✅ {name:12s}  :{url.split(':')[2].split('/')[0]}  正常")
        except Exception:
            print(f"  ❌ {name:12s}  :{url.split(':')[2].split('/')[0]}  不可达")

    # 2. LLM API Key 状态
    print(f"\n{'─' * 50}")
    print("🔑 API Key 配置:\n")
    env_path = os.path.join(PROJECT_ROOT, "config", ".env")
    env_vars = {}
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()

    llm_api_key = env_vars.get("LLM_API_KEY", "")
    llm_base_url = env_vars.get("LLM_BASE_URL", "")
    llm_model = env_vars.get("LLM_MODEL", "")
    api_key_ok = bool(llm_api_key and llm_api_key != "your_api_key_here")

    if api_key_ok:
        masked = llm_api_key[:8] + "..." + llm_api_key[-4:] if len(llm_api_key) > 12 else "***"
        print(f"  ✅ LLM_API_KEY    = {masked}")
    else:
        print(f"  ❌ LLM_API_KEY    = (未设置)")
    print(f"     LLM_BASE_URL  = {llm_base_url or '(未设置)'}")
    print(f"     LLM_MODEL     = {llm_model or '(未设置)'}")

    if api_key_ok:
        print(f"\n  🤖 Clawcross 轻量级 Agent：可用")
        print(f"     基于 LLM API 驱动的内置 Agent，无需额外安装")
        print(f"     支持: 对话 / 工具调用 / 多轮推理")
    else:
        print(f"\n  ⚠️  API Key 未配置 → 内部 Agent (Internal Agent) 无法使用！")
        print(f"     Clawcross 轻量级 Agent 需要 LLM_API_KEY 才能工作")
        print(f"     请运行 bash scripts/setup_apikey.sh 或手动编辑 config/.env")
        print(f"\n  💡 即使没有 API Key，仍可使用以下外部 Agent 平台:")
        print(f"     openclaw / codex / claude (claude-code) / gemini (gemini-cli) / aider")

    # 3. 外部 Agent 平台检测
    print(f"\n{'─' * 50}")
    print("🖥️  外部 Agent 平台:\n")

    platforms = [
        ("openclaw", "OpenClaw",     "本地多 Agent 编排平台"),
        ("codex",    "Codex CLI",    "OpenAI Codex 命令行 Agent"),
        ("claude",   "Claude Code",  "Anthropic Claude 命令行 Agent"),
        ("gemini",   "Gemini CLI",   "Google Gemini 命令行 Agent"),
        ("aider",    "Aider",        "AI Pair Programming 工具"),
    ]

    available_platforms = []
    for cmd_name, display_name, description in platforms:
        path = shutil.which(cmd_name)
        if path:
            # 尝试获取版本信息
            version_str = ""
            try:
                result = subprocess.run(
                    [cmd_name, "--version"], capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    ver_line = result.stdout.strip().splitlines()[0]
                    # 截取版本号（最多 60 字符）
                    version_str = f"  ({ver_line[:60]})"
                elif result.stderr.strip():
                    ver_line = result.stderr.strip().splitlines()[0]
                    version_str = f"  ({ver_line[:60]})"
            except Exception:
                pass
            print(f"  ✅ {display_name:14s} — {description}{version_str}")
            print(f"     路径: {path}")
            available_platforms.append(display_name)
        else:
            print(f"  ❌ {display_name:14s} — 未安装 (命令 '{cmd_name}' 不在 PATH 中)")

    # OpenClaw 额外检查: API URL 和 sessions file
    openclaw_api_url = env_vars.get("OPENCLAW_API_URL", "")
    openclaw_sessions = env_vars.get("OPENCLAW_SESSIONS_FILE", "")
    if shutil.which("openclaw"):
        if openclaw_api_url:
            print(f"\n  📡 OpenClaw API URL     = {openclaw_api_url}")
        if openclaw_sessions:
            exists = os.path.isfile(openclaw_sessions)
            icon = "✅" if exists else "⚠️"
            print(f"  {icon} OpenClaw Sessions   = {openclaw_sessions}")

    # 4. 综合总结
    print(f"\n{'─' * 50}")
    print("📋 总结:\n")

    if api_key_ok:
        print(f"  ✅ Clawcross 轻量级 Agent：可用 (内置，基于 LLM API)")
        print(f"     模型: {llm_model}  Base URL: {llm_base_url}")
    else:
        print(f"  ❌ Clawcross 轻量级 Agent：不可用 (未配置 LLM_API_KEY)")
        print(f"     → 设置方法: bash scripts/setup_apikey.sh")

    if available_platforms:
        print(f"\n  ✅ 可用的外部 Agent 平台 ({len(available_platforms)} 个):")
        for p in available_platforms:
            print(f"     • {p}")
    else:
        print(f"\n  ⚠️  未检测到任何外部 Agent 平台")
        print(f"     可安装: openclaw / codex / claude (claude-code) / gemini (gemini-cli) / aider")

    _print_doc_hint("status")
    print()


# ═══════════════════════════════════════════════════════════════════════
#  参数解析
# ═══════════════════════════════════════════════════════════════════════

def build_parser():
    """构建命令行参数解析器"""
    p = argparse.ArgumentParser(
        prog="clawcross",
        description="Clawcross CLI — 命令行控制工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
⚠️  【必读文档】执行任何操作前，请务必先阅读对应文档，否则可能导致配置错误！

  📖 docs/build_team.md       — 创建/配置 Team (成员、人设、JSON 文件)
  📖 docs/create_workflow.md  — 创建 OASIS 工作流 YAML (图格式、人设类型、示例)
  📖 docs/cli.md              — 完整 CLI 命令参考和示例
  📖 docs/example_team.md     — 示例 Team 文件结构和内容
  📖 docs/openclaw-commands.md — OpenClaw agent 集成命令
  📖 docs/ports.md            — 端口配置和冲突处理

提示: 使用 'clawcross <command> --help' 查看各命令的详细用法
""",
    )
    p.add_argument("-u", "--user", default=DEFAULT_USER, help="用户名 (默认: admin, chat 时必须显式指定)")
    sub = p.add_subparsers(dest="command", help="子命令")

    # chat
    c = sub.add_parser("chat", help="发送消息（流式输出）")
    c.add_argument("message", help="消息内容")
    c.add_argument("-s", "--session", required=True, help="会话 ID（必填）")
    c.add_argument("-m", "--model", help="模型名称")

    # sessions
    sub.add_parser("sessions", help="查看会话列表")

    # sessions-status
    sub.add_parser("sessions-status", help="查看所有会话忙碌状态")

    # session-status
    c = sub.add_parser("session-status", help="查看单个会话状态")
    c.add_argument("-s", "--session", help="会话 ID (默认: default)")

    # history
    c = sub.add_parser("history", help="查看会话历史")
    c.add_argument("-s", "--session", help="会话 ID (默认: default)")
    c.add_argument("-n", "--limit", type=int, help="最近 N 条")
    c.add_argument("--full", action="store_true", help="不截断长消息")

    # delete-session
    c = sub.add_parser("delete-session", help="删除会话")
    c.add_argument("session", help="会话 ID")

    # settings
    c = sub.add_parser("settings", help="查看/修改设置")
    c.add_argument("--full", action="store_true", help="完整设置（含高级项）")
    c.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"), dest="set_pair", help="修改设置")

    # tools
    c = sub.add_parser("tools", help="查看可用工具")
    c.add_argument("--brief", action="store_true", help="仅显示名称")

    # tts
    c = sub.add_parser("tts", help="文字转语音")
    c.add_argument("text", help="要转换的文本")
    c.add_argument("-o", "--output", help="输出文件 (默认: tts_output.mp3)")
    c.add_argument("--voice", help="语音角色")

    # cancel
    c = sub.add_parser("cancel", help="取消当前生成")
    c.add_argument("-s", "--session", help="会话 ID")

    # restart
    sub.add_parser("restart", help="重启 Agent 服务")

    # groups
    c = sub.add_parser("groups", help="群组管理")
    c.add_argument("action", nargs="?", default="list",
                   choices=["list", "create", "get", "update", "delete",
                            "messages", "send", "private-send", "mute", "unmute",
                            "mute-status", "sessions", "sync-members"],
                   help="操作 (默认: list)")
    c.add_argument("--group-id", help="群组 ID")
    c.add_argument("--name", help="群组名称 (创建时)")
    c.add_argument("--team-name", help="Team 名称 (创建时)")
    c.add_argument("--message", help="消息内容 (send 时)")
    c.add_argument("--sender", help="发送者标识 (send 时，用于 agent 认证，格式: 'ext#agent名')")
    c.add_argument("--data", help="JSON 数据")
    c.add_argument("--after-id", help="增量获取消息 (messages 时)")

    # profile
    c = sub.add_parser("profile", help="用户画像管理")
    c.add_argument("action", nargs="?", default="get", choices=["get", "set", "path"],
                   help="操作 (默认: get)")
    c.add_argument("-c", "--content", help="画像内容 (set 时)")
    c.add_argument("-f", "--file", dest="file", help="从文件读取画像内容 (set 时)")

    # openclaw
    c = sub.add_parser("openclaw", help="OpenClaw Agent 管理",
                       epilog="""
⚠️  【必读】操作 OpenClaw Agent 前务必先阅读以下文档：
  📖 docs/openclaw-commands.md — OpenClaw agent 集成命令详解
  📖 docs/build_team.md        — 将 OpenClaw agent 加入 Team
  📖 docs/cli.md               — 完整 CLI 命令参考
  ❗ 不阅读文档直接操作可能导致配置错误或功能异常！
""",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    c.add_argument("action", nargs="?", default="sessions",
                   choices=["sessions", "add", "default-workspace",
                            "workspace-files", "workspace-file-read",
                            "workspace-file-save", "detail", "skills",
                            "tool-groups", "update-config", "channels",
                            "bindings", "bind", "remove"],
                   help="操作 (默认: sessions)")
    c.add_argument("--filter", help="过滤关键词 (sessions 时)")
    c.add_argument("--name", help="Agent 名称 (detail/remove 时)")
    c.add_argument("--agent", help="Agent 名称 (skills/bindings 时)")
    c.add_argument("--workspace", help="工作区路径")
    c.add_argument("--filename", help="文件名 (workspace-file-read 时)")
    c.add_argument("--data", help="JSON 数据")

    # openclaw-snapshot
    c = sub.add_parser("openclaw-snapshot", help="OpenClaw 快照管理")
    c.add_argument("action", nargs="?", default="get",
                   choices=["get", "export", "sync-all", "restore",
                            "export-all", "restore-all"],
                   help="操作 (默认: get)")
    c.add_argument("--team", help="Team 名称 (必需)")
    c.add_argument("--agent-name", help="Agent 全名 (export 时)")
    c.add_argument("--short-name", help="显示名 (export/restore 时)")
    c.add_argument("--target-name", help="恢复目标 Agent 名 (restore 时)")

    # visual
    c = sub.add_parser("visual", help="可视化编排管理")
    c.add_argument("action", nargs="?", default="personas",
                   choices=["personas", "add-persona", "delete-persona",
                            "generate-yaml", "agent-generate-yaml",
                            "save-layout", "load-layouts", "load-layout",
                            "load-yaml-raw", "delete-layout", "upload-yaml",
                            "sessions-status"],
                   help="操作 (默认: personas)")
    c.add_argument("--team", help="Team 名称")
    c.add_argument("--tag", help="人设 tag (delete-persona 时)")
    c.add_argument("--name", help="布局名称 (load-layout/load-yaml-raw/delete-layout 时)")
    c.add_argument("--data", help="JSON 数据")

    # internal-agents
    c = sub.add_parser("internal-agents", help="内部 Agent CRUD")
    c.add_argument("action", nargs="?", default="list",
                   choices=["list", "add", "update", "delete"],
                   help="操作 (默认: list)")
    c.add_argument("--team", help="Team 名称")
    c.add_argument("--sid", help="Session ID (update/delete 时)")
    c.add_argument("--session", help="Session ID (add 时)")
    c.add_argument("--data", help="JSON 数据")

    # teams
    c = sub.add_parser("teams", help="Team 管理",
                       epilog="""
⚠️  【必读】操作 Team 前务必先阅读以下文档：
  📖 docs/build_team.md   — 创建/配置 Team (成员、人设、JSON 文件)
  📖 docs/example_team.md — 示例 Team 文件结构和内容
  📖 docs/cli.md          — 完整 CLI 命令参考
  ❗ 不阅读文档直接操作可能导致配置错误或功能异常！
""",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    c.add_argument("action", nargs="?", default="list",
                   choices=["list", "info", "create", "delete", "rename", "members",
                            "add-ext-member", "delete-ext-member",
                            "update-ext-member", "personas", "add-persona",
                            "update-persona", "delete-persona",
                            "snapshot-preview", "snapshot-download", "snapshot-upload"],
                   help="操作 (默认: list)")
    c.add_argument("--team-name", help="Team 名称")
    c.add_argument("--new-name", help="rename 时的新文件夹名（仅改 teams 目录名）")
    c.add_argument("--tag", help="人设 tag (update-persona/delete-persona 时)")
    c.add_argument("--data", help="JSON 数据")
    c.add_argument("-o", "--output", help="输出文件 (snapshot-download 时)")
    c.add_argument("--file", help="上传文件路径 (snapshot-upload 时)")
    c.add_argument("--include", help='选择性导出 JSON (snapshot-download 时)，例如: \'{"agents":true,"personas":true,"skills":true,"cron":true,"workflows":true}\'')

    # topics
    c = sub.add_parser("topics", help="OASIS 话题管理")
    c.add_argument("action", nargs="?", default="list",
                   choices=["list", "show", "watch", "cancel", "purge", "callback", "human-reply", "delete-all"],
                   help="操作 (默认: list)")
    c.add_argument("--topic-id", help="话题 ID")
    c.add_argument("--raw", action="store_true", help="输出原始 JSON (show 时)")
    c.add_argument("--full", action="store_true", help="不截断长内容 (show 时)")
    c.add_argument("--author", help="回传作者名 (callback 时)")
    c.add_argument("--round-num", type=int, help="目标轮次 (callback 时)")
    c.add_argument("--data", help="回传 JSON 对象 (callback 时)")
    c.add_argument("--node-id", help="等待中的 human 节点 ID (human-reply 时)")
    c.add_argument("--message", help="人类普通文本回复 (human-reply 时)")

    # experts
    c = sub.add_parser("personas", help="OASIS 人设管理")
    c.add_argument("action", nargs="?", default="list",
                   choices=["list", "add", "update", "delete"],
                   help="操作 (默认: list)")
    c.add_argument("--tag", help="人设标签 (唯一标识)")
    c.add_argument("--persona-name", help="人设显示名称")
    c.add_argument("--persona", help="人设描述")
    c.add_argument("--temperature", type=float, help="温度参数 (0-2)")
    c.add_argument("--team", help="Team 名称")

    # workflows
    c = sub.add_parser("workflows", help="OASIS Workflow 管理",
                       epilog="""
⚠️  【必读】操作 Workflow 前务必先阅读以下文档：
  📖 docs/create_workflow.md — 创建 OASIS 工作流 YAML (图格式、人设类型、示例)
  📖 docs/cli.md             — 完整 CLI 命令参考
  ❗ 不阅读文档直接操作可能导致配置错误或功能异常！
""",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    c.add_argument("action", nargs="?", default="list",
                   choices=["list", "show", "save", "run", "conclusion"],
                   help="操作 (默认: list)")
    c.add_argument("--team", help="Team 名称")
    c.add_argument("--type", choices=["all", "yaml", "python"], default="all",
                   help="workflow 类型 (list/show/run 时；默认: all)")
    c.add_argument("--name", help="Workflow 文件名 (show/save/run 时)")
    c.add_argument("--python-file", help="Python workflow 文件路径 (run 时)")
    c.add_argument("--yaml", help="YAML 内容 (save/run 时，直接传入)")
    c.add_argument("--yaml-file", help="YAML 文件路径 (save/run 时，从文件读取)")
    c.add_argument("--description", help="Workflow 描述 (save 时)")
    c.add_argument("--question", help="讨论问题/任务 (run 时)")
    c.add_argument("--max-rounds", type=int, help="最大轮数 (run 时, 1-20)")
    c.add_argument("--discussion", type=lambda x: x.lower() in ("true", "1", "yes"),
                   default=None, help="讨论模式 (run 时, true/false)")
    c.add_argument("--early-stop", action="store_true", help="提前终止 (run 时)")
    c.add_argument("--topic-id", help="话题 ID (conclusion 时)")
    c.add_argument("--timeout", type=int, help="等待超时秒数 (conclusion 时, 默认 300)")

    # tunnel
    c = sub.add_parser("tunnel", help="Cloudflare Tunnel 管理")
    c.add_argument("action", nargs="?", default="status",
                   choices=["status", "start", "stop"],
                   help="操作 (默认: status)")

    # status
    sub.add_parser("status", help="检查各服务状态")

    # token
    c = sub.add_parser("token", help="Token 生成与验证")
    c.add_argument("action", nargs="?", default="generate",
                   choices=["generate", "verify", "decode"],
                   help="操作 (默认: generate)")
    c.add_argument("-u", "--user", help="单用户 (generate 时)")
    c.add_argument("--token", help="Token 字符串 (verify/decode 时)")
    c.add_argument("--users", help="多用户列表，逗号分隔")
    c.add_argument("--valid-hours", type=int, help="Token 有效期 (小时，默认: 24)")

    return p


# 全局 CLI 用户名缓存（用于 front_headers 回退）
_cli_user = ""


def main():
    """CLI 主入口函数"""
    global _cli_user
    parser = build_parser()
    args = parser.parse_args()
    _cli_user = getattr(args, "user", "") or ""

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # settings 命令特殊处理
    if args.command == "settings":
        args.set_key = args.set_pair[0] if args.set_pair else None
        args.set_value = args.set_pair[1] if args.set_pair else None

    # 命令分发映射
    dispatch = {
        "chat": cmd_chat,
        "sessions": cmd_sessions,
        "sessions-status": cmd_sessions_status,
        "session-status": cmd_session_status,
        "history": cmd_history,
        "delete-session": cmd_delete_session,
        "settings": cmd_settings,
        "tools": cmd_tools,
        "tts": cmd_tts,
        "cancel": cmd_cancel,
        "restart": cmd_restart,
        "groups": cmd_groups,
        "profile": cmd_profile,
        "openclaw": cmd_openclaw,
        "openclaw-snapshot": cmd_openclaw_snapshot,
        "visual": cmd_visual,
        "internal-agents": cmd_internal_agents,
        "teams": cmd_teams,
        "topics": cmd_topics,
        "personas": cmd_experts,
        "workflows": cmd_workflows,
        "tunnel": cmd_tunnel,
        "token": cmd_token,
        "status": cmd_status,
    }

    fn = dispatch.get(args.command)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
