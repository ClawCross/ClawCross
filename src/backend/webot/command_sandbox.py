"""Command isolation backends and bounded system-managed permission retries.

The model reviewer remains responsible for authorization.  This module only
constructs a bounded sandbox process with an explicit, per-command policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import subprocess
import signal
import platform
import ctypes
import ctypes.util


class SandboxUnavailable(RuntimeError):
    """The requested native sandbox cannot be used safely."""


MAX_PERMISSION_RETRIES = 8


def protected_control_paths() -> list[Path]:
    from common.runtime_paths import CONFIG_DIR
    from webot import runtime_settings, policy
    return list(dict.fromkeys(path.resolve() for path in (
        CONFIG_DIR, runtime_settings.USER_FILES_DIR / '.control',
        policy.get_tool_policy_path('control_probe').parent.parent,
    )))


def validate_workspace_root(root: Path, *, strict: bool = False) -> None:
    """An allow rule must never encompass the backend's control plane."""
    protected = protected_control_paths() + [Path(sys.prefix)]
    if strict:
        from common.runtime_paths import PROJECT_ROOT
        protected.append(PROJECT_ROOT)
    root = root.resolve()
    for path in protected:
        path = path.resolve()
        if root.is_relative_to(path) or path.is_relative_to(root):
            raise SandboxUnavailable('工作区与后端配置、运行环境或受保护源代码重叠，拒绝执行。')


def _command_settings_file(prefix: str) -> tuple[int, Path]:
    from common.runtime_paths import CONFIG_DIR
    directory = CONFIG_DIR / 'sandbox' / 'commands'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=prefix, suffix='.json', dir=directory)
    return fd, Path(path)


def approved_retry_chain(user_id: str, session_id: str, args: dict, root: Path, *, roots=None) -> list[dict]:
    """Recover only the linked, consumed approvals for this exact command call."""
    from webot import runtime_store as store
    from webot.approval_actions import canonical_action_args
    chain = args.get('sandbox_approval_chain') or []
    if not isinstance(chain, list) or len(chain) > MAX_PERMISSION_RETRIES or any(not isinstance(key, str) for key in chain) or len(set(chain)) != len(chain):
        raise SandboxUnavailable('沙盒重试审批链无效。')
    if chain and args.get('sandbox_access', 'default') == 'default':
        raise SandboxUnavailable('已批准权限仅用于系统恢复的沙盒重试。')
    fields = {'sandbox_access', 'escalation_target', 'escalation_reason', 'sandbox_approval_chain'}
    def original_action(value):
        return {k:v for k,v in canonical_action_args('run_command', value).items() if k not in fields}
    action = original_action(args)
    grants = []
    for index, key in enumerate(chain):
        record = store.get_tool_approval(key, user_id)
        if (record is None or record.session_id != session_id or record.tool_name != 'run_command'
                or record.status != 'used' or record.expires_at <= store.utc_now()):
            raise SandboxUnavailable('此前沙盒授权已失效或不属于本次命令。')
        prior = json.loads(record.args_json or '{}')
        metadata = json.loads(record.review_metadata_json or '{}')
        if (original_action(prior) != action or prior.get('sandbox_approval_chain', []) != chain[:index]
                or metadata.get('sandbox_permissions', {}).get('workspace_root') != str(root.resolve())
                or sorted(metadata.get('sandbox_permissions', {}).get('workspace_roots', [str(root.resolve())])) != sorted(str(Path(path).resolve()) for path in (roots or [root]))):
            raise SandboxUnavailable('此前沙盒授权的命令、工作区或审批链不匹配。')
        if metadata.get('sandbox_retry_closed'):
            raise SandboxUnavailable('该命令调用已结束，不能复用此前一次性授权。')
        access = prior.get('sandbox_access', 'default')
        target = bounded_escalation(access, prior.get('escalation_target', ''), root)
        grants.append({'access':access, 'target':target, 'approval_id':key,
                       'decision':(metadata.get('verdict') or {}).get('decision') or metadata.get('human_resolution', ''),
                       'reason':record.resolution_reason})
    return grants


