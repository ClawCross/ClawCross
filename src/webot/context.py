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
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from utils.checkpoint_repository import (
    ContextCompactionRecord,
    get_context_compaction,
    save_context_compaction,
)
from webot.runtime_store import create_runtime_artifact


PROJECT_ROOT = Path(__file__).resolve().parents[2]
from utils.runtime_paths import USER_FILES_DIR

DEFAULT_TOOL_RESULT_CHAR_BUDGET = 12000
DEFAULT_TOOL_RESULT_ITEM_LIMIT = 1600
DEFAULT_USER_INPUT_CHAR_BUDGET = 131072
DEFAULT_USER_INPUT_ITEM_LIMIT = 10000
DEFAULT_CONTEXT_TOKEN_BUDGET = 12000
DEFAULT_RECENT_MESSAGE_COUNT = 10
DEFAULT_MAX_HISTORY_MESSAGES = 28
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


def budget_user_messages(
    *,
    user_id: str,
    session_id: str,
    messages: list[BaseMessage],
    total_char_budget: int | None = None,
    item_char_limit: int | None = None,
    preserve_latest_human_messages: int | None = None,
) -> list[BaseMessage]:
    resolved_total_budget = _resolve_user_input_char_budget() if total_char_budget is None else total_char_budget
    resolved_item_limit = _resolve_user_input_item_limit() if item_char_limit is None else item_char_limit
    preserve_latest_count = (
        _resolve_latest_human_message_preserve_count()
        if preserve_latest_human_messages is None
        else max(0, preserve_latest_human_messages)
    )

    preserved_indexes: set[int] = set()
    if preserve_latest_count > 0:
        remaining_to_preserve = preserve_latest_count
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, HumanMessage) and isinstance(message.content, str):
                preserved_indexes.add(index)
                remaining_to_preserve -= 1
                if remaining_to_preserve <= 0:
                    break

    remaining_budget: int | None
    if resolved_total_budget <= 0:
        remaining_budget = None
    else:
        remaining_budget = max(0, resolved_total_budget)
    budgeted: list[BaseMessage] = []
    for index, message in enumerate(messages):
        if not isinstance(message, HumanMessage) or not isinstance(message.content, str):
            budgeted.append(message)
            continue

        if index in preserved_indexes:
            budgeted.append(message)
            continue

        raw_text = message.content
        within_item_limit = resolved_item_limit <= 0 or len(raw_text) <= resolved_item_limit
        within_total_budget = remaining_budget is None or len(raw_text) <= remaining_budget
        keep_inline = within_item_limit and within_total_budget
        if keep_inline:
            if remaining_budget is not None:
                remaining_budget = max(0, remaining_budget - len(raw_text))
            budgeted.append(message)
            continue

        excerpt_limit = min(resolved_item_limit, 700) if resolved_item_limit > 0 else 700
        excerpt = _trim_text(raw_text, excerpt_limit)
        stored_path: str | None = None
        if _runtime_artifacts_enabled():
            path_obj = _store_runtime_text(
                user_id=user_id,
                session_id=session_id,
                bucket="webot_user_inputs",
                prefix=f"user-{index + 1}",
                content=raw_text,
            )
            stored_path = str(path_obj)
            create_runtime_artifact(
                user_id=user_id,
                session_id=session_id,
                kind="user_input",
                title=f"user_message_{index + 1}",
                path=stored_path,
                summary=_trim_text(raw_text, 220),
                metadata={"message_index": index},
            )
        budgeted.append(
            HumanMessage(
                content=(
                    "[User input budgeted]\n"
                    + (f"saved_to={stored_path}\n\n" if stored_path else "")
                    + f"{excerpt}"
                )
            )
        )
        if remaining_budget is not None:
            budget_cost = min(len(excerpt), resolved_item_limit) if resolved_item_limit > 0 else len(excerpt)
            remaining_budget = max(0, remaining_budget - budget_cost)
    return budgeted


