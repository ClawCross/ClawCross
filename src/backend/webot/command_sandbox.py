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


class SandboxUnavailable(RuntimeError):
    """The requested native sandbox cannot be used safely."""


def sandbox_failure_hint(stderr: str) -> str:
    """Distinguish sandbox startup failures from denied workload operations."""
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
            "沙盒报告了权限拒绝。核对具体路径或域名后，可用同一 run_command 的 "
            "sandbox_access 与 escalation_target 申请单次提权；本次命令不会自动重跑。"
        )
    return ""


@dataclass(frozen=True)
class SrtCommand:
    argv: tuple[str, ...]
    settings_path: Path


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
    if path == Path("/") or root.is_relative_to(path) or path.is_relative_to(root):
        raise SandboxUnavailable("路径提权仅用于工作区外的具体目标，不能指定工作区或其上级目录。")
    if path == home or home.is_relative_to(path) or any(
        path.is_relative_to(private) or private.is_relative_to(path)
        for private in (home / name for name in _PRIVATE_NAMES)
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
    target = normalize_escalation(access, target, root)
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
