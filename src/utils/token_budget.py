"""
Token Budget Manager – Claude Code style token tracking & continuation.

Features:
- Per-turn and per-session token accounting
- Marginal utility detection (diminishing returns → auto-stop)
- Auto-continuation with budget awareness
- Context window pressure monitoring

Ported from Claude Code's token budget tracking patterns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from utils.context_limits import infer_model_context_window


@dataclass
class TurnTokenUsage:
    """Token counts for a single LLM turn."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    timestamp: float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def effective_input(self) -> int:
        """Input tokens minus cache hits."""
        return max(0, self.input_tokens - self.cache_read_tokens)


@dataclass
class SessionTokenBudget:
    """
    Tracks token usage across a session with budget enforcement.

    Based on Claude Code's approach:
    - Set a max context window budget
    - Track marginal utility (info gained per token spent)
    - Auto-suggest continuation or stopping
    """

    max_context_tokens: int = field(default_factory=infer_model_context_window)
    max_output_tokens_per_turn: int = 16_000
    warning_threshold: float = 0.8  # Warn at 80% usage
    critical_threshold: float = 0.95  # Critical at 95%

    turns: list[TurnTokenUsage] = field(default_factory=list)
    current_context_tokens: int = 0
    current_context_budget: int = 0
    _session_start: float = field(default_factory=time.time)

    @property
    def total_input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.turns)

    @property
    def total_output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(t.cache_read_tokens for t in self.turns)

    @property
    def total_cache_creation_tokens(self) -> int:
        return sum(t.cache_creation_tokens for t in self.turns)

    @property
    def context_pressure(self) -> float:
        """0.0-1.0 ratio of current compressed context usage."""
        budget = self.current_context_budget or self.max_context_tokens
        if budget <= 0:
            return 0.0
        return min(1.0, self.current_context_tokens / budget)

    @property
    def context_percent(self) -> int:
        """0-100 integer context usage percentage (openclaw-compatible)."""
        if self.current_context_tokens <= 0:
            return 0
        return max(1, min(100, round(self.context_pressure * 100)))

    @property
    def is_warning(self) -> bool:
        return self.context_pressure >= self.warning_threshold

    @property
    def is_critical(self) -> bool:
        return self.context_pressure >= self.critical_threshold

    def record_turn(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> TurnTokenUsage:
        """Record token usage for a turn."""
        turn = TurnTokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
        )
        self.turns.append(turn)
        return turn

    def update_current_context(self, used_tokens: int, budget_tokens: int) -> None:
        """Record current prompt context size after ClawCross compaction/compression."""
        self.current_context_tokens = max(0, int(used_tokens or 0))
        self.current_context_budget = max(0, int(budget_tokens or 0))

    def marginal_utility(self, window: int = 3) -> float:
        """
        Estimate the marginal utility of recent turns.

        Returns a 0.0-1.0 score:
        - 1.0 = each turn produces lots of new output per input token
        - 0.0 = turns are mostly re-processing the same context with little new output

        Claude Code uses this to detect when the agent is spinning.
        """
        if len(self.turns) < 2:
            return 1.0

        recent = self.turns[-window:] if len(self.turns) >= window else self.turns
        if not recent:
            return 1.0

        total_effective_input = sum(t.effective_input for t in recent)
        total_output = sum(t.output_tokens for t in recent)

        if total_effective_input <= 0:
            return 1.0 if total_output > 0 else 0.0

        # Ratio of new output per effective (non-cached) input
        ratio = total_output / total_effective_input
        # Normalize to 0-1 range (0.5 ratio = 1.0 utility)
        return min(1.0, ratio / 0.5)

    def should_auto_continue(self, min_utility: float = 0.15) -> bool:
        """
        Whether the agent should continue based on token economics.

        Returns False if:
        - Context is at critical pressure
        - Marginal utility has dropped below threshold
        """
        if self.is_critical:
            return False
        if len(self.turns) >= 3 and self.marginal_utility() < min_utility:
            return False
        return True

    def remaining_budget(self) -> int:
        """Tokens remaining before hitting max context."""
        budget = self.current_context_budget or self.max_context_tokens
        return max(0, budget - self.current_context_tokens)

    def get_status(self) -> dict[str, Any]:
        """Get a serializable status report."""
        return {
            "total_turns": len(self.turns),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cache_read": self.total_cache_read_tokens,
            "total_cache_creation": self.total_cache_creation_tokens,
            "current_context_tokens": self.current_context_tokens,
            "current_context_budget": self.current_context_budget or self.max_context_tokens,
            "context_pressure": round(self.context_pressure, 3),
            "marginal_utility": round(self.marginal_utility(), 3),
            "remaining_budget": self.remaining_budget(),
            "is_warning": self.is_warning,
            "is_critical": self.is_critical,
            "should_continue": self.should_auto_continue(),
        }

    def format_budget_notice(self) -> str:
        """Format a human-readable budget status for injection into prompts."""
        pressure_pct = int(self.context_pressure * 100)
        if self.is_critical:
            return (
                f"⚠️ 上下文容量严重不足 ({pressure_pct}% 已用)。"
                f"剩余 ~{self.remaining_budget():,} tokens。"
                "请立即总结进展并结束当前任务。"
            )
        if self.is_warning:
            return (
                f"⚡ 上下文容量偏高 ({pressure_pct}% 已用)。"
                f"剩余 ~{self.remaining_budget():,} tokens。"
                "建议精简后续操作，优先完成核心任务。"
            )
        return ""


# Per-session budget store
_session_budgets: dict[str, SessionTokenBudget] = {}


def get_session_budget(user_id: str, session_id: str) -> SessionTokenBudget:
    """Get or create token budget tracker for a session."""
    key = f"{user_id}#{session_id}"
    if key not in _session_budgets:
        _session_budgets[key] = SessionTokenBudget()
    return _session_budgets[key]


def reset_session_budget(user_id: str, session_id: str) -> None:
    """Reset token budget for a session."""
    key = f"{user_id}#{session_id}"
    _session_budgets.pop(key, None)
