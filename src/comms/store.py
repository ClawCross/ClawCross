"""Conversations, their members and their messages.

A member or a sender is a principal: ``ag_…`` for an agent, ``u:<user>`` for a
human. Agent members reference the agents table, so deleting an agent takes it
out of every conversation.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from agents.store import AGENT_ID_PREFIX, AgentStore

HUMAN_PREFIX = "u:"
GROUP = "group"
DIRECT = "direct"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    conv_id       TEXT PRIMARY KEY,
    owner         TEXT NOT NULL,
    title         TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('group', 'direct')),
    primary_agent TEXT REFERENCES agents(agent_id) ON DELETE SET NULL,
    dnd           INTEGER NOT NULL DEFAULT 0,
    meta_json     TEXT NOT NULL DEFAULT '{}',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations(owner);
CREATE TABLE IF NOT EXISTS conversation_members (
    conv_id     TEXT NOT NULL REFERENCES conversations(conv_id) ON DELETE CASCADE,
    principal   TEXT NOT NULL,
    agent_id    TEXT REFERENCES agents(agent_id) ON DELETE CASCADE,
    nickname    TEXT NOT NULL DEFAULT '',
    muted       INTEGER NOT NULL DEFAULT 0,
    read_cursor INTEGER NOT NULL DEFAULT 0,
    joined_at   REAL NOT NULL,
    PRIMARY KEY (conv_id, principal)
);
CREATE INDEX IF NOT EXISTS idx_conversation_members_principal ON conversation_members(principal);
CREATE TABLE IF NOT EXISTS conversation_messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id          TEXT NOT NULL REFERENCES conversations(conv_id) ON DELETE CASCADE,
    sender           TEXT NOT NULL,
    content          TEXT NOT NULL,
    mentions_json    TEXT NOT NULL DEFAULT '[]',
    reply_to         INTEGER,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    client_msg_id    TEXT,
    created_at       REAL NOT NULL,
    UNIQUE (conv_id, sender, client_msg_id)
);
CREATE INDEX IF NOT EXISTS idx_conversation_messages_conv ON conversation_messages(conv_id, id);
"""


def human(user_id: str) -> str:
    return f"{HUMAN_PREFIX}{user_id}"


def is_agent(principal: str) -> bool:
    return (principal or "").startswith(AGENT_ID_PREFIX)


def new_conversation_id() -> str:
    return "g_" + secrets.token_hex(6)


@dataclass(frozen=True, slots=True)
class Conversation:
    conv_id: str
    owner: str
    title: str
    kind: str
    primary_agent: str | None
    dnd: bool
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass(frozen=True, slots=True)
class Membership:
    principal: str
    nickname: str
    muted: bool
    read_cursor: int
    joined_at: float


@dataclass(frozen=True, slots=True)
class Message:
    id: int
    conv_id: str
    sender: str
    content: str
    mentions: list[str]
    reply_to: int | None
    attachments: list[dict]
    created_at: float


