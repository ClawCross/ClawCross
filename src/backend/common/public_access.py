"""Shared public URL discovery; Quick Tunnels never replace a configured domain."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit

from dotenv import dotenv_values

from . import runtime_paths


def normalize_public_domain(value: str | None) -> str:
    domain = (value or "").strip().rstrip("/")
    if domain in {"", "wait to set"}:
        return ""
    return domain if domain.lower().startswith(("http://", "https://")) else "https://" + domain


def is_quick_tunnel_domain(value: str | None) -> bool:
    hostname = urlsplit(normalize_public_domain(value)).hostname or ""
    return hostname.lower().endswith(".trycloudflare.com")


def read_public_domain(env_file: Path | None = None, *, tunnel_running: bool | None = None) -> str:
    env_file = env_file if env_file is not None else runtime_paths.ENV_FILE
    values = dotenv_values(str(env_file))
    domain = normalize_public_domain(values.get("PUBLIC_DOMAIN", os.getenv("PUBLIC_DOMAIN")))
    if tunnel_running is False and is_quick_tunnel_domain(domain):
        return ""
    return domain


def write_tunnel_domain(value: str, env_file: Path | None = None, *, expected: str | None = None) -> bool:
    """Atomically update only a Quick Tunnel URL, preserving user configured URLs."""
    env_file = env_file if env_file is not None else runtime_paths.ENV_FILE
    domain = normalize_public_domain(value)
    if domain and not is_quick_tunnel_domain(domain):
        raise ValueError("A Quick Tunnel URL must end in .trycloudflare.com")
    current = normalize_public_domain(dotenv_values(str(env_file)).get("PUBLIC_DOMAIN"))
    if current and not is_quick_tunnel_domain(current):
        return False
    if expected is not None and current != normalize_public_domain(expected):
        return False
    if not env_file.exists() and not domain:
        return False
    lines = env_file.read_text(encoding="utf-8").splitlines(keepends=True) if env_file.exists() else []
    pattern = re.compile(r"^\s*(?:export\s+)?PUBLIC_DOMAIN\s*=")
    replacement = f"PUBLIC_DOMAIN={domain}\n"
    updated = [replacement if pattern.match(line) else line for line in lines]
    if not any(pattern.match(line) for line in lines):
        if updated and not updated[-1].endswith("\n"):
            updated.append("\n")
        updated.append(replacement)
    permissions = stat.S_IMODE(env_file.stat().st_mode) if env_file.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=".env.tunnel-", dir=env_file.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.chmod(temporary, permissions)
            handle.write("".join(updated))
        os.replace(temporary, env_file)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if not domain and is_quick_tunnel_domain(os.getenv("PUBLIC_DOMAIN")):
        os.environ.pop("PUBLIC_DOMAIN", None)
    return True
