"""
Context budgeting helpers for WeBot.

This module keeps runtime budgeting deterministic and cheap:
- trims oversized tool results and stores full payloads on disk
- trims oversized user inputs into runtime artifacts
- compacts old transcript segments into a synthetic summary message
- exposes approximate token accounting for routing and tests
"""

from __future__ import annotations

import hashlib
import json
import os
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from webot.checkpoint_repository import (
    ContextCompactionRecord,
    get_context_compaction,
    save_context_compaction,
)
from webot.runtime_store import create_runtime_artifact


from common.runtime_paths import PROJECT_ROOT  # noqa: E402
from common.runtime_paths import USER_FILES_DIR

DEFAULT_TOOL_RESULT_CHAR_BUDGET = 12000
DEFAULT_TOOL_RESULT_ITEM_LIMIT = 1600
DEFAULT_USER_INPUT_CHAR_BUDGET = 131072
DEFAULT_USER_INPUT_ITEM_LIMIT = 10000
DEFAULT_CONTEXT_TOKEN_BUDGET = 12000
DEFAULT_RECENT_MESSAGE_COUNT = 10
DEFAULT_MAX_HISTORY_MESSAGES = 28
RUNTIME_STATE_KEY = "framework_runtime_state"
RUNTIME_DELTA_KEY = "framework_runtime_delta"
_LEGACY_SKILLS_HEADING = "\n【用户技能 / Memory 条目】"
_SOUL_HEADING = "\n【Personality (SOUL.md)】"
_ARTIFACTS_ENV = "WEBOT_RUNTIME_ARTIFACTS_ENABLED"
_COMPACTION_STATE_ENV = "WEBOT_COMPACTION_STATE_ENABLED"
_COMPACTION_TRIGGER_RATIO_ENV = "WEBOT_COMPACTION_TRIGGER_RATIO"
_COMPACTION_TARGET_RATIO_ENV = "WEBOT_COMPACTION_TARGET_RATIO"
_COMPACTION_MIN_NEW_MESSAGES_ENV = "WEBOT_COMPACTION_MIN_NEW_MESSAGES"
_USER_INPUT_CHAR_BUDGET_ENV = "WEBOT_USER_INPUT_CHAR_BUDGET"
_USER_INPUT_ITEM_LIMIT_ENV = "WEBOT_USER_INPUT_ITEM_LIMIT"
_SKIP_LATEST_USER_INPUT_BUDGET_ENV = "WEBOT_SKIP_LATEST_USER_INPUT_BUDGET"
DEFAULT_COMPACTION_TRIGGER_RATIO = 0.80
DEFAULT_COMPACTION_TARGET_RATIO = 0.50
DEFAULT_COMPACTION_MIN_NEW_MESSAGES = 8


def approximate_token_count(text: str) -> int:
    normalized = (text or "").strip()
    if not normalized:
        return 0
    return max(1, len(normalized) // 4)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _trim_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(120, limit // 2)
    tail = max(80, limit - head - 48)
    return (
        text[:head]
        + f"\n\n... [截断，原始长度 {len(text)} 字符] ...\n\n"
        + text[-tail:]
    )


def _content_has_image_block(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(part, dict) and part.get("type") == "image" for part in content)


def _artifact_dir(user_id: str, session_id: str, bucket: str) -> Path:
    base = USER_FILES_DIR / (user_id or "anonymous") / bucket / (session_id or "default")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _store_runtime_text(
    *,
    user_id: str,
    session_id: str,
    bucket: str,
    prefix: str,
    content: str,
) -> Path:
    key = hashlib.sha256(f"{prefix}:{content}".encode("utf-8")).hexdigest()[:16]
    path = _artifact_dir(user_id, session_id, bucket) / f"{prefix}-{key}.txt"
    path.write_text(content, encoding="utf-8")
    return path


def _runtime_artifacts_enabled() -> bool:
    raw = os.getenv(_ARTIFACTS_ENV, "0").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "off", "no"}


def _resolve_user_input_char_budget() -> int:
    return _env_int(_USER_INPUT_CHAR_BUDGET_ENV, DEFAULT_USER_INPUT_CHAR_BUDGET)


def _resolve_user_input_item_limit() -> int:
    return _env_int(_USER_INPUT_ITEM_LIMIT_ENV, DEFAULT_USER_INPUT_ITEM_LIMIT)


def _resolve_latest_human_message_preserve_count() -> int:
    return 1 if _env_flag(_SKIP_LATEST_USER_INPUT_BUDGET_ENV, True) else 0


def persistent_compaction_enabled() -> bool:
    return _env_flag(_COMPACTION_STATE_ENV, True)


def _resolve_compaction_trigger_ratio() -> float:
    return min(0.95, max(0.10, _env_float(_COMPACTION_TRIGGER_RATIO_ENV, DEFAULT_COMPACTION_TRIGGER_RATIO)))


