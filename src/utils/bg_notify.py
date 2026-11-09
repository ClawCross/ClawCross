"""Background-job completion notifier (event-driven, decoupled from commander).

Why this module exists
----------------------
The commander MCP server runs as a *per-tool-call* stdio subprocess
(``MultiServerMCPClient.get_tools`` opens a fresh session per call), so any
in-process watcher created inside ``start_background_command`` is killed the
moment the tool returns — long before the detached job finishes. The detached
runner is sandboxed and deliberately has no ``INTERNAL_TOKEN``, so it cannot
fire the wake-up itself either.

Design (no polling)
-------------------
  * commander registers a *trusted pointer* (``register_pending_notify``) mapping
    ``job_id -> meta_path`` when a job opts into ``notify_on_done``;
  * the detached runner, on reaching a terminal status, pushes a one-shot
    ``{"job_id": ...}`` to the main process endpoint ``/internal/bg_job_done``
    (no token needed — it only names a job id);
  * the main process (``mainagent``, which holds ``INTERNAL_TOKEN`` and serves
    ``/system_trigger``) resolves the pointer it itself trusts, loads the job
    meta, and wakes the originating session via ``deliver_by_job_id``.

Security: the push carries only a job id. The session to wake comes from the
job meta, whose path is taken from the commander-written pointer (never from the
request), so a loopback caller cannot inject an arbitrary wake. Delivery is
idempotent (``notified`` flag), and ``job_id`` is format-checked before any
filesystem lookup.

``reconcile_pending_once`` runs a single pass at startup (not a loop) to catch
jobs that finished while the main process was down.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

from utils.logging_utils import get_logger
from utils.runtime_paths import DATA_DIR

logger = get_logger("bg_notify")

# Central registry of jobs awaiting a completion notification (trusted pointers).
NOTIFY_DIR: Path = Path(DATA_DIR) / "bg_notify"

TERMINAL_JOB_STATUS = {"completed", "failed", "timeout", "cancelled"}

# job_id is uuid4().hex[:12]; allow a generous hex window and nothing else so a
# pushed id can never escape NOTIFY_DIR via path traversal.
_JOB_ID_RE = re.compile(r"^[a-f0-9]{6,32}$")

# Drop a stale pointer (during startup reconcile) if its job never reached a
# terminal status within this slack beyond its own timeout.
_STALE_SLACK_SEC = 120


def register_pending_notify(job_id: str, meta_path: str) -> None:
    """Record that ``job_id`` wants a completion notification (called by commander).

    Writes a trusted pointer file that persists after the short-lived commander
    process exits; it is the only source the main process trusts for resolving a
    pushed job id to its meta path.
    """
    try:
        NOTIFY_DIR.mkdir(parents=True, exist_ok=True)
        (NOTIFY_DIR / f"{job_id}.json").write_text(
            json.dumps({"job_id": job_id, "meta_path": meta_path}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # never let notification bookkeeping break job launch
        logger.warning("register_pending_notify failed for %s: %s", job_id, exc)


def _pointer_path(job_id: str) -> Path | None:
    if not _JOB_ID_RE.match(job_id or ""):
        return None
    return NOTIFY_DIR / f"{job_id}.json"


def _read_json(path: str | Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _tail_text(path: str, limit: int = 1500) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")[-limit:]
    except Exception:
        return ""


def _is_stale(meta: dict) -> bool:
    try:
        started = float(meta.get("started_at") or 0)
        timeout = int(meta.get("timeout_seconds") or 0)
    except (TypeError, ValueError):
        return False
    return bool(started) and time.time() > started + timeout + _STALE_SLACK_SEC


async def _fire_trigger(meta: dict) -> bool:
    """Wake the originating session via /system_trigger. Returns True on success."""
    import os
    token = os.getenv("INTERNAL_TOKEN", "").strip()
    if not token:
        logger.warning("notify skipped (no INTERNAL_TOKEN) for job %s", meta.get("job_id"))
        return False
    url = f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}/system_trigger"
    text = (
        f"[后台任务完成] job {meta.get('job_id')} 状态={meta.get('status')} "
        f"退出码={meta.get('exit_code')}\n"
        f"命令: {str(meta.get('command') or '')[:200]}\n"
        f"输出尾部:\n{_tail_text(str(meta.get('stdout_path') or ''))}\n\n"
        f"（这是后台任务的完成回报。若需向群/用户汇报结果，请用 send_to_group 主动发送；"
        f"无需再重复启动该任务。）"
    )
    body = {
        "user_id": meta.get("username") or "",
        "session_id": meta.get("session_id") or "",
        "text": text,
        "coalesce_key": f"bgjob:{meta.get('session_id')}",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers={"X-Internal-Token": token}, json=body)
            resp.raise_for_status()
        logger.info("notify_on_done woke session %s (job %s %s)",
                    meta.get("session_id"), meta.get("job_id"), meta.get("status"))
        return True
    except Exception as exc:
        logger.warning("notify_on_done system_trigger failed for %s: %s",
                       meta.get("job_id"), exc)
        return False


async def deliver_by_job_id(job_id: str) -> bool:
    """Deliver the completion wake for ``job_id`` (event push or reconcile).

    Resolves the meta path only from the commander-written pointer (never from
    the caller), validates terminal status, fires once, and clears the pointer.
    Idempotent: a second call after success is a no-op. Returns True if a wake
    was delivered on this call.
    """
    ptr = _pointer_path(job_id)
    if ptr is None or not ptr.is_file():
        return False
    info = _read_json(ptr)
    meta_path = (info or {}).get("meta_path")
    if not meta_path:
        ptr.unlink(missing_ok=True)
        return False
    meta = _read_json(meta_path)
    if meta is None:
        ptr.unlink(missing_ok=True)
        return False
    if meta.get("status") not in TERMINAL_JOB_STATUS:
        if _is_stale(meta):
            logger.warning("dropping stale pending notify for job %s", job_id)
            ptr.unlink(missing_ok=True)
        return False
    if meta.get("notified"):
        ptr.unlink(missing_ok=True)
        return False
    if not meta.get("session_id"):
        ptr.unlink(missing_ok=True)  # no originating session — nothing to wake
        return False
    if not await _fire_trigger(meta):
        return False  # keep pointer for a later retry (e.g. next push / reconcile)
    meta["notified"] = True
    try:
        Path(meta_path).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    ptr.unlink(missing_ok=True)
    return True


async def reconcile_pending_once() -> int:
    """One-shot startup sweep (NOT a loop): deliver any jobs that reached a
    terminal status while the main process was down. Returns count delivered."""
    if not NOTIFY_DIR.is_dir():
        return 0
    delivered = 0
    for ptr in NOTIFY_DIR.glob("*.json"):
        job_id = ptr.stem
        try:
            if await deliver_by_job_id(job_id):
                delivered += 1
        except Exception as exc:
            logger.warning("reconcile delivery error for %s: %s", job_id, exc)
    if delivered:
        logger.info("startup reconcile delivered %d pending notification(s)", delivered)
    return delivered
