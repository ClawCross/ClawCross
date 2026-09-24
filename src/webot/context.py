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




def render_runtime_context_block(
    *,
    workspace: str = "",
    mode: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
    todos: dict[str, Any] | None = None,
    verifications: list[dict[str, Any]] | None = None,
    pending_approvals: list[dict[str, Any]] | None = None,
    inbox: list[dict[str, Any]] | None = None,
    recent_artifacts: list[dict[str, Any]] | None = None,
    recent_runs: list[dict[str, Any]] | None = None,
    memory: dict[str, Any] | None = None,
    bridge: dict[str, Any] | None = None,
    voice: dict[str, Any] | None = None,
    buddy: dict[str, Any] | None = None,
) -> str:
    # workspace is optional: it is fixed per session, so the caller carries it
    # in the stable system prompt rather than re-sending it here every turn.
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


def assemble_input_messages(
    *,
    base_prompt: str,
    history: list[BaseMessage],
    runtime_state: str,
    last_sent_state: str = "",
) -> tuple[list[BaseMessage], str]:
    """Build the request sent to the model, keeping the cacheable prefix intact.

    Two invariants, both required for prompt/KV cache reuse:

    1. ``base_prompt`` is the whole system message. Runtime state never gets
       appended to it — the system message renders ahead of tools and history,
       so a per-turn edit there invalidates the entire prefix every call.
    2. Runtime state rides at the tail, and only when it changed. It is sent
       but never written back to history, so a request carrying it produces a
       cache entry ending in content the next request no longer has — written,
       never read. Re-sending unchanged state buys nothing and costs every
       later hit, so the tool rounds in between end on stored messages instead.

    Returns the messages plus the state actually injected ("" when skipped).
    """
    messages: list[BaseMessage] = [SystemMessage(content=base_prompt)] + list(history)
    if not runtime_state or not history:
        return messages, ""

    last_msg = history[-1]
    if isinstance(last_msg, HumanMessage):
        # 本轮首调：并进用户这条消息，模型每轮至少拿到一次当前状态。
        # 必须排在用户原文**之后**：这条消息落库时不含状态块，所以本轮之后的每次
        # 请求看到的都是没有状态块的原文。状态块放前面，分歧点就落在这条消息的开头，
        # 整段用户输入在后续调用里全部重算；放后面，分歧点在末尾，用户输入仍在公共
        # 前缀里。@file/@diff 展开后单条输入可达 24000 字符，这个差别不小。
        state_text = f"\n\n---\n[系统状态]\n{runtime_state}"
        if isinstance(last_msg.content, list):
            content: Any = list(last_msg.content) + [{"type": "text", "text": state_text}]
        else:
            content = f"{last_msg.content}{state_text}"
        return (
            [SystemMessage(content=base_prompt)] + list(history[:-1]) + [HumanMessage(content=content)],
            runtime_state,
        )

    if isinstance(last_msg, ToolMessage) and runtime_state != last_sent_state:
        # 工具回合：追加在全部 tool_result 之后，不破坏 tool_calls → ToolMessage
        # 配对（provider 会把相邻的 tool/user 合并进同一个 user turn）。
        return (
            [SystemMessage(content=base_prompt)]
            + list(history)
            + [HumanMessage(content=f"[系统状态]\n{runtime_state}")],
            runtime_state,
        )

    # 状态没变，或以 AIMessage 收尾（正常循环下不可达）：让请求以落库消息结尾。
    return messages, ""
