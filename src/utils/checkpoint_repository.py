"""
SQLite checkpoint 持久化操作模块

当前使用分片目录布局：
- `data/agent_checkpoints/*.db`（每个 thread 一个文件）
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import aiosqlite

from utils.checkpoint_paths import (
    candidate_checkpoint_db_paths_for_thread,
    checkpoint_db_path_for_thread,
    is_checkpoint_db_file,
    iter_checkpoint_db_paths,
)

_VACUUM_FREE_PAGE_RATIO_THRESHOLD = 0.35


@dataclass(frozen=True)
class ContextCompactionRecord:
    thread_id: str
    summary: str
    compacted_until: int
    source_message_count: int
    summary_token_estimate: int
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""
    created_at: str = ""

    @property
    def metadata_json(self) -> str:
        return _json_dumps(self.metadata)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_missing_table_error(exc: sqlite3.OperationalError) -> bool:
    """判断异常是否为 'no such table' 错误。"""
    return "no such table" in str(exc).lower()


def _ensure_context_compaction_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS context_compactions (
            thread_id TEXT PRIMARY KEY,
            summary TEXT NOT NULL DEFAULT '',
            compacted_until INTEGER NOT NULL DEFAULT 0,
            source_message_count INTEGER NOT NULL DEFAULT 0,
            summary_token_estimate INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def _row_to_context_compaction(row: sqlite3.Row | None) -> ContextCompactionRecord | None:
    if row is None:
        return None
    data = dict(row)
    data["metadata"] = _json_loads_dict(data.pop("metadata_json", ""))
    return ContextCompactionRecord(**data)


def get_context_compaction(
    store_path: str | Path | None,
    thread_id: str,
) -> ContextCompactionRecord | None:
    """Load persistent compaction state from this thread's checkpoint DB."""
    for path in candidate_checkpoint_db_paths_for_thread(store_path, thread_id):
        with sqlite3.connect(path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT * FROM context_compactions
                    WHERE thread_id = ?
                    """,
                    (thread_id,),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if _is_missing_table_error(exc):
                    continue
                raise
        record = _row_to_context_compaction(row)
        if record is not None:
            return record
    return None


def save_context_compaction(
    store_path: str | Path | None,
    thread_id: str,
    *,
    summary: str,
    compacted_until: int,
    source_message_count: int,
    summary_token_estimate: int,
    metadata: dict[str, Any] | None = None,
) -> ContextCompactionRecord:
    """Persist compaction metadata beside the per-thread conversation context."""
    candidates = candidate_checkpoint_db_paths_for_thread(store_path, thread_id)
    path = candidates[0] if candidates else checkpoint_db_path_for_thread(thread_id, store_path)
    now = _utc_now()
    with sqlite3.connect(path, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        _ensure_context_compaction_table(conn)
        conn.execute(
            """
            INSERT INTO context_compactions (
                thread_id, summary, compacted_until, source_message_count,
                summary_token_estimate, metadata_json, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                summary=excluded.summary,
                compacted_until=excluded.compacted_until,
                source_message_count=excluded.source_message_count,
                summary_token_estimate=excluded.summary_token_estimate,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                thread_id,
                summary.strip(),
                max(0, int(compacted_until)),
                max(0, int(source_message_count)),
                max(0, int(summary_token_estimate)),
                _json_dumps(metadata or {}),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT * FROM context_compactions
            WHERE thread_id = ?
            """,
            (thread_id,),
        ).fetchone()
    record = _row_to_context_compaction(row)
    assert record is not None
    return record


def delete_context_compaction(
    store_path: str | Path | None,
    thread_id: str,
) -> None:
    for path in candidate_checkpoint_db_paths_for_thread(store_path, thread_id):
        with sqlite3.connect(path, timeout=30) as conn:
            try:
                conn.execute(
                    """
                    DELETE FROM context_compactions
                    WHERE thread_id = ?
                    """,
                    (thread_id,),
                )
            except sqlite3.OperationalError as exc:
                if not _is_missing_table_error(exc):
                    raise
            conn.commit()


def _ensure_context_usage_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS context_usage (
            thread_id TEXT PRIMARY KEY,
            usage_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
        """
    )


def get_context_usage_record(
    store_path: str | Path | None,
    thread_id: str,
) -> dict[str, Any] | None:
    """Load the last API-reported context usage persisted for this thread."""
    for path in candidate_checkpoint_db_paths_for_thread(store_path, thread_id):
        with sqlite3.connect(path, timeout=30) as conn:
            try:
                row = conn.execute(
                    "SELECT usage_json FROM context_usage WHERE thread_id = ?",
                    (thread_id,),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if _is_missing_table_error(exc):
                    continue
                raise
        if row:
            return _json_loads_dict(row[0])
    return None


def save_context_usage_record(
    store_path: str | Path | None,
    thread_id: str,
    record: dict[str, Any],
) -> None:
    """Persist context usage beside the per-thread conversation context."""
    candidates = candidate_checkpoint_db_paths_for_thread(store_path, thread_id)
    path = candidates[0] if candidates else checkpoint_db_path_for_thread(thread_id, store_path)
    with sqlite3.connect(path, timeout=30) as conn:
        _ensure_context_usage_table(conn)
        conn.execute(
            """
            INSERT INTO context_usage (thread_id, usage_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                usage_json=excluded.usage_json,
                updated_at=excluded.updated_at
            """,
            (thread_id, _json_dumps(record), _utc_now()),
        )
        conn.commit()


async def list_thread_ids_by_prefix(db_path: str, prefix: str) -> list[str]:
    """按 thread_id 前缀查询会话列表。

    :param db_path: SQLite 数据库路径
    :param prefix: thread_id 前缀（如 "user#session_"）
    :return: 匹配的 thread_id 列表
    """
    return await list_thread_ids_like(db_path, f"{prefix}%")


async def list_thread_ids_like(db_path: str, pattern: str) -> list[str]:
    """按 LIKE 模式查询 thread_id。"""
    thread_ids: set[str] = set()
    for path in iter_checkpoint_db_paths(db_path):
        async with aiosqlite.connect(path) as db:
            for table in ("context_messages", "agent_state", "checkpoints"):
                try:
                    cursor = await db.execute(
                        f"SELECT DISTINCT thread_id FROM {table} WHERE thread_id LIKE ? ORDER BY thread_id",
                        (pattern,),
                    )
                    rows = await cursor.fetchall()
                except sqlite3.OperationalError as exc:
                    if _is_missing_table_error(exc):
                        continue
                    raise
                thread_ids.update(row[0] for row in rows)
    return sorted(thread_ids)


async def fetch_thread_checkpoint_times(
    db_path: str,
    thread_id: str,
) -> dict[str, Any]:
    """Return best-effort created/updated times for one checkpoint thread."""
    for path in candidate_checkpoint_db_paths_for_thread(db_path, thread_id):
        stat = path.stat()
        created_ts = stat.st_ctime
        updated_ts = stat.st_mtime
        first_checkpoint_id = ""
        latest_checkpoint_id = ""
        async with aiosqlite.connect(path) as db:
            try:
                cursor = await db.execute(
                    "SELECT MIN(sequence), MAX(sequence), MIN(created_at), MAX(created_at) "
                    "FROM context_messages WHERE thread_id = ?",
                    (thread_id,),
                )
                context_row = await cursor.fetchone()
            except sqlite3.OperationalError as exc:
                if not _is_missing_table_error(exc):
                    raise
                context_row = None
            if context_row and context_row[0] is not None:
                created_at = str(context_row[2] or "")
                updated_at = str(context_row[3] or "")
                try:
                    created_ts = datetime.fromisoformat(created_at).timestamp()
                    updated_ts = datetime.fromisoformat(updated_at).timestamp()
                except ValueError:
                    pass
                return {
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "created_at_ts": created_ts,
                    "updated_at_ts": updated_ts,
                    "first_checkpoint_id": str(context_row[0]),
                    "latest_checkpoint_id": str(context_row[1]),
                }
            try:
                cursor = await db.execute(
                    "SELECT checkpoint_id, created_at, updated_at FROM agent_state WHERE thread_id = ?",
                    (thread_id,),
                )
                state_row = await cursor.fetchone()
            except sqlite3.OperationalError as exc:
                if not _is_missing_table_error(exc):
                    raise
                state_row = None
            if state_row:
                created_at = str(state_row[1] or "")
                updated_at = str(state_row[2] or "")
                try:
                    created_ts = datetime.fromisoformat(created_at).timestamp()
                    updated_ts = datetime.fromisoformat(updated_at).timestamp()
                except ValueError:
                    pass
                return {
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "created_at_ts": created_ts,
                    "updated_at_ts": updated_ts,
                    "first_checkpoint_id": str(state_row[0] or ""),
                    "latest_checkpoint_id": str(state_row[0] or ""),
                }
            try:
                cursor = await db.execute(
                    "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ? ORDER BY ROWID ASC LIMIT 1",
                    (thread_id,),
                )
                first_row = await cursor.fetchone()
                cursor = await db.execute(
                    "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ? ORDER BY ROWID DESC LIMIT 1",
                    (thread_id,),
                )
                latest_row = await cursor.fetchone()
            except sqlite3.OperationalError as exc:
                if _is_missing_table_error(exc):
                    continue
                raise
        if first_row:
            first_checkpoint_id = str(first_row[0] or "")
        if latest_row:
            latest_checkpoint_id = str(latest_row[0] or "")
        return {
            "created_at": datetime.fromtimestamp(created_ts, timezone.utc).isoformat(),
            "updated_at": datetime.fromtimestamp(updated_ts, timezone.utc).isoformat(),
            "created_at_ts": created_ts,
            "updated_at_ts": updated_ts,
            "first_checkpoint_id": first_checkpoint_id,
            "latest_checkpoint_id": latest_checkpoint_id,
        }
    return {}


async def fetch_latest_checkpoint_blob(
    db_path: str,
    thread_id: str,
) -> tuple[str, bytes | str] | None:
    """Return the latest serialized checkpoint payload for one thread."""
    for path in candidate_checkpoint_db_paths_for_thread(db_path, thread_id):
        async with aiosqlite.connect(path) as db:
            try:
                cursor = await db.execute(
                    "SELECT type, checkpoint FROM checkpoints WHERE thread_id = ? ORDER BY ROWID DESC LIMIT 1",
                    (thread_id,),
                )
                row = await cursor.fetchone()
            except sqlite3.OperationalError as exc:
                if _is_missing_table_error(exc):
                    continue
                raise
        if row:
            return row[0], row[1]
    return None


async def fetch_thread_message_count(db_path: str, thread_id: str) -> int:
    """Return how many messages are recorded for a thread, across schema generations."""
    for path in candidate_checkpoint_db_paths_for_thread(db_path, thread_id):
        async with aiosqlite.connect(path) as db:
            try:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM context_messages WHERE thread_id = ?",
                    (thread_id,),
                )
                row = await cursor.fetchone()
            except sqlite3.OperationalError as exc:
                if not _is_missing_table_error(exc):
                    raise
                row = None
            if row and row[0]:
                return int(row[0])

            try:
                cursor = await db.execute(
                    "SELECT state_json FROM agent_state WHERE thread_id = ?",
                    (thread_id,),
                )
                row = await cursor.fetchone()
            except sqlite3.OperationalError as exc:
                if not _is_missing_table_error(exc):
                    raise
                row = None
            if row and row[0]:
                with suppress(Exception):
                    payload = json.loads(row[0])
                    return len(payload.get("messages") or [])

            try:
                cursor = await db.execute(
                    "SELECT checkpoint FROM checkpoints WHERE thread_id = ? ORDER BY ROWID DESC LIMIT 1",
                    (thread_id,),
                )
                row = await cursor.fetchone()
            except sqlite3.OperationalError as exc:
                if not _is_missing_table_error(exc):
                    raise
                row = None
            if row and row[0]:
                with suppress(Exception):
                    blob = row[0]
                    if isinstance(blob, (bytes, bytearray)):
                        blob = blob.decode("utf-8", errors="ignore")
                    data = json.loads(blob) if isinstance(blob, str) else {}
                    return len(data.get("channel_values", {}).get("messages", []))
    return 0


async def delete_thread_records(db_path: str, thread_id: str) -> None:
    """删除指定 thread 的上下文及遗留 checkpoint 记录。

    :param db_path: SQLite 数据库路径
    :param thread_id: 要删除的线程 ID
    """
    for path in candidate_checkpoint_db_paths_for_thread(db_path, thread_id):
        async with aiosqlite.connect(path) as db:
            for table in ("context_messages", "agent_state", "context_usage", "checkpoints", "writes"):
                try:
                    await db.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))
                except sqlite3.OperationalError as exc:
                    if not _is_missing_table_error(exc):
                        raise
            await db.commit()
        await _maybe_delete_empty_checkpoint_db(Path(path), db_path)


