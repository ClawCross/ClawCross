"""Native SRT isolation for commands in Auto approval mode.

The model reviewer remains responsible for authorization.  This module only
constructs a bounded SRT process with an explicit, per-command policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


class SandboxUnavailable(RuntimeError):
    """The requested native sandbox cannot be used safely."""


@dataclass(frozen=True)
class SrtCommand:
    argv: tuple[str, ...]
    settings_path: Path


def _srt_binary() -> str:
    binary = shutil.which("srt")
    if not binary:
        raise SandboxUnavailable("未找到 srt；请安装 @anthropic-ai/sandbox-runtime。命令不会在宿主机直接执行。")
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


def _policy(root: Path, settings_path: Path) -> dict:
    home = Path.home().resolve()
    private_names = (
        ".ssh", ".aws", ".gnupg", ".kube", ".docker", ".claude", ".codex",
        ".npmrc", ".pypirc", ".config/gcloud",
    )
    deny_read = [str(path) for name in private_names if (path := home / name).exists()]
    deny_read.append(str(settings_path))
    allow_read = list(dict.fromkeys(str(path.resolve()) for path in (root, Path(sys.prefix), Path(sys.base_prefix))))
    allow_write = list(dict.fromkeys((str(root), str(Path(tempfile.gettempdir()).resolve()))))
    return {
        "network": {
            "allowedDomains": [], "deniedDomains": [],
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


def build_srt_command(*, root: Path, cwd: Path, command: str, language: str,
                      python_executable: str, script_path: Path | None = None) -> SrtCommand:
    """Create an SRT invocation with a private settings file; never use a host shell."""
    root, cwd = root.resolve(), cwd.resolve()
    if not cwd.is_relative_to(root):
        raise SandboxUnavailable("命令工作目录超出会话工作区。")
    if language == "python":
        if script_path is None or not script_path.resolve().is_relative_to(root):
            raise SandboxUnavailable("Python 脚本超出会话工作区。")
        wrapped = [python_executable, str(script_path.resolve())]
    elif language == "shell":
        if os.name == "nt":
            wrapped = [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]
        else:
            wrapped = ["/bin/sh", "-c", command]
    else:
        raise SandboxUnavailable("不支持的 SRT 命令语言。")
    binary = _srt_binary()
    fd, raw_path = tempfile.mkstemp(prefix="clawcross-srt-", suffix=".json")
    settings_path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_policy(root, settings_path), handle, ensure_ascii=False)
        return SrtCommand((binary, "--settings", str(settings_path), "--", *wrapped), settings_path)
    except BaseException:
        settings_path.unlink(missing_ok=True)
        raise
