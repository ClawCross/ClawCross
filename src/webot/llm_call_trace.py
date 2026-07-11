"""
Per-call LLM trace — capture every individual LLM invocation.

A default-OFF tracing mode (sibling to webot/trajectory.py). Unlike trajectory saving
(one entry per *completed conversation*), this records ONE entry per *LLM call*:
user/session, the full input messages, the output, and the real API-returned token usage
(including cache fields). Intended for token/IO analysis and comparison, not for normal
operation — enable with CLAWCROSS_LLM_CALL_TRACE=1.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.runtime_paths import DATA_DIR as RUNTIME_DATA_DIR

DATA_DIR = RUNTIME_DATA_DIR / "llm_call_traces"
DEFAULT_MAX_BYTES = 20 * 1024 * 1024

_write_lock = threading.Lock()


def llm_call_trace_enabled() -> bool:
    """Whether each LLM call should be traced. OFF unless explicitly enabled."""
    value = os.getenv("CLAWCROSS_LLM_CALL_TRACE", "false")
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def llm_call_trace_max_bytes() -> int:
    value = os.getenv("CLAWCROSS_LLM_CALL_TRACE_MAX_BYTES", "").strip()
    if not value:
        return DEFAULT_MAX_BYTES
    try:
        return max(0, int(value))
    except ValueError:
        return DEFAULT_MAX_BYTES


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
        data = data[newline_index + 1:]
    path.write_bytes(data)


def _ensure_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def llm_call_trace_max_content() -> int:
    """Per-field content cap (chars). 0 / empty = unlimited (store full text).

    Default is unlimited: the prompt (system prompt etc.) is just text and worth keeping in
    full for analysis; the file-level max-bytes cap bounds total size. Set
    CLAWCROSS_LLM_CALL_TRACE_MAX_CONTENT to a positive number to cap each field instead.
    """
    value = os.getenv("CLAWCROSS_LLM_CALL_TRACE_MAX_CONTENT", "").strip()
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        return 0


def _truncate(value: Any) -> Any:
    limit = llm_call_trace_max_content()
    if limit and isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"…(+{len(value) - limit} chars)"
    return value


def _normalize_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Keep the real API-returned token fields (incl. cache), tolerating both shapes."""
    if not isinstance(usage, dict) or not usage:
        return {}
    out = {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }
    details = usage.get("input_token_details")
    if isinstance(details, dict):
        out["cache_read"] = details.get("cache_read")
        out["cache_creation"] = details.get("cache_creation")
    else:
        out["cache_read"] = usage.get("cache_read_input_tokens")
        out["cache_creation"] = usage.get("cache_creation_input_tokens")
    return {k: v for k, v in out.items() if v is not None}


def save_llm_call(
    *,
    user_id: str,
    session_id: str,
    model: str = "",
    input_messages: list[dict[str, Any]] | None = None,
    output: str = "",
    tool_calls: list[Any] | None = None,
    token_usage: dict[str, Any] | None = None,
    turn: int = 0,
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Append one LLM-call record to data/llm_call_traces/llm_calls.jsonl."""
    _ensure_dir()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "session_id": session_id,
        "model": model or "unknown",
        "turn": turn,
        "input": [{"role": m.get("role"), "content": _truncate(m.get("content", ""))}
                  for m in (input_messages or [])],
        "output": _truncate(output),
        "tool_calls": tool_calls or [],
        "token_usage": _normalize_usage(token_usage),
    }
    if metadata:
        entry["metadata"] = metadata

    path = DATA_DIR / "llm_calls.jsonl"
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    line_bytes = line.encode("utf-8")
    with _write_lock:
        _trim_jsonl_to_fit(path, len(line_bytes), llm_call_trace_max_bytes())
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    return path
