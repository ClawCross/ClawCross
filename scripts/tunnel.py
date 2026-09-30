#!/usr/bin/env python3
"""Run one preconfigured Cloudflare named tunnel for the frontend.

A stable public URL needs a hostname routed to a named tunnel in Cloudflare.
This script never creates a quick tunnel, downloads cloudflared, or manages DNS.
"""

from __future__ import annotations

import atexit
import ipaddress
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.common.runtime_paths import (  # noqa: E402
    ENV_FILE, PID_DIR, WORKSPACE_DIR, cloudflared_path, ensure_runtime_dirs,
)

ensure_runtime_dirs()
PID_FILE = PID_DIR / "tunnel.pid"
CLOUDFLARED_PID_FILE = PID_DIR / "cloudflared.pid"
_child: subprocess.Popen | None = None
_public_url = ""
_claimed = False
_cleaned = False


def _setting(key: str) -> str:
    values = dotenv_values(str(ENV_FILE))
    return str((values[key] if key in values else os.getenv(key)) or "").strip()


def _public_hostname_url() -> str:
    raw = _setting("CLOUDFLARE_PUBLIC_HOSTNAME")
    if not raw:
        raise RuntimeError("Set CLOUDFLARE_PUBLIC_HOSTNAME to a hostname routed to your named tunnel")
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https" or parsed.port is not None or parsed.username
        or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host)
        or host.endswith(".trycloudflare.com")
    ):
        raise RuntimeError("CLOUDFLARE_PUBLIC_HOSTNAME must be one fixed HTTPS hostname, without a path or port")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise RuntimeError("CLOUDFLARE_PUBLIC_HOSTNAME must be a DNS hostname")
    return f"https://{host}"


def _cloudflared_binary() -> str:
    explicit = _setting("CLOUDFLARED_BIN")
    candidates = [explicit] if explicit else [str(cloudflared_path()), shutil.which("cloudflared") or ""]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return str(Path(candidate).resolve())
    raise RuntimeError("cloudflared is not installed; install it manually and set CLOUDFLARED_BIN or add it to PATH")


def _tunnel_command() -> list[str]:
    binary = _cloudflared_binary()
    token_file = _setting("CLOUDFLARE_TUNNEL_TOKEN_FILE")
    config_file = _setting("CLOUDFLARE_TUNNEL_CONFIG")
    tunnel_id = _setting("CLOUDFLARE_TUNNEL_ID")
    if bool(token_file) == bool(config_file):
        raise RuntimeError("Set exactly one of CLOUDFLARE_TUNNEL_TOKEN_FILE or CLOUDFLARE_TUNNEL_CONFIG")
    if token_file:
        path = Path(token_file).expanduser().resolve()
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("CLOUDFLARE_TUNNEL_TOKEN_FILE must name an existing nonempty file")
        return [binary, "tunnel", "run", "--token-file", str(path)]
    path = Path(config_file).expanduser().resolve()
    if not path.is_file() or not tunnel_id:
        raise RuntimeError("A local tunnel needs an existing CLOUDFLARE_TUNNEL_CONFIG and CLOUDFLARE_TUNNEL_ID")
    return [binary, "tunnel", "--config", str(path), "run", tunnel_id]


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
    """Replace the runtime URL atomically; the configured hostname stays intact."""
    mode = stat.S_IMODE(ENV_FILE.stat().st_mode) if ENV_FILE.exists() else 0o600
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True) if ENV_FILE.exists() else []
    replacement = f"PUBLIC_DOMAIN={value}\n"
    updated = [replacement if line.strip().startswith("PUBLIC_DOMAIN=") else line for line in lines]
    if not any(line.strip().startswith("PUBLIC_DOMAIN=") for line in lines):
        if updated and not updated[-1].endswith("\n"):
            updated.append("\n")
        updated.append(replacement)
    fd, temporary = tempfile.mkstemp(prefix=".env.tunnel-", dir=ENV_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.chmod(temporary, mode)
            handle.write("".join(updated))
        os.replace(temporary, ENV_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
            if dotenv_values(str(ENV_FILE)).get("PUBLIC_DOMAIN") == _public_url:
                _write_public_domain("")
        finally:
            _remove_owned_pid(PID_FILE, os.getpid())


def _signal_exit(_signum, _frame) -> None:
    raise SystemExit(0)


def start_tunnels() -> None:
    global _child, _public_url
    _public_url = _public_hostname_url()
    command = _tunnel_command()
    _claim_pid()
    atexit.register(cleanup)
    signal.signal(signal.SIGINT, _signal_exit)
    if os.name != "nt":
        signal.signal(signal.SIGTERM, _signal_exit)
    try:
        _write_public_domain("")
        popen_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        _child = subprocess.Popen(command, cwd=WORKSPACE_DIR, **popen_options)
        CLOUDFLARED_PID_FILE.write_text(str(_child.pid), encoding="utf-8")
        for _ in range(8):
            time.sleep(0.25)
            if _child.poll() is not None:
                raise RuntimeError(f"cloudflared exited during startup (code {_child.returncode})")
        _write_public_domain(_public_url)
        print(f"Named tunnel running at {_public_url}", flush=True)
        code = _child.wait()
        if code:
            raise RuntimeError(f"cloudflared exited with code {code}")
    finally:
        cleanup()


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--check"]:
            _public_hostname_url()
            _tunnel_command()
            print("Named tunnel configuration is ready", flush=True)
        elif not sys.argv[1:]:
            start_tunnels()
        else:
            raise RuntimeError("Usage: tunnel.py [--check]")
    except Exception as exc:
        print(f"Tunnel unavailable: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