def sandbox_failure_hint(stderr: str) -> str:
    """Distinguish sandbox startup failures from denied workload operations."""
    if "ClawCross Landlock 初始化失败:" in stderr:
        return "❌ Landlock 沙盒初始化失败，命令尚未启动；不会降级为宿主执行。"
    if 'ClawCross Windows resource initialization failed:' in stderr:
        return '❌ Windows 资源限制初始化失败，命令未能启动；不会降级为宿主执行。'
    if 'ClawCross Windows sandbox initialization failed:' in stderr:
        return '❌ Windows 沙盒初始化失败；请在沙盒组件设置中检查并初始化 Windows 隔离。不会降级为宿主执行。'
    if "listen EPERM" in stderr and "srt-" in stderr:
        return "❌ SRT 沙盒初始化失败：当前环境禁止创建代理 socket；命令尚未启动。可选择 Linux Landlock 后端。"
    if "apply-seccomp:" in stderr and any(marker in stderr for marker in (
        "setgroups", "uid_map", "gid_map", "unshare", "Operation not permitted",
    )):
        return (
            "❌ SRT 隔离初始化失败：嵌套 user namespace 被系统策略拒绝，命令尚未启动。"
            "Linux 上可选择自动或 Landlock 后端，无需修改 AppArmor；固定 SRT 不会自动切换。"
            "这不是工作区路径或域名提权问题。"
            "不要为此自动申请 host 执行、关闭 seccomp 或降级沙盒。"
        )
    if "Sandbox dependencies not available" in stderr or "bwrap: Creating new namespace failed" in stderr:
        return "❌ 沙盒初始化失败，命令尚未启动；请检查 SRT 依赖和系统 namespace 策略。不会降级为宿主机执行。"
    if "<sandbox_violations>" in stderr:
        return (
            "沙盒报告了权限拒绝。系统仅在能定位具体目标且未超出管理员权限上限时申请审核；"
            "不会交给 Agent 申请宿主执行。"
        )
    return ""


@dataclass(frozen=True)
class SrtCommand:
    argv: tuple[str, ...]
    settings_path: Path
    backend: str = "srt"
    temporary_dir: Path | None = None


def escalation_ceiling() -> dict[str, list[str]]:
    """Empty explicitly denies. Unset network ceiling permits scoped review.

    '*' in the operator ceiling permits reviewing a specific public target;
    it never becomes an unrestricted proxy grant or removes kernel isolation.
    File access remains closed until the operator configures a path ceiling.
    """
    result = {}
    for access, suffix in (('read_path', 'READ_PATHS'), ('write_path', 'WRITE_PATHS'), ('network', 'DOMAINS')):
        try:
            values = json.loads(os.environ.get('CLAWCROSS_SANDBOX_MAX_' + suffix, '["*"]' if access == 'network' else '[]'))
        except ValueError:
            values = []
        result[access] = values if isinstance(values, list) and all(isinstance(v, str) for v in values) else []
    return result


def bounded_escalation(access: str, target: str, root: Path) -> str:
    if access not in {'read_path', 'write_path', 'network'}:
        raise SandboxUnavailable('系统提权不能退出沙盒或使用宿主权限。')
    original_target = target
    target = normalize_escalation(access, target, root)
    if target != original_target:
        raise SandboxUnavailable('提权目标已改变，必须重新审核。')
    maximum = escalation_ceiling()[access]
    if access == 'network':
        allowed = '*' in maximum or target in maximum or target.rsplit(':',1)[0] in maximum
    else:
        path = Path(target)
        if any(path.is_relative_to(prefix) for prefix in (Path('/proc'), Path('/sys'), Path('/dev'), Path('/etc'))):
            raise SandboxUnavailable('系统、设备和账户配置路径不能自动提权。')
        allowed = any(Path(v).is_absolute() and path.is_relative_to(Path(v).resolve()) for v in maximum)
    if not allowed:
        raise SandboxUnavailable('所需权限超出管理员设置的沙盒提权上限；审核不能解除此限制。')
    return target


