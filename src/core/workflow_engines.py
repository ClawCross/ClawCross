"""
Workflow Engines – Advanced execution patterns from oh-my-codex & Claude Code.

Features:
1. Ralph Persistent Loop: execute→verify→retry cycle until success
2. Deep Interview: structured requirement gathering before implementation
3. Autopilot: fully autonomous execution with configurable guardrails
4. Pre-context Gate: validate context sufficiency before expensive operations
5. Session Fork: clone a session to explore alternatives
6. HUD (Heads-Up Display): real-time status overlay for long operations

Ported from oh-my-codex's skill system and openclaw-claude-code.
"""

from __future__ import annotations

import asyncio
import utils.scheduler_service
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable


# ============================================================================
# 1. Ralph Persistent Loop
# ============================================================================

class RalphStatus(str, Enum):
    """
    Status of a Ralph persistent loop.

    7-phase state machine ported from oh-my-codex/src/ralph/contract.ts:
    starting → executing → verifying → fixing → complete/failed/cancelled
    """
    STARTING = "starting"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    FIXING = "fixing"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"

    # Keep backward compat aliases
    RUNNING = "executing"
    RETRYING = "fixing"
    SUCCEEDED = "complete"


# Phase sets from oh-my-codex ralph/contract.ts
RALPH_PHASES = ("starting", "executing", "verifying", "fixing", "complete", "failed", "cancelled")
RALPH_TERMINAL_PHASES = frozenset({"complete", "failed", "cancelled"})

# Legacy phase aliases from oh-my-codex
RALPH_LEGACY_ALIASES: dict[str, str] = {
    "start": "starting",
    "started": "starting",
    "execution": "executing",
    "execute": "executing",
    "running": "executing",
    "verify": "verifying",
    "verification": "verifying",
    "fix": "fixing",
    "retrying": "fixing",
    "complete": "complete",
    "completed": "complete",
    "succeeded": "complete",
    "fail": "failed",
    "error": "failed",
    "cancel": "cancelled",
}


def normalize_ralph_phase(raw_phase: str) -> tuple[str, str]:
    """
    Normalize a Ralph phase name.

    Returns (normalized_phase, warning_or_empty).
    Ported from oh-my-codex's normalizeRalphPhase().
    """
    if not raw_phase or not raw_phase.strip():
        return ("", "ralph.current_phase must be a non-empty string")

    normalized = raw_phase.strip().lower()
    if normalized in RALPH_PHASES:
        return (normalized, "")

    alias = RALPH_LEGACY_ALIASES.get(normalized)
    if alias:
        return (alias, f'normalized legacy Ralph phase "{raw_phase}" -> "{alias}"')

    return ("", f"ralph.current_phase must be one of: {', '.join(RALPH_PHASES)}")