async def delete_thread_records_like(db_path: str, pattern: str) -> None:
    """按 LIKE 模式删除上下文及遗留 checkpoint 记录。

    :param db_path: SQLite 数据库路径
    :param pattern: LIKE 模式（如 "user#%"）
    """
    for path in iter_checkpoint_db_paths(db_path):
        async with aiosqlite.connect(path) as db:
            for table in ("context_messages", "agent_state", "context_usage", "checkpoints", "writes"):
                try:
                    await db.execute(f"DELETE FROM {table} WHERE thread_id LIKE ?", (pattern,))
                except sqlite3.OperationalError as exc:
                    if not _is_missing_table_error(exc):
                        raise
            await db.commit()
        await _maybe_delete_empty_checkpoint_db(Path(path), db_path)


async def _table_row_count(db: aiosqlite.Connection, table: str) -> int:
    try:
        row = await (await db.execute(f"SELECT COUNT(*) FROM {table}")).fetchone()
    except sqlite3.OperationalError as exc:
        if _is_missing_table_error(exc):
            return 0
        raise
    return int(row[0] if row and row[0] is not None else 0)


async def _maybe_delete_empty_checkpoint_db(path: Path, store_path: str) -> bool:
    if not path.exists():
        return False

    store_root = Path(store_path)
    if is_checkpoint_db_file(store_root):
        return False

    async with aiosqlite.connect(path) as db:
        total_rows = await _table_row_count(db, "checkpoints")
        total_rows += await _table_row_count(db, "writes")
        total_rows += await _table_row_count(db, "agent_state")
        total_rows += await _table_row_count(db, "context_messages")

    if total_rows > 0:
        return False

    for suffix in ("", "-wal", "-shm", "-journal"):
        with suppress(FileNotFoundError):
            Path(f"{path}{suffix}").unlink()
    return True


