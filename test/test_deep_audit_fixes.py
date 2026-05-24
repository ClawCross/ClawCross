"""
Deep audit fix tests — covers all specific gaps identified by source comparison.

Tests:
1. Streaming executor: per-tool timeout, progress callback
2. Token budget: context_percent (in+out)/200k, context_pressure fix
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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))


class TestStreamingExecutorTimeout:
    """Test per-tool timeout and progress callback."""

    @pytest.mark.asyncio
    async def test_timeout_fires(self):
        from core.streaming_tool_executor import StreamingToolExecutor

        async def slow_executor(tc):
            await asyncio.sleep(10)  # Way too slow
            return "done"

        executor = StreamingToolExecutor(per_tool_timeout=0.05)
        calls = [{"name": "run_command", "id": "tc1", "args": {}}]

        results = []
        async for result in executor.execute_tool_calls(calls, slow_executor):
            results.append(result)

        assert len(results) == 1
        assert not results[0].success
        assert "Error" in results[0].content

    @pytest.mark.asyncio
    async def test_progress_callback(self):
        from core.streaming_tool_executor import StreamingToolExecutor

        progress_calls = []

        def on_progress(name, status, pct):
            progress_calls.append((name, status, pct))

        async def fast_executor(tc):
            return "ok"

        executor = StreamingToolExecutor(on_progress=on_progress)
        calls = [{"name": "read_file", "id": "tc1", "args": {}}]

        async for _ in executor.execute_tool_calls(calls, fast_executor):
            pass

        assert len(progress_calls) == 1
        assert progress_calls[0] == ("read_file", "completed", 1.0)


class TestTokenBudgetContextPercent:
    """Test context_percent uses (in+out)/200k per openclaw."""

    def test_context_percent_includes_output(self):
        from utils.token_budget import SessionTokenBudget
        budget = SessionTokenBudget(max_context_tokens=200_000)
        budget.record_turn(input_tokens=100_000, output_tokens=100_000)
        # (100k + 100k) / 200k = 100%
        assert budget.context_percent == 100
        assert budget.context_pressure == 1.0

    def test_context_percent_partial(self):
        from utils.token_budget import SessionTokenBudget
        budget = SessionTokenBudget(max_context_tokens=200_000)
        budget.record_turn(input_tokens=50_000, output_tokens=50_000)
        # (50k + 50k) / 200k = 50%
        assert budget.context_percent == 50

    def test_context_pressure_with_output(self):
        from utils.token_budget import SessionTokenBudget
        budget = SessionTokenBudget(max_context_tokens=1000)
        budget.record_turn(input_tokens=400, output_tokens=500)
        # (400 + 500) / 1000 = 0.9
        assert budget.context_pressure == 0.9
        assert budget.is_warning  # >= 0.8
        assert not budget.is_critical  # < 0.95


class TestBashSafetyRuntime:
    """Test runtime allowlist/blocklist and deep analysis."""

    def test_add_to_allowlist(self):
        from utils.bash_safety import add_to_allowlist, check_runtime_lists, remove_from_allowlist
        add_to_allowlist("docker compose")
        result = check_runtime_lists("docker compose up -d")
        assert result is not None
        assert result.risk_level.value == "safe"
        remove_from_allowlist("docker compose")

    def test_add_to_blocklist(self):
        from utils.bash_safety import add_to_blocklist, check_runtime_lists, remove_from_blocklist
        add_to_blocklist(r"npm\s+run\s+deploy")
        result = check_runtime_lists("npm run deploy --prod")
        assert result is not None
        assert result.risk_level.value == "high"
        remove_from_blocklist(r"npm\s+run\s+deploy")

    def test_detect_operator_chains(self):
        from utils.bash_safety import detect_operator_chains
        warnings = detect_operator_chains("ls && rm -rf / && echo done")
        assert any("dangerous" in w for w in warnings)

    def test_detect_env_injection(self):
        from utils.bash_safety import detect_env_injection
        warnings = detect_env_injection("export LD_PRELOAD=/tmp/evil.so")
        assert len(warnings) > 0

    def test_detect_heredoc(self):
        from utils.bash_safety import detect_heredoc
        warnings = detect_heredoc("cat << EOF\nmalicious content\nEOF")
        assert len(warnings) > 0

    def test_detect_subshell_nesting(self):
        from utils.bash_safety import detect_subshell_nesting
        warnings = detect_subshell_nesting("$($($(echo nested)))")
        assert len(warnings) > 0
        assert "depth" in warnings[0]

    def test_deep_analyze(self):
        from utils.bash_safety import deep_analyze
        result = deep_analyze("ls && echo safe && rm -rf /tmp/test ; cat file")
        assert len(result.reasons) > 0

    def test_get_lists(self):
        from utils.bash_safety import get_allowlist, get_blocklist
        assert isinstance(get_allowlist(), frozenset)
        assert isinstance(get_blocklist(), frozenset)


class TestCouncilAbortInjectTranscript:
    """Test Council abort, inject_message, and save_transcript."""

    def test_abort_session(self):
        from core.agent_orchestrator import (
            create_council_full_session, abort_council_session,
            CouncilAgentPersona,
        )
        agents = [CouncilAgentPersona(name="A", emoji="🏗️")]
        session = create_council_full_session(task="Test abort", agents=agents)
        assert session.status == "running"
        result = abort_council_session(session.session_id)
        assert result is True
        assert session.status == "error"

    def test_abort_nonexistent(self):
        from core.agent_orchestrator import abort_council_session
        assert abort_council_session("nonexistent") is False

    def test_inject_message(self):
        from core.agent_orchestrator import (
            create_council_full_session, inject_council_message,
            consume_council_injection, CouncilAgentPersona,
        )
        agents = [CouncilAgentPersona(name="A", emoji="🏗️")]
        session = create_council_full_session(task="Test inject", agents=agents)
        result = inject_council_message(session.session_id, "Please also check security")
        assert result is True
        injection = consume_council_injection(session.session_id)
        assert "security" in injection
        # Second consume should be empty
        assert consume_council_injection(session.session_id) == ""

    def test_save_transcript(self):
        from core.agent_orchestrator import (
            create_council_full_session, record_council_agent_response,
            evaluate_council_round, save_council_transcript,
            CouncilAgentPersona,
        )
        agents = [CouncilAgentPersona(name="A", emoji="🏗️")]
        session = create_council_full_session(task="Test transcript", agents=agents)
        record_council_agent_response(session.session_id, agent_name="A", round_num=1, content="Done [CONSENSUS: YES]")
        evaluate_council_round(session.session_id, 1)

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = save_council_transcript(session, log_dir=tmpdir)
            assert os.path.exists(filepath)
            content = open(filepath, "r").read()
            assert "Council Transcript" in content
            assert "Test transcript" in content
            assert "Done" in content


class TestNotificationLevelEnum:
    """Test NotificationLevel is a proper Enum."""

    def test_is_enum(self):
        from services.notification_system import NotificationLevel
        from enum import Enum
        assert issubclass(NotificationLevel, Enum)

    def test_values(self):
        from services.notification_system import NotificationLevel
        assert NotificationLevel.INFO == "info"
        assert NotificationLevel.ERROR == "error"

    def test_iterable(self):
        from services.notification_system import NotificationLevel
        levels = list(NotificationLevel)
        assert len(levels) == 4


class TestAutopilotPipeline:
    """Test Autopilot 5-phase pipeline state management."""

    def test_start_autopilot(self):
        from core.workflow_engines import start_autopilot, get_autopilot_state
        state = start_autopilot(user_id="u1", session_id="s1", task="Build REST API")
        assert state.active
        assert state.current_phase == "pre_context"
        assert state.task == "Build REST API"

        retrieved = get_autopilot_state("u1", "s1")
        assert retrieved is not None
        assert retrieved.task == "Build REST API"

    def test_phase_advancement(self):
        from core.workflow_engines import start_autopilot, AUTOPILOT_PHASES
        state = start_autopilot(user_id="u1", session_id="phase_test", task="Test")
        state.advance_phase("expansion")
        assert state.current_phase == "expansion"
        state.advance_phase("planning")
        assert state.current_phase == "planning"
        state.advance_phase("complete")
        assert not state.active
        assert state.completed_at != ""

    def test_qa_error_counting(self):
        from core.workflow_engines import start_autopilot
        state = start_autopilot(user_id="u1", session_id="qa_test", task="Test")
        state.advance_phase("qa")

        # First 2 occurrences of same error: no stop
        assert state.record_qa_error("TypeError: undefined") is False
        assert state.record_qa_error("TypeError: undefined") is False
        # Third occurrence: should stop
        assert state.record_qa_error("TypeError: undefined") is True
        assert state.should_stop_qa()

    def test_qa_cycle_limit(self):
        from core.workflow_engines import start_autopilot, AutopilotConfig
        config = AutopilotConfig(max_qa_cycles=3)
        state = start_autopilot(user_id="u1", session_id="qa_limit", task="Test", config=config)
        state.qa_cycle = 3
        assert state.should_stop_qa()

    def test_validator_approval(self):
        from core.workflow_engines import start_autopilot
        state = start_autopilot(user_id="u1", session_id="val_test", task="Test")
        assert not state.all_validators_approved()

        state.validation_results["architect"] = "approved"
        state.validation_results["security"] = "approved"
        assert not state.all_validators_approved()  # Missing code_reviewer

        state.validation_results["code_reviewer"] = "approved"
        assert state.all_validators_approved()

    def test_state_dict(self):
        from core.workflow_engines import start_autopilot
        state = start_autopilot(user_id="u1", session_id="dict_test", task="Build it")
        d = state.to_state_dict()
        assert d["mode"] == "autopilot"
        assert d["active"] is True
        assert d["current_phase"] == "pre_context"

    def test_autopilot_phases_defined(self):
        from core.workflow_engines import AUTOPILOT_PHASES
        assert "pre_context" in AUTOPILOT_PHASES
        assert "expansion" in AUTOPILOT_PHASES
        assert "qa" in AUTOPILOT_PHASES
        assert "validation" in AUTOPILOT_PHASES
        assert "cleanup" in AUTOPILOT_PHASES
        assert "complete" in AUTOPILOT_PHASES