def validate_ralph_state(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Validate and normalize a Ralph state dict.

    Ported from oh-my-codex's validateAndNormalizeRalphState().
    Returns {"ok": bool, "state": dict, "warning": str, "error": str}
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    state = dict(candidate)
    warning = ""

    if "current_phase" in state and state["current_phase"] is not None:
        phase, phase_warning = normalize_ralph_phase(str(state["current_phase"]))
        if not phase:
            return {"ok": False, "error": phase_warning}
        state["current_phase"] = phase
        if phase_warning:
            warning = phase_warning

    if state.get("active") is True:
        state.setdefault("iteration", 0)
        state.setdefault("max_iterations", 50)
        state.setdefault("current_phase", "starting")
        state.setdefault("started_at", now_iso)

    if "iteration" in state and state["iteration"] is not None:
        val = state["iteration"]
        if not isinstance(val, int) or val < 0:
            return {"ok": False, "error": "ralph.iteration must be a finite integer >= 0"}

    if "max_iterations" in state and state["max_iterations"] is not None:
        val = state["max_iterations"]
        if not isinstance(val, int) or val <= 0:
            return {"ok": False, "error": "ralph.max_iterations must be a finite integer > 0"}

    if isinstance(state.get("current_phase"), str) and state["current_phase"] in RALPH_TERMINAL_PHASES:
        if state.get("active") is True:
            return {"ok": False, "error": "terminal Ralph phases require active=false"}
        state.setdefault("completed_at", now_iso)

    return {"ok": True, "state": state, "warning": warning}


@dataclass
class RalphIteration:
    """One iteration of the Ralph loop."""
    iteration: int
    action_result: str = ""
    verification_result: str = ""
    passed: bool = False
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class RalphLoop:
    """
    Ralph Persistent Loop: execute → verify → retry until success.

    Named after oh-my-codex's $ralph pattern:
    - Execute an action
    - Run verification
    - If verification fails, analyze failure and retry
    - Repeat up to max_retries
    """
    loop_id: str
    user_id: str
    session_id: str
    task: str
    verification_criteria: str
    max_retries: int = 5
    status: RalphStatus = RalphStatus.STARTING
    iterations: list[RalphIteration] = field(default_factory=list)
    current_iteration: int = 0
    created_at: str = ""
    completed_at: str = ""
    final_result: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def can_retry(self) -> bool:
        return (
            self.status in (RalphStatus.STARTING, RalphStatus.EXECUTING, RalphStatus.FIXING)
            and self.current_iteration < self.max_retries
        )

    def record_iteration(
        self,
        action_result: str,
        verification_result: str,
        passed: bool,
    ) -> RalphIteration:
        """Record a loop iteration result."""
        iteration = RalphIteration(
            iteration=self.current_iteration,
            action_result=action_result,
            verification_result=verification_result,
            passed=passed,
        )
        self.iterations.append(iteration)
        self.current_iteration += 1

        if passed:
            self.status = RalphStatus.COMPLETE
            self.final_result = action_result
            self.completed_at = datetime.now(timezone.utc).isoformat()
        elif not self.can_retry:
            self.status = RalphStatus.FAILED
            self.final_result = f"Failed after {self.current_iteration} iterations"
            self.completed_at = datetime.now(timezone.utc).isoformat()
        else:
            self.status = RalphStatus.FIXING

        return iteration


_ralph_loops: dict[str, RalphLoop] = {}


def create_ralph_loop(
    *,
    user_id: str,
    session_id: str,
    task: str,
    verification_criteria: str,
    max_retries: int = 5,
) -> RalphLoop:
    """Create a new Ralph persistent loop."""
    loop_id = f"ralph_{uuid.uuid4().hex[:12]}"
    loop = RalphLoop(
        loop_id=loop_id,
        user_id=user_id,
        session_id=session_id,
        task=task,
        verification_criteria=verification_criteria,
        max_retries=max_retries,
    )
    _ralph_loops[loop_id] = loop
    return loop


def get_ralph_loop(loop_id: str) -> RalphLoop | None:
    return _ralph_loops.get(loop_id)


def get_ralph_prompt(loop: RalphLoop) -> str:
    """Generate a prompt for the current Ralph iteration."""
    if loop.status in (RalphStatus.STARTING, RalphStatus.EXECUTING) and loop.current_iteration == 0:
        return (
            f"【Ralph 持久循环 - 首次执行】\n"
            f"任务: {loop.task}\n"
            f"验证标准: {loop.verification_criteria}\n"
            f"最大重试次数: {loop.max_retries}\n\n"
            "请执行任务。完成后，系统将自动验证结果。"
        )

    last_iteration = loop.iterations[-1] if loop.iterations else None
    return (
        f"【Ralph 持久循环 - 第 {loop.current_iteration + 1}/{loop.max_retries} 次重试】\n"
        f"任务: {loop.task}\n"
        f"验证标准: {loop.verification_criteria}\n\n"
        f"上次验证失败:\n{last_iteration.verification_result[:500] if last_iteration else '无'}\n\n"
        "请根据验证失败原因修正实现，然后系统将再次验证。"
    )


# ============================================================================
# 2. Deep Interview
# ============================================================================

@dataclass
class InterviewQuestion:
    """A question in the deep interview flow."""
    question_id: str
    question: str
    purpose: str  # Why this question matters
    target_dimension: str = ""  # Which clarity dimension this targets
    answer: str = ""
    follow_up: str = ""


# Depth profiles from oh-my-codex deep-interview/SKILL.md
INTERVIEW_DEPTH_PROFILES: dict[str, dict[str, Any]] = {
    "quick": {"threshold": 0.30, "max_rounds": 5},
    "standard": {"threshold": 0.20, "max_rounds": 12},
    "deep": {"threshold": 0.15, "max_rounds": 20},
}

# Clarity dimensions with weights (from oh-my-codex SKILL.md)
GREENFIELD_WEIGHTS: dict[str, float] = {
    "intent": 0.30,
    "outcome": 0.25,
    "scope": 0.20,
    "constraints": 0.15,
    "success_criteria": 0.10,
}

BROWNFIELD_WEIGHTS: dict[str, float] = {
    "intent": 0.25,
    "outcome": 0.20,
    "scope": 0.20,
    "constraints": 0.15,
    "success_criteria": 0.10,
    "context": 0.10,
}

# Challenge modes from oh-my-codex SKILL.md
CHALLENGE_MODES = ("Contrarian", "Simplifier", "Ontologist")


@dataclass
class ClarityScore:
    """Score for a single clarity dimension (0.0 = no clarity, 1.0 = fully clear)."""
    dimension: str
    score: float = 0.0
    justification: str = ""
    gap: str = ""


@dataclass
class DeepInterview:
    """
    Structured requirement gathering before implementation.

    Ported from oh-my-codex's deep-interview/SKILL.md:
    - Socratic interview loop with weighted ambiguity scoring
    - 3 depth profiles: quick/standard/deep
    - 3 challenge modes: Contrarian/Simplifier/Ontologist
    - Readiness gates: Non-goals and Decision Boundaries must be explicit
    - 5 phases: Preflight → Initialize → Interview Loop → Crystallize → Execution Bridge
    """
    interview_id: str
    user_id: str
    session_id: str
    topic: str
    depth_profile: str = "standard"  # quick | standard | deep
    project_type: str = "greenfield"  # greenfield | brownfield
    questions: list[InterviewQuestion] = field(default_factory=list)
    clarity_scores: dict[str, ClarityScore] = field(default_factory=dict)
    challenge_modes_used: list[str] = field(default_factory=list)
    current_ambiguity: float = 1.0
    threshold: float = 0.20
    max_rounds: int = 12
    current_round: int = 0
    current_stage: str = "intent-first"  # intent-first | feasibility | brownfield-grounding
    non_goals_explicit: bool = False
    decision_boundaries_explicit: bool = False
    pressure_pass_complete: bool = False
    specification: str = ""
    context_snapshot_path: str = ""
    status: str = "gathering"  # gathering | complete | cancelled
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        profile = INTERVIEW_DEPTH_PROFILES.get(self.depth_profile, INTERVIEW_DEPTH_PROFILES["standard"])
        self.threshold = profile["threshold"]
        self.max_rounds = profile["max_rounds"]

    def compute_ambiguity(self) -> float:
        """
        Compute weighted ambiguity score.

        Greenfield: ambiguity = 1 - (intent×0.30 + outcome×0.25 + scope×0.20 + constraints×0.15 + success×0.10)
        Brownfield: ambiguity = 1 - (intent×0.25 + outcome×0.20 + scope×0.20 + constraints×0.15 + success×0.10 + context×0.10)
        """
        weights = BROWNFIELD_WEIGHTS if self.project_type == "brownfield" else GREENFIELD_WEIGHTS
        weighted_sum = 0.0
        for dim, weight in weights.items():
            score_obj = self.clarity_scores.get(dim)
            if score_obj:
                weighted_sum += score_obj.score * weight
        self.current_ambiguity = max(0.0, min(1.0, 1.0 - weighted_sum))
        return self.current_ambiguity

    def is_ready_to_crystallize(self) -> bool:
        """
        Check if the interview can be crystallized.

        Readiness gates (from oh-my-codex):
        - Ambiguity below threshold
        - Non-goals must be explicit
        - Decision Boundaries must be explicit
        - At least one pressure pass must be complete
        """
        if self.current_ambiguity > self.threshold:
            return False
        if not self.non_goals_explicit:
            return False
        if not self.decision_boundaries_explicit:
            return False
        if not self.pressure_pass_complete:
            return False
        return True

    def get_weakest_dimension(self) -> str:
        """Get the clarity dimension with the lowest score."""
        weights = BROWNFIELD_WEIGHTS if self.project_type == "brownfield" else GREENFIELD_WEIGHTS
        lowest_dim = ""
        lowest_score = 2.0
        for dim in weights:
            score_obj = self.clarity_scores.get(dim)
            s = score_obj.score if score_obj else 0.0
            if s < lowest_score:
                lowest_score = s
                lowest_dim = dim
        return lowest_dim

    def should_use_challenge_mode(self) -> str | None:
        """
        Determine which challenge mode to activate.

        From oh-my-codex:
        - Contrarian (round 2+): challenge core assumptions
        - Simplifier (round 4+): probe minimal viable scope
        - Ontologist (round 5+ and ambiguity > 0.25): ask for essence-level reframing
        """
        if "Contrarian" not in self.challenge_modes_used and self.current_round >= 2:
            return "Contrarian"
        if "Simplifier" not in self.challenge_modes_used and self.current_round >= 4:
            return "Simplifier"
        if (
            "Ontologist" not in self.challenge_modes_used
            and self.current_round >= 5
            and self.current_ambiguity > 0.25
        ):
            return "Ontologist"
        return None


_interviews: dict[str, DeepInterview] = {}


def create_deep_interview(
    *,
    user_id: str,
    session_id: str,
    topic: str,
) -> DeepInterview:
    """Start a new deep interview session."""
    interview_id = f"interview_{uuid.uuid4().hex[:12]}"
    interview = DeepInterview(
        interview_id=interview_id,
        user_id=user_id,
        session_id=session_id,
        topic=topic,
    )
    _interviews[interview_id] = interview
    return interview


def add_interview_question(
    interview_id: str,
    question: str,
    purpose: str = "",
) -> InterviewQuestion | None:
    """Add a question to the interview."""
    interview = _interviews.get(interview_id)
    if not interview or interview.status != "gathering":
        return None

    q = InterviewQuestion(
        question_id=f"q_{len(interview.questions) + 1}",
        question=question,
        purpose=purpose,
    )
    interview.questions.append(q)
    return q


def answer_interview_question(
    interview_id: str,
    question_id: str,
    answer: str,
) -> bool:
    """Answer an interview question."""
    interview = _interviews.get(interview_id)
    if not interview:
        return False

    for q in interview.questions:
        if q.question_id == question_id:
            q.answer = answer
            return True
    return False


def complete_interview(interview_id: str, specification: str) -> DeepInterview | None:
    """Complete the interview with a generated specification."""
    interview = _interviews.get(interview_id)
    if not interview:
        return None
    interview.specification = specification
    interview.status = "complete"
    return interview


def get_interview_prompt(interview: DeepInterview) -> str:
    """Generate a prompt for the deep interview process."""
    unanswered = [q for q in interview.questions if not q.answer]
    answered = [q for q in interview.questions if q.answer]

    lines = [
        f"【深度访谈 - {interview.topic}】",
        f"状态: {interview.status}",
        "",
    ]

    if answered:
        lines.append("已收集的需求：")
        for q in answered:
            lines.append(f"  Q: {q.question}")
            lines.append(f"  A: {q.answer}")
            lines.append("")

    if unanswered:
        lines.append("待回答的问题：")
        for q in unanswered:
            lines.append(f"  - {q.question}")
            if q.purpose:
                lines.append(f"    (目的: {q.purpose})")

    return "\n".join(lines)


# ============================================================================
# 3. Autopilot Mode
# ============================================================================

@dataclass
class AutopilotConfig:
    """Configuration for fully autonomous execution."""
    enabled: bool = False
    max_turns: int = 50
    allow_file_writes: bool = True
    allow_commands: bool = True
    allow_network: bool = False
    require_verification: bool = True
    pause_on_error: bool = True
    cost_limit_usd: float = 5.0
    notification_interval: int = 10  # Turns between status notifications
    max_qa_cycles: int = 5
    max_validation_rounds: int = 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_turns": self.max_turns,
            "allow_file_writes": self.allow_file_writes,
            "allow_commands": self.allow_commands,
            "allow_network": self.allow_network,
            "require_verification": self.require_verification,
            "pause_on_error": self.pause_on_error,
            "cost_limit_usd": self.cost_limit_usd,
            "max_qa_cycles": self.max_qa_cycles,
            "max_validation_rounds": self.max_validation_rounds,
        }