async def _maybe_vacuum_on_free_page_ratio(db: aiosqlite.Connection) -> bool:
    """Run VACUUM when freelist/page_count ratio is too high."""
    page_row = await (await db.execute("PRAGMA page_count")).fetchone()
    free_row = await (await db.execute("PRAGMA freelist_count")).fetchone()
    page_count = int(page_row[0] if page_row and page_row[0] is not None else 0)
    freelist_count = int(free_row[0] if free_row and free_row[0] is not None else 0)
    if page_count <= 0:
        return False
    free_ratio = freelist_count / page_count
    if free_ratio < _VACUUM_FREE_PAGE_RATIO_THRESHOLD:
        return False
    await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    await db.execute("VACUUM")
    return True


async def purge_old_checkpoints(db_path: str, thread_id: str, keep: int = 1) -> int:
    """清理指定 thread 的旧 checkpoint，只保留最近 `keep` 个。

    轻量状态表始终只有一行，因此无需清理；此函数仍会清理升级前遗留的
    LangGraph checkpoint/writes 记录，只保留最新的 `keep` 个。

    :param db_path: SQLite 数据库路径
    :param thread_id: 线程 ID
    :param keep: 要保留的最新 checkpoint 数量，默认为 1
    :return: 删除的 checkpoint 数量
    """
    deleted = 0
    target_path = checkpoint_db_path_for_thread(thread_id, Path(db_path))
    candidate_paths = [target_path, *candidate_checkpoint_db_paths_for_thread(db_path, thread_id)]
    seen: set[Path] = set()
    for raw_path in candidate_paths:
        path = Path(raw_path)
        resolved = path.resolve()
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        deleted += await _purge_old_checkpoints_file(str(path), thread_id, keep=keep)
    return deleted