def active_sandbox_grants(grants, root: Path) -> dict[str, list[str]]:
    """Revalidate stored capabilities; revoked ceilings/changed paths grant nothing."""
    active = {'network': [], 'read_path': [], 'write_path': []}
    for grant in grants:
        try:
            access = grant.access
            target = bounded_escalation(access, grant.target, root)
        except SandboxUnavailable:
            continue
        if target not in active[access]:
            active[access].append(target)
    return active


def proxy_denied_network_target(stderr: str) -> str | None:
    """Read the proxy supervisor's denial marker, even if the client exits 0.

    This remains untrusted evidence; a target still needs bounds and review.
    Ordinary HTTP 403 responses from an upstream site have no such marker.
    """
    match = re.search(
        r'^ClawCross proxy denied network target: ([^\s()]+) \([^\r\n]*\)$',
        stderr, re.MULTILINE,
    )
    return match.group(1).lower() if match else None


def permission_failure_target(stderr: str, root: Path) -> tuple[str, str] | None:
    """Failure evidence is untrusted: at most one bounded exception, still reviewed.

    Ambiguous EACCES cannot distinguish read from write, so is not widened.
    No general command error or sandbox initialization error causes escalation.
    """
    if '初始化失败' in sandbox_failure_hint(stderr):
        return None
    proxy_target = proxy_denied_network_target(stderr)
    if proxy_target:
        return 'network', bounded_escalation('network', proxy_target, root)
    for line in stderr.splitlines():
        access = ''
        if 'Read-only file system' in line:
            access = 'write_path'
        elif 'Permission denied' in line and re.search(r'(?:cannot create|cannot create regular file)', line, re.I):
            access = 'write_path'
        elif 'Permission denied' in line and re.match(r'^(?:cat|head|tail|less|more): ', line):
            access = 'read_path'
        if access:
            target_line = re.sub(r"^/[^:]+:\s*(?:[0-9]+:\s*)?", "", line)
            paths = re.findall(r"(?:^|[\s:'\"])(/[^\s:'\"]+)", target_line)
            if len(paths) != 1:
                continue
            target = Path(paths[0])
            # Writes may fail when creating a file: grant only its existing parent.
            if access == 'write_path' and not target.exists():
                target = target.parent
            return access, bounded_escalation(access, str(target), root)
        network = re.search(r'(?:blocked|denied|not allowed).*?(?:https?://)?([a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+(?::[0-9]+)?)', line, re.I)
        if network and any(marker in line.lower() for marker in ('proxy', 'domain', 'network', 'connect')):
            return 'network', bounded_escalation('network', network.group(1).lower(), root)
    return None


_PRIVATE_NAMES = (
    ".ssh", ".aws", ".gnupg", ".kube", ".docker", ".claude", ".codex",
    ".npmrc", ".pypirc", ".config/gcloud",
    ".clawcross/config", ".clawcross/data/user_files", ".clawcross/data/agent_checkpoints",
    ".clawcross/data/webot_agents", ".clawcross/state",
)

# These limits apply to the wrapped command and every child it starts. The
# existing command timeout and output cap remain in force outside SRT.
_LIMIT_CODE = """
import os
import resource
import sys
for name, maximum in (
    ("RLIMIT_CPU", 120),
    ("RLIMIT_AS", 2 * 1024**3),
    ("RLIMIT_FSIZE", 128 * 1024**2),
    ("RLIMIT_NOFILE", 256),
    ("RLIMIT_NPROC", 256),
):
    kind = getattr(resource, name, None)
    if kind is None:
        continue
    soft, hard = resource.getrlimit(kind)
    cap = min(maximum, soft) if soft != resource.RLIM_INFINITY else maximum
    if hard != resource.RLIM_INFINITY:
        cap = min(cap, hard)
    resource.setrlimit(kind, (cap, cap))
os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
"""