def budget_tool_messages(
    *,
    user_id: str,
    session_id: str,
    messages: list[BaseMessage],
    total_char_budget: int = DEFAULT_TOOL_RESULT_CHAR_BUDGET,
    item_char_limit: int = DEFAULT_TOOL_RESULT_ITEM_LIMIT,
) -> list[BaseMessage]:
    remaining_budget = max(0, total_char_budget)
    budgeted: list[BaseMessage] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            budgeted.append(message)
            continue
        if _content_has_image_block(message.content):
            budgeted.append(message)
            continue

        raw_text = _stringify(message.content)
        keep_inline = len(raw_text) <= item_char_limit and len(raw_text) <= remaining_budget
        if keep_inline:
            remaining_budget -= len(raw_text)
            budgeted.append(message)
            continue

        tool_name = getattr(message, "name", "") or "tool"
        tool_call_id = getattr(message, "tool_call_id", "")
        excerpt = _trim_text(raw_text, min(item_char_limit, 600))
        stored_path: str | None = None
        if _runtime_artifacts_enabled():
            path_obj = _store_runtime_text(
                user_id=user_id,
                session_id=session_id,
                bucket="webot_tool_results",
                prefix=f"{tool_name}-{tool_call_id or 'result'}",
                content=raw_text,
            )
            stored_path = str(path_obj)
            create_runtime_artifact(
                user_id=user_id,
                session_id=session_id,
                kind="tool_result",
                title=tool_name,
                path=stored_path,
                summary=_trim_text(raw_text, 220),
                metadata={"tool_call_id": tool_call_id},
            )
        replacement = (
            f"[Tool result budgeted]\n"
            f"tool={tool_name}\n"
            + (f"saved_to={stored_path}\n\n" if stored_path else "")
            + f"{excerpt}"
        )
        budgeted.append(
            ToolMessage(
                content=replacement,
                tool_call_id=tool_call_id,
                name=tool_name,
            )
        )
        remaining_budget = max(0, remaining_budget - min(len(excerpt), item_char_limit))
    return budgeted


def _message_summary_line(message: BaseMessage, limit: int = 280) -> str:
    role = "assistant"
    if isinstance(message, HumanMessage):
        role = "user"
    elif isinstance(message, ToolMessage):
        role = f"tool:{getattr(message, 'name', '') or 'unknown'}"
    elif isinstance(message, SystemMessage):
        role = "system"
    text = _trim_text(_stringify(message.content).replace("\n", " "), limit)
    return f"- {role}: {text}"


_COMPACTION_SUMMARY_PREFIX = "以下为早期对话的压缩摘要"


def _message_has_tool_calls(message: BaseMessage) -> bool:
    if not isinstance(message, AIMessage):
        return False
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        return True
    content = getattr(message, "content", None)
    if isinstance(content, list):
        return any(isinstance(part, dict) and part.get("type") == "tool_use" for part in content)
    return False


def find_safe_compaction_boundary(messages: list[BaseMessage], desired_until: int) -> int:
    """Return a boundary that does not leave a ToolMessage orphaned at tail start."""
    if not messages:
        return 0
    boundary = min(max(0, desired_until), len(messages))
    if boundary <= 0 or boundary >= len(messages):
        return boundary

    # If the tail would start with ToolMessage(s), move the whole AI/tool block
    # back into the tail. The summarized prefix is no longer sent as tool calls,
    # so the live tail must be sequence-valid on its own.
    if isinstance(messages[boundary], ToolMessage):
        while boundary > 0 and isinstance(messages[boundary], ToolMessage):
            boundary -= 1
        if boundary > 0 and _message_has_tool_calls(messages[boundary - 1]):
            boundary -= 1
    return max(0, boundary)


def _latest_human_index(messages: list[BaseMessage]) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return index
    return len(messages)


def _estimate_messages(messages: list[BaseMessage]) -> int:
    return sum(approximate_token_count(_stringify(msg.content)) for msg in messages)


def _valid_compaction_record(
    record: ContextCompactionRecord | None,
    messages: list[BaseMessage],
) -> ContextCompactionRecord | None:
    if record is None:
        return None
    if not record.summary.strip():
        return None
    if record.compacted_until <= 0:
        return None
    if record.compacted_until > len(messages):
        return None
    return record


def _summary_message(summary: str) -> HumanMessage:
    text = summary.strip()
    if not text.startswith(_COMPACTION_SUMMARY_PREFIX):
        text = f"{_COMPACTION_SUMMARY_PREFIX}，仅保留任务关键上下文、已做尝试和结论：\n{text}"
    return HumanMessage(content=text)


def _build_incremental_summary(
    *,
    previous_summary: str,
    segment: list[BaseMessage],
    max_lines: int,
) -> str:
    lines = [
        "以下为早期对话的压缩摘要，仅保留任务关键上下文、已做尝试和结论：",
    ]
    previous = previous_summary.strip()
    if previous:
        previous_body = previous
        if previous_body.startswith(_COMPACTION_SUMMARY_PREFIX):
            previous_body = "\n".join(previous_body.splitlines()[1:]).strip()
        if previous_body:
            lines.append("- previous_summary: " + _trim_text(previous_body.replace("\n", " "), 1200))

    for message in segment[-max(4, max_lines):]:
        lines.append(_message_summary_line(message))
    return "\n".join(lines)


