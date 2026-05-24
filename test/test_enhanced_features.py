"""
Enhanced test suite covering all features aligned with source implementations.

Tests the deep enhancements ported from:
- openclaw-claude-code: consensus parsing, council two-phase protocol
- oh-my-codex: Ralph 7-phase state, deep interview ambiguity scoring, HUD presets
"""

import asyncio
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))


# ============================================================================
# Consensus Parsing (from openclaw consensus.ts)
# ============================================================================

class TestConsensus:
    """Test consensus vote parsing ported from openclaw."""

    def test_strict_format_yes(self):
        from core.consensus import parse_consensus
        assert parse_consensus("Some text [CONSENSUS: YES] end") is True

    def test_strict_format_no(self):
        from core.consensus import parse_consensus
        assert parse_consensus("Some text [CONSENSUS: NO] end") is False

    def test_chinese_colon(self):
        from core.consensus import parse_consensus
        assert parse_consensus("[CONSENSUS：YES]") is True
        assert parse_consensus("[CONSENSUS：NO]") is False

    def test_last_match_wins(self):
        from core.consensus import parse_consensus
        # Two tags — the LAST one wins (ported from openclaw)
        assert parse_consensus("[CONSENSUS: NO] ... [CONSENSUS: YES]") is True
        assert parse_consensus("[CONSENSUS: YES] ... [CONSENSUS: NO]") is False

    def test_variant_patterns(self):
        from core.consensus import parse_consensus
        assert parse_consensus("consensus: yes") is True
        assert parse_consensus("CONSENSUS=YES") is True
        assert parse_consensus("共识投票: YES") is True
        assert parse_consensus("**consensus**: no") is False

    def test_tail_fallback_positive(self):
        from core.consensus import parse_consensus
        text = "Lots of text\n" * 20 + "达成共识"
        assert parse_consensus(text) is True

    def test_tail_fallback_negative(self):
        from core.consensus import parse_consensus
        text = "Lots of text\n" * 20 + "未达成共识"
        assert parse_consensus(text) is False

    def test_default_false(self):
        from core.consensus import parse_consensus
        assert parse_consensus("No consensus tags here at all") is False

    def test_strip_tags(self):
        from core.consensus import strip_consensus_tags
        assert strip_consensus_tags("hello [CONSENSUS: YES] world") == "hello  world"

    def test_has_marker(self):
        from core.consensus import has_consensus_marker
        assert has_consensus_marker("[CONSENSUS: YES]") is True
        assert has_consensus_marker("共识投票: NO") is True
        assert has_consensus_marker("no tags here") is False


# ============================================================================
# Council Two-Phase Protocol (from openclaw council.ts)
# ============================================================================

