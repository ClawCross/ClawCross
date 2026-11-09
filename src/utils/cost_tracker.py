"""
Cost Tracker – OpenClaw style real-time cost monitoring.

Features:
- Per-session and per-user cost tracking
- Model-specific pricing (input/output/cache rates)
- Cost limit enforcement with configurable thresholds
- Session cost breakdown by model and tool usage
- Historical cost analytics

Ported from openclaw-claude-code's cost tracking system.
"""

from __future__ import annotations

import json
import time
import utils.scheduler_service
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Default pricing per 1M tokens (in USD)
_MODEL_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-3-opus": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-3-haiku": {"input": 0.25, "output": 1.25, "cache_read": 0.03, "cache_write": 0.3},
    # OpenAI
    "gpt-4o": {"input": 2.5, "output": 10.0, "cache_read": 1.25, "cache_write": 0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.6, "cache_read": 0.075, "cache_write": 0},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0, "cache_read": 5.0, "cache_write": 0},
    "o3": {"input": 10.0, "output": 40.0, "cache_read": 2.5, "cache_write": 0},
    "o3-mini": {"input": 1.1, "output": 4.4, "cache_read": 0.55, "cache_write": 0},
    # DeepSeek
    "deepseek-chat": {"input": 0.14, "output": 0.28, "cache_read": 0.014, "cache_write": 0},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19, "cache_read": 0.055, "cache_write": 0},
    # Google
    "gemini-2.5-pro": {"input": 1.25, "output": 10.0, "cache_read": 0.31, "cache_write": 0},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.6, "cache_read": 0.0375, "cache_write": 0},
    # Default fallback
    "_default": {"input": 1.0, "output": 3.0, "cache_read": 0.1, "cache_write": 0},
}


def _get_model_pricing(model: str) -> dict[str, float]:
    """Get pricing for a model, with fuzzy matching."""
    model_lower = model.lower()
    for key, pricing in _MODEL_PRICING.items():
        if key in model_lower or model_lower.startswith(key.split("-")[0]):
            return pricing
    return _MODEL_PRICING["_default"]


@dataclass
class CostEntry:
    """A single cost entry from one LLM call."""
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: float = field(default_factory=time.time)

    def calculate_cost(self) -> float:
        """Calculate cost based on token counts and model pricing."""
        pricing = _get_model_pricing(self.model)
        cost = (
            (self.input_tokens / 1_000_000) * pricing["input"]
            + (self.output_tokens / 1_000_000) * pricing["output"]
            + (self.cache_read_tokens / 1_000_000) * pricing["cache_read"]
            + (self.cache_write_tokens / 1_000_000) * pricing["cache_write"]
        )
        self.cost_usd = round(cost, 6)
        return self.cost_usd


@dataclass
class SessionCostTracker:
    """Tracks costs for a single session."""
    user_id: str
    session_id: str
    entries: list[CostEntry] = field(default_factory=list)
    cost_limit_usd: float = 10.0  # Default $10 limit per session
    _created_at: float = field(default_factory=time.time)

    @property
    def total_cost(self) -> float:
        return sum(e.cost_usd for e in self.entries)

    @property
    def total_input_tokens(self) -> int:
        return sum(e.input_tokens for e in self.entries)

    @property
    def total_output_tokens(self) -> int:
        return sum(e.output_tokens for e in self.entries)

    @property
    def is_over_limit(self) -> bool:
        return self.total_cost >= self.cost_limit_usd

    @property
    def remaining_budget(self) -> float:
        return max(0.0, self.cost_limit_usd - self.total_cost)

    def record(
        self,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> CostEntry:
        """Record a new cost entry."""
        entry = CostEntry(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )
        entry.calculate_cost()
        self.entries.append(entry)
        return entry

    def get_breakdown(self) -> dict[str, Any]:
        """Get cost breakdown by model."""
        by_model: dict[str, dict[str, Any]] = {}
        for entry in self.entries:
            if entry.model not in by_model:
                by_model[entry.model] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                }
            by_model[entry.model]["calls"] += 1
            by_model[entry.model]["input_tokens"] += entry.input_tokens
            by_model[entry.model]["output_tokens"] += entry.output_tokens
            by_model[entry.model]["cost_usd"] += entry.cost_usd

        return {
            "total_cost_usd": round(self.total_cost, 4),
            "total_calls": len(self.entries),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "cost_limit_usd": self.cost_limit_usd,
            "remaining_budget_usd": round(self.remaining_budget, 4),
            "is_over_limit": self.is_over_limit,
            "by_model": by_model,
        }

    def format_cost_notice(self) -> str:
        """Format a cost notice for system prompt injection."""
        total = self.total_cost
        if total < 0.01:
            return ""
        limit_pct = int((total / self.cost_limit_usd) * 100) if self.cost_limit_usd > 0 else 0
        if limit_pct >= 90:
            return (
                f"⚠️ 会话成本已达 ${total:.4f} ({limit_pct}% of ${self.cost_limit_usd} limit)。"
                "请立即完成当前任务。"
            )
        if limit_pct >= 70:
            return (
                f"💰 会话成本: ${total:.4f} ({limit_pct}% of ${self.cost_limit_usd} limit)。"
                "请注意控制成本。"
            )
        return ""


# Global tracker store
_trackers: dict[str, SessionCostTracker] = {}


def get_cost_tracker(user_id: str, session_id: str) -> SessionCostTracker:
    """Get or create cost tracker for a session."""
    key = f"{user_id}#{session_id}"
    if key not in _trackers:
        _trackers[key] = SessionCostTracker(user_id=user_id, session_id=session_id)
    return _trackers[key]


def set_session_cost_limit(user_id: str, session_id: str, limit_usd: float) -> None:
    """Set cost limit for a session."""
    tracker = get_cost_tracker(user_id, session_id)
    tracker.cost_limit_usd = max(0.01, limit_usd)


def get_user_total_cost(user_id: str) -> float:
    """Get total cost across all sessions for a user."""
    return sum(
        t.total_cost for key, t in _trackers.items()
        if key.startswith(f"{user_id}#")
    )


def update_model_pricing(model: str, pricing: dict[str, float]) -> None:
    """Update pricing for a model at runtime."""
    _MODEL_PRICING[model] = pricing
