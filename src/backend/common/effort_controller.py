"""
Effort Controller – Dynamic reasoning depth adjustment.

Features:
- Classifies task complexity (simple/medium/complex/expert)
- Adjusts model parameters based on effort level
- Controls max turns, token budget, and tool constraints per effort level
- Supports manual effort override via session commands

Ported from openclaw-claude-code's effort control mechanism.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EffortLevel(str, Enum):
    """Task complexity / effort level."""
    MINIMAL = "minimal"    # Quick factual answers, simple lookups
    LOW = "low"            # Single-step tasks, minor edits
    MEDIUM = "medium"      # Multi-step tasks, moderate complexity
    HIGH = "high"          # Complex tasks requiring planning
    EXPERT = "expert"      # Expert-level tasks, deep analysis, multi-file changes


@dataclass(frozen=True)
class EffortConfig:
    """Configuration parameters for an effort level."""
    level: EffortLevel
    max_turns: int
    max_context_tokens: int
    max_output_tokens: int
    temperature: float
    enable_planning: bool
    enable_verification: bool
    tool_concurrency: int
    compress_threshold: int  # Messages before triggering compression


# Predefined effort configurations
EFFORT_CONFIGS: dict[EffortLevel, EffortConfig] = {
    EffortLevel.MINIMAL: EffortConfig(
        level=EffortLevel.MINIMAL,
        max_turns=2,
        max_context_tokens=4000,
        max_output_tokens=1024,
        temperature=0.3,
        enable_planning=False,
        enable_verification=False,
        tool_concurrency=4,
        compress_threshold=10,
    ),
    EffortLevel.LOW: EffortConfig(
        level=EffortLevel.LOW,
        max_turns=5,
        max_context_tokens=8000,
        max_output_tokens=2048,
        temperature=0.5,
        enable_planning=False,
        enable_verification=False,
        tool_concurrency=6,
        compress_threshold=16,
    ),
    EffortLevel.MEDIUM: EffortConfig(
        level=EffortLevel.MEDIUM,
        max_turns=15,
        max_context_tokens=16000,
        max_output_tokens=4096,
        temperature=0.7,
        enable_planning=True,
        enable_verification=True,
        tool_concurrency=8,
        compress_threshold=24,
    ),
    EffortLevel.HIGH: EffortConfig(
        level=EffortLevel.HIGH,
        max_turns=30,
        max_context_tokens=32000,
        max_output_tokens=12288,
        temperature=0.7,
        enable_planning=True,
        enable_verification=True,
        tool_concurrency=10,
        compress_threshold=32,
    ),
    EffortLevel.EXPERT: EffortConfig(
        level=EffortLevel.EXPERT,
        max_turns=50,
        max_context_tokens=64000,
        max_output_tokens=16384,
        temperature=0.8,
        enable_planning=True,
        enable_verification=True,
        tool_concurrency=12,
        compress_threshold=48,
    ),
}


# Complexity detection patterns
_COMPLEXITY_SIGNALS: dict[str, tuple[EffortLevel, float]] = {
    # Expert signals
    r"\barchitect": (EffortLevel.EXPERT, 0.8),
    r"\brefactor\s+entire": (EffortLevel.EXPERT, 0.9),
    r"\bmigrat(e|ion)\b": (EffortLevel.EXPERT, 0.7),
    r"\bcomprehensive\b": (EffortLevel.EXPERT, 0.6),
    r"\bfull\s+(rewrite|implementation)": (EffortLevel.EXPERT, 0.8),
    r"\bdesign\s+(system|pattern)": (EffortLevel.EXPERT, 0.7),
    # High signals
    r"\bimplement\b": (EffortLevel.HIGH, 0.6),
    r"\bcreate\s+(?:a\s+)?new\b": (EffortLevel.HIGH, 0.5),
    r"\bdebug\b": (EffortLevel.HIGH, 0.5),
    r"\boptimiz": (EffortLevel.HIGH, 0.6),
    r"\btest\s+suite": (EffortLevel.HIGH, 0.6),
    r"\bmulti[-\s]?file": (EffortLevel.HIGH, 0.7),
    # Medium signals
    r"\bfix\b": (EffortLevel.MEDIUM, 0.5),
    r"\badd\s+(?:a\s+)?feature": (EffortLevel.MEDIUM, 0.6),
    r"\bupdate\b": (EffortLevel.MEDIUM, 0.4),
    r"\bmodify\b": (EffortLevel.MEDIUM, 0.4),
    r"\bchange\b": (EffortLevel.MEDIUM, 0.3),
    # Low signals
    r"\brename\b": (EffortLevel.LOW, 0.6),
    r"\bformat\b": (EffortLevel.LOW, 0.5),
    r"\blint\b": (EffortLevel.LOW, 0.5),
    r"\btypecheck": (EffortLevel.LOW, 0.5),
    # Minimal signals
    r"\bwhat\s+is\b": (EffortLevel.MINIMAL, 0.7),
    r"\bhow\s+to\b": (EffortLevel.MINIMAL, 0.5),
    r"\bexplain\b": (EffortLevel.MINIMAL, 0.6),
    r"\bshow\s+me\b": (EffortLevel.MINIMAL, 0.5),
    r"\blist\b": (EffortLevel.MINIMAL, 0.5),
    r"\bversion\b": (EffortLevel.MINIMAL, 0.8),
}


def estimate_effort(user_input: str) -> EffortLevel:
    """
    Estimate the effort level needed for a user request.

    Uses pattern matching on the input text to determine complexity.
    Returns the highest-scoring effort level.
    """
    if not user_input or not user_input.strip():
        return EffortLevel.MEDIUM  # Default

    text_lower = user_input.lower()
    scores: dict[EffortLevel, float] = {level: 0.0 for level in EffortLevel}

    for pattern, (level, weight) in _COMPLEXITY_SIGNALS.items():
        if re.search(pattern, text_lower):
            scores[level] += weight

    # Length-based bonus
    word_count = len(text_lower.split())
    if word_count > 100:
        scores[EffortLevel.EXPERT] += 0.3
    elif word_count > 50:
        scores[EffortLevel.HIGH] += 0.2
    elif word_count < 10:
        scores[EffortLevel.MINIMAL] += 0.3

    # Get highest scoring level
    best_level = max(scores, key=lambda k: scores[k])
    if scores[best_level] <= 0:
        return EffortLevel.MEDIUM  # Default if no signals

    return best_level


def get_effort_config(level: EffortLevel | str | None = None) -> EffortConfig:
    """Get configuration for an effort level."""
    if level is None:
        return EFFORT_CONFIGS[EffortLevel.MEDIUM]
    if isinstance(level, str):
        try:
            level = EffortLevel(level.lower())
        except ValueError:
            return EFFORT_CONFIGS[EffortLevel.MEDIUM]
    return EFFORT_CONFIGS.get(level, EFFORT_CONFIGS[EffortLevel.MEDIUM])


def resolve_default_chat_max_output_tokens() -> int:
    """Resolve the default max output tokens for primary WeBot chat.

    Priority:
    1. WEBOT_DEFAULT_MAX_OUTPUT_TOKENS env override when it is a positive int
    2. high-effort default output tokens from EFFORT_CONFIGS
    """
    raw = (os.getenv("WEBOT_DEFAULT_MAX_OUTPUT_TOKENS") or "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return EFFORT_CONFIGS[EffortLevel.HIGH].max_output_tokens


# Per-session effort overrides
_session_efforts: dict[str, EffortLevel] = {}


def set_session_effort(user_id: str, session_id: str, level: EffortLevel) -> None:
    """Override effort level for a session."""
    _session_efforts[f"{user_id}#{session_id}"] = level


def get_session_effort(user_id: str, session_id: str) -> EffortLevel | None:
    """Get overridden effort level for a session."""
    return _session_efforts.get(f"{user_id}#{session_id}")


def clear_session_effort(user_id: str, session_id: str) -> None:
    """Clear effort override for a session."""
    _session_efforts.pop(f"{user_id}#{session_id}", None)


def resolve_effort(user_id: str, session_id: str, user_input: str = "") -> EffortConfig:
    """Resolve the effective effort config for a session."""
    override = get_session_effort(user_id, session_id)
    if override is not None:
        return get_effort_config(override)

    if user_input:
        estimated = estimate_effort(user_input)
        return get_effort_config(estimated)

    return get_effort_config(EffortLevel.MEDIUM)
