"""Native SRT isolation for commands in Auto approval mode.

The model reviewer remains responsible for authorization.  This module only
constructs a bounded SRT process with an explicit, per-command policy.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def sandbox_failure_hint(stderr: str) -> str:
    """Distinguish sandbox startup failures from denied workload operations."""
    if "ClawCross Landlock 初始化失败:" in stderr:
        return "❌ Landlock 沙盒初始化失败，命令尚未启动；不会降级为宿主执行。"
    if "listen EPERM" in stderr and "srt-" in stderr:
        return "❌ SRT 沙盒初始化失败：当前环境禁止创建代理 socket；命令尚未启动。可选择 Linux Landlock 后端。"
    if "apply-seccomp:" in stderr and any(marker in stderr for marker in (
        "setgroups", "uid_map", "gid_map", "unshare", "Operation not permitted",
    )):
        return (
            "❌ SRT 隔离初始化失败：嵌套 user namespace 被系统策略拒绝，命令尚未启动。"
            "需要管理员检查 AppArmor/bwrap 与 SRT 的兼容性；这不是工作区路径或域名提权问题。"
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
    """Operator configuration, separate from agent/session settings. Empty denies."""
    result = {}
    for access, suffix in (('read_path', 'READ_PATHS'), ('write_path', 'WRITE_PATHS'), ('network', 'DOMAINS')):
        try:
            values = json.loads(os.environ.get('CLAWCROSS_SANDBOX_MAX_' + suffix, '[]'))
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
        allowed = target in maximum  # Exact host/port only, no wildcard expansion.
    else:
        path = Path(target)
        if any(path.is_relative_to(prefix) for prefix in (Path('/proc'), Path('/sys'), Path('/dev'), Path('/etc'))):
            raise SandboxUnavailable('系统、设备和账户配置路径不能自动提权。')
        allowed = any(Path(v).is_absolute() and path.is_relative_to(Path(v).resolve()) for v in maximum)
    if not allowed:
        raise SandboxUnavailable('所需权限超出管理员设置的沙盒提权上限；审核不能解除此限制。')
    return target


def permission_failure_target(stderr: str, root: Path) -> tuple[str, str] | None:
    """Failure evidence is untrusted: at most one bounded exception, still reviewed.

    Ambiguous EACCES cannot distinguish read from write, so is not widened.
    No general command error or sandbox initialization error causes escalation.
    """
    if '初始化失败' in sandbox_failure_hint(stderr):
        return None
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
    if path == Path("/") or root.is_relative_to(path) or path.is_relative_to(root):
        raise SandboxUnavailable("路径提权仅用于工作区外的具体目标，不能指定工作区或其上级目录。")
    if path == home or home.is_relative_to(path) or any(
        path.is_relative_to(private) or private.is_relative_to(path)
        for private in private_paths
    ):
        raise SandboxUnavailable("常见凭据目录或其上级目录不能作为路径提权目标。")
    return str(path)


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
    executable = Path(binary).resolve()
    candidates = (
        executable.parent.parent / "package.json",  # npm .bin symlink -> dist/cli.js
        executable.parent / "node_modules/@anthropic-ai/sandbox-runtime/package.json",  # Windows .cmd shim
    )
    manifest = None
    for path in candidates:
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if candidate.get("name") == "@anthropic-ai/sandbox-runtime":
            manifest = candidate
            break
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str((manifest or {}).get("version", "")))
    if not match or tuple(map(int, match.groups())) < (0, 0, 77):
        raise SandboxUnavailable("需要 SRT 0.0.77 或更新版本；命令不会在宿主机直接执行。")
    return binary


def _policy(root: Path, settings_path: Path, *, access: str = "default", target: str = "", srt_binary: str = "") -> dict:
    home = Path.home().resolve()
    deny_read = [str(home)]
    deny_read.extend(str(path) for name in _PRIVATE_NAMES if (path := home / name).exists())
    deny_read.append(str(settings_path))
    allow_read = list(dict.fromkeys(str(path.resolve()) for path in (root, Path(sys.prefix), Path(sys.base_prefix))))
    if sys.platform.startswith("linux") and srt_binary:
        # SRT executes its seccomp helper inside the sandbox. Its explicit
        # per-user install path is otherwise hidden by denyRead(home).
        seccomp = Path(srt_binary).resolve().parent.parent / "vendor" / "seccomp"
        if seccomp.is_dir():
            allow_read.append(str(seccomp))
    allow_write = list(dict.fromkeys((str(root), str(Path(tempfile.gettempdir()).resolve()))))
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
            "allowWrite": allow_write, "denyWrite": [str(settings_path)],
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
                      target: str = "") -> SrtCommand:
    """Create an SRT invocation with a private settings file; never use a host shell."""
    root, cwd = root.resolve(), cwd.resolve()
    if not cwd.is_relative_to(root):
        raise SandboxUnavailable("命令工作目录超出会话工作区。")
    target = bounded_escalation(access, target, root) if access != 'default' else normalize_escalation(access, target, root)
    if language == "python":
        if script_path is None or not script_path.resolve().is_relative_to(root):
            raise SandboxUnavailable("Python 脚本超出会话工作区。")
        wrapped = [python_executable, *(["-i"] if interactive else []), str(script_path.resolve())]
    elif language == "shell":
        if os.name == "nt":
            wrapped = [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
        else:
            wrapped = ["/bin/sh", "-c", command]
    else:
        raise SandboxUnavailable("不支持的 SRT 命令语言。")
    if os.name == "nt":
        raise SandboxUnavailable("Windows SRT 资源限制尚不可用；已阻止本次沙盒命令。")
    binary = _srt_binary()
    fd, raw_path = tempfile.mkstemp(prefix="clawcross-srt-", suffix=".json")
    settings_path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_policy(root, settings_path, access=access, target=target, srt_binary=binary), handle, ensure_ascii=False)
        limited = (sys.executable, "-c", _LIMIT_CODE, *wrapped)
        return SrtCommand((binary, "--settings", str(settings_path), "--", *limited), settings_path)
    except BaseException:
        settings_path.unlink(missing_ok=True)
        raise


def landlock_available() -> bool:
    """Capability hint only; the launcher still verifies every filter installation."""
    if sys.platform != "linux" or platform.machine() not in {"x86_64", "aarch64"} or os.geteuid() == 0:
        return False
    return ctypes.CDLL(None, use_errno=True).syscall(444, 0, 0, 1) >= 6 and ctypes.util.find_library("seccomp") is not None


def build_landlock_command(*, root: Path, cwd: Path, command: str, language: str,
                           python_executable: str, script_path: Path | None = None,
                           interactive: bool = False, access: str = "default", target: str = "") -> SrtCommand:
    if not landlock_available():
        raise SandboxUnavailable("Landlock 需要 Linux x86_64/aarch64、ABI ≥ 6、libseccomp 及非 root 账号；不会降级为宿主执行。")
    root, cwd = root.resolve(), cwd.resolve()
    if not cwd.is_relative_to(root):
        raise SandboxUnavailable("命令工作目录超出会话工作区。")
    if access == "network":
        raise SandboxUnavailable("Landlock 后端禁用全部网络，尚不支持网络提权。")
    target = bounded_escalation(access, target, root) if access != "default" else normalize_escalation(access, target, root)
    if language == "python":
        if script_path is None or not script_path.resolve().is_relative_to(root):
            raise SandboxUnavailable("Python 脚本超出会话工作区。")
        wrapped = [python_executable, *(["-i"] if interactive else []), str(script_path.resolve())]
    elif language == "shell":
        wrapped = ["/bin/sh", "-c", command]
    else:
        raise SandboxUnavailable("不支持的沙盒命令语言。")
    # Private per-command temporary directory, covered by the workspace rule.
    temporary_dir = Path(tempfile.mkdtemp(prefix=".command-tmp-", dir=root))
    fd, raw_path = tempfile.mkstemp(prefix="clawcross-landlock-", suffix=".json")
    settings_path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"root": str(root), "read_paths": [target] if access == "read_path" else [],
                       "write_paths": [target] if access == "write_path" else []}, handle)
        launcher = Path(__file__).with_name("landlock_launcher.py")
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
    if landlock_available():
        return "landlock"
    raise SandboxUnavailable("SRT 探测失败，且当前内核不支持 Landlock + seccomp；命令未启动。")
