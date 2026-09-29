"""Discover acpx subcommands that launch ACP agents (cached, shared by front + group routing)."""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache

# When `acpx --help` is unavailable, keep routing in sync with common installs.
_FALLBACK: frozenset[str] = frozenset(
    {
        "openclaw",
        "codex",
        "claude",
        "gemini",
        "cursor",
        "copilot",
        "droid",
        "iflow",
        "kilocode",
        "kimi",
        "kiro",
        "opencode",
        "pi",
        "qoder",
        "qwen",
        "trae",
        "aider",
        "claude-code",
        "gemini-cli",
    }
)


@lru_cache(maxsize=1)
def acpx_agent_command_names() -> frozenset[str]:
    acpx_bin = shutil.which("acpx")
    if not acpx_bin:
        return _FALLBACK
    try:
        p = subprocess.run(
            [acpx_bin, "--help"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except Exception:
        return _FALLBACK
    txt = (p.stdout or "") + "\n" + (p.stderr or "")
    tools: list[str] = []
    for line in txt.splitlines():
        m = re.match(
            r"^\s*([a-z0-9_-]+)\s+\[options\].*Use\s+\1\s+agent\s*$",
            line.strip(),
            re.I,
        )
        if not m:
            continue
        name = m.group(1).strip().lower()
        if name and name not in tools:
            tools.append(name)
    return frozenset(tools) if tools else _FALLBACK


def acpx_agent_tags_with_legacy() -> frozenset[str]:
    """Tags allowed for acpx `tool` argument (includes legacy aliases not always in --help)."""
    return acpx_agent_command_names() | frozenset({"aider", "claude-code", "gemini-cli"})
