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
