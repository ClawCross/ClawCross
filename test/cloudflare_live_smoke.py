"""
Opt-in live smoke test for Cloudflare quick tunnels via scripts/tunnel.py.

The test starts a tiny local HTTP server, launches scripts/tunnel.py, waits for
the trycloudflare URL, fetches that public URL, verifies the response body, and
then restores config/.env.
"""

from __future__ import annotations

import argparse
import http.server
import os
import re
import signal
import socketserver
import subprocess
import sys
import threading
import time
import utils.scheduler_service
from collections import deque
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / "config" / ".env"
TUNNEL_SCRIPT = PROJECT_ROOT / "scripts" / "tunnel.py"
URL_PATTERN = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a real Cloudflare quick tunnel smoke test.")
    parser.add_argument("--port", type=int, default=int(os.getenv("CLOUDFLARE_LIVE_PORT", "51239")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("CLOUDFLARE_LIVE_TIMEOUT", "180")))
    parser.add_argument("--sentinel", default=os.getenv("CLOUDFLARE_LIVE_SENTINEL", "CLAWCROSS_CLOUDFLARE_SMOKE_OK"))
    return parser.parse_args()


def _read_env_text() -> str | None:
    if not ENV_PATH.exists():
        return None
    return ENV_PATH.read_text(encoding="utf-8")


def _write_env_key(text: str | None, key: str, value: str) -> str:
    lines = [] if text is None else text.splitlines(keepends=True)
    updated: list[str] = []
    found = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
            updated.append(f"{key}={value}\n")
            found = True
        else:
            updated.append(line)
    if not found:
        if updated and not updated[-1].endswith("\n"):
            updated.append("\n")
        updated.append(f"{key}={value}\n")
    return "".join(updated)


def _get_public_domain() -> str:
    if not ENV_PATH.exists():
        return ""
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("PUBLIC_DOMAIN="):
            return line.split("=", 1)[1].strip()
    return ""


def main() -> int:
    args = parse_args()
    original_env = _read_env_text()
    PORT = args.port
    sentinel = args.sentinel

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = sentinel.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A003
            return

    server = socketserver.TCPServer(("127.0.0.1", PORT), Handler)
    server.allow_reuse_address = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    patched_env = _write_env_key(original_env, "PORT_FRONTEND", str(PORT))
    patched_env = _write_env_key(patched_env, "PUBLIC_DOMAIN", "")
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text(patched_env, encoding="utf-8")

    proc = subprocess.Popen(
        [sys.executable, str(TUNNEL_SCRIPT)],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    logs: deque[str] = deque(maxlen=200)

    def _pump_output() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            logs.append(line.rstrip())

    reader = threading.Thread(target=_pump_output, daemon=True)
    reader.start()

    public_url = ""
    try:
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("tunnel.py exited early.\n" + "\n".join(logs))
            for line in list(logs):
                match = URL_PATTERN.search(line)
                if match:
                    public_url = match.group(0)
                    break
            if public_url:
                break
            public_url = _get_public_domain()
            if public_url:
                break
            time.sleep(1)

        if not public_url:
            raise RuntimeError("Cloudflare public URL was not generated in time.\n" + "\n".join(logs))

        response = requests.get(public_url, timeout=30)
        response.raise_for_status()
        print(f"public_url={public_url}")
        print(f"status={response.status_code}")
        if sentinel not in response.text:
            raise RuntimeError("Sentinel text was not returned from the public tunnel URL.")
        return 0
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        server.shutdown()
        server.server_close()
        if original_env is None:
            if ENV_PATH.exists():
                ENV_PATH.unlink()
        else:
            ENV_PATH.write_text(original_env, encoding="utf-8")