def _resolve_compaction_target_ratio() -> float:
    return min(0.90, max(0.05, _env_float(_COMPACTION_TARGET_RATIO_ENV, DEFAULT_COMPACTION_TARGET_RATIO)))


def _resolve_compaction_min_new_messages() -> int:
    return max(0, _env_int(_COMPACTION_MIN_NEW_MESSAGES_ENV, DEFAULT_COMPACTION_MIN_NEW_MESSAGES))




def render_runtime_context_block(
    *,
    workspace: str = "",
    mode: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    todos: dict[str, Any] | None = None,
    verifications: list[dict[str, Any]] | None = None,
    pending_approvals: list[dict[str, Any]] | None = None,
    inbox: list[dict[str, Any]] | None = None,
    inbox_unread_count: int = 0,
    inbox_new_count: int = 0,
    recent_artifacts: list[dict[str, Any]] | None = None,
    recent_runs: list[dict[str, Any]] | None = None,
    memory: dict[str, Any] | None = None,
) -> str:
    # Resolve current workspace for each invocation; runtime snapshots track changes.
    lines = ["【Runtime Context】"]
    if workspace:
        lines.append(f"workspace: {workspace}")
    if mode:
        lines.append(f"session_mode: {mode.get('mode', 'execute')}")
        if mode.get("reason"):
            lines.append(f"session_mode_reason: {_trim_text(str(mode.get('reason') or ''), 120)}")
    if plan:
        lines.append(f"plan_status: {plan.get('status', 'active')}")
        if plan.get("title"):
            lines.append(f"plan_title: {plan['title']}")
        for item in plan.get("items", [])[:8]:
            lines.append(f"plan::{item.get('status', 'pending')}::{item.get('step', '')}")
    if todos:
        for item in todos.get("items", [])[:10]:
            lines.append(f"todo::{item.get('status', 'pending')}::{item.get('step', '')}")
    if verifications:
        for item in verifications[:5]:
            lines.append(
                f"verification::{item.get('status', '')}::{item.get('title', '')}::{_trim_text(item.get('details', ''), 120)}"
            )
    if pending_approvals:
        lines.append(f"pending_tool_approvals: {len(pending_approvals)}")
        for item in pending_approvals[:3]:
            lines.append(f"approval::{item.get('tool_name', '')}::{item.get('status', '')}")
    if inbox_new_count:
        lines.append(f"inbox_unread: {inbox_unread_count}")
        lines.append(f"inbox_new: {inbox_new_count}")
        for item in (inbox or [])[:3]:
            sender = item.get("source_label") or item.get("source_session") or "unknown"
            lines.append(f"inbox::new::{item.get('message_id', '')}::{sender}::{item.get('summary', '')}")
    if recent_artifacts:
        lines.append(f"runtime_artifacts: {len(recent_artifacts)}")
        for item in recent_artifacts[:3]:
            lines.append(
                f"artifact::{item.get('artifact_kind', '')}::{item.get('title', '') or item.get('path', '')}"
            )
    if recent_runs:
        lines.append(f"recent_runs: {len(recent_runs)}")
        for item in recent_runs[:3]:
            lines.append(
                f"run::{item.get('run_kind', '')}::{item.get('status', '')}::{item.get('title', '') or item.get('run_id', '')}"
            )
    if memory and (memory.get("entry_count") or memory.get("kairos_enabled") or memory.get("last_dream_at")):
        lines.append(f"memory_entries: {memory.get('entry_count', 0)}")
        if memory.get("kairos_enabled"):
            lines.append("kairos: enabled")
        if memory.get("last_dream_at"):
            lines.append(f"last_dream_at: {_trim_text(str(memory.get('last_dream_at') or ''), 80)}")
        for item in (memory.get("relevant_entries") or [])[:3]:
            lines.append(
                f"memory::{item.get('type', 'project')}::{item.get('name', '')}::{_trim_text(item.get('description') or item.get('snippet', ''), 100)}"
            )
    return "\n".join(lines)


from common.agent_prompt import render_team_skill_context


def render_group_context(history: list[BaseMessage], *, memberships: list[dict] | None = None) -> str:
    """Live membership is separate from this turn's source channel and old history."""
    current = next((m for m in reversed(history) if isinstance(m, HumanMessage)), None)
    groups = current.additional_kwargs.get("framework_groups", []) if current else []
    from common.conversation_context import normalize_group_metadata, render_group_metadata, current_group_metadata
    groups = normalize_group_metadata(groups)
    metadata = render_group_metadata(groups)
    membership_block = ("【所属群聊 / 私聊】\n以下是当前完整群归属，替代历史列表；移出或删除的群不再属于你。群号用于明确选择发送目标。\n"
                        + (render_group_metadata(memberships) or "无。")) if memberships is not None else ""
    if not metadata:
        return membership_block + "\n【当前群聊 / 私聊】\n无。本轮不使用历史群身份或回复通道。"
    if memberships is not None:
        groups = current_group_metadata(groups, memberships)
        metadata = render_group_metadata(groups) or "来源群已退出或删除，不得向该群发送。"
    return membership_block + "\n【当前群聊 / 私聊】\n以下仅说明本轮消息来源，不改变所属群列表；按各消息 group_id 回复。\n" + metadata


