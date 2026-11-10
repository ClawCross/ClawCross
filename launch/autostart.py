"""Opt-in, per-user startup registration. No browser, credentials prompt or root service."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.backend.common import runtime_paths as paths

MARKER = 'ClawCross managed autostart'
ENVIRONMENT_KEYS = {
    'PATH', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'GEMINI_CLI_HOME', 'XDG_CONFIG_HOME',
    'XDG_DATA_HOME', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
    'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE',
    'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS',
    'npm_config_registry', 'NPM_CONFIG_REGISTRY',
}


def _state_file():
    return paths.CONFIG_DIR / 'autostart.json'


def _read():
    try:
        value = json.loads(_state_file().read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    if value.get('managed_by') != MARKER:
        raise RuntimeError('Unrecognized autostart configuration; nothing was changed.')
    return value


def _write(path, data, *, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError(f'Refusing to replace a symbolic link: {path}')
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run(command, *, check=True):
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise RuntimeError(f'{command[0]} failed ({result.returncode}): {detail}')
    return result


def _kind():
    if sys.platform == 'win32':
        return 'windows'
    if sys.platform == 'darwin':
        return 'launchd'
    if sys.platform.startswith('linux'):
        return 'systemd'
    raise RuntimeError('Autostart supports Linux, macOS and Windows.')


def _identity():
    return 'clawcross-bridge-' + hashlib.sha256(str(paths.CLAWCROSS_HOME.resolve()).encode()).hexdigest()[:12]


def _registration(kind):
    name = _identity()
    if kind == 'systemd':
        config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
        return config / 'systemd/user' / (name + '.service'), name + '.service'
    if kind == 'launchd':
        label = 'net.clawcross.' + name
        return Path.home() / 'Library/LaunchAgents' / (label + '.plist'), label
    return paths.CONFIG_DIR / (name + '.xml'), name


def _profile(requested):
    if requested:
        return requested
    previous = _read()
    if previous.get('profile') in {'bridge', 'full'}:
        return previous['profile']
    try:
        pid = int((paths.PID_DIR / 'clawcross.pid').read_text().strip())
        active = json.loads((paths.PID_DIR / 'profile.json').read_text(encoding='utf-8'))
        if active.get('pid') == pid and active.get('mode') in {'bridge', 'full'}:
            return active['mode']
    except (OSError, ValueError):
        pass
    return 'bridge'


def _command(python):
    return [str(python), str(ROOT / 'launch/autostart.py'), 'run', '--state', str(_state_file())]


def _systemd_quote(value):
    # systemd performs specifier and environment expansion even without a shell.
    if any(c in value for c in ('\0', '\r', '\n')):
        raise ValueError('A startup path contains a control character.')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def _unit(command):
    return ('# ' + MARKER + '\n[Unit]\nDescription=ClawCross background group bridge\n'
            'After=network-online.target\nStartLimitIntervalSec=300\nStartLimitBurst=3\n'
            '\n[Service]\nType=simple\nExecStart=' + ' '.join(map(_systemd_quote, command)) + '\n'
            'Restart=on-failure\nRestartSec=30\nTimeoutStopSec=20\nKillMode=control-group\n'
            '\n[Install]\nWantedBy=default.target\n').encode()


def _plist(command, label):
    return plistlib.dumps({'Label': label, 'ProgramArguments':command, 'RunAtLoad':True,
        'KeepAlive':{'SuccessfulExit':False}, 'ThrottleInterval':30,
        'StandardOutPath':str(paths.LOGS_DIR / 'autostart.log'),
        'StandardErrorPath':str(paths.LOGS_DIR / 'autostart.log')})


def _task(command, name):
    ns = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
    ET.register_namespace('', ns)
    task = ET.Element('{' + ns + '}Task', version='1.2')
    def add(parent, tag, text=None, **attrs):
        element = ET.SubElement(parent, '{' + ns + '}' + tag, attrs)
        element.text = text
        return element
    add(add(task,'RegistrationInfo'),'Description',MARKER)
    triggers = add(task,'Triggers')
    add(add(triggers,'LogonTrigger'),'Enabled','true')
    principals = add(task,'Principals')
    principal = add(principals,'Principal',id='Author')
    user = getpass.getuser()
    domain = os.environ.get('USERDOMAIN', '')
    add(principal,'UserId',domain + '\\' + user if domain else user)
    add(principal,'LogonType','InteractiveToken')
    add(principal,'RunLevel','LeastPrivilege')
    settings = add(task,'Settings')
    for tag, value in {'MultipleInstancesPolicy':'IgnoreNew', 'DisallowStartIfOnBatteries':'false',
            'StopIfGoingOnBatteries':'false', 'StartWhenAvailable':'true',
            'ExecutionTimeLimit':'PT0S', 'Enabled':'true'}.items():
        add(settings,tag,value)
    restart = add(settings,'RestartOnFailure')
    add(restart,'Interval','PT1M');add(restart,'Count','3')
    action = add(add(task,'Actions',Context='Author'),'Exec')
    add(action,'Command',command[0])
    add(action,'Arguments',subprocess.list2cmdline(command[1:]))
    return ET.tostring(task, encoding='utf-16', xml_declaration=True)


def enable(*, profile=None):
    kind = _kind()
    python = paths.venv_python()
    if not paths.ENV_FILE.is_file() or not python.is_file():
        raise RuntimeError('请先执行 clawcross join … --prepare 或 clawcross start --bridge。')
    if '_npx' in ROOT.parts:
        raise RuntimeError('自启动需要持久安装。请先 npm install -g clawcross，或克隆 GitHub 仓库后执行；npx 临时缓存可能被清理。')
    _read()  # Refuse to overwrite an unrelated file.
    location, name = _registration(kind)
    profile = _profile(profile)
    paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    environment = {key:value for key,value in os.environ.items() if key in ENVIRONMENT_KEYS}
    environment = paths.set_subprocess_env(environment)
    record = {'managed_by':MARKER, 'backend':kind, 'name':name, 'registration':str(location),
              'profile':profile, 'code_root':str(ROOT), 'python':str(python),
              'environment':environment, 'enabled':True, 'trigger':'login'}
    record['tunnel'] = profile == 'full' and (paths.PID_DIR / 'tunnel.pid').is_file()
    # Credential values from .env stay in .env. PATH/proxy/CLI homes may also be
    # private, so the captured environment is owner-readable only and never printed.
    before = _state_file().read_bytes() if _state_file().exists() else None
    command = _command(python)
    if kind == 'windows' and python.with_name('pythonw.exe').is_file():
        command[0] = str(python.with_name('pythonw.exe'))
    data = _unit(command) if kind == 'systemd' else _plist(command, name) if kind == 'launchd' else _task(command, name)
    old_registration = location.read_bytes() if location.is_file() else None
    _write(_state_file(), json.dumps(record, ensure_ascii=False, indent=2).encode())
    _write(location, data, mode=0o600 if kind != 'systemd' else 0o644)
    try:
        if kind == 'systemd':
            _run(['systemctl','--user','daemon-reload'])
            _run(['systemctl','--user','enable',name])
        elif kind == 'launchd':
            _run(['launchctl','enable',f'gui/{os.getuid()}/{name}'])
        else:
            _run(['schtasks','/Create','/TN',name,'/XML',str(location),'/F'])
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        if old_registration is None:
            location.unlink(missing_ok=True)
        else:
            _write(location, old_registration, mode=0o644 if kind == 'systemd' else 0o600)
        if before is None:
            _state_file().unlink(missing_ok=True)
        else:
            _write(_state_file(), before)
        raise
    warnings = []
    if kind == 'systemd':
        try:
            linger = _run(['loginctl','show-user',getpass.getuser(),'-p','Linger','--value'], check=False)
            if linger.returncode or linger.stdout.strip() != 'yes':
                _run(['loginctl','--no-ask-password','enable-linger',getpass.getuser()], check=False)
                linger = _run(['loginctl','show-user',getpass.getuser(),'-p','Linger','--value'], check=False)
            if linger.returncode == 0 and linger.stdout.strip() == 'yes':
                record['trigger'] = 'boot'
        except (OSError, subprocess.TimeoutExpired):
            pass
        if record['trigger'] != 'boot':
            warnings.append('用户服务已设置为登录时启动；开机且未登录时运行需要管理员允许该用户的 linger。')
    _write(_state_file(), json.dumps(record, ensure_ascii=False, indent=2).encode())
    return {'ok':True, 'enabled':True, 'backend':kind, 'profile':profile,
            'trigger':record['trigger'], 'warnings':warnings}


def status():
    record = _read()
    if not record:
        return {'ok':True, 'enabled':False}
    kind, name = record['backend'], record['name']
    if not record.get('enabled'):
        return {'ok':True, 'enabled':False}
    enabled = bool(record.get('enabled') and Path(record['registration']).is_file())
    if enabled and kind == 'systemd':
        enabled = _run(['systemctl','--user','is-enabled',name], check=False).returncode == 0
    elif enabled and kind == 'windows':
        enabled = _run(['schtasks','/Query','/TN',name], check=False).returncode == 0
    return {'ok':True, 'enabled':enabled, 'backend':kind, 'profile':record['profile'],
            'trigger':record['trigger'], 'installation_available':Path(record['code_root'],'launch/runtime_control.py').is_file()}


def disable():
    record = _read()
    if not record or not record.get('enabled'):
        return {'ok':True, 'enabled':False}
    kind, name = record['backend'], record['name']
    if kind == 'systemd':
        _run(['systemctl','--user','disable',name])
    elif kind == 'launchd':
        _run(['launchctl','disable',f'gui/{os.getuid()}/{name}'])
    else:
        _run(['schtasks','/Delete','/TN',name,'/F'])
    # Do not stop a running Agent just because next-boot startup was disabled.
    Path(record['registration']).unlink(missing_ok=True)
    if kind == 'systemd':
        _run(['systemctl','--user','daemon-reload'])
    record['enabled'] = False
    _write(_state_file(), json.dumps(record, ensure_ascii=False, indent=2).encode())
    return {'ok':True, 'enabled':False}


def run(state):
    value = json.loads(Path(state).read_text(encoding='utf-8'))
    if value.get('managed_by') != MARKER or not value.get('enabled'):
        return 0
    root = Path(value['code_root'])
    controller = root / 'launch/runtime_control.py'
    if not controller.is_file():
        raise RuntimeError('ClawCross installation was moved or removed; re-enable autostart from the installed bridge.')
    environment = {**os.environ, **value['environment'], 'PYTHONUTF8':'1', 'PYTHONIOENCODING':'utf-8'}
    command = [value['python'], str(controller), 'run-service']
    if value['profile'] == 'bridge':
        command.append('--bridge')
    elif value.get('tunnel'):
        command.append('--tunnel')
    log_path = Path(environment['CLAWCROSS_LOG_DIR']) / 'autostart.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # pythonw has no console; service output must still remain inspectable.
    with os.fdopen(os.open(log_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600), 'a', encoding='utf-8') as log:
        previous = signal.getsignal(signal.SIGTERM)
        def terminate(_signum, _frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, terminate)
        process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT)
        try:
            return process.wait()
        except KeyboardInterrupt:
            process.terminate()
            return process.wait(timeout=20)
        finally:
            signal.signal(signal.SIGTERM, previous)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='clawcross autostart', description='设置当前用户的后台转接模式自启动')
    sub = parser.add_subparsers(dest='action', required=True)
    add = sub.add_parser('enable', help='启用自启动；当前服务不重启')
    modes = add.add_mutually_exclusive_group()
    modes.add_argument('--bridge', action='store_const', const='bridge', dest='profile')
    modes.add_argument('--full', action='store_const', const='full', dest='profile')
    for name in ('disable','status'):
        sub.add_parser(name)
    for item in sub.choices.values():
        item.add_argument('--json', action='store_true')
    worker = sub.add_parser('run', help=argparse.SUPPRESS)
    worker.add_argument('--state', required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'run':
            return run(args.state)
        result = enable(profile=args.profile) if args.action == 'enable' else disable() if args.action == 'disable' else status()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f'ClawCross autostart: {exc}', file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print('自启动已启用' if result['enabled'] else '自启动已关闭')
        if result.get('trigger'):
            print('开机后运行' if result['trigger'] == 'boot' else '登录系统后运行')
        for warning in result.get('warnings', []):
            print(warning)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
