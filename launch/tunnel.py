#!/usr/bin/env python3
"""Run one Cloudflare Quick Tunnel for the frontend without downloading binaries."""

from __future__ import annotations

import atexit
import os
import platform
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.common.runtime_paths import (  # noqa: E402
    ENV_FILE, PID_DIR, WORKSPACE_DIR, cloudflared_path, ensure_runtime_dirs,
)
from src.backend.common.public_access import write_tunnel_domain  # noqa: E402

ensure_runtime_dirs()
PID_FILE = PID_DIR / "tunnel.pid"
CLOUDFLARED_PID_FILE = PID_DIR / "cloudflared.pid"
_child: subprocess.Popen | None = None
_public_url = ""
_claimed = False
_cleaned = False
_URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def _setting(key: str) -> str:
    values = dotenv_values(str(ENV_FILE))
    return str((values[key] if key in values else os.getenv(key)) or "").strip()


def _cloudflared_binary() -> str:
    explicit = _setting("CLOUDFLARED_BIN")
    candidates = [explicit] if explicit else [str(cloudflared_path()), shutil.which("cloudflared") or ""]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return str(Path(candidate).resolve())
    raise RuntimeError("cloudflared is not installed; install it manually and set CLOUDFLARED_BIN or add it to PATH")


def _tunnel_command() -> list[str]:
    binary = _cloudflared_binary()
    try:
        port = int(_setting("PORT_FRONTEND") or "51209")
    except ValueError as exc:
        raise RuntimeError("PORT_FRONTEND must be a valid port") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("PORT_FRONTEND must be a valid port")
    return [binary, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.system().lower() == "windows":
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=5)
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _claim_pid() -> None:
    global _claimed
    for _ in range(3):
        try:
            fd = os.open(PID_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                existing = int(PID_FILE.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                existing = 0
            if _pid_alive(existing):
                raise RuntimeError(f"Tunnel manager is already running (PID {existing})")
            if not existing:
                try:
                    if time.time() - PID_FILE.stat().st_mtime < 10:
                        raise RuntimeError("Tunnel manager is already starting")
                except FileNotFoundError:
                    continue
            try:
                PID_FILE.unlink()
            except FileNotFoundError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        _claimed = True
        return
    raise RuntimeError("Could not claim the tunnel PID file")


def _write_public_domain(value: str) -> None:
    """Publish a Quick Tunnel URL while retaining a configured fixed domain."""
    write_tunnel_domain(value, ENV_FILE)


def _owns_pid(path: Path, pid: int) -> bool:
    try:
        return path.read_text(encoding="utf-8").strip() == str(pid)
    except FileNotFoundError:
        return False


def _remove_owned_pid(path: Path, pid: int) -> None:
    try:
        if _owns_pid(path, pid):
            path.unlink()
    except FileNotFoundError:
        pass


def cleanup() -> None:
    global _cleaned
    if _cleaned:
        return
    _cleaned = True
    if _child is not None:
        if _child.poll() is None:
            _child.terminate()
            try:
                _child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _child.kill()
                _child.wait()
        _remove_owned_pid(CLOUDFLARED_PID_FILE, _child.pid)
    if _claimed and _owns_pid(PID_FILE, os.getpid()):
        try:
            write_tunnel_domain("", ENV_FILE, expected=_public_url)
        finally:
            _remove_owned_pid(PID_FILE, os.getpid())


def _signal_exit(_signum, _frame) -> None:
    raise SystemExit(0)


def start_tunnels() -> None:
    global _child, _public_url
    command = _tunnel_command()
    _claim_pid()
    atexit.register(cleanup)
    signal.signal(signal.SIGINT, _signal_exit)
    if os.name != "nt":
        signal.signal(signal.SIGTERM, _signal_exit)
    try:
        _write_public_domain("")
        popen_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        _child = subprocess.Popen(
            command, cwd=WORKSPACE_DIR, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1, **popen_options,
        )
        CLOUDFLARED_PID_FILE.write_text(str(_child.pid), encoding="utf-8")
        found_urls: queue.Queue[str] = queue.Queue(maxsize=1)

        def forward_output() -> None:
            assert _child is not None and _child.stdout is not None
            for line in _child.stdout:
                print(line, end="", flush=True)
                match = _URL_PATTERN.search(line)
                if match and found_urls.empty():
                    found_urls.put_nowait(match.group(0))

        threading.Thread(target=forward_output, daemon=True).start()
        try:
            _public_url = found_urls.get(timeout=60)
        except queue.Empty as exc:
            raise RuntimeError("Cloudflare did not provide a public URL within 60 seconds") from exc
        if _child.poll() is not None:
            raise RuntimeError(f"cloudflared exited during startup (code {_child.returncode})")
        _write_public_domain(_public_url)
        print(f"Quick tunnel running at {_public_url}", flush=True)
        code = _child.wait()
        if code:
            raise RuntimeError(f"cloudflared exited with code {code}")
    finally:
        cleanup()


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--check"]:
            _tunnel_command()
            print("Cloudflare Quick Tunnel is ready to start", flush=True)
        elif not sys.argv[1:]:
            start_tunnels()
        else:
            raise RuntimeError("Usage: tunnel.py [--check]")
    except Exception as exc:
        print(f"Tunnel unavailable: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