def _process_limit() -> int:
    """NPROC counts the shared UID, including host tasks outside SRT's PID view."""
    if not sys.platform.startswith('linux'):
        return 256
    count = 0
    for path in Path('/proc').glob('[0-9]*/status'):
        try:
            fields = dict(line.split(':', 1) for line in path.read_text().splitlines() if ':' in line)
            if int(fields['Uid'].split()[0]) == os.getuid():
                count += int(fields.get('Threads', '1').strip())
        except (OSError, KeyError, ValueError):
            continue
    return max(256, count + 64)


def normalize_escalation(access: str, target: str, root: Path) -> str:
    """Keep each SRT exception to one explicit path or domain."""
    if access in {"default", "host"}:
        if target.strip():
            raise SandboxUnavailable("该权限等级不接受额外目标。")
        return ""
    if access == "network":
        domain = target.strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::[0-9]{1,5})?", domain) or ".." in domain:
            raise SandboxUnavailable("网络提权只能指定一个域名或域名:端口，不接受通配符、URL 或 IP 范围。")
        if ':' in domain and not 1 <= int(domain.rsplit(':',1)[1]) <= 65535:
            raise SandboxUnavailable('网络目标端口必须在 1–65535 范围内。')
        host = domain.split(":", 1)[0]
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if host == "localhost" or (address is not None and not address.is_global):
            raise SandboxUnavailable("本地服务不能通过网络域名提权；需要单独审核宿主机权限。")
        return domain
    if access not in {"read_path", "write_path"}:
        raise SandboxUnavailable("不支持的沙盒权限等级。")
    path = Path(target.strip()).expanduser()
    if not path.is_absolute() or not path.exists():
        raise SandboxUnavailable("路径提权需要指定一个已存在的绝对文件或目录。")
    path = path.resolve()
    root = root.resolve()
    home = Path.home().resolve()
    from common.runtime_paths import CONFIG_DIR, USER_FILES_DIR, STATE_DIR
    private_paths = [home / name for name in _PRIVATE_NAMES]
    private_paths.extend(p.resolve() for p in (CONFIG_DIR, USER_FILES_DIR, STATE_DIR))
    private_paths.extend(protected_control_paths())
    if path == Path("/") or root.is_relative_to(path) or path.is_relative_to(root):
        raise SandboxUnavailable("路径提权仅用于工作区外的具体目标，不能指定工作区或其上级目录。")
    if path == home or home.is_relative_to(path) or any(
        path.is_relative_to(private) or private.is_relative_to(path)
        for private in private_paths
    ):
        raise SandboxUnavailable("常见凭据目录或其上级目录不能作为路径提权目标。")
    return str(path)


def _srt_manifest(binary: str) -> tuple[Path, dict]:
    executable = Path(binary).resolve()
    candidates = (
        executable.parent.parent / 'package.json',  # POSIX .bin symlink
        executable.parent / 'node_modules/@anthropic-ai/sandbox-runtime/package.json',  # global Windows shim
        executable.parent.parent / '@anthropic-ai/sandbox-runtime/package.json',  # local Windows .bin shim
    )
    for path in candidates:
        try:
            manifest = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if manifest.get('name') == '@anthropic-ai/sandbox-runtime':
            return path.parent, manifest
    raise SandboxUnavailable('无法确认 SRT 安装版本；请在组件设置中重新安装 SRT。')


