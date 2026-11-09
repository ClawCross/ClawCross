"""
Trajectory saving — capture conversation traces for analysis and learning.

Ported from Hermes Agent's trajectory system:
- Save completed conversations to JSONL in ShareGPT format
- Separate success vs failure trajectories
- Enables post-hoc analysis of agent reasoning patterns
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.runtime_paths import DATA_DIR as RUNTIME_DATA_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = RUNTIME_DATA_DIR / "trajectories"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024

_write_lock = threading.Lock()


def auto_trajectory_enabled() -> bool:
    """Whether runtime agent turns should be auto-saved as trajectories."""
    value = os.getenv("CLAWCROSS_TRAJECTORY_ENABLED", "true")
    return value.strip().lower() not in {"0", "false", "no", "off"}


def trajectory_max_bytes() -> int:
    """Maximum bytes allowed per trajectory JSONL file."""
    value = os.getenv("CLAWCROSS_TRAJECTORY_MAX_BYTES", "").strip()
    if not value:
        return DEFAULT_MAX_BYTES
    try:
        parsed = int(value)
    except ValueError:
        return DEFAULT_MAX_BYTES
    return max(0, parsed)


def _trim_jsonl_to_fit(path: Path, incoming_bytes: int, max_bytes: int) -> None:
    if max_bytes <= 0 or incoming_bytes > max_bytes or not path.exists():
        return
    current_size = path.stat().st_size
    if current_size + incoming_bytes <= max_bytes:
        return

    keep_bytes = max_bytes - incoming_bytes
    if keep_bytes <= 0:
        path.write_text("", encoding="utf-8")
        return

    with path.open("rb") as f:
        if current_size > keep_bytes:
            f.seek(-keep_bytes, os.SEEK_END)
        data = f.read()

    newline_index = data.find(b"\n")
    if newline_index != -1:
        data = data[newline_index + 1 :]
    path.write_bytes(data)


def _ensure_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_trajectory(
    *,
    user_id: str,
    session_id: str,
    messages: list[dict[str, Any]],
    model: str = "",
    completed: bool = True,
    tool_calls_count: int = 0,
    token_usage: dict[str, int] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Append a conversation trajectory to the appropriate JSONL file.

    Args:
        user_id: User identifier
        session_id: Session identifier
        messages: ShareGPT-format conversation list
        model: Model name used
        completed: Whether the conversation completed successfully
        tool_calls_count: Total tool calls made
        token_usage: Token usage stats (input_tokens, output_tokens)
        metadata: Additional metadata

    Returns:
        Path to the JSONL file written to
    """
    _ensure_dir()

    # Convert messages to ShareGPT format if needed
    conversations = _normalize_messages(messages)

    entry = {
        "conversations": conversations,
        "timestamp": _utc_now_iso(),
        "user_id": user_id,
        "session_id": session_id,
        "model": model or "unknown",
        "completed": completed,
        "tool_calls_count": tool_calls_count,
        "message_count": len(conversations),
    }
    if token_usage:
        entry["token_usage"] = token_usage
    if metadata:
        entry["metadata"] = metadata

    filename = "trajectory_samples.jsonl" if completed else "failed_trajectories.jsonl"
    path = DATA_DIR / filename

    line = json.dumps(entry, ensure_ascii=False) + "\n"
    line_bytes = line.encode("utf-8")
    with _write_lock:
        _trim_jsonl_to_fit(path, len(line_bytes), trajectory_max_bytes())
        with path.open("a", encoding="utf-8") as f:
            f.write(line)

    if not completed:
        try:
            from webot.skill_evolution import record_failure_feedback

            record_failure_feedback(user_id=user_id, session_id=session_id, entry=entry)
        except Exception:
            pass

    return path


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalize messages to ShareGPT format [{from, value}]."""
    conversations: list[dict[str, str]] = []

    role_map = {
        "system": "system",
        "human": "human",
        "user": "human",
        "ai": "gpt",
        "assistant": "gpt",
        "tool": "tool",
    }

    for msg in messages:
        role = msg.get("role") or msg.get("type") or "unknown"
        content = msg.get("content") or ""
        if isinstance(content, list):
            # Flatten multipart content
            parts = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
            content = "\n".join(parts)

        mapped_role = role_map.get(role.lower(), role)

        # Include tool call info
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            tool_names = [tc.get("name", "?") for tc in tool_calls if isinstance(tc, dict)]
            if tool_names:
                content += f"\n[Tool calls: {', '.join(tool_names)}]"

        conversations.append({
            "from": mapped_role,
            "value": str(content)[:10000],  # Cap individual message size
        })

    return conversations


def list_trajectories(
    *,
    completed: bool | None = None,
    limit: int = 50,
    user_id: str = "",
) -> list[dict[str, Any]]:
    """List recent trajectories from JSONL files.

    Args:
        completed: Filter by completion status (None = all)
        limit: Max entries to return
        user_id: Filter by user ID (empty = all)

    Returns:
        List of trajectory entries (most recent first)
    """
    _ensure_dir()
    entries: list[dict[str, Any]] = []

    files_to_read = []
    if completed is None or completed:
        files_to_read.append(DATA_DIR / "trajectory_samples.jsonl")
    if completed is None or not completed:
        files_to_read.append(DATA_DIR / "failed_trajectories.jsonl")

    for path in files_to_read:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if user_id and entry.get("user_id") != user_id:
                    continue
                entries.append(entry)
            except json.JSONDecodeError:
                continue

    # Sort by timestamp descending
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries[:limit]


def get_trajectory_stats(user_id: str = "", days: int = 30) -> dict[str, Any]:
    """Get trajectory statistics.

    Returns:
        Dict with success_count, failure_count, total_tool_calls,
        avg_message_count, model_breakdown, etc.
    """
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    entries = list_trajectories(user_id=user_id, limit=10000)
    recent = [e for e in entries if e.get("timestamp", "") >= cutoff]

    if not recent:
        return {
            "total": 0,
            "success_count": 0,
            "failure_count": 0,
            "period_days": days,
        }

    success = [e for e in recent if e.get("completed")]
    failed = [e for e in recent if not e.get("completed")]
    total_tools = sum(e.get("tool_calls_count", 0) for e in recent)
    avg_messages = sum(e.get("message_count", 0) for e in recent) / len(recent)

    model_counts: dict[str, int] = {}
    for e in recent:
        m = e.get("model", "unknown")
        model_counts[m] = model_counts.get(m, 0) + 1

    return {
        "total": len(recent),
        "success_count": len(success),
        "failure_count": len(failed),
        "success_rate": f"{len(success) / len(recent) * 100:.1f}%",
        "total_tool_calls": total_tools,
        "avg_tool_calls_per_session": round(total_tools / len(recent), 1),
        "avg_message_count": round(avg_messages, 1),
        "model_breakdown": model_counts,
        "period_days": days,
    }