class TestCouncilTwoPhase:
    """Test the full two-phase council protocol from openclaw."""

    def test_default_agents(self):
        from core.agent_orchestrator import get_default_council_agents
        agents = get_default_council_agents()
        assert len(agents) == 3
        names = {a["name"] for a in agents}
        assert names == {"Architect", "Engineer", "Reviewer"}

    def test_create_full_session(self):
        from core.agent_orchestrator import create_council_full_session, CouncilAgentPersona
        agents = [
            CouncilAgentPersona(name="A", emoji="🏗️", persona="Architect"),
            CouncilAgentPersona(name="B", emoji="⚙️", persona="Engineer"),
        ]
        session = create_council_full_session(task="Build feature", agents=agents, max_rounds=10)
        assert session.status == "running"
        assert len(session.agents) == 2
        assert session.max_rounds == 10

    def test_plan_round_prompt(self):
        from core.agent_orchestrator import (
            build_council_agent_prompt, CouncilAgentPersona, CouncilAgentResponse,
        )
        agents = [
            CouncilAgentPersona(name="Arch", emoji="🏗️"),
            CouncilAgentPersona(name="Eng", emoji="⚙️"),
        ]
        prompt = build_council_agent_prompt(agents[0], "Build X", 1, [], agents)
        assert "规划轮" in prompt
        assert "不要写任何业务代码" in prompt
        assert "plan.md" in prompt

    def test_execution_round_prompt(self):
        from core.agent_orchestrator import (
            build_council_agent_prompt, CouncilAgentPersona, CouncilAgentResponse,
        )
        agents = [
            CouncilAgentPersona(name="Arch", emoji="🏗️"),
            CouncilAgentPersona(name="Eng", emoji="⚙️"),
        ]
        prev = [CouncilAgentResponse(agent="Arch", round=1, content="Plan done [CONSENSUS: NO]", consensus=False)]
        prompt = build_council_agent_prompt(agents[1], "Build X", 2, prev, agents)
        assert "执行轮" in prompt
        assert "按计划执行" in prompt
        assert "之前的协作记录" in prompt

    def test_system_prompt(self):
        from core.agent_orchestrator import build_council_system_prompt, CouncilAgentPersona
        agents = [
            CouncilAgentPersona(name="Arch", emoji="🏗️", persona="System architect"),
            CouncilAgentPersona(name="Eng", emoji="⚙️", persona="Engineer"),
        ]
        prompt = build_council_system_prompt(agents[0], agents, "/tmp/project")
        assert "Arch" in prompt
        assert "council/Arch" in prompt
        assert "System architect" in prompt

    def test_record_and_evaluate(self):
        from core.agent_orchestrator import (
            create_council_full_session, record_council_agent_response,
            evaluate_council_round, CouncilAgentPersona,
        )
        agents = [
            CouncilAgentPersona(name="A", emoji="🏗️"),
            CouncilAgentPersona(name="B", emoji="⚙️"),
        ]
        session = create_council_full_session(task="Test", agents=agents, max_rounds=3)

        record_council_agent_response(session.session_id, agent_name="A", round_num=1, content="Plan [CONSENSUS: NO]")
        record_council_agent_response(session.session_id, agent_name="B", round_num=1, content="Plan [CONSENSUS: NO]")
        result = evaluate_council_round(session.session_id, 1)
        assert not result["all_yes"]
        assert result["yes_count"] == 0

    def test_consensus_reached(self):
        from core.agent_orchestrator import (
            create_council_full_session, record_council_agent_response,
            evaluate_council_round, CouncilAgentPersona,
        )
        agents = [
            CouncilAgentPersona(name="A", emoji="🏗️"),
            CouncilAgentPersona(name="B", emoji="⚙️"),
        ]
        session = create_council_full_session(task="Test", agents=agents)

        record_council_agent_response(session.session_id, agent_name="A", round_num=2, content="Done [CONSENSUS: YES]")
        record_council_agent_response(session.session_id, agent_name="B", round_num=2, content="Done [CONSENSUS: YES]")
        result = evaluate_council_round(session.session_id, 2)
        assert result["all_yes"]
        assert session.status == "awaiting_user"
        assert session.final_summary != ""

    def test_max_rounds_reached(self):
        from core.agent_orchestrator import (
            create_council_full_session, record_council_agent_response,
            evaluate_council_round, CouncilAgentPersona,
        )
        agents = [CouncilAgentPersona(name="A", emoji="🏗️")]
        session = create_council_full_session(task="Test", agents=agents, max_rounds=1)

        record_council_agent_response(session.session_id, agent_name="A", round_num=1, content="Not done [CONSENSUS: NO]")
        evaluate_council_round(session.session_id, 1)
        assert session.status == "max_rounds"

    def test_summary_generation(self):
        from core.agent_orchestrator import (
            create_council_full_session, record_council_agent_response,
            evaluate_council_round, generate_council_summary, CouncilAgentPersona,
        )
        agents = [
            CouncilAgentPersona(name="A", emoji="🏗️"),
            CouncilAgentPersona(name="B", emoji="⚙️"),
        ]
        session = create_council_full_session(task="Build it", agents=agents)
        record_council_agent_response(session.session_id, agent_name="A", round_num=1, content="Did work [CONSENSUS: YES]")
        record_council_agent_response(session.session_id, agent_name="B", round_num=1, content="Did work [CONSENSUS: YES]")
        evaluate_council_round(session.session_id, 1)
        assert "Council Summary" in session.final_summary
        assert "Build it" in session.final_summary


# ============================================================================
# Ralph 7-Phase State Machine (from oh-my-codex ralph/contract.ts)
# ============================================================================

