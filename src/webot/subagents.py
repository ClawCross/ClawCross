"""
Persistent metadata store for WeBot subagents.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
import sqlite3
from pathlib import Path

from utils.runtime_paths import DATA_DIR


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = DATA_DIR / "webot_subagents.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SubagentRecord:
    agent_id: str
    user_id: str
    session_id: str
    agent_type: str
    name: str
    description: str
    parent_session: str
    workspace_mode: str
    workspace_root: str
    cwd: str
    remote: str
    status: str
    created_at: str
    updated_at: str
    last_result: str = ""


def get_runtime_db_path(db_path: str | os.PathLike | None = None) -> Path:
    explicit = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    explicit.parent.mkdir(parents=True, exist_ok=True)
    return explicit


def _connect(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(get_runtime_db_path(db_path), factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_subagents (
            agent_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL UNIQUE,
            agent_type TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            parent_session TEXT NOT NULL DEFAULT '',
            workspace_mode TEXT NOT NULL DEFAULT 'isolated',
            workspace_root TEXT NOT NULL DEFAULT '',
            cwd TEXT NOT NULL DEFAULT '',
            remote TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'idle',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_result TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_subagents_user_updated
        ON webot_subagents(user_id, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_subagents_user_name
        ON webot_subagents(user_id, name)
        """
    )
    existing_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(webot_subagents)").fetchall()
    }
    column_defaults = {
        "workspace_mode": "'isolated'",
        "workspace_root": "''",
        "cwd": "''",
        "remote": "''",
    }
    for column_name, default_value in column_defaults.items():
        if column_name not in existing_columns:
            conn.execute(
                f"ALTER TABLE webot_subagents ADD COLUMN {column_name} TEXT NOT NULL DEFAULT {default_value}"
            )
    return conn


def _row_to_record(row: sqlite3.Row | None) -> SubagentRecord | None:
    if row is None:
        return None
    return SubagentRecord(**dict(row))


def upsert_subagent(record: SubagentRecord, db_path: str | os.PathLike | None = None) -> SubagentRecord:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_subagents (
                agent_id, user_id, session_id, agent_type, name, description,
                parent_session, workspace_mode, workspace_root, cwd, remote,
                status, created_at, updated_at, last_result
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                session_id=excluded.session_id,
                agent_type=excluded.agent_type,
                name=excluded.name,
                description=excluded.description,
                parent_session=excluded.parent_session,
                workspace_mode=excluded.workspace_mode,
                workspace_root=excluded.workspace_root,
                cwd=excluded.cwd,
                remote=excluded.remote,
                status=excluded.status,
                updated_at=excluded.updated_at,
                last_result=excluded.last_result
            """,
            (
                record.agent_id,
                record.user_id,
                record.session_id,
                record.agent_type,
                record.name,
                record.description,
                record.parent_session,
                record.workspace_mode,
                record.workspace_root,
                record.cwd,
                record.remote,
                record.status,
                record.created_at,
                record.updated_at,
                record.last_result,
            ),
        )
        conn.commit()
    return record


def create_subagent_record(
    *,
    agent_id: str,
    user_id: str,
    session_id: str,
    agent_type: str,
    name: str,
    description: str,
    parent_session: str,
    workspace_mode: str = "isolated",
    workspace_root: str = "",
    cwd: str = "",
    remote: str = "",
    status: str = "idle",
) -> SubagentRecord:
    now = utc_now()
    return SubagentRecord(
        agent_id=agent_id,
        user_id=user_id,
        session_id=session_id,
        agent_type=agent_type,
        name=name,
        description=description,
        parent_session=parent_session,
        workspace_mode=workspace_mode or "isolated",
        workspace_root=workspace_root,
        cwd=cwd,
        remote=remote,
        status=status,
        created_at=now,
        updated_at=now,
        last_result="",
    )


def get_subagent(agent_id: str, user_id: str, db_path: str | os.PathLike | None = None) -> SubagentRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_subagents
            WHERE agent_id = ? AND user_id = ?
            """,
            (agent_id, user_id),
        ).fetchone()
    return _row_to_record(row)


def get_subagent_by_name(name: str, user_id: str, db_path: str | os.PathLike | None = None) -> SubagentRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_subagents
            WHERE user_id = ? AND name = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id, name),
        ).fetchone()
    return _row_to_record(row)


def get_subagent_by_session(
    session_id: str,
    user_id: str,
    db_path: str | os.PathLike | None = None,
) -> SubagentRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_subagents
            WHERE user_id = ? AND session_id = ?
            LIMIT 1
            """,
            (user_id, session_id),
        ).fetchone()
    return _row_to_record(row)


def list_subagents_for_user(user_id: str, db_path: str | os.PathLike | None = None) -> list[SubagentRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM webot_subagents
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [_row_to_record(row) for row in rows if row is not None]


def list_subagents_for_parent_session(
    user_id: str,
    parent_session: str,
    db_path: str | os.PathLike | None = None,
    limit: int = 50,
) -> list[SubagentRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM webot_subagents
            WHERE user_id = ? AND parent_session = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (user_id, parent_session, max(1, limit)),
        ).fetchall()
    return [_row_to_record(row) for row in rows if row is not None]


def update_subagent_status(
    agent_id: str,
    user_id: str,
    *,
    status: str | None = None,
    last_result: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> SubagentRecord | None:
    record = get_subagent(agent_id, user_id, db_path=db_path)
    if record is None:
        return None
    if status is not None:
        record.status = status
    if last_result is not None:
        record.last_result = last_result
    record.updated_at = utc_now()
    return upsert_subagent(record, db_path=db_path)


def update_subagent_metadata(
    agent_id: str,
    user_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    parent_session: str | None = None,
    workspace_mode: str | None = None,
    workspace_root: str | None = None,
    cwd: str | None = None,
    remote: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> SubagentRecord | None:
    record = get_subagent(agent_id, user_id, db_path=db_path)
    if record is None:
        return None
    if name is not None:
        record.name = name
    if description is not None:
        record.description = description
    if parent_session is not None:
        record.parent_session = parent_session
    if workspace_mode is not None:
        record.workspace_mode = workspace_mode
    if workspace_root is not None:
        record.workspace_root = workspace_root
    if cwd is not None:
        record.cwd = cwd
    if remote is not None:
        record.remote = remote
    record.updated_at = utc_now()
    return upsert_subagent(record, db_path=db_path)


def delete_subagent_by_session(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
) -> int:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            DELETE FROM webot_subagents
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        )
        conn.commit()
        return cursor.rowcount


def delete_subagents_for_user(
    user_id: str,
    db_path: str | os.PathLike | None = None,
) -> int:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            DELETE FROM webot_subagents
            WHERE user_id = ?
            """,
            (user_id,),
        )
        conn.commit()
        return cursor.rowcount