def _windows_srt_runtime(binary: str) -> tuple[str, Path]:
    package, manifest = _srt_manifest(binary)
    version = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)', str(manifest.get('version', '')))
    if not version or tuple(map(int, version.groups())) < (0, 0, 78):
        raise SandboxUnavailable('Windows 需要 SRT 0.0.78 或更新版本；请更新沙盒组件。')
    arch = {'amd64': 'x64', 'x86_64': 'x64', 'arm64': 'arm64', 'aarch64': 'arm64'}.get(platform.machine().lower())
    if not arch or not (package / 'vendor/srt-win' / arch / 'srt-win.exe').is_file():
        raise SandboxUnavailable('缺少适合本机架构的 Windows SRT helper；请重新安装沙盒组件。')
    node = shutil.which('node.exe') or shutil.which('node')
    entry = package / 'dist/index.js'
    if not node or not entry.is_file():
        raise SandboxUnavailable('Windows SRT 需要 Node.js 和完整的运行包；请重新安装沙盒组件。')
    return node, entry


def windows_srt_operation(mode: str) -> tuple[str, ...]:
    """Only trusted component controls can initialize the Windows backend."""
    if mode not in {'status', 'install'}:
        raise ValueError('Unsupported Windows SRT operation')
    binary = _srt_binary()
    node, entry = _windows_srt_runtime(binary)
    return node, str(Path(__file__).with_name('windows_srt_bridge.mjs')), str(entry), mode


def windows_srt_status() -> dict:
    try:
        argv = windows_srt_operation('status')
    except (SandboxUnavailable, OSError, ValueError) as exc:
        return {'ready': False, 'errors': [str(exc)], 'can_initialize': False, 'needs_update': True}
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
        if result.returncode:
            raise SandboxUnavailable(result.stderr.strip()[-1500:] or 'Windows SRT 状态检查失败。')
        status = json.loads(result.stdout)
        if (not isinstance(status, dict) or not isinstance(status.get('ready'), bool)
                or not isinstance(status.get('errors'), list)
                or any(not isinstance(error, str) for error in status['errors'])):
            raise ValueError('Invalid Windows SRT status')
        return {**status, 'can_initialize': True, 'needs_update': False}
    except (SandboxUnavailable, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {'ready': False, 'errors': [str(exc)], 'can_initialize': True, 'needs_update': False}


def _srt_binary() -> str:
    binary = shutil.which("srt")
    if not binary:
        home = Path(os.environ.get("CLAWCROSS_HOME") or Path.home() / ".clawcross")
        local_bin = Path(os.environ.get("CLAWCROSS_BIN_DIR") or home / "bin")
        local = local_bin / "node" / "node_modules" / ".bin" / ("srt.cmd" if os.name == "nt" else "srt")
        if local.is_file():
            binary = str(local)
    if not binary:
        raise SandboxUnavailable("未找到 srt；请运行 install-component srt。命令不会在宿主机直接执行。")
    if sys.platform.startswith("linux"):
        missing = [name for name in ("bwrap", "socat", "rg") if not shutil.which(name)]
        if missing:
            raise SandboxUnavailable(
                "SRT 缺少 Linux 依赖：" + ", ".join(missing) + "。命令不会在宿主机直接执行。"
            )
    elif sys.platform == "darwin" and not shutil.which("rg"):
        raise SandboxUnavailable("SRT 缺少 macOS 依赖 rg。命令不会在宿主机直接执行。")
    _, manifest = _srt_manifest(binary)
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str((manifest or {}).get("version", "")))
    if not match or tuple(map(int, match.groups())) < (0, 0, 77):
        raise SandboxUnavailable("需要 SRT 0.0.77 或更新版本；命令不会在宿主机直接执行。")
    if sys.platform == 'win32':
        _windows_srt_runtime(binary)
    return binary


