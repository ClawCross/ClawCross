"""Explicit optional component installation; never called during startup."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import PackageNotFoundError, distribution
import threading
from common.runtime_paths import BIN_DIR, PROJECT_ROOT

COMPONENTS = {"srt": "Anthropic Sandbox Runtime", "acpx": "ACP agent bridge", "srt-system": "Sandbox system dependencies", "weclaw": "WeClaw", "cloudflared": "Cloudflare Tunnel", "nonebot": "NoneBot", "channels": "QQ and Telegram connectors"}
_lock = threading.Lock()
_jobs = {}


def binary_path(name):
    if name in {"weclaw", "cloudflared"}:
        local = BIN_DIR / (name + (".exe" if os.name == "nt" else ""))
        return shutil.which(name) or (str(local) if local.is_file() else None)
    local = BIN_DIR / "node" / "node_modules" / ".bin" / (name + (".cmd" if os.name == "nt" else ""))
    return shutil.which(name) or (str(local) if local.is_file() else None)


def component_status(name):
    if name not in COMPONENTS:
        raise ValueError("Unsupported component")
    if name == "srt-system":
        if sys.platform == 'win32':
            from webot.command_sandbox import windows_srt_status
            status = windows_srt_status()
            with _lock:
                job = dict(_jobs.get(name, {}))
            return {'name': name, 'installed': status['ready'], 'ready': status['ready'],
                    'missing': [] if status['ready'] else ['windows-install'],
                    'platform': sys.platform, 'can_install': status['can_initialize'],
                    'detail': '\n'.join(status['errors']), **job}
        dependencies = ["bwrap", "socat", "rg"] if sys.platform.startswith("linux") else ["rg"] if sys.platform == "darwin" else []
        missing = [item for item in dependencies if not shutil.which(item)]
        supported = bool(shutil.which("apt-get")) if sys.platform.startswith("linux") else bool(shutil.which("brew")) if sys.platform == "darwin" else False
        with _lock:
            job = dict(_jobs.get(name, {}))
        return {"name": name, "installed": not missing, "ready": not missing, "missing": missing,
                "platform": sys.platform, "can_install": supported, **job}
    if name in {"weclaw", "cloudflared", "nonebot", "channels"}:
        installed = bool(binary_path(name)) if name in {"weclaw", "cloudflared"} else True
        if name in {"nonebot", "channels"}:
            for package in (["nonebot2"] if name == "nonebot" else ["qq-botpy", "python-telegram-bot"]):
                try:
                    distribution(package)
                except PackageNotFoundError:
                    installed = False
        uv = os.getenv("CLAWCROSS_UV_BIN") or shutil.which("uv")
        missing = ["uv"] if name in {"nonebot", "channels"} and not uv else []
        with _lock:
            job = dict(_jobs.get(name, {}))
        return {"name": name, "installed": installed, "ready": installed, "missing": missing,
                "platform": sys.platform, "can_install": not missing, **job}
    prerequisites = ["npm.cmd" if os.name == "nt" else "npm"]
    if name == "srt":
        prerequisites += ["bwrap", "socat", "rg"] if sys.platform.startswith("linux") else ["rg"] if sys.platform == "darwin" else []
    missing = [item for item in prerequisites if not shutil.which(item)]
    installed = bool(binary_path(name))
    windows_status = None
    if name == 'srt' and installed and sys.platform == 'win32':
        from webot.command_sandbox import windows_srt_status
        windows_status = windows_srt_status()
        if not windows_status['ready']:
            missing.append('srt-update' if windows_status['needs_update'] else 'windows-install')
    runtime_missing = [item for item in missing if not item.startswith("npm")]
    with _lock:
        job = dict(_jobs.get(name, {}))
    return {"name": name, "installed": installed, "ready": installed and not runtime_missing,
            'needs_update': bool(windows_status and windows_status['needs_update']),
            'detail': '\n'.join(windows_status['errors']) if windows_status else '',
            "missing": missing, "platform": sys.platform, "can_install": not any(item.startswith("npm") for item in missing), **job}


def _install(name):
    try:
        # File-backed output bounds memory, and no shell or user-supplied arguments are accepted.
        with tempfile.TemporaryFile() as output:
            command = [sys.executable, str(PROJECT_ROOT / "launch" / "environment.py"), "install", name]
            if name == "srt-system":
                if sys.platform == 'win32':
                    from webot.command_sandbox import windows_srt_operation
                    command = list(windows_srt_operation('install'))
                elif sys.platform.startswith("linux") and shutil.which("apt-get"):
                    command = [shutil.which("apt-get"), "install", "--yes", "--no-upgrade", "bubblewrap", "socat", "ripgrep"]
                    if os.geteuid() != 0:
                        sudo = shutil.which("sudo")
                        if not sudo:
                            raise ValueError("系统依赖安装需要服务器管理员权限。")
                        command = [sudo, "-n", "--"] + command
                elif sys.platform == "darwin" and shutil.which("brew"):
                    command = [shutil.which("brew"), "install", "ripgrep"]
                else:
                    raise ValueError("此系统需要管理员手动安装沙盒系统依赖。")
            result = subprocess.run(command, cwd=PROJECT_ROOT, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=660, check=False)
            output.seek(0, 2)
            output.seek(max(0, output.tell() - 4000))
            detail = output.read().decode("utf-8", errors="replace")
        state = "complete" if result.returncode == 0 else "failed"
    except Exception as exc:
        state, detail = "failed", str(exc)
    with _lock:
        _jobs[name] = {"state": state, "detail": detail}


def start_install(name):
    status = component_status(name)
    if not status["can_install"]:
        if name in {"nonebot", "channels"}:
            raise ValueError("未找到 uv，请检查服务器的 Python 启动环境。")
        raise ValueError("此系统需要管理员手动安装沙盒系统依赖。" if name == "srt-system" else "请先在服务器安装 Node.js 20.11+（含 npm），然后重试。")
    with _lock:
        if any(job.get("state") == "installing" for job in _jobs.values()):
            return component_busy(name)
        _jobs[name] = {"state": "installing", "detail": ""}
    threading.Thread(target=_install, args=(name,), daemon=True).start()
    return component_status(name)


def component_busy(name):
    return {"name": name, "state": "busy", "detail": "另一个组件正在安装，请稍后重试。"}
