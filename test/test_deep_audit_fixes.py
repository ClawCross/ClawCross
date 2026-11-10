"""
Deep audit fix tests — covers all specific gaps identified by source comparison.

Tests:
1. Streaming executor: per-tool timeout, progress callback
2. Token budget: context_percent uses compressed context / compression budget
3. Bash safety: runtime allowlist/blocklist, deep analysis, env injection, heredoc, operator chains
4. Council: abort, inject_message, save_transcript
5. Notification: NotificationLevel is proper Enum
6. Autopilot: 5-phase pipeline state, QA error counting, validator approval
"""

import asyncio
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "backend"))


class TestTokenBudgetContextPercent:
    """Test context_percent follows compressed context usage."""

    def test_context_percent_hits_100_at_compression_budget(self):
        from webot.token_budget import SessionTokenBudget
        budget = SessionTokenBudget(max_context_tokens=200_000)
        budget.update_current_context(used_tokens=64_000, budget_tokens=64_000)
        assert budget.context_percent == 100
        assert budget.context_pressure == 1.0

    def test_context_percent_partial(self):
        from webot.token_budget import SessionTokenBudget
        budget = SessionTokenBudget(max_context_tokens=200_000)
        budget.update_current_context(used_tokens=32_000, budget_tokens=64_000)
        assert budget.context_percent == 50

    def test_context_pressure_warning_before_compression_budget(self):
        from webot.token_budget import SessionTokenBudget
        budget = SessionTokenBudget(max_context_tokens=1000)
        budget.update_current_context(used_tokens=900, budget_tokens=1000)
        assert budget.context_pressure == 0.9
        assert budget.is_warning  # >= 0.8
        assert not budget.is_critical  # < 0.95


class TestBashSafetyRuntime:
    """Test runtime allowlist/blocklist and deep analysis."""

    def test_add_to_allowlist(self):
        from webot.bash_safety import add_to_allowlist, check_runtime_lists, remove_from_allowlist
        add_to_allowlist("docker compose")
        result = check_runtime_lists("docker compose up -d")
        assert result is not None
        assert result.risk_level.value == "safe"
        remove_from_allowlist("docker compose")

    def test_add_to_blocklist(self):
        from webot.bash_safety import add_to_blocklist, check_runtime_lists, remove_from_blocklist
        add_to_blocklist(r"npm\s+run\s+deploy")
        result = check_runtime_lists("npm run deploy --prod")
        assert result is not None
        assert result.risk_level.value == "high"
        remove_from_blocklist(r"npm\s+run\s+deploy")

    def test_detect_operator_chains(self):
        from webot.bash_safety import detect_operator_chains
        warnings = detect_operator_chains("ls && rm -rf / && echo done")
        assert any("dangerous" in w for w in warnings)

    def test_detect_env_injection(self):
        from webot.bash_safety import detect_env_injection
        warnings = detect_env_injection("export LD_PRELOAD=/tmp/evil.so")
        assert len(warnings) > 0

    def test_detect_heredoc(self):
        from webot.bash_safety import detect_heredoc
        warnings = detect_heredoc("cat << EOF\nmalicious content\nEOF")
        assert len(warnings) > 0

    def test_detect_subshell_nesting(self):
        from webot.bash_safety import detect_subshell_nesting
        warnings = detect_subshell_nesting("$($($(echo nested)))")
        assert len(warnings) > 0
        assert "depth" in warnings[0]

    def test_deep_analyze(self):
        from webot.bash_safety import deep_analyze
        result = deep_analyze("ls && echo safe && rm -rf /tmp/test ; cat file")
        assert len(result.reasons) > 0

    def test_get_lists(self):
        from webot.bash_safety import get_allowlist, get_blocklist
        assert isinstance(get_allowlist(), frozenset)
        assert isinstance(get_blocklist(), frozenset)