def _policy(root: Path, settings_path: Path, *, access: str = "default", target: str = "", srt_binary: str = "", strict: bool = False, temporary_dir: Path | None = None, workspace_roots=None) -> dict:
    home = Path.home().resolve()
    deny_read = [str(home)]
    deny_read.extend(str(path) for name in _PRIVATE_NAMES if (path := home / name).exists())
    deny_read.append(str(settings_path))
    allow_read = list(dict.fromkeys(str(path.resolve()) for path in (*(workspace_roots or [root]), Path(sys.prefix), Path(sys.base_prefix))))
    deny_read.extend(str(path) for path in protected_control_paths())
    if strict:
        from common.runtime_paths import PROJECT_ROOT
        # Keep SRT's platform runtime defaults. Strict changes escalation and
        # the workspace, rather than disabling OS libraries and normal tools.
        deny_read.append(str(PROJECT_ROOT.resolve()))
    if sys.platform == 'linux':
        # SRT reads broadly by default; avoid exposing process environments,
        # memory and descriptor aliases even in the ordinary security level.
        deny_read.extend(f'/proc/{entry.name}/{name}'
                         for entry in Path('/proc').glob('[0-9]*')
                         for name in ('environ', 'mem', 'fd', 'root', 'cwd', 'map_files'))
    if sys.platform.startswith("linux") and srt_binary:
        # SRT executes its seccomp helper inside the sandbox. Its explicit
        # per-user install path is otherwise hidden by denyRead(home).
        seccomp = Path(srt_binary).resolve().parent.parent / "vendor" / "seccomp"
        if seccomp.is_dir():
            allow_read.append(str(seccomp))
    allow_write = [str(Path(path).resolve()) for path in (workspace_roots or [root])]
    if temporary_dir is not None:
        allow_write.append(str(temporary_dir))
    if access in {"read_path", "write_path"}:
        allow_read.append(target)
    if access == "write_path":
        allow_write.append(target)
    policy = {
        "network": {
            "allowedDomains": [target] if access == "network" else [], "deniedDomains": [],
            "allowUnixSockets": [], "allowLocalBinding": False,
        },
        "filesystem": {
            "denyRead": deny_read, "allowRead": allow_read,
            "allowWrite": allow_write, "denyWrite": [str(settings_path), *(str(path) for path in protected_control_paths())],
        },
        "enableWeakerNestedSandbox": False,
        "enableWeakerNetworkIsolation": False,
        "allowAppleEvents": False,
    }
    if sys.platform.startswith("linux"):
        # Optional administrator-installed binary with a dedicated AppArmor
        # profile. Never trust a user-writable replacement for this launcher.
        dedicated = Path("/usr/local/libexec/clawcross/bwrap")
        if dedicated.is_file():
            for path in (dedicated, *dedicated.parents):
                info = path.stat()
                if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
                    raise SandboxUnavailable("ClawCross 专用 bwrap 必须由 root 持有，且其路径不能允许普通用户修改。")
            policy["bwrapPath"] = str(dedicated)
    return policy


