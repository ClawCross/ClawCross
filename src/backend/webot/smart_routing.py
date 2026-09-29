"""
Smart Model Routing — route queries by complexity to appropriate models.

Ported from Hermes Agent's smart_model_routing:
- Analyze message complexity via heuristics
- Route simple queries to cheaper/faster models
- Route complex queries to primary model
- Configurable via environment variables
"""

from __future__ import annotations

import os
import re
from typing import Any


# Keywords indicating complex queries that need the primary model
_COMPLEXITY_KEYWORDS = frozenset([
    "debug", "test", "deploy", "docker", "kubernetes", "k8s",
    "error", "exception", "traceback", "stacktrace",
    "analyze", "architect", "design", "implement", "refactor",
    "migrate", "upgrade", "security", "vulnerability",
    "performance", "optimize", "benchmark", "profile",
    "database", "schema", "migration", "index",
    "api", "endpoint", "middleware", "authentication",
    "ci/cd", "pipeline", "workflow", "terraform",
    "review", "audit", "compliance",
    "fix", "bug", "issue", "regression", "hotfix",
    "build", "compile", "link", "bundle",
    "config", "configure", "setup", "install",
])

# Default routing configuration
_DEFAULT_MAX_SIMPLE_CHARS = 160
_DEFAULT_MAX_SIMPLE_WORDS = 28


def choose_cheap_model_route(
    user_message: str,
    *,
    routing_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Analyze a user message and decide if it can be routed to a cheap model.

    Args:
        user_message: The user's input message
        routing_config: Optional config with cheap_model settings

    Returns:
        Dict with {model, provider, routing_reason} if cheap route chosen,
        None if the primary model should be used.
    """
    config = routing_config or _load_routing_config()
    if not config.get("enabled", False):
        return None

    cheap_model = config.get("cheap_model", {})
    if not cheap_model.get("model"):
        return None

    max_chars = config.get("max_simple_chars", _DEFAULT_MAX_SIMPLE_CHARS)
    max_words = config.get("max_simple_words", _DEFAULT_MAX_SIMPLE_WORDS)

    # Check complexity heuristics — if ANY match, use primary model
    text = user_message.strip()

    # 1. Message length
    if len(text) > max_chars:
        return None

    # 2. Word count
    words = text.split()
    if len(words) > max_words:
        return None

    # 3. Multiline (complex structure)
    if "\n\n" in text:
        return None

    # 4. Code fences
    if "```" in text or "`" in text:
        return None

    # 5. URLs
    if re.search(r"https?://", text):
        return None

    # 6. Complexity keywords
    text_lower = text.lower()
    for keyword in _COMPLEXITY_KEYWORDS:
        if keyword in text_lower:
            return None

    # 7. Question complexity (multiple questions, lists)
    if text.count("?") > 1:
        return None
    if re.search(r"^\d+\.", text, re.MULTILINE):
        return None

    # All heuristics passed — route to cheap model
    return {
        "model": cheap_model["model"],
        "provider": cheap_model.get("provider", ""),
        "api_key": _resolve_api_key(cheap_model),
        "base_url": cheap_model.get("base_url", ""),
        "routing_reason": "simple_turn",
    }


def resolve_turn_route(
    user_message: str,
    *,
    routing_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Full routing resolution: try cheap route, fall back to None (primary).

    Returns:
        Route dict if cheap model should be used, None for primary model.
    """
    return choose_cheap_model_route(user_message, routing_config=routing_config)


def get_routing_config() -> dict[str, Any]:
    """Get current routing configuration."""
    return _load_routing_config()


def set_routing_config(config: dict[str, Any]) -> dict[str, Any]:
    """Update routing configuration (runtime only, not persisted)."""
    global _runtime_config
    _runtime_config = config
    return config


# ── Internal helpers ────────────────────────────────────────────────

_runtime_config: dict[str, Any] | None = None


def _load_routing_config() -> dict[str, Any]:
    """Load routing configuration from env vars or runtime config."""
    if _runtime_config is not None:
        return _runtime_config

    enabled = os.getenv("SMART_ROUTING_ENABLED", "").strip().lower() in ("1", "true", "yes")
    cheap_model = os.getenv("SMART_ROUTING_CHEAP_MODEL", "").strip()
    cheap_provider = os.getenv("SMART_ROUTING_CHEAP_PROVIDER", "").strip()
    cheap_base_url = os.getenv("SMART_ROUTING_CHEAP_BASE_URL", "").strip()
    cheap_api_key_env = os.getenv("SMART_ROUTING_CHEAP_API_KEY_ENV", "").strip()

    return {
        "enabled": enabled,
        "cheap_model": {
            "model": cheap_model,
            "provider": cheap_provider,
            "base_url": cheap_base_url,
            "api_key_env": cheap_api_key_env,
        },
        "max_simple_chars": _DEFAULT_MAX_SIMPLE_CHARS,
        "max_simple_words": _DEFAULT_MAX_SIMPLE_WORDS,
    }


def _resolve_api_key(cheap_model: dict[str, Any]) -> str:
    """Resolve API key for cheap model from env var reference."""
    key_env = cheap_model.get("api_key_env", "")
    if key_env:
        return os.getenv(key_env, "")
    return cheap_model.get("api_key", "")
