"""Local-DB session/history adapter for ClawCross CLI.

Reads the SQLite files written by ``src/utils/external_agent_history.py``
directly, bypassing the acpx subprocess. This gives the CLI a uniform
way to list sessions and replay history for every external agent
(openclaw, codex, claude, gemini, ...) without depending on the backend
service or the acpx binary being healthy.

DB layout (mirrored from ExternalAgentHistoryStore):

    ~/.clawcross/data/external_agent_history/<platform>#<sanitized_session_key>.db

Each DB has two tables: ``session_meta`` (1 row) and ``messages``
(append-only stream with ``direction`` in {send,recv,tool_call,
tool_result,error}).

This module exposes the two functions the CLI needs:

* :func:`list_history_sessions` — every session DB for a platform
* :func:`fetch_history_messages` — the tail of one session's messages
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path


# Same regex the backend uses to sanitize path segments — keep in sync with
# ``src/utils/external_agent_history.py``'s _PATH_UNSAFE_RE so the file
# names CLI computes match the ones the backend wrote.
_PATH_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]+')


def _sanitize(value: str | None, fallback: str = "default") -> str:
    raw = (value or "").strip() or fallback
    cleaned = _PATH_UNSAFE_RE.sub("_", raw).strip(" .") or fallback
    return cleaned[:128]


def _history_dir() -> Path:
    home = Path(os.environ.get("CLAWCROSS_HOME", Path.home() / ".clawcross"))
    return home / "data" / "external_agent_history"


def _db_path_for(platform: str, session_id: str) -> Path:
    return _history_dir() / f"{_sanitize(platform, 'unknown')}#{_sanitize(session_id, '__default__')}.db"


def _connect_ro(path: Path) -> sqlite3.Connection:
    """Open the DB for SELECT-only access.

    NOTE: We deliberately *don't* use ``?mode=ro`` — that mode skips the
    WAL file, which hides any rows committed since the last checkpoint.
    SQLite's WAL allows concurrent readers without blocking the writer,
    so opening RW and running only SELECT is safe.
    """
    conn = sqlite3.connect(str(path), timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def list_history_sessions(platform: str) -> tuple[list[dict], str | None]:
    """Return every session DB whose filename starts with ``<platform>#``.

    The returned ``session`` field is the original (un-sanitized) key as
    stored in ``session_meta.session_key`` — that's what the chat payload
    expects, so callers can use it verbatim.
    """
    root = _history_dir()
    if not root.is_dir():
        return [], None
    prefix = f"{_sanitize(platform, 'unknown')}#"
    out: list[dict] = []
    for path in root.glob("*.db"):
        if not path.name.startswith(prefix):
            continue
        try:
            with _connect_ro(path) as conn:
                meta = conn.execute(
                    "SELECT session_key, last_used_at FROM session_meta LIMIT 1"
                ).fetchone()
                if not meta:
                    continue
                count_row = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()
                msg_count = int(count_row["c"]) if count_row else 0
                first_user = conn.execute(
                    "SELECT content FROM messages WHERE direction='send' "
                    "ORDER BY rowid ASC LIMIT 1"
                ).fetchone()
        except sqlite3.OperationalError:
            # Missing table / corrupt — skip silently.
            continue
        except sqlite3.DatabaseError:
            continue
        title = (first_user["content"] or "")[:120] if first_user else ""
        out.append({
            "session": meta["session_key"] or "",
            "title": title,
            "message_count": msg_count,
            "last_ts": meta["last_used_at"],
        })
    out.sort(key=lambda d: d.get("last_ts") or 0, reverse=True)
    return out, None


def fetch_history_messages(
    platform: str,
    session_id: str,
    *,
    limit: int = 10,
) -> tuple[list[dict], str | None]:
    """Return up to ``limit`` of the most-recent message rows, oldest-first.

    Each row is normalized to ``{role, content, tool_name?}`` so the
    existing _print_history_tail renderer works unchanged.
    """
    path = _db_path_for(platform, session_id)
    if not path.exists():
        return [], None
    try:
        with _connect_ro(path) as conn:
            rows = list(conn.execute(
                "SELECT direction, role, content, meta_json FROM messages "
                "ORDER BY rowid DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall())
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return [], None
        return [], str(exc)
    except sqlite3.DatabaseError as exc:
        return [], str(exc)
    rows.reverse()
    out: list[dict] = []
    for row in rows:
        direction = (row["direction"] or "").lower()
        content = row["content"] or ""
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        if direction == "send":
            out.append({"role": "user", "content": content})
        elif direction == "recv":
            out.append({"role": "assistant", "content": content})
        elif direction in ("tool_call", "tool_result"):
            out.append({
                "role": "tool",
                "tool_name": meta.get("tool_name") or "",
                "content": content,
            })
        elif direction == "error":
            out.append({"role": "assistant", "content": f"[error] {content}"})
        else:
            out.append({"role": (row["role"] or "").lower() or "?", "content": content})
    return out, None