def build_srt_command(*, root: Path, cwd: Path, command: str, language: str,
                      python_executable: str, script_path: Path | None = None,
                      interactive: bool = False, access: str = "default",
                      target: str = "", allowed_domains: list[str] | None = None,
                      allowed_read_paths: list[str] | None = None, allowed_write_paths: list[str] | None = None,
                      wall_timeout: int = 180, strict: bool = False, workspace_roots=None) -> SrtCommand:
    """Create an SRT invocation with a private settings file; never use a host shell."""
    root, cwd = root.resolve(), cwd.resolve()
    roots = tuple(dict.fromkeys(Path(path).resolve() for path in (workspace_roots or [root])))
    if root not in roots:
        raise SandboxUnavailable('主目录必须属于配置的工作区集合。')
    for folder in roots:
        validate_workspace_root(folder, strict=strict)
    if strict and (access != 'default' or allowed_read_paths or allowed_write_paths):
        raise SandboxUnavailable('严格安全模式不允许提权或使用历史文件授权。')
    if not any(cwd.is_relative_to(folder) for folder in roots):
        raise SandboxUnavailable("命令工作目录超出会话工作区。")
    target = bounded_escalation(access, target, root) if access != 'default' else normalize_escalation(access, target, root)
    if language == "python":
        if script_path is None or not any(script_path.resolve().is_relative_to(folder) for folder in roots):
            raise SandboxUnavailable("Python 脚本超出会话工作区。")
        wrapped = [python_executable, *(["-i"] if interactive else []), str(script_path.resolve())]
    elif language == "shell":
        if sys.platform == 'win32':
            wrapped = [os.environ.get('COMSPEC') or str(Path(os.environ.get('SYSTEMROOT', 'C:/Windows')) / 'System32/cmd.exe'), '/d', '/s', '/c', command]
        else:
            wrapped = ["/bin/sh", "-c", command]
    else:
        raise SandboxUnavailable("不支持的 SRT 命令语言。")
    binary = _srt_binary()
    windows_runtime = _windows_srt_runtime(binary) if sys.platform == 'win32' else None
    temporary_dir = Path(tempfile.mkdtemp(prefix='.command-tmp-', dir=root))
    fd, settings_path = _command_settings_file('clawcross-srt-')
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            config = _policy(root, settings_path, access=access, target=target, srt_binary=binary, strict=strict, temporary_dir=temporary_dir, workspace_roots=roots)
            config['network']['allowedDomains'] = list(dict.fromkeys([*(allowed_domains or []), *([target] if access == 'network' else [])]))
            reads = [bounded_escalation('read_path', path, root) for path in (allowed_read_paths or [])]
            writes = [bounded_escalation('write_path', path, root) for path in (allowed_write_paths or [])]
            config['filesystem']['allowRead'].extend(reads + writes)
            config['filesystem']['allowWrite'].extend(writes)
            json.dump(config, handle, ensure_ascii=False)
        if windows_runtime:
            node, entry = windows_runtime
            payload = base64.b64encode(json.dumps({'argv': wrapped, 'timeout': wall_timeout}, ensure_ascii=False).encode()).decode('ascii')
            module = Path(__file__)
            argv = (node, str(module.with_name('windows_srt_bridge.mjs')), str(entry), 'run',
                    str(settings_path), python_executable, str(module.with_name('windows_resource_limits.py')), payload)
            return SrtCommand(argv, settings_path, temporary_dir=temporary_dir)
        limits = _LIMIT_CODE.replace('(\"RLIMIT_NPROC\", 256)', f'(\"RLIMIT_NPROC\", {_process_limit()})')
        limited = (sys.executable, "-c", limits, *wrapped)
        return SrtCommand((binary, "--settings", str(settings_path), "--", *limited), settings_path, temporary_dir=temporary_dir)
    except BaseException:
        settings_path.unlink(missing_ok=True)
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def landlock_available() -> bool:
    """Capability hint only; the launcher still verifies every filter installation."""
    if sys.platform != "linux" or platform.machine() not in {"x86_64", "aarch64"} or os.geteuid() == 0:
        return False
    return ctypes.CDLL(None, use_errno=True).syscall(444, 0, 0, 1) >= 6 and ctypes.util.find_library("seccomp") is not None


