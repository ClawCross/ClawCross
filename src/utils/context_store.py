"""Append-only conversation context persistence for the agent runtime."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

import aiosqlite
from langchain_core.messages import BaseMessage, messages_from_dict, messages_to_dict

from utils.checkpoint_paths import (
    candidate_checkpoint_db_paths_for_thread,
    checkpoint_db_path_for_thread,
)


_CREATE_CONTEXT_MESSAGES = """
CREATE TABLE IF NOT EXISTS context_messages (
    thread_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    message_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, sequence)
)
"""


def _encode_message(message: BaseMessage) -> str:
    payload = messages_to_dict([message])[0]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def _decode_message(raw: str) -> BaseMessage:
    return messages_from_dict([json.loads(raw)])[0]


def _messages_from_state_json(raw: str) -> list[BaseMessage]:
    """Read the temporary full-state format used by the first lightweight runtime."""
    try:
        payload = json.loads(raw)
        return list(messages_from_dict(payload.get("messages") or []))
    except Exception:
        return []


def _messages_from_langgraph_checkpoint(row: tuple[Any, Any] | None) -> list[BaseMessage]:
    """Best-effort import for installations that still have the old serializer."""
    if not row:
        return []
    try:
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

        checkpoint = JsonPlusSerializer().loads_typed((row[0], row[1]))
        values = checkpoint.get("channel_values") or {}
        return list(values.get("messages") or []) if isinstance(values, dict) else []
    except Exception:
        return []


class ContextStore:
    """Store only conversation messages; runtime state belongs outside this class."""

    def __init__(self, checkpoint_dir: str | Path) -> None:
        self.checkpoint_dir = Path(checkpoint_dir)
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        # One long-lived connection per resolved db file, reused across calls
        # (instead of opening/closing a fresh connection every read/write) —
        # cuts per-call connection overhead and lets WAL mode actually help.
        self._connections: dict[Path, aiosqlite.Connection] = {}
        self._connections_guard = asyncio.Lock()
        # CREATE TABLE IF NOT EXISTS is idempotent but not free — only run it
        # once per connection instead of on every append_messages() call.
        self._tables_ensured: set[Path] = set()

    async def __aenter__(self) -> "ContextStore":
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        return self

    async def __aexit__(self, *_: Any) -> None:
        async with self._connections_guard:
            conns = list(self._connections.values())
            self._connections.clear()
        for conn in conns:
            with contextlib.suppress(Exception):
                await conn.close()

    async def _lock_for(self, thread_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(thread_id, asyncio.Lock())

    async def _connection_for(self, path: Path) -> aiosqlite.Connection:
        resolved = path.resolve()
        conn = self._connections.get(resolved)
        if conn is not None:
            return conn
        async with self._connections_guard:
            conn = self._connections.get(resolved)
            if conn is not None:
                return conn
            resolved.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(resolved)
            # WAL: readers don't block the writer and vice versa; NORMAL sync
            # is the standard safe pairing with WAL (only fsyncs at WAL
            # checkpoints, not on every commit) — both are one-time per-file
            # pragmas, cheap to set on first open.
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            self._connections[resolved] = conn
            return conn

    async def load_context(self, thread_id: str) -> list[BaseMessage]:
        paths = candidate_checkpoint_db_paths_for_thread(self.checkpoint_dir, thread_id)
        if not paths:
            return []

        legacy_messages: list[BaseMessage] = []
        for path in paths:
            db = await self._connection_for(path)
            try:
                rows = await (await db.execute(
                    "SELECT message_json FROM context_messages "
                    "WHERE thread_id = ? ORDER BY sequence",
                    (thread_id,),
                )).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise
                rows = []
            if rows:
                return [_decode_message(row[0]) for row in rows]

            # One-time compatibility with the earlier full-state prototype.
            try:
                state_row = await (await db.execute(
                    "SELECT state_json FROM agent_state WHERE thread_id = ?",
                    (thread_id,),
                )).fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise
                state_row = None
            if state_row:
                legacy_messages = _messages_from_state_json(state_row[0])

            # One-time compatibility with the original LangGraph database.
            if not legacy_messages:
                try:
                    checkpoint_row = await (await db.execute(
                        "SELECT type, checkpoint FROM checkpoints "
                        "WHERE thread_id = ? ORDER BY ROWID DESC LIMIT 1",
                        (thread_id,),
                    )).fetchone()
                except sqlite3.OperationalError as exc:
                    if "no such table" not in str(exc).lower():
                        raise
                    checkpoint_row = None
                legacy_messages = _messages_from_langgraph_checkpoint(checkpoint_row)

            if legacy_messages:
                await self.append_messages(thread_id, legacy_messages)
                return legacy_messages
        return []

    async def append_messages(
        self,
        thread_id: str,
        messages: Iterable[BaseMessage],
    ) -> None:
        new_messages = list(messages)
        if not new_messages:
            return
        lock = await self._lock_for(thread_id)
        async with lock:
            path = checkpoint_db_path_for_thread(thread_id, self.checkpoint_dir)
            now = datetime.now(timezone.utc).isoformat()
            db = await self._connection_for(path)
            resolved = path.resolve()
            if resolved not in self._tables_ensured:
                await db.execute(_CREATE_CONTEXT_MESSAGES)
                self._tables_ensured.add(resolved)
            row = await (await db.execute(
                "SELECT COALESCE(MAX(sequence), -1) FROM context_messages WHERE thread_id = ?",
                (thread_id,),
            )).fetchone()
            start = int(row[0]) + 1
            await db.executemany(
                "INSERT INTO context_messages "
                "(thread_id, sequence, message_json, created_at) VALUES (?, ?, ?, ?)",
                [
                    (thread_id, start + offset, _encode_message(message), now)
                    for offset, message in enumerate(new_messages)
                ],
            )
            await db.commit()

    async def aclose_thread(self, thread_id: str) -> None:
        self._locks.pop(thread_id, None)
        path = checkpoint_db_path_for_thread(thread_id, self.checkpoint_dir).resolve()
        async with self._connections_guard:
            conn = self._connections.pop(path, None)
        self._tables_ensured.discard(path)
        if conn is not None:
            with contextlib.suppress(Exception):
                await conn.close()