# Autopilot 5-phase pipeline (from oh-my-codex skills/autopilot/SKILL.md)
AUTOPILOT_PHASES = ("pre_context", "expansion", "planning", "execution", "qa", "validation", "cleanup", "complete")


@dataclass
class AutopilotState:
    """
    Runtime state for a running Autopilot pipeline.

    Ported from oh-my-codex's autopilot State Management:
    - Pre-context intake → Expansion → Planning → Execution → QA → Validation → Cleanup
    - QA cycles with same-error-3-times detection
    - Validation with multi-perspective review
    """
    user_id: str
    session_id: str
    task: str
    current_phase: str = "pre_context"
    active: bool = True
    context_snapshot_path: str = ""
    spec_path: str = ""
    plan_path: str = ""
    qa_cycle: int = 0
    qa_error_counts: dict[str, int] = field(default_factory=dict)  # error_signature -> count
    validation_round: int = 0
    validation_results: dict[str, str] = field(default_factory=dict)  # reviewer -> verdict
    started_at: str = ""
    completed_at: str = ""
    config: AutopilotConfig = field(default_factory=AutopilotConfig)

    def __post_init__(self):
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat()

    def advance_phase(self, next_phase: str) -> None:
        """Advance to the next pipeline phase."""
        if next_phase in AUTOPILOT_PHASES:
            self.current_phase = next_phase
            if next_phase == "complete":
                self.active = False
                self.completed_at = datetime.now(timezone.utc).isoformat()

    def record_qa_error(self, error_signature: str) -> bool:
        """Record a QA error. Returns True if same error seen 3+ times (should stop)."""
        self.qa_error_counts[error_signature] = self.qa_error_counts.get(error_signature, 0) + 1
        return self.qa_error_counts[error_signature] >= 3

    def should_stop_qa(self) -> bool:
        """Check if QA should stop (cycles exhausted or repeated error)."""
        if self.qa_cycle >= self.config.max_qa_cycles:
            return True
        return any(count >= 3 for count in self.qa_error_counts.values())

    def all_validators_approved(self) -> bool:
        """Check if all validation reviewers approved."""
        required = {"architect", "security", "code_reviewer"}
        return all(
            self.validation_results.get(r) == "approved"
            for r in required
        )

    def to_state_dict(self) -> dict[str, Any]:
        """Serialize to dict for state_write MCP tool."""
        return {
            "mode": "autopilot",
            "active": self.active,
            "current_phase": self.current_phase,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "state": {
                "context_snapshot_path": self.context_snapshot_path,
                "spec_path": self.spec_path,
                "plan_path": self.plan_path,
                "qa_cycle": self.qa_cycle,
                "validation_round": self.validation_round,
            },
        }