def build_landlock_command(*, root: Path, cwd: Path, command: str, language: str,
                           python_executable: str, script_path: Path | None = None,
                           interactive: bool = False, access: str = "default", target: str = "",
                           allowed_domains: list[str] | None = None,
                           allowed_read_paths: list[str] | None = None, allowed_write_paths: list[str] | None = None,
                           wall_timeout: int = 180, strict: bool = False, workspace_roots=None) -> SrtCommand:
    if not landlock_available():
        raise SandboxUnavailable("Landlock 需要 Linux x86_64/aarch64、ABI ≥ 6、libseccomp 及非 root 账号；不会降级为宿主执行。")
    root, cwd = root.resolve(), cwd.resolve()
    roots = tuple(dict.fromkeys(Path(path).resolve() for path in (workspace_roots or [root])))
    if root not in roots:
        raise SandboxUnavailable('主目录必须属于配置的工作区集合。')
    for folder in roots:
        validate_workspace_root(folder, strict=strict)
    if strict and (access != 'default' or allowed_read_paths or allowed_write_paths):
        raise SandboxUnavailable('严格安全模式不允许提权或使用历史文件授权。')
    if not any(cwd.is_relative_to(folder) for folder in roots):
        raise SandboxUnavailable("命令工作目录超出会话工作区。")
    controlled_network = network_fence_available()
    if not controlled_network and (access == "network" or allowed_domains):
        raise SandboxUnavailable("受控联网需要可管理的 systemd/cgroup 网络规则；当前环境不支持，未执行命令。")
    target = bounded_escalation(access, target, root) if access != "default" else normalize_escalation(access, target, root)
    reads = [bounded_escalation('read_path', path, root) for path in (allowed_read_paths or [])]
    writes = [bounded_escalation('write_path', path, root) for path in (allowed_write_paths or [])]
    if language == "python":
        if script_path is None or not any(script_path.resolve().is_relative_to(folder) for folder in roots):
            raise SandboxUnavailable("Python 脚本超出会话工作区。")
        wrapped = [python_executable, *(["-i"] if interactive else []), str(script_path.resolve())]
    elif language == "shell":
        wrapped = ["/bin/sh", "-c", command]
    else:
        raise SandboxUnavailable("不支持的沙盒命令语言。")
    # Private per-command temporary directory, covered by the workspace rule.
    temporary_dir = Path(tempfile.mkdtemp(prefix=".command-tmp-", dir=root))
    fd, settings_path = _command_settings_file('clawcross-landlock-')
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"root": str(root), "workspace_roots":[str(path) for path in roots], "read_paths": list(dict.fromkeys([*reads, *([target] if access == "read_path" else [])])),
                       "write_paths": list(dict.fromkeys([*writes, *([target] if access == "write_path" else [])])),
                       "allowed_domains": list(dict.fromkeys([*(allowed_domains or []), *([target] if access == 'network' else [])])),
                       "strict": strict, "wall_timeout": max(1, int(wall_timeout))}, handle)
        launcher = Path(__file__).with_name("landlock_network.py" if controlled_network else "landlock_launcher.py")
        return SrtCommand((sys.executable, str(launcher), str(settings_path), *wrapped), settings_path,
                          backend="landlock", temporary_dir=temporary_dir)
    except BaseException:
        settings_path.unlink(missing_ok=True)
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise


def select_sandbox_backend(requested: str, *, root: Path, cwd: Path, env: dict) -> str:
    """Auto probes only a harmless command before executing any user workload.

    Never replay a user command to discover which backend works. Explicit SRT
    retains fail-closed behavior. Auto on Linux may use the Landlock alternative.
    """
    if requested != "auto":
        return requested
    if sys.platform != "linux":
        return "srt"
    sandbox = None
    proc = None
    try:
        sandbox = build_srt_command(root=root, cwd=cwd, command="true", language="shell", python_executable=sys.executable)
        proc = subprocess.Popen(sandbox.argv, cwd=cwd, env=env, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        if proc.wait(timeout=5) == 0:
            return "srt"
    except (SandboxUnavailable, OSError, subprocess.TimeoutExpired):
        pass
    finally:
        if proc is not None:
            # Also clean any proxy helpers spawned by the probe.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        if sandbox is not None:
            sandbox.settings_path.unlink(missing_ok=True)
            if sandbox.temporary_dir is not None:
                shutil.rmtree(sandbox.temporary_dir, ignore_errors=True)
    if landlock_available():
        return "landlock"
    raise SandboxUnavailable("SRT 探测失败，且当前内核不支持 Landlock + seccomp；命令未启动。")


def network_fence_available() -> bool:
    """A hint; each unit verifies actual network enforcement before exec."""
    if sys.platform != 'linux':
        return False
    try:
        if Path('/proc/1/comm').read_text().strip() != 'systemd':
            return False
        if not all(shutil.which(name) for name in ('sudo','systemd-run','ip')):
            return False
        # Test this exact trusted operation, never rely on a TTY/polkit prompt.
        return subprocess.run(['sudo','-n','systemctl','show','--property=Version'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
