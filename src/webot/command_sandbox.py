"""Optional OCI isolation for commands in Auto approval mode.

Only the selected workspace is bind-mounted.  The container has no network,
container socket, extra capabilities, or writable rootfs; host credentials
are not passed into its environment.
The reviewer still decides whether the requested action is authorized.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import uuid


DEFAULT_IMAGE = "python:3.12-slim"


class SandboxUnavailable(RuntimeError):
    """The requested container sandbox cannot be used safely."""


@dataclass(frozen=True)
class ContainerCommand:
    argv: tuple[str, ...]
    runtime: str
    name: str


def _runtime_and_image() -> tuple[str, str]:
    preferred = os.getenv("WEBOT_SANDBOX_RUNTIME", "auto").strip().lower()
    if preferred not in {"auto", "podman", "docker"}:
        raise SandboxUnavailable("WEBOT_SANDBOX_RUNTIME 必须是 auto、podman 或 docker。")
    choices = ("podman", "docker") if preferred == "auto" else (preferred,)
    image = os.getenv("WEBOT_SANDBOX_IMAGE", DEFAULT_IMAGE).strip()
    if not image or image.startswith("-") or any(char.isspace() for char in image):
        raise SandboxUnavailable("WEBOT_SANDBOX_IMAGE 无效。")
    found = False
    for choice in choices:
        runtime = shutil.which(choice)
        if not runtime:
            continue
        found = True
        try:
            check = subprocess.run(
                [runtime, "image", "inspect", image], capture_output=True, text=True,
                timeout=8, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if check.returncode == 0:
            return runtime, image
    if not found:
        raise SandboxUnavailable("未找到 Podman 或 Docker；Auto 沙盒已启用，命令不会在宿主机执行。")
    raise SandboxUnavailable(
        f"容器运行时不可用或本地缺少镜像 {image}；请先在宿主机准备镜像。命令不会回退宿主机。"
    )


def build_container_command(*, root: Path, cwd: Path, command: str, language: str,
                            script_path: Path | None = None) -> ContainerCommand:
    """Build a local, non-pulling container call without invoking a host shell."""
    runtime, image = _runtime_and_image()
    root, cwd = root.resolve(), cwd.resolve()
    if not cwd.is_relative_to(root):
        raise SandboxUnavailable("命令工作目录超出会话工作区。")
    if script_path is not None:
        script_path = script_path.resolve()
        if language != "python" or not script_path.is_relative_to(root):
            raise SandboxUnavailable("Python 脚本超出会话工作区。")
    # OCI --mount uses comma-separated key/value pairs.  Reject paths that
    # could be parsed as additional mount options by the runtime.
    if "," in str(root):
        raise SandboxUnavailable("工作区路径含逗号，容器挂载无法安全解析。")
    name = "clawcross-auto-" + uuid.uuid4().hex
    container_cwd = "/workspace" + ("/" + cwd.relative_to(root).as_posix() if cwd != root else "")
    uid = f"{os.getuid()}:{os.getgid()}" if hasattr(os, "getuid") and os.getuid() else "1000:1000"
    argv = [
        runtime, "run", "--rm", "--pull=never", "--name", name,
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=128",
        "--memory=512m", "--cpus=1", "--user", uid,
        "--tmpfs=/tmp:rw,nosuid,nodev,size=64m",
        "--mount", f"type=bind,source={root},target=/workspace",
        "--workdir", container_cwd, "--env", "HOME=/tmp",
        "--env", "PYTHONDONTWRITEBYTECODE=1",
    ]
    if language == "python":
        argv.extend(["--entrypoint", "/usr/local/bin/python", image])
        if script_path is None:
            argv.extend(["-c", command])
        else:
            argv.append("/workspace/" + script_path.relative_to(root).as_posix())
    elif language == "shell":
        argv.extend(["--entrypoint", "/bin/sh", image, "-c", command])
    else:
        raise SandboxUnavailable("不支持的容器命令语言。")
    return ContainerCommand(tuple(argv), runtime, name)