def _choose_compaction_boundary(
    messages: list[BaseMessage],
    *,
    preserve_recent: int,
    target_tokens: int,
) -> int:
    if len(messages) <= 2:
        return 0

    latest_human = _latest_human_index(messages)
    max_boundary = max(0, latest_human)
    boundary = min(max_boundary, max(0, len(messages) - max(1, preserve_recent)))
    if boundary <= 0:
        return 0

    while boundary < max_boundary and _estimate_messages(messages[boundary:]) > target_tokens:
        boundary += 1
    return find_safe_compaction_boundary(messages, boundary)


def apply_persistent_compaction(
    *,
    user_id: str,
    session_id: str,
    messages: list[BaseMessage],
    context_token_budget: int,
    preserve_recent: int,
    max_messages: int,
    checkpoint_store_path: str | os.PathLike | None = None,
) -> tuple[list[BaseMessage], dict[str, Any]]:
    """
    Replace already-compacted prefix history with a persistent summary.

    The original LangGraph checkpoint remains intact. This only shapes the
    message list sent to the model and persists a deterministic summary cursor
    so old history is not re-compacted on every turn.
    """
    info: dict[str, Any] = {
        "enabled": persistent_compaction_enabled(),
        "loaded": False,
        "updated": False,
        "compacted_until": 0,
        "reason": "disabled",
    }
    if not info["enabled"] or not user_id or not session_id or not messages:
        return messages, info

    thread_id = f"{user_id}#{session_id}"
    trigger_ratio = _resolve_compaction_trigger_ratio()
    target_ratio = min(_resolve_compaction_target_ratio(), trigger_ratio)
    trigger_tokens = max(1, int(context_token_budget * trigger_ratio))
    target_tokens = max(1, int(context_token_budget * target_ratio))
    min_new_messages = _resolve_compaction_min_new_messages()

    record = _valid_compaction_record(
        get_context_compaction(checkpoint_store_path, thread_id),
        messages,
    )
    previous_until = record.compacted_until if record else 0
    previous_summary = record.summary if record else ""
    if record:
        info.update(
            {
                "loaded": True,
                "compacted_until": previous_until,
                "summary_tokens": record.summary_token_estimate,
                "reason": "loaded",
            }
        )

    current_view = (
        [_summary_message(previous_summary)] + messages[previous_until:]
        if record
        else list(messages)
    )
    current_tokens = _estimate_messages(current_view)
    if len(current_view) <= max_messages and current_tokens <= trigger_tokens:
        info.update({"tokens": current_tokens, "reason": "below_trigger"})
        return current_view, info

    boundary = _choose_compaction_boundary(
        messages,
        preserve_recent=preserve_recent,
        target_tokens=target_tokens,
    )
    new_message_count = max(0, boundary - previous_until)
    if boundary <= previous_until or (record and new_message_count < min_new_messages):
        info.update(
            {
                "tokens": current_tokens,
                "reason": "min_new_messages",
                "new_message_count": new_message_count,
            }
        )
        return current_view, info

    summary = _build_incremental_summary(
        previous_summary=previous_summary,
        segment=messages[previous_until:boundary],
        max_lines=max_messages,
    )
    summary_tokens = approximate_token_count(summary)
    saved = save_context_compaction(
        checkpoint_store_path,
        thread_id,
        summary=summary,
        compacted_until=boundary,
        source_message_count=len(messages),
        summary_token_estimate=summary_tokens,
        metadata={
            "trigger_tokens": trigger_tokens,
            "target_tokens": target_tokens,
            "preserve_recent": preserve_recent,
            "new_message_count": new_message_count,
        },
    )
    compacted_view = [_summary_message(saved.summary)] + messages[saved.compacted_until:]
    info.update(
        {
            "loaded": bool(record),
            "updated": True,
            "compacted_until": saved.compacted_until,
            "summary_tokens": saved.summary_token_estimate,
            "tokens": _estimate_messages(compacted_view),
            "reason": "updated",
            "new_message_count": new_message_count,
        }
    )
    return compacted_view, info


