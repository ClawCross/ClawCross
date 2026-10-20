"""External agent chat history persistence.

模仿 ``data/agent_checkpoints`` 的"每会话一库"模式，记录与外部 agent
（HTTP / ACP）之间的每一轮：prompt，然后按发生顺序的文字段与工具调用（每个工具一行）。

- 文件布局：``data/external_agent_history/<platform>#<session_key>.db``
- 表：``session_meta``（每库 1 行）、``messages``（明细流水）
- 写入失败仅记录日志，不抛出，避免影响正在进行的对话
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from common.runtime_paths import DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_DB_DIR = DATA_DIR / "external_agent_history"

_PATH_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]+')

_OPT_USER_ID = "_history_user_id"
_OPT_GROUP_ID = "_history_group_id"
_OPT_DISABLED = "_history_disabled"
_OPT_GLOBAL_NAME = "_history_global_name"


def _sanitize(value: str | None, fallback: str = "default") -> str:
    raw = (value or "").strip() or fallback
    cleaned = _PATH_UNSAFE_RE.sub("_", raw).strip(" .") or fallback
    return cleaned[:128]


def history_db_name_for(platform: str, session_key: str | None) -> str:
    plat = _sanitize(platform, "unknown")
    sess = _sanitize(session_key, "__default__")
    return f"{plat}#{sess}.db"


def history_db_path_for(
    platform: str,
    session_key: str | None,
    history_dir: str | Path | None = None,
) -> Path:
    root = Path(history_dir) if history_dir else DEFAULT_HISTORY_DB_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root / history_db_name_for(platform, session_key)


def iter_history_db_paths(history_dir: str | Path | None = None) -> list[Path]:
    root = Path(history_dir) if history_dir else DEFAULT_HISTORY_DB_DIR
    if not root.is_dir():
        return []
    return sorted(root.glob("*.db"))


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _json_dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return json.dumps(str(value), ensure_ascii=False)


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text = _extract_user_text_from_openai_messages(value)
        if text:
            return text
    return _json_dump(value)


def _extract_user_text_from_openai_messages(messages: list[Any]) -> str:
    """If `messages` looks like an OpenAI chat-completions list, return the last
    user message's plain text. Returns "" otherwise so callers can fall back to
    JSON serialization."""
    if not messages or not isinstance(messages[-1], dict):
        return ""
    last = messages[-1]
    if last.get("role") != "user":
        return ""
    content = last.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                txt = item.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
        if parts:
            return "\n".join(parts)
    return ""


def _item_row(item: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    """The log row of one turn item (``external.acp_events.apply``)."""
    if item.get("kind") == "tool":
        return ("tool_result", "tool", str(item.get("output") or ""),
                {"tool_name": item.get("title") or "", "tool_call_id": item.get("id"),
                 "status": item.get("status"), "input": item.get("input")})
    return ("recv", "assistant", str(item.get("text") or ""), {})


async def _order_turn_rows(db: aiosqlite.Connection) -> None:
    """One-shot rewrite of turns logged before items: their reply came before their
    tools, and a tool's call and result were two rows. Each tool becomes one row and
    the reply follows the tools, as turns are logged now."""
    cursor = await db.execute(
        "SELECT rowid, request_id, direction, content, meta_json FROM messages WHERE request_id IN "
        "(SELECT request_id FROM messages WHERE direction IN ('tool_call', 'tool_result') "
        "AND request_id NOT LIKE 'native:%') ORDER BY rowid")
    turns: dict[str, list] = {}
    for row in await cursor.fetchall():
        turns.setdefault(row[1], []).append(row)
    top = (await (await db.execute("SELECT COALESCE(MAX(rowid), 0) FROM messages")).fetchone())[0]
    for rows in turns.values():
        prompt, tools, reply, calls, dropped = [], [], [], {}, []
        for rowid, _, direction, content, meta_json in rows:
            try:
                meta = json.loads(meta_json or "{}")
            except ValueError:
                meta = {}
            if direction not in ("tool_call", "tool_result"):
                (prompt if direction == "send" else reply).append([rowid, direction, content, meta])
                continue
            try:
                item = json.loads(content)
            except ValueError:
                item = None
            if not isinstance(item, dict):
                tools.append([rowid, "tool_result", content, meta])
                continue
            name = str(item.get("name") or item.get("tool_name") or meta.get("tool_name") or "")
            if direction == "tool_call":
                entry = [rowid, "tool_result", "", {"tool_name": name, "tool_call_id": str(item.get("id") or ""),
                                                    "input": item.get("input", item.get("args"))}]
                tools.append(entry)
                if item.get("id"):
                    calls[str(item["id"])] = entry
                continue
            output = next((item[k] for k in ("content", "output", "output_text", "result") if k in item), "")
            output = output if isinstance(output, str) else _json_dump(output)
            entry = calls.pop(str(item.get("tool_call_id") or ""), None)
            if entry is None:
                tools.append([rowid, "tool_result", output, {"tool_name": name, "status": item.get("status"),
                                                             "tool_call_id": str(item.get("tool_call_id") or "")}])
            else:
                entry[2] = output
                entry[3]["status"] = item.get("status")
                dropped.append(rowid)
        order = prompt + tools + reply
        slots = sorted(entry[0] for entry in order)
        await db.executemany("DELETE FROM messages WHERE rowid = ?", [(rowid,) for rowid in dropped])
        # Through spare row IDs, so no two rows ever hold the same one.
        await db.executemany(
            "UPDATE messages SET rowid = ?, direction = ?, content = ?, meta_json = ? WHERE rowid = ?",
            [(top + 1 + index, direction, content, _json_dump(meta), rowid)
             for index, (rowid, direction, content, meta) in enumerate(order)])
        await db.executemany("UPDATE messages SET rowid = ? WHERE rowid = ?",
                             [(slot, top + 1 + index) for index, slot in enumerate(slots)])
        top += len(order)


def history_options_disabled(options: dict[str, Any] | None) -> bool:
    return bool((options or {}).get(_OPT_DISABLED))


def attach_history_context(
    options: dict[str, Any] | None,
    *,
    user_id: str | None = None,
    group_id: str | None = None,
    global_name: str | None = None,
) -> dict[str, Any]:
    """Return a new options dict with history context fields filled in.

    Existing `_history_*` keys are preserved if already set by an inner caller.
    """
    out = dict(options or {})
    if user_id and not out.get(_OPT_USER_ID):
        out[_OPT_USER_ID] = user_id
    if group_id and not out.get(_OPT_GROUP_ID):
        out[_OPT_GROUP_ID] = group_id
    if global_name and not out.get(_OPT_GLOBAL_NAME):
        out[_OPT_GLOBAL_NAME] = global_name
    return out


@dataclass(frozen=True)
class HistoryContext:
    user_id: str = ""
    group_id: str = ""
    global_name: str = ""

    @classmethod
    def from_options(cls, options: dict[str, Any] | None) -> "HistoryContext":
        opts = options or {}
        return cls(
            user_id=str(opts.get(_OPT_USER_ID) or ""),
            group_id=str(opts.get(_OPT_GROUP_ID) or ""),
            global_name=str(opts.get(_OPT_GLOBAL_NAME) or ""),
        )


_INIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_meta (
    platform TEXT NOT NULL,
    session_key TEXT NOT NULL,
    connect_type TEXT NOT NULL DEFAULT '',
    global_name TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    last_used_at REAL NOT NULL,
    cumulative_input_tokens INTEGER NOT NULL DEFAULT 0,
    cumulative_output_tokens INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (platform, session_key)
);

CREATE TABLE IF NOT EXISTS messages (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}',
    user_id TEXT NOT NULL DEFAULT '',
    group_id TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_messages_request ON messages(request_id);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);
"""