def strip_legacy_skills_from_system_prompt(prompt: str) -> str:
    """Remove the old frozen catalog from existing session prompts at read time."""
    soul_start = prompt.find(_SOUL_HEADING)
    search_end = soul_start if soul_start >= 0 else len(prompt)
    start = prompt.rfind(_LEGACY_SKILLS_HEADING, 0, search_end)
    if start < 0:
        return prompt
    catalog = prompt[start + len(_LEGACY_SKILLS_HEADING):search_end]
    if not any(label in catalog for label in ("团队「", "个人技能：", "可用技能：", "当前暂无已注册条目。")):
        return prompt
    return prompt[:start].rstrip() + (prompt[soul_start:] if soul_start >= 0 else "")


def _runtime_state_change(previous: str | None, current: str) -> str:
    """Render an initial snapshot or only the lines changed since the last call."""
    if previous is None:
        return current
    if previous == current:
        return ""
    old_lines = previous.splitlines()
    new_lines = current.splitlines()
    changes: list[str] = []
    for operation, old_start, old_end, new_start, new_end in SequenceMatcher(
        None, old_lines, new_lines, autojunk=False,
    ).get_opcodes():
        if operation in {"replace", "delete"}:
            changes.extend(f"- {line}" for line in old_lines[old_start:old_end])
        if operation in {"replace", "insert"}:
            changes.extend(f"+ {line}" for line in new_lines[new_start:new_end])
    return "【Runtime Context Update】\n" + "\n".join(changes)


def _append_runtime_delta(message: BaseMessage, delta: str) -> BaseMessage:
    state_text = f"\n\n---\n[系统状态]\n{delta}"
    if isinstance(message.content, list):
        content: Any = list(message.content) + [{"type": "text", "text": state_text}]
    else:
        content = f"{message.content}{state_text}"
    provider_kwargs = {
        key: value for key, value in message.additional_kwargs.items()
        if key not in {RUNTIME_STATE_KEY, RUNTIME_DELTA_KEY, "framework_groups"}
    }
    return message.model_copy(update={"content": content, "additional_kwargs": provider_kwargs})


def assemble_input_messages(
    *,
    base_prompt: str,
    history: list[BaseMessage],
    runtime_state: str,
    force_snapshot: bool = False,
) -> tuple[list[BaseMessage], str]:
    """Build the request sent to the model, keeping the cacheable prefix intact.

    Two invariants, both required for prompt/KV cache reuse:

    1. ``base_prompt`` is the whole system message. Runtime state never gets
       appended to it — the system message renders ahead of tools and history,
       so a per-turn edit there invalidates the entire prefix every call.
    2. Runtime state changes are attached to the last user query or tool result.
       The checkpoint keeps the injected delta in message metadata, so later
       model calls can replay it without changing user-visible message content.

    Returns the messages plus the state actually injected ("" when skipped).
    """
    # Reconstruct earlier injections from checkpoint metadata. The stored
    # content remains clean for history APIs and the frontend.
    visible: list[BaseMessage] = []
    previous: str | None = None
    for message in history:
        delta = message.additional_kwargs.get(RUNTIME_DELTA_KEY)
        if isinstance(delta, str) and delta and isinstance(message, (HumanMessage, ToolMessage)):
            snapshot = message.additional_kwargs.get(RUNTIME_STATE_KEY)
            # Compaction can remove the initial full snapshot while retaining
            # later patches. Rebase the first retained transition onto its
            # complete snapshot so patches never refer to an invisible base.
            replay = snapshot if previous is None and isinstance(snapshot, str) else delta
            visible.append(_append_runtime_delta(message, replay))
            if isinstance(snapshot, str):
                previous = snapshot
        else:
            provider_kwargs = {key: value for key, value in message.additional_kwargs.items()
                               if key not in {RUNTIME_STATE_KEY, RUNTIME_DELTA_KEY, "framework_groups"}}
            visible.append(message.model_copy(update={"additional_kwargs": provider_kwargs}))

    messages: list[BaseMessage] = [SystemMessage(content=base_prompt)] + visible
    if not history or not isinstance(history[-1], (HumanMessage, ToolMessage)):
        return messages, ""

    delta = _runtime_state_change(None if force_snapshot else previous, runtime_state)
    if delta:
        messages[-1] = _append_runtime_delta(messages[-1], delta)
    return messages, delta