def compact_history_messages(
    messages: list[BaseMessage],
    *,
    max_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
    preserve_recent: int = DEFAULT_RECENT_MESSAGE_COUNT,
    context_token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
    user_id: str | None = None,
    session_id: str | None = None,
) -> list[BaseMessage]:
    if not messages:
        return messages

    def _estimated(messages_to_count: list[BaseMessage]) -> int:
        return sum(approximate_token_count(_stringify(msg.content)) for msg in messages_to_count)

    if len(messages) <= max_messages and _estimated(messages) <= context_token_budget:
        return messages

    recent_count = min(max(1, preserve_recent), len(messages))
    older = messages[:-recent_count]
    recent = messages[-recent_count:]
    if not older:
        return recent

    summary_lines = [
        "以下为早期对话的压缩摘要，仅保留任务关键上下文、已做尝试和结论：",
    ]
    for message in older[-max(4, max_messages):]:
        summary_lines.append(_message_summary_line(message))
    summary_text = "\n".join(summary_lines)
    if user_id and session_id and _runtime_artifacts_enabled():
        stored_path = _store_runtime_text(
            user_id=user_id,
            session_id=session_id,
            bucket="webot_compactions",
            prefix="compact-summary",
            content=summary_text,
        )
        create_runtime_artifact(
            user_id=user_id,
            session_id=session_id,
            kind="compact_summary",
            title="history_compaction",
            path=str(stored_path),
            summary=_trim_text(summary_text, 220),
            metadata={"older_message_count": len(older)},
        )
    summary = HumanMessage(content=summary_text)
    compacted = [summary] + recent

    while len(compacted) > max_messages and len(compacted) > 2:
        compacted = [summary] + compacted[-(max_messages - 1):]

    while _estimated(compacted) > context_token_budget and len(compacted) > 2:
        compacted = [summary] + compacted[-(len(compacted) - 2):]

    return compacted


def render_runtime_context_block(
    *,
    workspace: str,
    mode: dict[str, Any] | None,
    plan: dict[str, Any] | None,
    todos: dict[str, Any] | None,
    verifications: list[dict[str, Any]] | None,
    pending_approvals: list[dict[str, Any]] | None,
    inbox: list[dict[str, Any]] | None = None,
    recent_artifacts: list[dict[str, Any]] | None = None,
    recent_runs: list[dict[str, Any]] | None = None,
    memory: dict[str, Any] | None = None,
    bridge: dict[str, Any] | None = None,
    voice: dict[str, Any] | None = None,
    buddy: dict[str, Any] | None = None,
) -> str:
    lines = ["【Runtime Context】", f"workspace: {workspace}"]
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
    if inbox:
        lines.append(f"inbox_pending: {len(inbox)}")
        for item in inbox[:3]:
            sender = item.get("source_label") or item.get("source_session") or "unknown"
            lines.append(f"inbox::{sender}::{_trim_text(item.get('body', ''), 100)}")
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
    if memory:
        lines.append(f"memory_entries: {memory.get('entry_count', 0)}")
        if memory.get("kairos_enabled"):
            lines.append("kairos: enabled")
        if memory.get("last_dream_at"):
            lines.append(f"last_dream_at: {_trim_text(str(memory.get('last_dream_at') or ''), 80)}")
        for item in (memory.get("relevant_entries") or [])[:3]:
            lines.append(
                f"memory::{item.get('type', 'project')}::{item.get('name', '')}::{_trim_text(item.get('description') or item.get('snippet', ''), 100)}"
            )
    if bridge:
        lines.append(f"bridge_attached: {bool(bridge.get('attached', False))}")
        lines.append(f"bridge_clients: {bridge.get('connected_clients', 0)}")
        roles = bridge.get("roles") or []
        if roles:
            lines.append(f"bridge_roles: {', '.join(str(role) for role in roles)}")
    if voice:
        lines.append(f"voice_enabled: {bool(voice.get('enabled', False))}")
        if voice.get("tts_available"):
            lines.append(f"voice_tts: {voice.get('tts_model', '')}:{voice.get('tts_voice', '')}")
    if buddy:
        lines.append(
            f"buddy::{buddy.get('species', '')}::{buddy.get('rarity', '')}::{buddy.get('name') or buddy.get('soul', {}).get('name', '')}"
        )
        buddy_note = buddy.get("reaction") or buddy.get("last_bubble")
        if buddy_note:
            lines.append(f"buddy_note: {_trim_text(str(buddy_note or ''), 100)}")
    return "\n".join(lines)
