"""
SOUL.md Personality System — per-user agent personality customization.

Ported from Hermes Agent's SOUL.md concept:
- Optional SOUL.md file per user defining personality
- Loaded fresh each message (no restart needed)
- Delete or leave empty for default personality
- Security scanning for injection attempts
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from webot.memory_guard import scan_memory_content

PROJECT_ROOT = Path(__file__).resolve().parents[2]
from utils.runtime_paths import USER_FILES_DIR

# Default personality (used when no SOUL.md exists)
DEFAULT_PERSONALITY = ""

# Example SOUL.md content for new users
SOUL_TEMPLATE = """# SOUL.md — Agent Personality

Define your agent's personality here. This file is loaded fresh with each message.
Delete everything and write your own, or leave empty for the default personality.

Examples:
- "You are a warm, playful assistant who uses kaomoji occasionally."
- "You are a concise technical expert. No fluff, just facts."
- "You speak like a friendly coworker who happens to know everything."
- "You are a senior engineer who values simplicity and pragmatism."

Write your personality below this line:
---

"""


def _soul_path(user_id: str) -> Path:
    """Per-user SOUL.md path."""
    user_dir = USER_FILES_DIR / (user_id or "anonymous")
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "SOUL.md"


def get_soul(user_id: str) -> str:
    """Load SOUL.md for a user. Returns empty string if not found or empty.

    The content is security-scanned before returning.
    """
    path = _soul_path(user_id)
    if not path.is_file():
        return DEFAULT_PERSONALITY

    content = path.read_text(encoding="utf-8", errors="replace").strip()
    if not content:
        return DEFAULT_PERSONALITY

    # Extract content after the --- separator (if template format)
    if "---" in content:
        parts = content.split("---")
        # Use content after the last --- separator
        if len(parts) > 1:
            personality = parts[-1].strip()
            if personality:
                content = personality

    # Security scan
    scan = scan_memory_content(content)
    if not scan.safe:
        return f"[SOUL.md blocked: {'; '.join(scan.violations)}]"

    return content


def set_soul(user_id: str, content: str) -> dict[str, Any]:
    """Set SOUL.md content for a user.

    Args:
        user_id: User identifier
        content: Personality text to set

    Returns:
        Dict with success status
    """
    # Security scan
    scan = scan_memory_content(content)
    if not scan.safe:
        return {
            "success": False,
            "error": f"Content blocked by security scan: {'; '.join(scan.violations)}",
        }

    path = _soul_path(user_id)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return {"success": True, "message": "SOUL.md updated", "path": str(path)}


def reset_soul(user_id: str) -> dict[str, Any]:
    """Reset SOUL.md to default template."""
    path = _soul_path(user_id)
    path.write_text(SOUL_TEMPLATE, encoding="utf-8")
    return {"success": True, "message": "SOUL.md reset to template", "path": str(path)}


def delete_soul(user_id: str) -> dict[str, Any]:
    """Delete SOUL.md (revert to default personality)."""
    path = _soul_path(user_id)
    if path.is_file():
        path.unlink()
    return {"success": True, "message": "SOUL.md deleted, using default personality"}


def build_soul_prompt(user_id: str) -> str:
    """Build personality prompt block for system prompt injection.

    Returns:
        Formatted personality block, or empty string if no custom personality.
    """
    soul = get_soul(user_id)
    if not soul or soul == DEFAULT_PERSONALITY:
        return ""

    return f"\n【Personality (SOUL.md)】\n{soul}\n"
