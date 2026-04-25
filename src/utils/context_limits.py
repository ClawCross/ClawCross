"""
Context window and history budget helpers.

These helpers keep chat-history budgeting configurable while giving modern
long-context models more room than the old fixed 8k/12k defaults.
"""

from __future__ import annotations

import os


DEFAULT_MODEL_CONTEXT_WINDOW = 128_000
MIN_HISTORY_TOKEN_BUDGET = 16_000
MAX_MAIN_HISTORY_TOKEN_BUDGET = 128_000
MAX_SUBAGENT_HISTORY_TOKEN_BUDGET = 64_000


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def infer_model_context_window(model: str | None = None) -> int:
    """Infer a conservative context window from the configured model name."""
    override = _env_int("LLM_CONTEXT_WINDOW")
    if override:
        return override

    name = (model or os.getenv("LLM_MODEL") or "").strip().lower()
    if not name:
        return DEFAULT_MODEL_CONTEXT_WINDOW

    if "gemini" in name:
        if "pro" in name or "3" in name:
            return 2_000_000
        return 1_000_000
    if "minimax" in name or "m2" in name:
        return 1_000_000
    if "claude" in name:
        return 200_000
    if name.startswith(("gpt-5", "gpt-4.1", "o3", "o4")):
        return 128_000
    if "deepseek" in name:
        return 64_000
    if any(marker in name for marker in ("qwen", "glm", "moonshot", "kimi")):
        return 128_000
    if any(marker in name for marker in ("llama", "mistral", "mixtral")):
        return 128_000
    return DEFAULT_MODEL_CONTEXT_WINDOW


def resolve_history_token_budget(*, is_subagent: bool = False, model: str | None = None) -> int:
    """
    Resolve the history budget sent to compaction.

    User override order:
    1. WEBOT_SUBAGENT_CONTEXT_TOKEN_BUDGET for subagents
    2. WEBOT_CONTEXT_TOKEN_BUDGET for all sessions
    3. model-derived budget with sane caps
    """
    if is_subagent:
        override = _env_int("WEBOT_SUBAGENT_CONTEXT_TOKEN_BUDGET")
        if override:
            return override

    override = _env_int("WEBOT_CONTEXT_TOKEN_BUDGET")
    if override:
        return override

    context_window = infer_model_context_window(model)
    cap = MAX_SUBAGENT_HISTORY_TOKEN_BUDGET if is_subagent else MAX_MAIN_HISTORY_TOKEN_BUDGET
    budget = int(context_window * 0.50)
    return min(cap, max(MIN_HISTORY_TOKEN_BUDGET, budget))


def resolve_history_message_limits(*, is_subagent: bool = False, token_budget: int | None = None) -> tuple[int, int]:
    """Return (max_messages, preserve_recent) tuned to the history token budget."""
    budget = token_budget or resolve_history_token_budget(is_subagent=is_subagent)
    if is_subagent:
        if budget >= 64_000:
            return 48, 18
        if budget >= 32_000:
            return 36, 14
        return 24, 8

    if budget >= 96_000:
        return 80, 28
    if budget >= 48_000:
        return 56, 20
    if budget >= 32_000:
        return 44, 16
    return 32, 12