_autopilot_states: dict[str, AutopilotState] = {}


_autopilot_configs: dict[str, AutopilotConfig] = {}


def set_autopilot(user_id: str, session_id: str, config: AutopilotConfig) -> None:
    """Enable/configure autopilot for a session."""
    _autopilot_configs[f"{user_id}#{session_id}"] = config


def start_autopilot(
    *,
    user_id: str,
    session_id: str,
    task: str,
    config: AutopilotConfig | None = None,
) -> AutopilotState:
    """Start a full autopilot pipeline run."""
    cfg = config or AutopilotConfig(enabled=True)
    state = AutopilotState(
        user_id=user_id,
        session_id=session_id,
        task=task,
        config=cfg,
    )
    key = f"{user_id}#{session_id}"
    _autopilot_states[key] = state
    _autopilot_configs[key] = cfg
    return state


def get_autopilot_state(user_id: str, session_id: str) -> AutopilotState | None:
    """Get running autopilot state."""
    return _autopilot_states.get(f"{user_id}#{session_id}")


def get_autopilot(user_id: str, session_id: str) -> AutopilotConfig | None:
    """Get autopilot config for a session."""
    return _autopilot_configs.get(f"{user_id}#{session_id}")


def disable_autopilot(user_id: str, session_id: str) -> None:
    """Disable autopilot for a session."""
    _autopilot_configs.pop(f"{user_id}#{session_id}", None)


