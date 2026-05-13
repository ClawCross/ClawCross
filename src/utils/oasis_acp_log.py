"""OASIS-only ACP timing log.

Writes to ``logs/oasis_acp_timing.log``. The contextvar gate means non-OASIS
callers (e.g. main-page group chat) do not emit anything, so this trace stays
isolated to OASIS workflow calls.

Usage::

    from utils.oasis_acp_log import begin_trace, end_trace, mark

    tid = begin_trace("call_api", session=acp_session_key)
    try:
        mark("send_to_agent.enter")
        result = await send_to_agent(...)
        mark("send_to_agent.exit", ok=result.ok)
    finally:
        end_trace()
"""
from __future__ import annotations

import contextvars
import logging
import os
import time
import uuid

from utils.runtime_paths import LOGS_DIR


_TRACE_FILE = os.path.join(str(LOGS_DIR), "oasis_acp_timing.log")
_trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "oasis_acp_trace_id", default=""
)
_t0_ctx: contextvars.ContextVar[float] = contextvars.ContextVar(
    "oasis_acp_t0", default=0.0
)

_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    os.makedirs(str(LOGS_DIR), exist_ok=True)
    lg = logging.getLogger("oasis.acp.timing")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    already = any(
        isinstance(h, logging.FileHandler)
        and getattr(h, "baseFilename", "") == _TRACE_FILE
        for h in lg.handlers
    )
    if not already:
        handler = logging.FileHandler(_TRACE_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        lg.addHandler(handler)
    _logger = lg
    return lg


def _kv(kv: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in kv.items() if v is not None)


def begin_trace(label: str = "", **kv) -> str:
    """Start a new OASIS ACP trace bound to the current asyncio task."""
    tid = uuid.uuid4().hex[:12]
    _trace_id_ctx.set(tid)
    _t0_ctx.set(time.monotonic())
    _get_logger().info(f"[{tid}] +0.000s BEGIN {label} {_kv(kv)}".rstrip())
    return tid


def mark(event: str, **kv) -> None:
    """Record an event under the current trace. No-op outside a trace."""
    tid = _trace_id_ctx.get("")
    if not tid:
        return
    elapsed = time.monotonic() - _t0_ctx.get(0.0)
    _get_logger().info(f"[{tid}] +{elapsed:.3f}s {event} {_kv(kv)}".rstrip())


def end_trace(label: str = "", **kv) -> None:
    tid = _trace_id_ctx.get("")
    if not tid:
        return
    elapsed = time.monotonic() - _t0_ctx.get(0.0)
    _get_logger().info(f"[{tid}] +{elapsed:.3f}s END {label} {_kv(kv)}".rstrip())
    _trace_id_ctx.set("")


def current_trace_id() -> str:
    return _trace_id_ctx.get("")


def trace_file_path() -> str:
    return _TRACE_FILE