class TestRalphPhases:
    """Test Ralph 7-phase state machine from oh-my-codex."""

    def test_all_phases_defined(self):
        from core.workflow_engines import RALPH_PHASES
        assert len(RALPH_PHASES) == 7
        assert "starting" in RALPH_PHASES
        assert "executing" in RALPH_PHASES
        assert "verifying" in RALPH_PHASES
        assert "fixing" in RALPH_PHASES
        assert "complete" in RALPH_PHASES
        assert "failed" in RALPH_PHASES
        assert "cancelled" in RALPH_PHASES

    def test_normalize_phase(self):
        from core.workflow_engines import normalize_ralph_phase
        assert normalize_ralph_phase("executing")[0] == "executing"
        assert normalize_ralph_phase("EXECUTING")[0] == "executing"

    def test_legacy_aliases(self):
        from core.workflow_engines import normalize_ralph_phase
        phase, warning = normalize_ralph_phase("started")
        assert phase == "starting"
        assert "legacy" in warning.lower()

        phase, _ = normalize_ralph_phase("running")
        assert phase == "executing"

        phase, _ = normalize_ralph_phase("succeeded")
        assert phase == "complete"

    def test_invalid_phase(self):
        from core.workflow_engines import normalize_ralph_phase
        phase, error = normalize_ralph_phase("invalid_phase")
        assert phase == ""
        assert "must be one of" in error

    def test_validate_state_active(self):
        from core.workflow_engines import validate_ralph_state
        result = validate_ralph_state({"active": True})
        assert result["ok"]
        state = result["state"]
        assert state["iteration"] == 0
        assert state["max_iterations"] == 50
        assert state["current_phase"] == "starting"
        assert "started_at" in state

    def test_validate_terminal_phase(self):
        from core.workflow_engines import validate_ralph_state
        result = validate_ralph_state({"current_phase": "complete", "active": True})
        assert not result["ok"]
        assert "terminal" in result["error"].lower()

        result = validate_ralph_state({"current_phase": "complete", "active": False})
        assert result["ok"]

    def test_validate_iteration_bounds(self):
        from core.workflow_engines import validate_ralph_state
        result = validate_ralph_state({"iteration": -1})
        assert not result["ok"]

        result = validate_ralph_state({"max_iterations": 0})
        assert not result["ok"]


# ============================================================================
# Deep Interview Ambiguity Scoring (from oh-my-codex deep-interview/SKILL.md)
# ============================================================================

class TestDeepInterviewEnhanced:
    """Test enhanced deep interview with weighted ambiguity scoring."""

    def test_depth_profiles(self):
        from core.workflow_engines import create_deep_interview
        quick = create_deep_interview(user_id="u1", session_id="s1", topic="Test")
        quick.depth_profile = "quick"
        quick.__post_init__()
        assert quick.threshold == 0.30
        assert quick.max_rounds == 5

    def test_ambiguity_computation_greenfield(self):
        from core.workflow_engines import create_deep_interview, ClarityScore
        interview = create_deep_interview(user_id="u1", session_id="s1", topic="New App")
        interview.project_type = "greenfield"
        interview.clarity_scores = {
            "intent": ClarityScore("intent", 0.8),
            "outcome": ClarityScore("outcome", 0.7),
            "scope": ClarityScore("scope", 0.6),
            "constraints": ClarityScore("constraints", 0.5),
            "success_criteria": ClarityScore("success_criteria", 0.4),
        }
        ambiguity = interview.compute_ambiguity()
        # 1 - (0.8*0.30 + 0.7*0.25 + 0.6*0.20 + 0.5*0.15 + 0.4*0.10)
        # = 1 - (0.24 + 0.175 + 0.12 + 0.075 + 0.04) = 1 - 0.65 = 0.35
        assert abs(ambiguity - 0.35) < 0.01

    def test_ambiguity_computation_brownfield(self):
        from core.workflow_engines import create_deep_interview, ClarityScore
        interview = create_deep_interview(user_id="u1", session_id="s1", topic="Refactor")
        interview.project_type = "brownfield"
        interview.clarity_scores = {
            "intent": ClarityScore("intent", 1.0),
            "outcome": ClarityScore("outcome", 1.0),
            "scope": ClarityScore("scope", 1.0),
            "constraints": ClarityScore("constraints", 1.0),
            "success_criteria": ClarityScore("success_criteria", 1.0),
            "context": ClarityScore("context", 1.0),
        }
        ambiguity = interview.compute_ambiguity()
        assert ambiguity == 0.0  # Perfect clarity

    def test_readiness_gates(self):
        from core.workflow_engines import create_deep_interview, ClarityScore
        interview = create_deep_interview(user_id="u1", session_id="s1", topic="Test")
        interview.current_ambiguity = 0.1  # Below threshold
        # Not ready: non_goals not explicit
        assert not interview.is_ready_to_crystallize()

        interview.non_goals_explicit = True
        assert not interview.is_ready_to_crystallize()  # Decision boundaries not explicit

        interview.decision_boundaries_explicit = True
        assert not interview.is_ready_to_crystallize()  # Pressure pass not complete

        interview.pressure_pass_complete = True
        assert interview.is_ready_to_crystallize()

    def test_weakest_dimension(self):
        from core.workflow_engines import create_deep_interview, ClarityScore
        interview = create_deep_interview(user_id="u1", session_id="s1", topic="Test")
        # Provide scores for ALL greenfield dimensions so we can control which is weakest
        interview.clarity_scores = {
            "intent": ClarityScore("intent", 0.9),
            "outcome": ClarityScore("outcome", 0.1),  # Weakest
            "scope": ClarityScore("scope", 0.5),
            "constraints": ClarityScore("constraints", 0.5),
            "success_criteria": ClarityScore("success_criteria", 0.5),
        }
        assert interview.get_weakest_dimension() == "outcome"

    def test_challenge_modes(self):
        from core.workflow_engines import create_deep_interview
        interview = create_deep_interview(user_id="u1", session_id="s1", topic="Test")

        interview.current_round = 1
        assert interview.should_use_challenge_mode() is None  # Too early

        interview.current_round = 2
        assert interview.should_use_challenge_mode() == "Contrarian"

        interview.challenge_modes_used.append("Contrarian")
        interview.current_round = 4
        assert interview.should_use_challenge_mode() == "Simplifier"

        interview.challenge_modes_used.append("Simplifier")
        interview.current_round = 5
        interview.current_ambiguity = 0.3
        assert interview.should_use_challenge_mode() == "Ontologist"


