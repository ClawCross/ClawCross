"""
WeBot workflow presets and lightweight recovery hints.

The presets expose a browser-native version of reusable operating patterns
inspired by oh-my-codex / oh-my-openagent. They are stored as normal WeBot
session plan metadata so the runtime panel, MCP tools, and model context all
see the same state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowPreset:
    preset_id: str
    name: str
    description: str
    source: str
    mode: str
    reason: str
    title: str
    items: tuple[dict[str, str], ...]
    notes: str = ""
    tags: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "mode": self.mode,
            "reason": self.reason,
            "title": self.title,
            "items": [dict(item) for item in self.items],
            "notes": self.notes,
            "tags": list(self.tags),
        }

    def plan_metadata(self) -> dict[str, Any]:
        return {
            "workflow": self.to_payload(),
            "source": self.source,
        }


WORKFLOW_PRESETS: tuple[WorkflowPreset, ...] = (
    WorkflowPreset(
        preset_id="deep_interview",
        name="Deep Interview",
        description="Turn a vague request into a scoped execution brief before coding.",
        source="oh-my-codex / deep interview",
        mode="plan",
        reason="workflow:deep_interview",
        title="Deep interview intake",
        items=(
            {"step": "Restate the user goal in one sentence", "status": "pending", "notes": "No solutioning yet."},
            {"step": "Extract missing constraints, files, risks, and success criteria", "status": "pending", "notes": "Ask only essential questions if truly blocked."},
            {"step": "Write a concrete execution brief with file targets and verification steps", "status": "pending", "notes": "This becomes the implementation handoff."},
            {"step": "Exit planning only when the brief is specific enough to execute", "status": "pending", "notes": "No generic recap."},
        ),
        notes="Best for ambiguous tasks, discovery work, and when the user hands over a large problem with unclear scope.",
        tags=("planning", "intake", "discovery"),
    ),
    WorkflowPreset(
        preset_id="ralph_loop",
        name="Ralph Loop",
        description="Use the iterative critique-and-revision loop before execution is declared done.",
        source="oh-my-codex / ralph",
        mode="execute",
        reason="workflow:ralph_loop",
        title="Ralph iterative loop",
        items=(
            {"step": "Build the smallest acceptable implementation slice", "status": "pending", "notes": "Prefer a narrow, testable vertical cut."},
            {"step": "Critique the slice for gaps, regressions, and edge cases", "status": "pending", "notes": "Be specific and adversarial."},
            {"step": "Revise using the critique and record what changed", "status": "pending", "notes": "Do not lose the previous reasoning trail."},
            {"step": "Repeat until the acceptance bar is met, then verify", "status": "pending", "notes": "Use tests or concrete proofs."},
        ),
        notes="Best for hard implementation tasks where one-shot coding tends to miss edge cases.",
        tags=("iteration", "execution", "quality"),
    ),
    WorkflowPreset(
        preset_id="review_gate",
        name="Review Gate",
        description="Force a reviewer/verifier discipline before the task is treated as complete.",
        source="oh-my-openagent / review gate",
        mode="review",
        reason="workflow:review_gate",
        title="Review gate",
        items=(
            {"step": "List the concrete claims the implementation makes", "status": "pending", "notes": "Features, fixes, and assumptions."},
            {"step": "Check each claim against code, tests, or runtime evidence", "status": "pending", "notes": "No trust without proof."},
            {"step": "Record failures as verifications or follow-up tasks", "status": "pending", "notes": "Use runtime verifications instead of prose only."},
            {"step": "Approve only when evidence matches the claim set", "status": "pending", "notes": "Missing proof means not done."},
        ),
        notes="Best when you need a sharp reviewer or verifier mode with explicit evidence.",
        tags=("review", "verification", "evidence"),
    ),
    WorkflowPreset(
        preset_id="execution_swarm",
        name="Execution Swarm",
        description="Use a planner/researcher/implementer/verifier split with inbox handoffs.",
        source="claw-code parity / browser-native swarm",
        mode="execute",
        reason="workflow:execution_swarm",
        title="Execution swarm",
        items=(
            {"step": "Delegate bounded research and collect findings through session inbox", "status": "pending", "notes": "Parallelize only read-only discovery."},
            {"step": "Synthesize the findings into exact implementation specs", "status": "pending", "notes": "File paths, APIs, and acceptance criteria."},
            {"step": "Execute one write scope at a time and keep artifacts", "status": "pending", "notes": "Avoid overlapping write ownership."},
            {"step": "Run verification with a fresh reviewer or verifier pass", "status": "pending", "notes": "Treat verification as independent work."},
        ),
        notes="Best when multiple WeBot subagents should coordinate through plan, inbox, and artifact surfaces.",
        tags=("multi-agent", "swarm", "coordination"),
    ),
)


def list_workflow_presets() -> list[dict[str, Any]]:
    return [preset.to_payload() for preset in WORKFLOW_PRESETS]


def get_workflow_preset(preset_id: str) -> WorkflowPreset | None:
    key = (preset_id or "").strip().lower()
    for preset in WORKFLOW_PRESETS:
        if preset.preset_id == key:
            return preset
    return None


def build_run_recovery_hint(
    *,
    status: str,
    last_error: str = "",
    last_result: str = "",
    interrupt_requested: bool = False,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    normalized_status = (status or "").strip().lower()
    error_text = f"{last_error}\n{last_result}".strip().lower()
    event_types = [str((event or {}).get("event_type") or "").strip().lower() for event in (events or [])]

    if normalized_status in {"completed", "succeeded", "success"}:
        return None
    if interrupt_requested or "cancelled" in error_text or normalized_status == "cancelled":
        return {
            "kind": "interrupted",
            "summary": "Run was interrupted before completion.",
            "action": "resume_with_latest_context",
            "suggestion": "Review the last artifact or event, then resume from the interrupted step instead of restarting from scratch.",
        }
    if "413" in error_text or "prompt too long" in error_text or "context" in error_text or "token" in error_text:
        return {
            "kind": "context_overflow",
            "summary": "Run appears to have exhausted context or token budget.",
            "action": "compact_and_continue",
            "suggestion": "Compact the session context, keep only active files/artifacts, and continue from the last completed step.",
        }
    if "timeout" in error_text or normalized_status == "timed_out":
        return {
            "kind": "timeout",
            "summary": "Run timed out before the task finished.",
            "action": "narrow_scope_and_retry",
            "suggestion": "Split the task into a smaller write scope or use a dedicated subagent with a narrower brief.",
        }
    if "429" in error_text or "529" in error_text or "rate limit" in error_text or "overloaded" in error_text:
        return {
            "kind": "provider_backpressure",
            "summary": "Run failed due to model/provider backpressure.",
            "action": "retry_or_fallback_model",
            "suggestion": "Retry after a short delay or switch to a fallback model for the next attempt.",
        }
    if "approval" in error_text or "permission" in error_text or "tool_approval_pending" in event_types:
        return {
            "kind": "approval_blocked",
            "summary": "Run is blocked on a permission or approval decision.",
            "action": "resolve_pending_approval",
            "suggestion": "Resolve the pending tool approval, then deliver the inbox or rerun the blocked step.",
        }
    if normalized_status in {"failed", "error"}:
        return {
            "kind": "generic_failure",
            "summary": "Run failed without a more specific classifier.",
            "action": "review_events_and_resume",
            "suggestion": "Inspect the recent run events and artifacts, then continue from the last known good checkpoint.",
        }
    return None