# ============================================================================
# 4. Pre-context Gate
# ============================================================================

@dataclass
class ContextGateResult:
    """Result of a pre-context gate check."""
    sufficient: bool
    missing_context: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    confidence: float = 0.0


def check_context_gate(
    *,
    task: str,
    available_context: dict[str, Any],
    required_signals: list[str] | None = None,
) -> ContextGateResult:
    """
    Check if the current context is sufficient before expensive operations.

    Pre-context gate prevents the agent from starting complex operations
    when essential context is missing (e.g., no codebase loaded, no
    requirements specified).
    """
    missing = []
    suggestions = []

    # Check for common required context
    if required_signals is None:
        required_signals = ["task", "workspace"]

    context_keys = set(available_context.keys())
    provided_values = {k: bool(v) for k, v in available_context.items()}

    for signal in required_signals:
        if signal not in context_keys or not provided_values.get(signal):
            missing.append(signal)

    # Heuristic checks
    task_lower = task.lower()
    if any(k in task_lower for k in ("file", "code", "implement", "fix")):
        if "workspace" not in context_keys or not provided_values.get("workspace"):
            missing.append("workspace/cwd not set")
            suggestions.append("请先确认工作目录 (workspace) 已正确设置")

    if any(k in task_lower for k in ("test", "verify", "check")):
        if "test_framework" not in context_keys:
            suggestions.append("建议先确认测试框架配置")

    confidence = 1.0 - (len(missing) / max(1, len(required_signals)))

    return ContextGateResult(
        sufficient=len(missing) == 0,
        missing_context=missing,
        suggestions=suggestions,
        confidence=confidence,
    )