class ExternalAgentHistoryStore:
    """Per-(platform, session_key) SQLite store for external agent traffic."""

    def __init__(self, history_dir: str | Path | None = None) -> None:
        self._root = Path(history_dir) if history_dir else DEFAULT_HISTORY_DB_DIR
        self._init_locks: dict[Path, asyncio.Lock] = {}
        self._initialized: set[Path] = set()
        self._global_lock = asyncio.Lock()

    @property
    def root(self) -> Path:
        return self._root

    def db_path(self, platform: str, session_key: str | None) -> Path:
        return history_db_path_for(platform, session_key, self._root)

    async def _ensure_schema(self, path: Path) -> None:
        if path in self._initialized:
            return
        async with self._global_lock:
            lock = self._init_locks.setdefault(path, asyncio.Lock())
        async with lock:
            if path in self._initialized:
                return
            path.parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(path) as db:
                await db.executescript(_INIT_SCHEMA)
                if (await (await db.execute("PRAGMA user_version")).fetchone())[0] < 1:
                    await _order_turn_rows(db)
                    await db.execute("PRAGMA user_version = 1")
                await db.commit()
            self._initialized.add(path)

    async def _touch_session(
        self,
        path: Path,
        *,
        platform: str,
        session_key: str,
        connect_type: str,
        ctx: HistoryContext,
        ts: float,
    ) -> None:
        async with aiosqlite.connect(path) as db:
            await db.execute(
                """
                INSERT INTO session_meta (
                    platform, session_key, connect_type, global_name,
                    user_id, group_id, created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, session_key) DO UPDATE SET
                    connect_type=CASE WHEN excluded.connect_type != '' THEN excluded.connect_type ELSE session_meta.connect_type END,
                    global_name=CASE WHEN excluded.global_name != '' THEN excluded.global_name ELSE session_meta.global_name END,
                    user_id=CASE WHEN excluded.user_id != '' THEN excluded.user_id ELSE session_meta.user_id END,
                    group_id=CASE WHEN excluded.group_id != '' THEN excluded.group_id ELSE session_meta.group_id END,
                    last_used_at=excluded.last_used_at
                """,
                (
                    platform,
                    session_key,
                    connect_type,
                    ctx.global_name,
                    ctx.user_id,
                    ctx.group_id,
                    ts,
                    ts,
                ),
            )
            await db.commit()

    async def _insert_messages(
        self,
        path: Path,
        *,
        ts: float,
        request_id: str,
        rows: list[tuple[str, str, str, dict[str, Any]]],
        ctx: HistoryContext,
    ) -> None:
        """Append ``(direction, role, content, meta)`` rows in order, in one transaction."""
        async with aiosqlite.connect(path) as db:
            await db.executemany(
                """
                INSERT INTO messages (
                    ts, request_id, direction, role, content, meta_json, user_id, group_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(ts, request_id, direction, role, content, _json_dump(meta), ctx.user_id, ctx.group_id)
                 for direction, role, content, meta in rows],
            )
            await db.commit()

    async def record_send(
        self,
        *,
        platform: str,
        session_key: str | None,
        connect_type: str,
        prompt: Any,
        options: dict[str, Any] | None,
        request_id: str | None = None,
    ) -> str:
        """Record an outgoing prompt. Returns the request_id used."""
        rid = request_id or _new_request_id()
        if history_options_disabled(options):
            return rid
        try:
            ctx = HistoryContext.from_options(options)
            # Sanitize ONLY for the file path; preserve original values inside rows
            # so the frontend can query with its natural session_key.
            path = self.db_path(platform, session_key)
            await self._ensure_schema(path)
            ts = time.time()
            await self._touch_session(
                path,
                platform=platform or "unknown",
                session_key=session_key or "",
                connect_type=connect_type,
                ctx=ctx,
                ts=ts,
            )
            content = _coerce_text(prompt)
            meta: dict[str, Any] = {key: options[key] for key in ('display_input', 'display_runtime_context')
                                    if isinstance((options or {}).get(key), str)}
            if isinstance(prompt, list):
                meta["prompt_kind"] = "messages"
            elif not isinstance(prompt, str):
                meta["prompt_kind"] = type(prompt).__name__
            await self._insert_messages(path, ts=ts, request_id=rid, rows=[("send", "user", content, meta)], ctx=ctx)
        except Exception as e:
            logger.warning("external history record_send failed: %s", e)
        return rid

    async def record_recv(
        self,
        *,
        platform: str,
        session_key: str | None,
        connect_type: str,
        request_id: str,
        ok: bool,
        content: str | None,
        raw_response: Any,
        error: str | None,
        options: dict[str, Any] | None,
    ) -> None:
        if history_options_disabled(options):
            return
        try:
            ctx = HistoryContext.from_options(options)
            path = self.db_path(platform, session_key)
            await self._ensure_schema(path)
            ts = time.time()
            await self._touch_session(
                path,
                platform=platform or "unknown",
                session_key=session_key or "",
                connect_type=connect_type,
                ctx=ctx,
                ts=ts,
            )
            # An ACP turn brings its items (text runs and tool calls) in the order they happened.
            items = raw_response.get("items") if isinstance(raw_response, dict) else None
            rows = [_item_row(item) for item in items or ()]
            if not ok:
                rows.append(("error", "assistant", _coerce_text(error or "unknown error"), {"ok": False}))
            elif items is None:
                meta: dict[str, Any] = {"ok": True}
                if raw_response is not None and not isinstance(raw_response, str):
                    meta["raw_kind"] = type(raw_response).__name__
                    if isinstance(raw_response, dict):
                        for k in ("usage", "cumulative_token_usage", "request_token_usage"):
                            if k in raw_response:
                                meta[k] = raw_response[k]
                rows.append(("recv", "assistant", _coerce_text(content), meta))
            await self._insert_messages(path, ts=ts, request_id=request_id, rows=rows, ctx=ctx)
        except Exception as e:
            logger.warning("external history record_recv failed: %s", e)

    async def import_native_messages(self, *, platform: str, session_key: str, native_id: str,
                                     messages: list[dict], user_id: str) -> int:
        """Prepend one idempotent snapshot; keep locally recorded turns and their IDs."""
        path = self.db_path(platform, session_key)
        await self._ensure_schema(path)
        request_id = 'native:' + native_id
        async with aiosqlite.connect(path) as db:
            await db.execute('BEGIN IMMEDIATE')
            cursor = await db.execute('SELECT COUNT(*) FROM messages WHERE request_id=?', (request_id,))
            count = (await cursor.fetchone())[0]
            if count:
                return count
            # Some registered Agents have already spoken through ClawCross. Stop
            # the native snapshot at the first managed prompt to avoid duplicating it.
            cursor = await db.execute("SELECT content FROM messages WHERE direction='send' AND rowid>0 ORDER BY rowid LIMIT 1")
            boundary = await cursor.fetchone()
            if boundary:
                for index, row in enumerate(messages):
                    if row.get('role') == 'user' and row.get('content') == boundary[0]:
                        messages = messages[:index]
                        break
            stamp = time.time()
            values = [(-len(messages)+index, stamp, request_id, row.get('direction') or 'recv',
                       row.get('role') or 'assistant', str(row.get('content') or ''),
                       _json_dump({**(row.get('meta') or {}), 'source':'native', 'native_id':native_id}), user_id, '')
                      for index, row in enumerate(messages)]
            await db.executemany('INSERT INTO messages (rowid,ts,request_id,direction,role,content,meta_json,user_id,group_id) VALUES (?,?,?,?,?,?,?,?,?)', values)
            await db.commit()
        await self._touch_session(path, platform=platform, session_key=session_key, connect_type='acpx',
                                  ctx=HistoryContext(user_id=user_id), ts=stamp)
        return len(messages)

    async def message_page(self, *, platform: str, session_key: str, limit: int = 200,
                           before: int | None = None) -> dict:
        """Page backwards using stable row IDs, even while new turns are appended."""
        path = self.db_path(platform, session_key)
        if not path.exists():
            return {'rows':[], 'has_more':False, 'next_before':None}
        await self._ensure_schema(path)
        async with aiosqlite.connect(path) as db:
            db.row_factory = aiosqlite.Row
            clause = 'WHERE rowid < ?' if before is not None else ''
            values = (before, limit+1) if before is not None else (limit+1,)
            cursor = await db.execute(f'SELECT * FROM messages {clause} ORDER BY rowid DESC LIMIT ?', values)
            rows = await cursor.fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            if has_more and rows[-1]['direction'] != 'send':
                # Start the page at a prompt, so no turn is split between two pages.
                oldest = rows[-1]['rowid']
                start = (await (await db.execute(
                    "SELECT MAX(rowid) FROM messages WHERE rowid < ? AND direction = 'send'", (oldest,))).fetchone())[0]
                if start is not None:
                    cursor = await db.execute(
                        'SELECT * FROM messages WHERE rowid >= ? AND rowid < ? ORDER BY rowid DESC', (start, oldest))
                    rows += await cursor.fetchall()
                    has_more = bool(await (await db.execute(
                        'SELECT 1 FROM messages WHERE rowid < ? LIMIT 1', (start,))).fetchone())
        out = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item['meta'] = json.loads(item.pop('meta_json') or '{}')
            except (TypeError, ValueError):
                item['meta'] = {}
            out.append(item)
        return {'rows':out, 'has_more':has_more, 'next_before':out[0]['rowid'] if has_more else None}

    async def list_messages(
        self,
        *,
        platform: str,
        session_key: str | None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        path = self.db_path(platform, session_key)
        if not path.exists():
            return []
        try:
            async with aiosqlite.connect(path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    """
                    SELECT rowid, ts, request_id, direction, role, content, meta_json, user_id, group_id
                    FROM messages
                    ORDER BY rowid ASC
                    LIMIT ? OFFSET ?
                    """,
                    (max(1, int(limit)), max(0, int(offset))),
                )
                rows = await cursor.fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return []
            raise
        out: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            try:
                data["meta"] = json.loads(data.pop("meta_json") or "{}")
            except (TypeError, ValueError):
                data["meta"] = {}
            out.append(data)
        return out

    async def get_session_meta(
        self,
        *,
        platform: str,
        session_key: str | None,
    ) -> dict[str, Any] | None:
        path = self.db_path(platform, session_key)
        if not path.exists():
            return None
        try:
            async with aiosqlite.connect(path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    "SELECT * FROM session_meta WHERE platform = ? AND session_key = ?",
                    (platform or "unknown", session_key or ""),
                )
                row = await cursor.fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                return None
            raise
        return dict(row) if row else None

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List all (platform, session_key) entries with latest meta row."""
        out: list[dict[str, Any]] = []
        for path in iter_history_db_paths(self._root):
            try:
                async with aiosqlite.connect(path) as db:
                    db.row_factory = aiosqlite.Row
                    cursor = await db.execute(
                        "SELECT * FROM session_meta ORDER BY last_used_at DESC LIMIT 1"
                    )
                    row = await cursor.fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    continue
                raise
            if row:
                data = dict(row)
                data["db_path"] = str(path)
                out.append(data)
        out.sort(key=lambda d: d.get("last_used_at") or 0, reverse=True)
        return out

    async def purge_old_messages(
        self,
        *,
        platform: str,
        session_key: str | None,
        keep_last: int = 500,
    ) -> int:
        """Keep only the most recent `keep_last` messages for a session."""
        path = self.db_path(platform, session_key)
        if not path.exists():
            return 0
        keep_last = max(1, int(keep_last))
        async with aiosqlite.connect(path) as db:
            try:
                cursor = await db.execute(
                    "SELECT rowid FROM messages ORDER BY rowid DESC LIMIT 1 OFFSET ?",
                    (keep_last,),
                )
                row = await cursor.fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc).lower():
                    return 0
                raise
            if not row:
                return 0
            cutoff_rowid = int(row[0])
            cursor = await db.execute(
                "DELETE FROM messages WHERE rowid <= ?", (cutoff_rowid,)
            )
            deleted = cursor.rowcount or 0
            await db.commit()
            await self._maybe_vacuum(db)
        return int(deleted)

    @staticmethod
    async def _maybe_vacuum(db: aiosqlite.Connection) -> None:
        page_row = await (await db.execute("PRAGMA page_count")).fetchone()
        free_row = await (await db.execute("PRAGMA freelist_count")).fetchone()
        page_count = int(page_row[0] if page_row and page_row[0] is not None else 0)
        freelist = int(free_row[0] if free_row and free_row[0] is not None else 0)
        if page_count > 0 and freelist / page_count >= 0.35:
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await db.execute("VACUUM")

    async def delete_session(
        self,
        *,
        platform: str,
        session_key: str | None,
    ) -> bool:
        path = self.db_path(platform, session_key)
        if not path.exists():
            return False
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                Path(f"{path}{suffix}").unlink()
            except FileNotFoundError:
                pass
        self._initialized.discard(path)
        return True


_STORE: ExternalAgentHistoryStore | None = None
_STORE_LOCK = asyncio.Lock()


async def get_store() -> ExternalAgentHistoryStore:
    global _STORE
    if _STORE is None:
        async with _STORE_LOCK:
            if _STORE is None:
                _STORE = ExternalAgentHistoryStore()
    return _STORE


def reset_store_for_test(history_dir: str | Path | None) -> ExternalAgentHistoryStore:
    """Replace the singleton store; meant for tests."""
    global _STORE
    _STORE = ExternalAgentHistoryStore(history_dir)
    return _STORE


__all__ = [
    "DEFAULT_HISTORY_DB_DIR",
    "ExternalAgentHistoryStore",
    "HistoryContext",
    "attach_history_context",
    "get_store",
    "history_db_name_for",
    "history_db_path_for",
    "history_options_disabled",
    "iter_history_db_paths",
    "reset_store_for_test",
]
