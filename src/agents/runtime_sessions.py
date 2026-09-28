"""Conversation state that external runtimes do not keep themselves.

An external agent reached over HTTP or ACP is sent ClawCross's identity prompt
on the first message of a session and again whenever that prompt changes. This
table remembers, per session key, which prompt the runtime has already seen.
It belongs to the drivers; nothing above the agent layer reads it.
"""

from __future__ import annotations

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runtime_sessions (
    session_key  TEXT PRIMARY KEY,
    global_name  TEXT NOT NULL DEFAULT '',
    prompt_text  TEXT NOT NULL DEFAULT '',
    transport    TEXT NOT NULL DEFAULT 'http',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    last_used_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_runtime_sessions_global_name ON agent_runtime_sessions(global_name);
"""

_COLUMNS = "session_key, global_name, prompt_text, transport, created_at, updated_at, last_used_at"


async def _open(db_path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA busy_timeout = 5000")
    await db.executescript(_SCHEMA)
    db.row_factory = aiosqlite.Row
    return db


async def remember_prompt(
    db_path: str, *, session_key: str, global_name: str, prompt_text: str, transport: str, now_ts: float,
) -> bool:
    """Record *prompt_text* for the session; True when it has to be (re)sent."""
    db = await _open(db_path)
    try:
        row = await (await db.execute(
            "SELECT prompt_text FROM agent_runtime_sessions WHERE session_key = ?", (session_key,),
        )).fetchone()
        if row is not None and (row["prompt_text"] or "") == prompt_text:
            return False
        await db.execute(
            "INSERT INTO agent_runtime_sessions (" + _COLUMNS + ") VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(session_key) DO UPDATE SET global_name = excluded.global_name,"
            " prompt_text = excluded.prompt_text, transport = excluded.transport,"
            " updated_at = excluded.updated_at, last_used_at = excluded.last_used_at",
            (session_key, global_name, prompt_text, transport, now_ts, now_ts, now_ts),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def get_session(db_path: str, session_key: str) -> dict | None:
    db = await _open(db_path)
    try:
        row = await (await db.execute(
            f"SELECT {_COLUMNS} FROM agent_runtime_sessions WHERE session_key = ?", (session_key,),
        )).fetchone()
    finally:
        await db.close()
    return dict(row) if row else None


async def list_sessions(db_path: str) -> list[dict]:
    db = await _open(db_path)
    try:
        rows = await (await db.execute(
            f"SELECT {_COLUMNS} FROM agent_runtime_sessions ORDER BY last_used_at DESC",
        )).fetchall()
    finally:
        await db.close()
    return [dict(row) for row in rows]


async def forget_session(db_path: str, session_key: str) -> int:
    db = await _open(db_path)
    try:
        cursor = await db.execute("DELETE FROM agent_runtime_sessions WHERE session_key = ?", (session_key,))
        await db.commit()
        return cursor.rowcount or 0
    finally:
        await db.close()


async def forget_agent(db_path: str, global_name: str) -> int:
    """Forget every session of one external agent (a reset or delete)."""
    db = await _open(db_path)
    try:
        cursor = await db.execute("DELETE FROM agent_runtime_sessions WHERE global_name = ?", (global_name,))
        await db.commit()
        return cursor.rowcount or 0
    finally:
        await db.close()