# ============================================================================
# 5. Session Fork
# ============================================================================

@dataclass
class SessionFork:
    """A forked session for exploring alternatives."""
    fork_id: str
    source_session: str
    forked_session: str
    user_id: str
    reason: str
    created_at: str = ""
    status: str = "active"  # active | merged | abandoned

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


_session_forks: dict[str, SessionFork] = {}


def fork_session(
    *,
    user_id: str,
    source_session: str,
    reason: str = "",
) -> SessionFork:
    """Fork a session to explore an alternative approach."""
    fork_id = f"sfork_{uuid.uuid4().hex[:12]}"
    forked_session = f"{source_session}_fork_{fork_id[-8:]}"

    fork = SessionFork(
        fork_id=fork_id,
        source_session=source_session,
        forked_session=forked_session,
        user_id=user_id,
        reason=reason,
    )
    _session_forks[fork_id] = fork
    return fork


def get_session_fork(fork_id: str) -> SessionFork | None:
    return _session_forks.get(fork_id)


def list_session_forks(user_id: str, source_session: str) -> list[SessionFork]:
    return [
        f for f in _session_forks.values()
        if f.user_id == user_id and f.source_session == source_session
    ]


# ============================================================================
# 6. HUD (Heads-Up Display)
# ============================================================================

@dataclass
class HUDState:
    """
    Real-time status overlay for long-running operations.

    Ported from oh-my-codex/src/hud/:
    - 3 presets: minimal / focused / full
    - Element renderers for each mode
    - Color-coded Ralph progress
    - Token/quota tracking
    """
    user_id: str
    session_id: str
    active: bool = False
    current_task: str = ""
    progress: float = 0.0  # 0.0 - 1.0
    phase: str = ""
    tool_in_progress: str = ""
    turns_completed: int = 0
    turns_remaining: int = 0
    estimated_completion: str = ""
    warnings: list[str] = field(default_factory=list)
    last_updated: str = ""
    # oh-my-codex mode states
    ralph_iteration: int = 0
    ralph_max_iterations: int = 0
    autopilot_phase: str = ""
    team_agent_count: int = 0
    team_name: str = ""
    interview_phase: str = ""
    interview_lock_active: bool = False
    # Token metrics
    session_turns: int = 0
    session_total_tokens: int = 0
    five_hour_limit_pct: float = 0.0
    weekly_limit_pct: float = 0.0
    # Git info
    git_branch: str = ""
    version: str = ""
    # Preset
    preset: str = "focused"  # minimal | focused | full

    def update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.last_updated = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "current_task": self.current_task,
            "progress": round(self.progress, 2),
            "phase": self.phase,
            "tool_in_progress": self.tool_in_progress,
            "turns_completed": self.turns_completed,
            "turns_remaining": self.turns_remaining,
            "estimated_completion": self.estimated_completion,
            "warnings": self.warnings[:5],
            "last_updated": self.last_updated,
            "ralph": {"iteration": self.ralph_iteration, "max": self.ralph_max_iterations}
                if self.ralph_max_iterations > 0 else None,
            "team": {"count": self.team_agent_count, "name": self.team_name}
                if self.team_agent_count > 0 else None,
            "preset": self.preset,
        }

    def _get_ralph_color_marker(self) -> str:
        """Color marker for Ralph progress (from oh-my-codex hud/colors.ts)."""
        if self.ralph_max_iterations <= 0:
            return ""
        warning_threshold = int(self.ralph_max_iterations * 0.7)
        critical_threshold = int(self.ralph_max_iterations * 0.9)
        if self.ralph_iteration >= critical_threshold:
            return "🔴"
        if self.ralph_iteration >= warning_threshold:
            return "🟡"
        return "🟢"

    def _render_elements(self) -> list[str]:
        """
        Render individual HUD elements.

        Element renderers ported from oh-my-codex/src/hud/render.ts.
        """
        elements: list[str] = []

        # Git branch
        if self.git_branch:
            elements.append(f"🌿 {self.git_branch}")

        # Ralph status
        if self.ralph_max_iterations > 0:
            marker = self._get_ralph_color_marker()
            elements.append(f"{marker} ralph:{self.ralph_iteration}/{self.ralph_max_iterations}")

        # Autopilot
        if self.autopilot_phase:
            elements.append(f"🤖 autopilot:{self.autopilot_phase}")

        # Team
        if self.team_agent_count > 0:
            name = f":{self.team_name}" if self.team_name else ""
            elements.append(f"👥 team:{self.team_agent_count} workers{name}")

        # Interview
        if self.interview_phase:
            lock = ":lock" if self.interview_lock_active else ""
            elements.append(f"🎤 interview:{self.interview_phase}{lock}")

        # Current task/phase
        if self.current_task and self.preset != "minimal":
            elements.append(f"📋 {self.current_task[:60]}")
        if self.phase and self.preset == "full":
            elements.append(f"⚙️ {self.phase}")

        # Progress bar
        if self.progress > 0:
            bar_len = 20
            filled = int(self.progress * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            elements.append(f"[{bar}] {int(self.progress * 100)}%")

        # Tool in progress
        if self.tool_in_progress and self.preset != "minimal":
            elements.append(f"🔧 {self.tool_in_progress}")

        # Turns
        if self.session_turns > 0:
            elements.append(f"turns:{self.session_turns}")

        # Tokens (focused + full only)
        if self.session_total_tokens > 0 and self.preset in ("focused", "full"):
            if self.session_total_tokens >= 1_000_000:
                tokens_str = f"{self.session_total_tokens / 1_000_000:.1f}M"
            elif self.session_total_tokens >= 1_000:
                tokens_str = f"{self.session_total_tokens / 1_000:.1f}k"
            else:
                tokens_str = str(self.session_total_tokens)
            elements.append(f"tokens:{tokens_str}")

        # Quota (focused + full only)
        if self.preset in ("focused", "full"):
            quota_parts = []
            if self.five_hour_limit_pct > 0:
                quota_parts.append(f"5h:{int(self.five_hour_limit_pct)}%")
            if self.weekly_limit_pct > 0:
                quota_parts.append(f"wk:{int(self.weekly_limit_pct)}%")
            if quota_parts:
                elements.append(f"quota:{','.join(quota_parts)}")

        # Turns remaining
        if self.turns_remaining > 0:
            elements.append(f"🔄 {self.turns_completed}/{self.turns_completed + self.turns_remaining}")

        # Warnings
        for w in self.warnings[:2]:
            elements.append(f"⚠️ {w}")

        return elements

    def format_display(self) -> str:
        """Format HUD as a compact text overlay with preset support."""
        if not self.active:
            return ""

        elements = self._render_elements()
        if not elements:
            return "[WeBot] No active modes."

        ver = f"#{self.version}" if self.version else ""
        label = f"[WeBot{ver}]"
        return f"{label} " + " | ".join(elements)


_hud_states: dict[str, HUDState] = {}


def get_hud(user_id: str, session_id: str) -> HUDState:
    """Get or create HUD state for a session."""
    key = f"{user_id}#{session_id}"
    if key not in _hud_states:
        _hud_states[key] = HUDState(user_id=user_id, session_id=session_id)
    return _hud_states[key]


def update_hud(user_id: str, session_id: str, **kwargs) -> HUDState:
    """Update HUD state."""
    hud = get_hud(user_id, session_id)
    hud.update(**kwargs)
    return hud