class ConversationStore:
    def __init__(self, agents: AgentStore):
        self.agents = agents
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        conn = self.agents._connect()  # same database: members reference agents
        if not self._ready:
            conn.executescript(_SCHEMA)
            self._ready = True
        return conn

    def _run(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    @staticmethod
    def _conversation(row: sqlite3.Row) -> Conversation:
        return Conversation(
            conv_id=row["conv_id"], owner=row["owner"], title=row["title"], kind=row["kind"],
            primary_agent=row["primary_agent"], dnd=bool(row["dnd"]), meta=json.loads(row["meta_json"] or "{}"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _message(row: sqlite3.Row) -> Message:
        return Message(
            id=row["id"], conv_id=row["conv_id"], sender=row["sender"], content=row["content"],
            mentions=json.loads(row["mentions_json"] or "[]"), reply_to=row["reply_to"],
            attachments=json.loads(row["attachments_json"] or "[]"), created_at=row["created_at"],
        )

    # ── conversations ────────────────────────────────────────────────────

    def create(self, owner: str, title: str, kind: str, *, members: list[str] = (), meta: dict | None = None,
               conv_id: str = "", created_at: float | None = None) -> Conversation:
        now = created_at or time.time()
        conv_id = conv_id or new_conversation_id()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO conversations (conv_id, owner, title, kind, meta_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conv_id, owner, title, kind, json.dumps(meta or {}, ensure_ascii=False), now, now),
            )
            for principal in [human(owner), *members]:
                conn.execute(
                    "INSERT OR IGNORE INTO conversation_members (conv_id, principal, agent_id, joined_at)"
                    " VALUES (?, ?, ?, ?)",
                    (conv_id, principal, principal if is_agent(principal) else None, now),
                )
            conn.execute("COMMIT")
        finally:
            conn.close()
        return self.get(conv_id)  # type: ignore[return-value]

    def get(self, conv_id: str) -> Conversation | None:
        rows = self._run("SELECT * FROM conversations WHERE conv_id = ?", (conv_id,))
        return self._conversation(rows[0]) if rows else None

    def list_for(self, principal: str) -> list[Conversation]:
        rows = self._run(
            "SELECT c.* FROM conversations c JOIN conversation_members m ON m.conv_id = c.conv_id"
            " WHERE m.principal = ? ORDER BY c.updated_at DESC",
            (principal,),
        )
        return [self._conversation(r) for r in rows]

    def update(self, conv_id: str, **fields: Any) -> None:
        columns = {"title": "title", "primary_agent": "primary_agent", "dnd": "dnd", "meta": "meta_json"}
        sets, params = [], []
        for key, value in fields.items():
            sets.append(f"{columns[key]} = ?")
            params.append(json.dumps(value, ensure_ascii=False) if key == "meta" else value)
        sets.append("updated_at = ?")
        params.append(time.time())
        self._run(f"UPDATE conversations SET {', '.join(sets)} WHERE conv_id = ?", (*params, conv_id))

    def delete(self, conv_id: str) -> None:
        self._run("DELETE FROM conversations WHERE conv_id = ?", (conv_id,))

    # ── members ──────────────────────────────────────────────────────────

    def members(self, conv_id: str) -> list[Membership]:
        rows = self._run(
            "SELECT * FROM conversation_members WHERE conv_id = ? ORDER BY joined_at, rowid", (conv_id,),
        )
        return [Membership(r["principal"], r["nickname"], bool(r["muted"]), r["read_cursor"], r["joined_at"])
                for r in rows]

    def membership(self, conv_id: str, principal: str) -> Membership | None:
        return next((m for m in self.members(conv_id) if m.principal == principal), None)

    def add_member(self, conv_id: str, principal: str, *, nickname: str = "") -> None:
        self._run(
            "INSERT OR IGNORE INTO conversation_members (conv_id, principal, agent_id, nickname, joined_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (conv_id, principal, principal if is_agent(principal) else None, nickname, time.time()),
        )

    def remove_member(self, conv_id: str, principal: str) -> None:
        self._run("DELETE FROM conversation_members WHERE conv_id = ? AND principal = ?", (conv_id, principal))
        self._run("UPDATE conversations SET primary_agent = NULL WHERE conv_id = ? AND primary_agent = ?",
                  (conv_id, principal))

    def set_muted(self, conv_id: str, principal: str, muted: bool) -> None:
        self._run("UPDATE conversation_members SET muted = ? WHERE conv_id = ? AND principal = ?",
                  (int(muted), conv_id, principal))

    def set_nickname(self, conv_id: str, principal: str, nickname: str) -> None:
        self._run("UPDATE conversation_members SET nickname = ? WHERE conv_id = ? AND principal = ?",
                  (nickname.strip(), conv_id, principal))

    def advance_cursor(self, conv_id: str, principal: str, message_id: int) -> None:
        self._run(
            "UPDATE conversation_members SET read_cursor = MAX(read_cursor, ?) WHERE conv_id = ? AND principal = ?",
            (message_id, conv_id, principal),
        )

    # ── messages ─────────────────────────────────────────────────────────

    def add_message(self, conv_id: str, sender: str, content: str, *, mentions: list[str] = (),
                    reply_to: int | None = None, attachments: list[dict] = (), client_msg_id: str | None = None,
                    created_at: float | None = None) -> tuple[Message, bool]:
        """Store a message; ``(message, False)`` when *client_msg_id* was already posted."""
        now = created_at or time.time()
        conn = self._connect()
        try:
            try:
                cursor = conn.execute(
                    "INSERT INTO conversation_messages (conv_id, sender, content, mentions_json, reply_to,"
                    " attachments_json, client_msg_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (conv_id, sender, content, json.dumps(list(mentions)), reply_to,
                     json.dumps(list(attachments), ensure_ascii=False), client_msg_id, now),
                )
                created, message_id = True, cursor.lastrowid
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT id FROM conversation_messages WHERE conv_id = ? AND sender = ? AND client_msg_id = ?",
                    (conv_id, sender, client_msg_id),
                ).fetchone()
                created, message_id = False, row["id"]
            conn.execute("UPDATE conversations SET updated_at = ? WHERE conv_id = ?", (now, conv_id))
            row = conn.execute("SELECT * FROM conversation_messages WHERE id = ?", (message_id,)).fetchone()
        finally:
            conn.close()
        return self._message(row), created

    def messages(self, conv_id: str, *, after_id: int = 0, before_id: int | None = None,
                 limit: int = 200, latest: bool = False) -> list[Message]:
        """Messages in order; ``latest`` takes the last *limit* instead of the first."""
        where, params = "conv_id = ? AND id > ?", [conv_id, after_id]
        if before_id is not None:
            where += " AND id < ?"
            params.append(before_id)
        order = "DESC" if latest else "ASC"
        rows = self._run(f"SELECT * FROM conversation_messages WHERE {where} ORDER BY id {order} LIMIT ?",
                         (*params, limit))
        messages = [self._message(r) for r in rows]
        return list(reversed(messages)) if latest else messages

    def last_message(self, conv_id: str) -> Message | None:
        found = self.messages(conv_id, limit=1, latest=True)
        return found[0] if found else None

    def message_count(self, conv_id: str) -> int:
        return self._run("SELECT COUNT(*) FROM conversation_messages WHERE conv_id = ?", (conv_id,))[0][0]