async def _purge_old_checkpoints_file(db_path: str, thread_id: str, keep: int = 1) -> int:
    deleted = 0
    async with aiosqlite.connect(db_path) as db:
        if await _table_row_count(db, "context_messages") or await _table_row_count(db, "agent_state"):
            # Context messages are append-only history, not disposable checkpoints.
            return 0
        try:
            # 找出该 thread 的所有 checkpoint_id，按 checkpoint_id 倒序排列
            # （LangGraph 的 checkpoint_id 是递增的 UUID/时间戳，越新越大）
            cursor = await db.execute(
                "SELECT checkpoint_id FROM checkpoints "
                "WHERE thread_id = ? "
                "ORDER BY checkpoint_id DESC",
                (thread_id,),
            )
            all_ids = [row[0] for row in await cursor.fetchall()]
        except sqlite3.OperationalError as exc:
            if _is_missing_table_error(exc):
                return 0
            raise

        if len(all_ids) <= keep:
            return 0  # 没有需要删除的

        # 要删除的 checkpoint_id
        ids_to_delete = all_ids[keep:]

        # 批量删除 checkpoints 和 writes
        placeholders = ",".join("?" for _ in ids_to_delete)
        for table in ("checkpoints", "writes"):
            try:
                await db.execute(
                    f"DELETE FROM {table} WHERE thread_id = ? AND checkpoint_id IN ({placeholders})",
                    [thread_id] + ids_to_delete,
                )
            except sqlite3.OperationalError as exc:
                if not _is_missing_table_error(exc):
                    raise
        await db.commit()
        await _maybe_vacuum_on_free_page_ratio(db)
        deleted = len(ids_to_delete)

    return deleted