# ============================================================================
# HUD Presets (from oh-my-codex src/hud/)
# ============================================================================

class TestHUDEnhanced:
    """Test enhanced HUD with presets from oh-my-codex."""

    def test_minimal_preset(self):
        from core.workflow_engines import get_hud, update_hud
        hud = get_hud("hud_test", "s1")
        update_hud("hud_test", "s1",
            active=True, preset="minimal",
            ralph_iteration=3, ralph_max_iterations=10,
            session_turns=42,
        )
        display = hud.format_display()
        assert "ralph:3/10" in display
        assert "WeBot" in display

    def test_focused_preset(self):
        from core.workflow_engines import get_hud, update_hud
        hud = get_hud("hud_test2", "s1")
        update_hud("hud_test2", "s1",
            active=True, preset="focused",
            ralph_iteration=3, ralph_max_iterations=10,
            team_agent_count=3, team_name="alpha",
            session_turns=42, session_total_tokens=150000,
        )
        display = hud.format_display()
        assert "ralph:3/10" in display
        assert "team:3 workers" in display
        assert "tokens:" in display

    def test_ralph_color_coding(self):
        from core.workflow_engines import get_hud, update_hud
        hud = get_hud("hud_color", "s1")

        # Normal (green)
        update_hud("hud_color", "s1", active=True, ralph_iteration=2, ralph_max_iterations=10)
        assert hud._get_ralph_color_marker() == "🟢"

        # Warning (yellow)
        update_hud("hud_color", "s1", ralph_iteration=7, ralph_max_iterations=10)
        assert hud._get_ralph_color_marker() == "🟡"

        # Critical (red)
        update_hud("hud_color", "s1", ralph_iteration=9, ralph_max_iterations=10)
        assert hud._get_ralph_color_marker() == "🔴"

    def test_hud_to_dict(self):
        from core.workflow_engines import get_hud, update_hud
        hud = get_hud("hud_dict", "s1")
        update_hud("hud_dict", "s1",
            active=True, ralph_iteration=5, ralph_max_iterations=10,
            team_agent_count=2,
        )
        d = hud.to_dict()
        assert d["ralph"]["iteration"] == 5
        assert d["team"]["count"] == 2
        assert d["preset"] == "focused"


# ============================================================================
# Run existing tests to ensure backward compatibility
# ============================================================================

class TestBackwardCompat:
    """Ensure enhanced features don't break existing functionality."""

    def test_ralph_loop_still_works(self):
        from core.workflow_engines import create_ralph_loop
        loop = create_ralph_loop(
            user_id="u1", session_id="s1",
            task="Fix bug", verification_criteria="Tests pass",
        )
        assert loop.status == RalphStatus.STARTING or loop.status.value == "executing"
        loop.record_iteration("Fixed", "Pass!", True)
        assert loop.status.value in ("complete", "succeeded")

    def test_council_simple_session_still_works(self):
        from core.agent_orchestrator import (
            create_council_session, submit_council_vote,
            evaluate_council_consensus,
        )
        council = create_council_session(question="Test?")
        submit_council_vote(
            council.council_id,
            voter_id="v1", model="test", decision="approve",
            reasoning="ok", confidence=0.9,
        )
        result = evaluate_council_consensus(council.council_id)
        assert result.consensus == "approved"

    def test_interview_basic_flow(self):
        from core.workflow_engines import (
            create_deep_interview, add_interview_question,
            answer_interview_question, complete_interview,
        )
        interview = create_deep_interview(user_id="u1", session_id="s1", topic="Test")
        q = add_interview_question(interview.interview_id, "What?")
        answer_interview_question(interview.interview_id, q.question_id, "This")
        complete_interview(interview.interview_id, "Spec...")
        assert interview.status == "complete"


# Import for backward compat test
from core.workflow_engines import RalphStatus
