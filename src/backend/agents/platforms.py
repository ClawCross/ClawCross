"""Installed ACP platform profiles (cached, shared by front + group routing)."""

from __future__ import annotations

from functools import lru_cache

# Public platform IDs retained for stored Agent compatibility.
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
        "codebuddy",
        "zcode",
        "acp",
        "deepseek-harness",
    }
)


@lru_cache(maxsize=1)
def acpx_agent_command_names() -> frozenset[str]:
    from external.acp_profiles import LAUNCHERS, PACKAGES
    return _FALLBACK | frozenset(LAUNCHERS) | frozenset(PACKAGES)


def acpx_agent_tags_with_legacy() -> frozenset[str]:
    """Tags allowed for acpx `tool` argument (includes legacy aliases not always in --help)."""
    return acpx_agent_command_names() | frozenset({"aider", "claude-code", "gemini-cli"})
