"""
Persistent runtime primitives for WeBot.

Runtime state lives in ``data/webot_agents/<user>#<agent>.db``.

Provides:
- durable delegated run records and control-plane state
- run attempt timelines
- session mode / state
- session inbox
- runtime artifact manifests
- plan / todo state
- verification records
- manual tool approval queue
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from common.runtime_paths import DATA_DIR
from webot.checkpoint_paths import checkpoint_db_name_for_thread, checkpoint_db_path_for_thread

from common.runtime_paths import PROJECT_ROOT  # noqa: E402
# An explicit override is available to isolated callers; production uses
# one database file per agent.
DEFAULT_DB_PATH: Path | None = None
AGENT_RUNTIME_DB_DIR = DATA_DIR / "webot_agents"
_INITIALIZED_DB_FILES: set[tuple[str, int]] = set()


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def summarize_inbox_content(content: str, limit: int = 100) -> str:
    """A bounded preview for notifications; the full body stays in the inbox."""
    lines = (content or "").splitlines()
    if lines and lines[0].startswith("[来自 ") and lines[0].endswith(" 的消息]"):
        lines = lines[1:]
    preview = " ".join(" ".join(lines).split())
    if not preview:
        return "（空消息）"
    return preview[:limit].rstrip() + ("…" if len(preview) > limit else "")


def _parse_timestamp(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _future_timestamp(*, hours: int = 0, seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours, seconds=seconds)).isoformat()


def is_timestamp_active(value: str | None) -> bool:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return False
    return timestamp >= datetime.now(timezone.utc)


def get_runtime_db_path(db_path: str | os.PathLike | None = None) -> Path:
    explicit = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if explicit is None:
        raise ValueError("An explicit database path is required")
    explicit.parent.mkdir(parents=True, exist_ok=True)
    return explicit


def _connect(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    path = get_runtime_db_path(db_path)
    conn = sqlite3.connect(path, timeout=30, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    # Schema migration belongs to the first open of a database file, not
    # every read. In particular, the legacy inbox UPDATE below must not run
    # for every status lookup in a model turn.
    file_key = (str(path.resolve()), path.stat().st_ino)
    if file_key in _INITIALIZED_DB_FILES:
        return conn
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_runs (
            run_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            parent_session TEXT NOT NULL DEFAULT '',
            agent_type TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            input_text TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            timeout_seconds INTEGER NOT NULL DEFAULT 300,
            max_turns INTEGER,
            wait_mode INTEGER NOT NULL DEFAULT 0,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            run_kind TEXT NOT NULL DEFAULT 'subagent',
            mode TEXT NOT NULL DEFAULT 'execute',
            parent_run_id TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            worker_id TEXT NOT NULL DEFAULT '',
            lease_expires_at TEXT NOT NULL DEFAULT '',
            heartbeat_at TEXT NOT NULL DEFAULT '',
            interrupt_requested INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            last_result TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_runs_user_updated
        ON webot_runs(user_id, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_runs_user_session_updated
        ON webot_runs(user_id, session_id, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_run_attempts (
            attempt_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT '',
            details TEXT NOT NULL DEFAULT '',
            worker_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_run_attempts_lookup
        ON webot_run_attempts(user_id, session_id, run_id, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_session_state (
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'execute',
            status TEXT NOT NULL DEFAULT 'active',
            summary TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, session_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_session_inbox (
            message_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            source_session TEXT NOT NULL,
            target_session TEXT NOT NULL,
            target_agent_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            delivery_status TEXT NOT NULL DEFAULT 'queued',
            wait_for_idle INTEGER NOT NULL DEFAULT 1,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            delivered_at TEXT NOT NULL DEFAULT '',
            read_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_session_inbox_lookup
        ON webot_session_inbox(user_id, target_session, delivery_status, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_runtime_artifacts (
            artifact_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            run_id TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            path TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_runtime_artifacts_lookup
        ON webot_runtime_artifacts(user_id, session_id, kind, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_memory_state (
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            project_slug TEXT NOT NULL DEFAULT '',
            memory_dir TEXT NOT NULL DEFAULT '',
            index_path TEXT NOT NULL DEFAULT '',
            kairos_enabled INTEGER NOT NULL DEFAULT 0,
            dream_status TEXT NOT NULL DEFAULT 'idle',
            active_run_id TEXT NOT NULL DEFAULT '',
            last_dream_at TEXT NOT NULL DEFAULT '',
            daily_log_path TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, session_id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_memory_state_lookup
        ON webot_memory_state(user_id, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_voice_state (
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            auto_read_aloud INTEGER NOT NULL DEFAULT 0,
            recording_supported INTEGER NOT NULL DEFAULT 1,
            tts_model TEXT NOT NULL DEFAULT '',
            tts_voice TEXT NOT NULL DEFAULT '',
            stt_model TEXT NOT NULL DEFAULT '',
            last_transcript TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, session_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_session_plans (
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            items_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, session_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_verifications (
            verification_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_verifications_session
        ON webot_verifications(user_id, session_id, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_tool_approvals (
            approval_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            args_json TEXT NOT NULL,
            args_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            request_reason TEXT NOT NULL DEFAULT '',
            resolution_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_tool_approvals_lookup
        ON webot_tool_approvals(user_id, session_id, tool_name, args_hash, status, expires_at)
        """
    )
    approval_columns = {row[1] for row in conn.execute("PRAGMA table_info(webot_tool_approvals)")}
    if "review_metadata_json" not in approval_columns:
        try:
            conn.execute("ALTER TABLE webot_tool_approvals ADD COLUMN review_metadata_json TEXT NOT NULL DEFAULT '{}'")
        except sqlite3.OperationalError:
            # Another worker may have completed the same migration.
            if "review_metadata_json" not in {row[1] for row in conn.execute("PRAGMA table_info(webot_tool_approvals)")}:
                conn.close()
                raise
    conn.execute("""
        CREATE TABLE IF NOT EXISTS webot_execution_permits (
            permit_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
            tool_name TEXT NOT NULL, args_hash TEXT NOT NULL, binding_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_session_todos (
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            items_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, session_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webot_claude_keepalive (
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            tool TEXT NOT NULL DEFAULT 'claude',
            prompt TEXT NOT NULL DEFAULT 'ping',
            model TEXT NOT NULL DEFAULT '',
            timezone TEXT NOT NULL DEFAULT '',
            start_time TEXT NOT NULL DEFAULT '06:00',
            sleep_time TEXT NOT NULL DEFAULT '23:00',
            weekdays TEXT NOT NULL DEFAULT 'MTWRFSU',
            use_caffeinate INTEGER NOT NULL DEFAULT 0,
            force_sleep_at_quiet_hours INTEGER NOT NULL DEFAULT 0,
            monitor_command TEXT NOT NULL DEFAULT 'claude-monitor --clear',
            timeout_seconds INTEGER NOT NULL DEFAULT 90,
            next_run_at TEXT NOT NULL DEFAULT '',
            last_run_at TEXT NOT NULL DEFAULT '',
            last_status TEXT NOT NULL DEFAULT 'idle',
            last_error TEXT NOT NULL DEFAULT '',
            last_result TEXT NOT NULL DEFAULT '',
            reset_at TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, session_id)
        )
        """
    )

    existing_run_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(webot_runs)").fetchall()
    }
    run_column_defaults = {
        "run_kind": "TEXT NOT NULL DEFAULT 'subagent'",
        "mode": "TEXT NOT NULL DEFAULT 'execute'",
        "parent_run_id": "TEXT NOT NULL DEFAULT ''",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
        "worker_id": "TEXT NOT NULL DEFAULT ''",
        "lease_expires_at": "TEXT NOT NULL DEFAULT ''",
        "heartbeat_at": "TEXT NOT NULL DEFAULT ''",
        "interrupt_requested": "INTEGER NOT NULL DEFAULT 0",
    }
    for column_name, ddl in run_column_defaults.items():
        if column_name not in existing_run_columns:
            conn.execute(f"ALTER TABLE webot_runs ADD COLUMN {column_name} {ddl}")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_runs_user_parent_run
        ON webot_runs(user_id, parent_run_id, updated_at DESC)
        """
    )

    existing_plan_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(webot_session_plans)").fetchall()
    }
    if "status" not in existing_plan_columns:
        conn.execute(
            "ALTER TABLE webot_session_plans ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        )
    if "metadata_json" not in existing_plan_columns:
        conn.execute(
            "ALTER TABLE webot_session_plans ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
        )
    existing_inbox_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(webot_session_inbox)").fetchall()
    }
    if "read_at" not in existing_inbox_columns:
        conn.execute("ALTER TABLE webot_session_inbox ADD COLUMN read_at TEXT NOT NULL DEFAULT ''")
        # Before this schema, a delivered message's full body was already
        # handed to the agent. Do not reclassify that history as unread.
        conn.execute(
            "UPDATE webot_session_inbox SET read_at = CASE WHEN delivered_at != '' "
            "THEN delivered_at ELSE created_at END WHERE delivery_status = 'delivered'"
        )
    conn.execute(
        """
        UPDATE webot_session_inbox
        SET delivery_status = 'queued'
        WHERE delivery_status = 'pending'
        """
    )
    conn.commit()
    _INITIALIZED_DB_FILES.add(file_key)
    return conn


def get_agent_runtime_db_path(user_id: str, session_id: str) -> Path:
    return checkpoint_db_path_for_thread(f"{user_id}#{session_id}", AGENT_RUNTIME_DB_DIR)


def _remove_agent_runtime_file(path: Path) -> None:
    resolved = str(path.resolve())
    _INITIALIZED_DB_FILES.difference_update(
        [key for key in _INITIALIZED_DB_FILES if key[0] == resolved]
    )
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def delete_agent_runtime_db(user_id: str, session_id: str) -> None:
    if DEFAULT_DB_PATH is None:
        _remove_agent_runtime_file(get_agent_runtime_db_path(user_id, session_id))


def delete_agent_runtime_dbs_for_user(user_id: str) -> None:
    if DEFAULT_DB_PATH is not None:
        return
    if AGENT_RUNTIME_DB_DIR.is_dir():
        prefix = checkpoint_db_name_for_thread(f"{user_id}#")[:-3]
        for path in AGENT_RUNTIME_DB_DIR.glob("*.db"):
            if path.name.startswith(prefix):
                _remove_agent_runtime_file(path)


def _connect_agent(
    user_id: str, session_id: str, db_path: str | os.PathLike | None = None,
) -> sqlite3.Connection:
    """Open this agent's database, or an explicitly supplied database."""
    if db_path is not None or DEFAULT_DB_PATH is not None:
        return _connect(db_path)
    return _connect(get_agent_runtime_db_path(user_id, session_id))


def _query_agent_rows(
    sql: str,
    params: tuple[Any, ...] | list[Any] = (),
    *,
    db_path: str | os.PathLike | None = None,
    identity: str | None = None,
) -> list[sqlite3.Row]:
    """Search agent database files, preferring one copy of each record ID."""
    if db_path is not None or DEFAULT_DB_PATH is not None:
        with _connect(db_path) as conn:
            return conn.execute(sql, params).fetchall()
    paths = sorted(AGENT_RUNTIME_DB_DIR.glob("*.db")) if AGENT_RUNTIME_DB_DIR.is_dir() else []
    rows: list[sqlite3.Row] = []
    seen: set[tuple[str, str]] = set()
    for path in paths:
        with _connect(path) as conn:
            for row in conn.execute(sql, params).fetchall():
                if identity:
                    key = (str(row["user_id"]), str(row[identity]))
                    if key in seen:
                        continue
                    seen.add(key)
                rows.append(row)
    return rows


def _record_session(
    table: str,
    id_column: str,
    record_id: str,
    user_id: str,
    *,
    session_column: str = "session_id",
    db_path: str | os.PathLike | None = None,
) -> str | None:
    rows = _query_agent_rows(
        f"SELECT {session_column} FROM {table} WHERE {id_column} = ? AND user_id = ? LIMIT 1",
        (record_id, user_id), db_path=db_path,
    )
    return str(rows[0][0]) if rows else None


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


def _stable_args_hash(tool_name: str, args: dict[str, Any]) -> str:
    normalized = _json_dumps({"tool_name": tool_name, "args": args or {}})
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WeBotRunRecord:
    run_id: str
    user_id: str
    agent_id: str
    session_id: str
    parent_session: str
    agent_type: str
    title: str
    input_text: str
    status: str
    timeout_seconds: int
    max_turns: int | None
    wait_mode: bool
    attempt_count: int
    run_kind: str = "subagent"
    mode: str = "execute"
    parent_run_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    worker_id: str = ""
    lease_expires_at: str = ""
    heartbeat_at: str = ""
    interrupt_requested: bool = False
    last_error: str = ""
    last_result: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def metadata_json(self) -> str:
        return _json_dumps(self.metadata)


@dataclass(frozen=True)
class RunAttemptRecord:
    attempt_id: str
    user_id: str
    run_id: str
    agent_id: str
    session_id: str
    event_type: str
    status: str
    details: str
    worker_id: str
    created_at: str


@dataclass(frozen=True)
class SessionStateRecord:
    user_id: str
    session_id: str
    mode: str
    status: str
    summary: str
    updated_at: str
    created_at: str

    @property
    def reason(self) -> str:
        return self.summary


@dataclass(frozen=True)
class InboxMessageRecord:
    message_id: str
    user_id: str
    source_session: str
    target_session: str
    target_agent_id: str
    title: str
    content: str
    delivery_status: str
    wait_for_idle: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    delivered_at: str = ""
    read_at: str = ""

    @property
    def body(self) -> str:
        return self.content

    @property
    def status(self) -> str:
        return self.delivery_status

    @property
    def summary(self) -> str:
        return summarize_inbox_content(self.title or self.content)

    @property
    def source_agent_id(self) -> str:
        return str(self.metadata.get("source_agent_id") or "")

    @property
    def source_label(self) -> str:
        return str(self.metadata.get("source_label") or self.title or self.source_session)

    @property
    def updated_at(self) -> str:
        return self.delivered_at or self.created_at

    @property
    def metadata_json(self) -> str:
        return _json_dumps(self.metadata)


@dataclass(frozen=True)
class RuntimeArtifactRecord:
    artifact_id: str
    user_id: str
    session_id: str
    run_id: str
    kind: str
    title: str
    summary: str
    path: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    @property
    def artifact_kind(self) -> str:
        return self.kind

    @property
    def preview(self) -> str:
        return self.summary

    @property
    def metadata_json(self) -> str:
        return _json_dumps(self.metadata)


@dataclass(frozen=True)
class ToolApprovalRecord:
    approval_id: str
    user_id: str
    session_id: str
    tool_name: str
    args_json: str
    args_hash: str
    status: str
    request_reason: str
    resolution_reason: str
    created_at: str
    updated_at: str
    expires_at: str
    review_metadata_json: str = "{}"


@dataclass(frozen=True)
class MemoryStateRecord:
    user_id: str
    session_id: str
    project_slug: str
    memory_dir: str
    index_path: str
    kairos_enabled: bool
    dream_status: str
    active_run_id: str
    last_dream_at: str
    daily_log_path: str
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""
    created_at: str = ""

    @property
    def metadata_json(self) -> str:
        return _json_dumps(self.metadata)


@dataclass(frozen=True)
class VoiceStateRecord:
    user_id: str
    session_id: str
    enabled: bool
    auto_read_aloud: bool
    recording_supported: bool
    tts_model: str
    tts_voice: str
    stt_model: str
    last_transcript: str
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""
    created_at: str = ""

    @property
    def metadata_json(self) -> str:
        return _json_dumps(self.metadata)


@dataclass(frozen=True)
class ClaudeKeepaliveRecord:
    user_id: str
    session_id: str
    enabled: bool
    tool: str
    prompt: str
    model: str
    timezone: str
    start_time: str
    sleep_time: str
    weekdays: str
    use_caffeinate: bool
    force_sleep_at_quiet_hours: bool
    monitor_command: str
    timeout_seconds: int
    next_run_at: str
    last_run_at: str
    last_status: str
    last_error: str
    last_result: str
    reset_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""
    created_at: str = ""

    @property
    def metadata_json(self) -> str:
        return _json_dumps(self.metadata)


def _row_to_run(row: sqlite3.Row | None) -> WeBotRunRecord | None:
    if row is None:
        return None
    data = dict(row)
    data["wait_mode"] = bool(data["wait_mode"])
    data["interrupt_requested"] = bool(data.get("interrupt_requested", 0))
    data["metadata"] = _json_loads_dict(data.pop("metadata_json", ""))
    return WeBotRunRecord(**data)


def _row_to_attempt(row: sqlite3.Row | None) -> RunAttemptRecord | None:
    if row is None:
        return None
    return RunAttemptRecord(**dict(row))


def _row_to_session_state(row: sqlite3.Row | None) -> SessionStateRecord | None:
    if row is None:
        return None
    return SessionStateRecord(**dict(row))


def _row_to_inbox_message(row: sqlite3.Row | None) -> InboxMessageRecord | None:
    if row is None:
        return None
    data = dict(row)
    data["wait_for_idle"] = bool(data["wait_for_idle"])
    data["metadata"] = _json_loads_dict(data.pop("metadata_json", ""))
    return InboxMessageRecord(**data)


def _row_to_artifact(row: sqlite3.Row | None) -> RuntimeArtifactRecord | None:
    if row is None:
        return None
    data = dict(row)
    data["metadata"] = _json_loads_dict(data.pop("metadata_json", ""))
    return RuntimeArtifactRecord(**data)


def _row_to_approval(row: sqlite3.Row | None) -> ToolApprovalRecord | None:
    if row is None:
        return None
    return ToolApprovalRecord(**dict(row))


def _row_to_memory_state(row: sqlite3.Row | None) -> MemoryStateRecord | None:
    if row is None:
        return None
    data = dict(row)
    data["kairos_enabled"] = bool(data["kairos_enabled"])
    data["metadata"] = _json_loads_dict(data.pop("metadata_json", ""))
    return MemoryStateRecord(**data)


def _row_to_voice_state(row: sqlite3.Row | None) -> VoiceStateRecord | None:
    if row is None:
        return None
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    data["auto_read_aloud"] = bool(data["auto_read_aloud"])
    data["recording_supported"] = bool(data["recording_supported"])
    data["metadata"] = _json_loads_dict(data.pop("metadata_json", ""))
    return VoiceStateRecord(**data)


def _row_to_claude_keepalive(row: sqlite3.Row | None) -> ClaudeKeepaliveRecord | None:
    if row is None:
        return None
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    data["use_caffeinate"] = bool(data["use_caffeinate"])
    data["force_sleep_at_quiet_hours"] = bool(data["force_sleep_at_quiet_hours"])
    data["timeout_seconds"] = int(data.get("timeout_seconds") or 90)
    data["metadata"] = _json_loads_dict(data.pop("metadata_json", ""))
    return ClaudeKeepaliveRecord(**data)


def create_run_record(
    *,
    run_id: str,
    user_id: str,
    agent_id: str,
    session_id: str,
    parent_session: str,
    agent_type: str,
    title: str,
    input_text: str,
    status: str,
    timeout_seconds: int,
    max_turns: int | None,
    wait_mode: bool,
    run_kind: str = "subagent",
    mode: str = "execute",
    parent_run_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> WeBotRunRecord:
    now = utc_now()
    return WeBotRunRecord(
        run_id=run_id,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        parent_session=parent_session,
        agent_type=agent_type,
        title=title,
        input_text=input_text,
        status=status,
        timeout_seconds=timeout_seconds,
        max_turns=max_turns,
        wait_mode=wait_mode,
        attempt_count=0,
        run_kind=(run_kind or "subagent").strip() or "subagent",
        mode=(mode or "execute").strip() or "execute",
        parent_run_id=parent_run_id or "",
        metadata=dict(metadata or {}),
        worker_id="",
        lease_expires_at="",
        heartbeat_at="",
        interrupt_requested=False,
        last_error="",
        last_result="",
        created_at=now,
        updated_at=now,
    )


def upsert_run(record: WeBotRunRecord, db_path: str | os.PathLike | None = None) -> WeBotRunRecord:
    with _connect_agent(record.user_id, record.session_id, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_runs (
                run_id, user_id, agent_id, session_id, parent_session, agent_type,
                title, input_text, status, timeout_seconds, max_turns, wait_mode,
                attempt_count, run_kind, mode, parent_run_id, metadata_json, worker_id,
                lease_expires_at, heartbeat_at, interrupt_requested, last_error,
                last_result, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,
                timeout_seconds=excluded.timeout_seconds,
                max_turns=excluded.max_turns,
                attempt_count=excluded.attempt_count,
                run_kind=excluded.run_kind,
                mode=excluded.mode,
                parent_run_id=excluded.parent_run_id,
                metadata_json=excluded.metadata_json,
                worker_id=excluded.worker_id,
                lease_expires_at=excluded.lease_expires_at,
                heartbeat_at=excluded.heartbeat_at,
                interrupt_requested=excluded.interrupt_requested,
                last_error=excluded.last_error,
                last_result=excluded.last_result,
                parent_session=excluded.parent_session,
                title=excluded.title,
                input_text=excluded.input_text,
                updated_at=excluded.updated_at
            """,
            (
                record.run_id,
                record.user_id,
                record.agent_id,
                record.session_id,
                record.parent_session,
                record.agent_type,
                record.title,
                record.input_text,
                record.status,
                record.timeout_seconds,
                record.max_turns,
                1 if record.wait_mode else 0,
                record.attempt_count,
                record.run_kind,
                record.mode,
                record.parent_run_id,
                _json_dumps(record.metadata),
                record.worker_id,
                record.lease_expires_at,
                record.heartbeat_at,
                1 if record.interrupt_requested else 0,
                record.last_error,
                record.last_result,
                record.created_at,
                record.updated_at,
            ),
        )
        conn.commit()
    return record


def get_run(run_id: str, user_id: str, db_path: str | os.PathLike | None = None) -> WeBotRunRecord | None:
    rows = _query_agent_rows(
        "SELECT * FROM webot_runs WHERE run_id = ? AND user_id = ?",
        (run_id, user_id), db_path=db_path, identity="run_id",
    )
    return _row_to_run(rows[0] if rows else None)


def list_runs_for_agent(
    user_id: str,
    agent_id: str,
    db_path: str | os.PathLike | None = None,
    limit: int = 20,
) -> list[WeBotRunRecord]:
    rows = _query_agent_rows(
        "SELECT * FROM webot_runs WHERE user_id = ? AND agent_id = ?",
        (user_id, agent_id), db_path=db_path, identity="run_id",
    )
    return sorted((_row_to_run(row) for row in rows), key=lambda item: item.updated_at, reverse=True)[:max(1, limit)]


def list_runs_for_session(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
    limit: int = 20,
) -> list[WeBotRunRecord]:
    with _connect_agent(user_id, session_id, db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM webot_runs
            WHERE user_id = ? AND session_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (user_id, session_id, max(1, limit)),
        ).fetchall()
    return [_row_to_run(row) for row in rows if row is not None]


def list_runs_for_parent_session(
    user_id: str,
    parent_session: str,
    db_path: str | os.PathLike | None = None,
    limit: int = 20,
    run_kind: str | None = None,
) -> list[WeBotRunRecord]:
    sql = "SELECT * FROM webot_runs WHERE user_id = ? AND parent_session = ?"
    params: tuple[Any, ...] = (user_id, parent_session)
    if run_kind:
        sql += " AND run_kind = ?"
        params += (run_kind,)
    rows = _query_agent_rows(sql, params, db_path=db_path, identity="run_id")
    return sorted((_row_to_run(row) for row in rows), key=lambda item: item.updated_at, reverse=True)[:max(1, limit)]


def list_child_runs(
    user_id: str,
    parent_run_id: str,
    db_path: str | os.PathLike | None = None,
    limit: int = 50,
) -> list[WeBotRunRecord]:
    rows = _query_agent_rows(
        "SELECT * FROM webot_runs WHERE user_id = ? AND parent_run_id = ?",
        (user_id, parent_run_id), db_path=db_path, identity="run_id",
    )
    return sorted((_row_to_run(row) for row in rows), key=lambda item: item.updated_at, reverse=True)[:max(1, limit)]


def get_latest_run_for_agent(
    user_id: str,
    agent_id: str,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    records = list_runs_for_agent(user_id, agent_id, db_path=db_path, limit=1)
    return records[0] if records else None


def list_recoverable_runs(db_path: str | os.PathLike | None = None) -> list[WeBotRunRecord]:
    rows = _query_agent_rows(
        "SELECT * FROM webot_runs",
        db_path=db_path, identity="run_id",
    )
    return sorted(
        (item for item in (_row_to_run(row) for row in rows)
         if item.status in {"queued", "running", "cancelling"}
         and item.run_kind in {"subagent", "ultraplan"}),
        key=lambda item: item.updated_at,
    )


def update_run_status(
    run_id: str,
    user_id: str,
    *,
    status: str | None = None,
    last_error: str | None = None,
    last_result: str | None = None,
    attempt_delta: int = 0,
    parent_session: str | None = None,
    run_kind: str | None = None,
    mode: str | None = None,
    parent_run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    worker_id: str | None = None,
    lease_expires_at: str | None = None,
    heartbeat_at: str | None = None,
    interrupt_requested: bool | None = None,
    clear_worker: bool = False,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    record = get_run(run_id, user_id, db_path=db_path)
    if record is None:
        return None
    updated = WeBotRunRecord(
        run_id=record.run_id,
        user_id=record.user_id,
        agent_id=record.agent_id,
        session_id=record.session_id,
        parent_session=parent_session if parent_session is not None else record.parent_session,
        agent_type=record.agent_type,
        title=record.title,
        input_text=record.input_text,
        status=status or record.status,
        timeout_seconds=record.timeout_seconds,
        max_turns=record.max_turns,
        wait_mode=record.wait_mode,
        attempt_count=record.attempt_count + attempt_delta,
        run_kind=run_kind if run_kind is not None else record.run_kind,
        mode=mode if mode is not None else record.mode,
        parent_run_id=parent_run_id if parent_run_id is not None else record.parent_run_id,
        metadata=dict(metadata) if metadata is not None else record.metadata,
        worker_id="" if clear_worker else (worker_id if worker_id is not None else record.worker_id),
        lease_expires_at="" if clear_worker else (
            lease_expires_at if lease_expires_at is not None else record.lease_expires_at
        ),
        heartbeat_at="" if clear_worker else (
            heartbeat_at if heartbeat_at is not None else record.heartbeat_at
        ),
        interrupt_requested=(
            interrupt_requested if interrupt_requested is not None else record.interrupt_requested
        ),
        last_error=last_error if last_error is not None else record.last_error,
        last_result=last_result if last_result is not None else record.last_result,
        created_at=record.created_at,
        updated_at=utc_now(),
    )
    return upsert_run(updated, db_path=db_path)


def add_run_attempt(
    *,
    user_id: str,
    run_id: str,
    agent_id: str,
    session_id: str,
    event_type: str,
    status: str = "",
    details: str = "",
    worker_id: str = "",
    attempt_id: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> RunAttemptRecord:
    record = RunAttemptRecord(
        attempt_id=attempt_id or f"attempt-{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        run_id=run_id,
        agent_id=agent_id,
        session_id=session_id,
        event_type=event_type,
        status=status,
        details=details,
        worker_id=worker_id,
        created_at=utc_now(),
    )
    with _connect_agent(user_id, session_id, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_run_attempts (
                attempt_id, user_id, run_id, agent_id, session_id, event_type,
                status, details, worker_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.attempt_id,
                record.user_id,
                record.run_id,
                record.agent_id,
                record.session_id,
                record.event_type,
                record.status,
                record.details,
                record.worker_id,
                record.created_at,
            ),
        )
        conn.commit()
    return record


def list_run_attempts(
    user_id: str,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
    db_path: str | os.PathLike | None = None,
    limit: int = 50,
) -> list[RunAttemptRecord]:
    query = ["SELECT * FROM webot_run_attempts WHERE user_id = ?"]
    params: list[Any] = [user_id]
    if session_id:
        query.append("AND session_id = ?")
        params.append(session_id)
    if run_id:
        query.append("AND run_id = ?")
        params.append(run_id)
    if agent_id:
        query.append("AND agent_id = ?")
        params.append(agent_id)
    if session_id:
        with _connect_agent(user_id, session_id, db_path) as conn:
            rows = conn.execute(" ".join(query), params).fetchall()
    else:
        rows = _query_agent_rows(" ".join(query), params, db_path=db_path, identity="attempt_id")
    return sorted((_row_to_attempt(row) for row in rows), key=lambda item: item.created_at, reverse=True)[:max(1, limit)]


def claim_run_lease(
    run_id: str,
    user_id: str,
    *,
    worker_id: str,
    lease_seconds: int = 120,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    record = get_run(run_id, user_id, db_path=db_path)
    if record is None:
        return None
    if record.worker_id and record.worker_id != worker_id and is_timestamp_active(record.lease_expires_at):
        return None
    now = utc_now()
    return update_run_status(
        run_id,
        user_id,
        worker_id=worker_id,
        heartbeat_at=now,
        lease_expires_at=_future_timestamp(seconds=max(15, lease_seconds)),
        db_path=db_path,
    )


def heartbeat_run_lease(
    run_id: str,
    user_id: str,
    *,
    worker_id: str,
    lease_seconds: int = 120,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    record = get_run(run_id, user_id, db_path=db_path)
    if record is None:
        return None
    if record.worker_id and record.worker_id != worker_id and is_timestamp_active(record.lease_expires_at):
        return None
    now = utc_now()
    return update_run_status(
        run_id,
        user_id,
        worker_id=worker_id,
        heartbeat_at=now,
        lease_expires_at=_future_timestamp(seconds=max(15, lease_seconds)),
        db_path=db_path,
    )


def release_run_lease(
    run_id: str,
    user_id: str,
    *,
    worker_id: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    record = get_run(run_id, user_id, db_path=db_path)
    if record is None:
        return None
    if worker_id and record.worker_id and record.worker_id != worker_id:
        return record
    return update_run_status(
        run_id,
        user_id,
        worker_id="",
        lease_expires_at="",
        heartbeat_at="",
        db_path=db_path,
    )


def request_run_interrupt(
    run_id: str,
    user_id: str,
    *,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    return update_run_status(
        run_id,
        user_id,
        interrupt_requested=True,
        db_path=db_path,
    )


def clear_run_interrupt(
    run_id: str,
    user_id: str,
    *,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    return update_run_status(
        run_id,
        user_id,
        interrupt_requested=False,
        db_path=db_path,
    )


def save_session_state(
    user_id: str,
    session_id: str,
    *,
    mode: str = "execute",
    status: str = "active",
    summary: str = "",
    db_path: str | os.PathLike | None = None,
) -> SessionStateRecord:
    from webot.runtime import normalize_session_mode
    normalized_mode = normalize_session_mode(mode)
    normalized_status = (status or "active").strip().lower() or "active"
    now = utc_now()
    with _connect_agent(user_id, session_id, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_session_state (
                user_id, session_id, mode, status, summary, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, session_id) DO UPDATE SET
                mode=excluded.mode,
                status=excluded.status,
                summary=excluded.summary,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                session_id,
                normalized_mode,
                normalized_status,
                summary.strip(),
                now,
                now,
            ),
        )
        conn.commit()
    return get_session_state(user_id, session_id, db_path=db_path)


def get_session_state(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
) -> SessionStateRecord:
    with _connect_agent(user_id, session_id, db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_session_state
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        ).fetchone()
    record = _row_to_session_state(row)
    if record is not None:
        return record
    now = utc_now()
    return SessionStateRecord(
        user_id=user_id,
        session_id=session_id,
        mode="execute",
        status="active",
        summary="",
        updated_at=now,
        created_at=now,
    )


def create_inbox_message(
    user_id: str,
    source_session: str = "",
    target_session: str = "",
    content: str = "",
    *,
    body: str | None = None,
    title: str = "",
    target_agent_id: str = "",
    source_agent_id: str = "",
    source_label: str = "",
    wait_for_idle: bool = True,
    metadata: dict[str, Any] | None = None,
    message_id: str | None = None,
    delivery_status: str = "queued",
    db_path: str | os.PathLike | None = None,
) -> InboxMessageRecord:
    normalized_content = body if body is not None else content
    merged_metadata = dict(metadata or {})
    if source_agent_id and "source_agent_id" not in merged_metadata:
        merged_metadata["source_agent_id"] = source_agent_id
    if source_label and "source_label" not in merged_metadata:
        merged_metadata["source_label"] = source_label
    normalized_status = (delivery_status or "queued").strip().lower() or "queued"
    if normalized_status == "pending":
        normalized_status = "queued"
    record = InboxMessageRecord(
        message_id=message_id or f"inbox-{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        source_session=source_session or "default",
        target_session=target_session or "default",
        target_agent_id=target_agent_id or "",
        title=title.strip()[:160],
        content=normalized_content,
        delivery_status=normalized_status,
        wait_for_idle=wait_for_idle,
        metadata=merged_metadata,
        created_at=utc_now(),
        delivered_at="",
        read_at="",
    )
    with _connect_agent(user_id, record.target_session, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_session_inbox (
                message_id, user_id, source_session, target_session, target_agent_id,
                title, content, delivery_status, wait_for_idle, metadata_json,
                created_at, delivered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.message_id,
                record.user_id,
                record.source_session,
                record.target_session,
                record.target_agent_id,
                record.title,
                record.content,
                record.delivery_status,
                1 if record.wait_for_idle else 0,
                _json_dumps(record.metadata),
                record.created_at,
                record.delivered_at,
            ),
        )
        conn.commit()
    return record


def list_inbox_messages(
    user_id: str,
    target_session: str,
    *,
    status: str | None = None,
    db_path: str | os.PathLike | None = None,
    limit: int | None = 50,
    oldest_first: bool = False,
) -> list[InboxMessageRecord]:
    query = [
        "SELECT * FROM webot_session_inbox WHERE user_id = ? AND target_session = ?",
    ]
    params: list[Any] = [user_id, target_session]
    if status == "unread":
        query.append("AND read_at = ''")
    elif status == "read":
        query.append("AND read_at != ''")
    elif status:
        query.append("AND delivery_status = ?")
        params.append(status)
    direction = "ASC" if oldest_first else "DESC"
    query.append(f"ORDER BY created_at {direction}, rowid {direction}")
    if limit is not None:
        query.append("LIMIT ?")
        params.append(max(1, limit))
    with _connect_agent(user_id, target_session, db_path) as conn:
        rows = conn.execute(" ".join(query), params).fetchall()
    return [_row_to_inbox_message(row) for row in rows if row is not None]


def list_queued_inbox_targets(*, db_path: str | os.PathLike | None = None) -> list[tuple[str, str]]:
    """Find durable inboxes to resume after the agent service restarts."""
    rows = _query_agent_rows(
        "SELECT message_id, user_id, target_session, delivery_status FROM webot_session_inbox",
        db_path=db_path, identity="message_id",
    )
    return sorted({(row["user_id"], row["target_session"]) for row in rows
                   if row["delivery_status"] == "queued"})


def get_inbox_message(
    user_id: str,
    target_session: str,
    message_id: str,
    *,
    db_path: str | os.PathLike | None = None,
) -> InboxMessageRecord | None:
    """A message ID is only readable in its owning user's target session."""
    with _connect_agent(user_id, target_session, db_path) as conn:
        row = conn.execute(
            "SELECT * FROM webot_session_inbox WHERE user_id = ? AND target_session = ? AND message_id = ?",
            (user_id, target_session, message_id),
        ).fetchone()
    return _row_to_inbox_message(row)


def mark_inbox_read(
    user_id: str,
    target_session: str,
    message_ids: list[str] | None = None,
    *,
    db_path: str | os.PathLike | None = None,
) -> int:
    """Mark selected or all unread messages read, without crossing a session boundary."""
    with _connect_agent(user_id, target_session, db_path) as conn:
        selected = set(message_ids) if message_ids is not None else None
        if selected is not None and not selected:
            return 0
        rows = conn.execute(
            "SELECT message_id, delivery_status, metadata_json FROM webot_session_inbox "
            "WHERE user_id = ? AND target_session = ? AND read_at = ''",
            (user_id, target_session),
        ).fetchall()
        eligible = [
            row["message_id"] for row in rows
            if (selected is None or row["message_id"] in selected)
            # A synchronous sender is waiting for this exact turn's reply.
            # Marking it read before the worker runs would strand its waiter.
            and not (
                row["delivery_status"] == "queued"
                and _json_loads_dict(row["metadata_json"]).get("wait_reply")
            )
        ]
        now = utc_now()
        cursor = conn.executemany(
            "UPDATE webot_session_inbox SET read_at = ?, delivery_status = 'delivered', "
            "delivered_at = CASE WHEN delivered_at = '' THEN ? ELSE delivered_at END "
            "WHERE user_id = ? AND target_session = ? AND message_id = ? AND read_at = ''",
            [(now, now, user_id, target_session, message_id) for message_id in eligible],
        )
        conn.commit()
        return cursor.rowcount


def mark_inbox_handled(
    user_id: str,
    target_session: str,
    message_id: str,
    *,
    db_path: str | os.PathLike | None = None,
) -> bool:
    """Atomically mark a synchronous inbox turn delivered and read."""
    now = utc_now()
    with _connect_agent(user_id, target_session, db_path) as conn:
        cursor = conn.execute(
            "UPDATE webot_session_inbox SET delivery_status = 'delivered', delivered_at = ?, read_at = ? "
            "WHERE user_id = ? AND target_session = ? AND message_id = ? AND delivery_status = 'queued'",
            (now, now, user_id, target_session, message_id),
        )
        conn.commit()
        return cursor.rowcount == 1


def update_inbox_message_status(
    message_id: str,
    user_id: str,
    *,
    status: str,
    db_path: str | os.PathLike | None = None,
) -> InboxMessageRecord | None:
    normalized_status = (status or "").strip().lower()
    if not normalized_status:
        return None
    delivered_at = utc_now() if normalized_status == "delivered" else ""
    target_session = _record_session(
        "webot_session_inbox", "message_id", message_id, user_id,
        session_column="target_session", db_path=db_path,
    )
    if target_session is None:
        return None
    with _connect_agent(user_id, target_session, db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_session_inbox
            WHERE message_id = ? AND user_id = ?
            """,
            (message_id, user_id),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            UPDATE webot_session_inbox
            SET delivery_status = ?, delivered_at = ?
            WHERE message_id = ? AND user_id = ?
            """,
            (normalized_status, delivered_at, message_id, user_id),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT * FROM webot_session_inbox
            WHERE message_id = ? AND user_id = ?
            """,
            (message_id, user_id),
        ).fetchone()
    return _row_to_inbox_message(row)


def create_runtime_artifact(
    *,
    user_id: str,
    session_id: str,
    kind: str,
    title: str = "",
    summary: str = "",
    path: str = "",
    run_id: str = "",
    metadata: dict[str, Any] | None = None,
    artifact_id: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> RuntimeArtifactRecord:
    record = RuntimeArtifactRecord(
        artifact_id=artifact_id or f"artifact-{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        kind=(kind or "artifact").strip() or "artifact",
        title=title.strip(),
        summary=summary,
        path=path,
        metadata=dict(metadata or {}),
        created_at=utc_now(),
    )
    with _connect_agent(user_id, session_id, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_runtime_artifacts (
                artifact_id, user_id, session_id, run_id, kind, title, summary,
                path, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
                session_id=excluded.session_id,
                run_id=excluded.run_id,
                kind=excluded.kind,
                title=excluded.title,
                summary=excluded.summary,
                path=excluded.path,
                metadata_json=excluded.metadata_json
            """,
            (
                record.artifact_id,
                record.user_id,
                record.session_id,
                record.run_id,
                record.kind,
                record.title,
                record.summary,
                record.path,
                _json_dumps(record.metadata),
                record.created_at,
            ),
        )
        conn.commit()
    return record


def update_runtime_artifact(
    artifact_id: str,
    user_id: str,
    *,
    session_id: str | None = None,
    run_id: str | None = None,
    kind: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    path: str | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: str | os.PathLike | None = None,
) -> RuntimeArtifactRecord | None:
    record = get_runtime_artifact(artifact_id, user_id, db_path=db_path)
    if record is None:
        return None
    updated = RuntimeArtifactRecord(
        artifact_id=record.artifact_id,
        user_id=record.user_id,
        session_id=session_id if session_id is not None else record.session_id,
        run_id=run_id if run_id is not None else record.run_id,
        kind=kind if kind is not None else record.kind,
        title=title if title is not None else record.title,
        summary=summary if summary is not None else record.summary,
        path=path if path is not None else record.path,
        metadata=metadata if metadata is not None else record.metadata,
        created_at=record.created_at,
    )
    result = create_runtime_artifact(
        artifact_id=updated.artifact_id,
        user_id=updated.user_id,
        session_id=updated.session_id,
        run_id=updated.run_id,
        kind=updated.kind,
        title=updated.title,
        summary=updated.summary,
        path=updated.path,
        metadata=updated.metadata,
        db_path=db_path,
    )
    if (updated.session_id != record.session_id and db_path is None
            and DEFAULT_DB_PATH is None):
        with _connect_agent(user_id, record.session_id, db_path) as conn:
            conn.execute("DELETE FROM webot_runtime_artifacts WHERE artifact_id = ? AND user_id = ?",
                         (artifact_id, user_id))
            conn.commit()
    return result


def get_runtime_artifact(
    artifact_id: str,
    user_id: str,
    db_path: str | os.PathLike | None = None,
) -> RuntimeArtifactRecord | None:
    session_id = _record_session("webot_runtime_artifacts", "artifact_id", artifact_id, user_id, db_path=db_path)
    if session_id is None:
        return None
    with _connect_agent(user_id, session_id, db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_runtime_artifacts
            WHERE artifact_id = ? AND user_id = ?
            """,
            (artifact_id, user_id),
        ).fetchone()
    return _row_to_artifact(row)


def list_runtime_artifacts(
    user_id: str,
    session_id: str | None = None,
    *,
    kind: str | None = None,
    db_path: str | os.PathLike | None = None,
    limit: int = 50,
) -> list[RuntimeArtifactRecord]:
    query = ["SELECT * FROM webot_runtime_artifacts WHERE user_id = ?"]
    params: list[Any] = [user_id]
    if session_id:
        query.append("AND session_id = ?")
        params.append(session_id)
    if kind:
        query.append("AND kind = ?")
        params.append(kind)
    if session_id:
        with _connect_agent(user_id, session_id, db_path) as conn:
            rows = conn.execute(" ".join(query), params).fetchall()
    else:
        rows = _query_agent_rows(" ".join(query), params, db_path=db_path, identity="artifact_id")
    return sorted((_row_to_artifact(row) for row in rows), key=lambda item: item.created_at, reverse=True)[:max(1, limit)]


def record_runtime_artifact(
    user_id: str,
    session_id: str,
    *,
    artifact_kind: str,
    title: str = "",
    path: str = "",
    preview: str = "",
    metadata: dict[str, Any] | None = None,
    run_id: str = "",
    artifact_id: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> RuntimeArtifactRecord:
    return create_runtime_artifact(
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        kind=artifact_kind,
        title=title,
        summary=preview,
        path=path,
        metadata=metadata,
        artifact_id=artifact_id,
        db_path=db_path,
    )


def count_inbox_messages(
    user_id: str,
    target_session: str,
    *,
    status: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> int:
    query = [
        "SELECT COUNT(*) AS count FROM webot_session_inbox WHERE user_id = ? AND target_session = ?",
    ]
    params: list[Any] = [user_id, target_session]
    if status == "unread":
        query.append("AND read_at = ''")
    elif status == "read":
        query.append("AND read_at != ''")
    elif status:
        query.append("AND delivery_status = ?")
        params.append(status)
    with _connect_agent(user_id, target_session, db_path) as conn:
        row = conn.execute(" ".join(query), params).fetchone()
    if row is None:
        return 0
    return int(row["count"])


def list_run_events(
    user_id: str,
    run_id: str,
    *,
    limit: int = 20,
    db_path: str | os.PathLike | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in list_run_attempts(user_id, run_id=run_id, db_path=db_path, limit=limit):
        payload = _json_loads_dict(item.details)
        event = {
            "attempt_id": item.attempt_id,
            "event_type": item.event_type,
            "status": item.status,
            "message": str(payload.get("message") or ""),
            "details": payload.get("details", item.details),
            "attempt": payload.get("attempt"),
            "worker_id": item.worker_id,
            "created_at": item.created_at,
        }
        events.append(event)
    return events


def list_session_run_events(
    user_id: str,
    session_id: str,
    *,
    limit: int = 50,
    db_path: str | os.PathLike | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in list_run_attempts(user_id, session_id=session_id, db_path=db_path, limit=limit):
        payload = _json_loads_dict(item.details)
        event = {
            "attempt_id": item.attempt_id,
            "run_id": item.run_id,
            "agent_id": item.agent_id,
            "event_type": item.event_type,
            "status": item.status,
            "message": str(payload.get("message") or ""),
            "details": payload.get("details", item.details),
            "attempt": payload.get("attempt"),
            "worker_id": item.worker_id,
            "created_at": item.created_at,
        }
        events.append(event)
    return events


def get_latest_active_run_for_session(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    candidates = list_runs_for_session(user_id, session_id, db_path=db_path, limit=20)
    candidates.extend(list_runs_for_parent_session(user_id, session_id, db_path=db_path, limit=20))
    candidates.sort(key=lambda item: item.updated_at, reverse=True)
    for record in candidates:
        if record.status in {"queued", "running", "cancelling"}:
            return record
    return None


def record_run_event(
    user_id: str,
    run_id: str,
    session_id: str,
    *,
    event_type: str,
    status: str = "",
    message: str = "",
    details: dict[str, Any] | str | None = None,
    attempt: int | None = None,
    worker_id: str = "",
    agent_id: str = "",
    db_path: str | os.PathLike | None = None,
) -> RunAttemptRecord:
    run_record = get_run(run_id, user_id, db_path=db_path)
    serialized_details = _json_dumps(
        {
            "message": message,
            "details": details if details is not None else {},
            "attempt": attempt,
        }
    )
    return add_run_attempt(
        user_id=user_id,
        run_id=run_id,
        agent_id=agent_id or (run_record.agent_id if run_record is not None else ""),
        session_id=session_id,
        event_type=event_type,
        status=status,
        details=serialized_details,
        worker_id=worker_id or (run_record.worker_id if run_record is not None else ""),
        db_path=db_path,
    )


def claim_run_worker(
    run_id: str,
    user_id: str,
    *,
    worker_id: str,
    lease_seconds: int = 120,
    status: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    claimed = claim_run_lease(
        run_id,
        user_id,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        db_path=db_path,
    )
    if claimed is None or status is None:
        return claimed
    return update_run_status(
        run_id,
        user_id,
        status=status,
        db_path=db_path,
    )


def heartbeat_run(
    run_id: str,
    user_id: str,
    *,
    worker_id: str,
    lease_seconds: int = 120,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    return heartbeat_run_lease(
        run_id,
        user_id,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        db_path=db_path,
    )


def release_run_worker(
    run_id: str,
    user_id: str,
    *,
    worker_id: str,
    status: str | None = None,
    last_result: str | None = None,
    last_error: str | None = None,
    clear_interrupt: bool = False,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    record = get_run(run_id, user_id, db_path=db_path)
    if record is None:
        return None
    if record.worker_id and record.worker_id != worker_id:
        return record
    return update_run_status(
        run_id,
        user_id,
        status=status,
        last_result=last_result,
        last_error=last_error,
        interrupt_requested=False if clear_interrupt else None,
        clear_worker=True,
        db_path=db_path,
    )


def mark_inbox_delivered(
    user_id: str,
    message_ids: list[str],
    *,
    db_path: str | os.PathLike | None = None,
) -> int:
    delivered = 0
    for message_id in message_ids:
        if update_inbox_message_status(
            message_id,
            user_id,
            status="delivered",
            db_path=db_path,
        ) is not None:
            delivered += 1
    return delivered


def save_session_mode(
    user_id: str,
    session_id: str,
    *,
    mode: str = "execute",
    reason: str = "",
    status: str = "active",
    db_path: str | os.PathLike | None = None,
) -> dict[str, Any]:
    record = save_session_state(
        user_id,
        session_id,
        mode=mode,
        status=status,
        summary=reason,
        db_path=db_path,
    )
    return {
        "mode": record.mode,
        "status": record.status,
        "reason": record.summary,
        "updated_at": record.updated_at,
        "created_at": record.created_at,
    }


def get_session_mode(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
) -> dict[str, Any]:
    record = get_session_state(user_id, session_id, db_path=db_path)
    return {
        "mode": record.mode,
        "status": record.status,
        "reason": record.summary,
        "updated_at": record.updated_at,
        "created_at": record.created_at,
    }


def _normalize_plan_items(items: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        step = str(item.get("step") or "").strip()
        if not step:
            continue
        status = str(item.get("status") or "pending").strip().lower()
        if status not in {"pending", "in_progress", "completed"}:
            status = "pending"
        notes = str(item.get("notes") or "").strip()
        normalized.append({"step": step, "status": status, "notes": notes})
    return normalized


def save_session_plan(
    user_id: str,
    session_id: str,
    *,
    title: str,
    status: str = "active",
    items: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
    db_path: str | os.PathLike | None = None,
) -> None:
    now = utc_now()
    normalized_items = _normalize_plan_items(items)
    normalized_status = (status or "active").strip().lower()
    if normalized_status not in {"active", "completed", "archived"}:
        normalized_status = "active"
    with _connect_agent(user_id, session_id, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_session_plans (
                user_id, session_id, title, status, items_json, metadata_json, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, session_id) DO UPDATE SET
                title=excluded.title,
                status=excluded.status,
                items_json=excluded.items_json,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                session_id,
                title.strip(),
                normalized_status,
                _json_dumps(normalized_items),
                _json_dumps(metadata or {}),
                now,
                now,
            ),
        )
        conn.commit()


def get_session_plan(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
) -> dict[str, Any] | None:
    with _connect_agent(user_id, session_id, db_path) as conn:
        row = conn.execute(
            """
            SELECT title, status, items_json, metadata_json, updated_at, created_at
            FROM webot_session_plans
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        ).fetchone()
    if row is None:
        return None
    try:
        items = json.loads(row["items_json"] or "[]")
    except json.JSONDecodeError:
        items = []
    return {
        "title": row["title"],
        "status": row["status"],
        "items": _normalize_plan_items(items),
        "metadata": _json_loads_dict(row["metadata_json"]),
        "updated_at": row["updated_at"],
        "created_at": row["created_at"],
    }


def delete_session_plan(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
) -> int:
    with _connect_agent(user_id, session_id, db_path) as conn:
        cursor = conn.execute(
            """
            DELETE FROM webot_session_plans
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        )
        conn.commit()
        return cursor.rowcount


def save_session_todos(
    user_id: str,
    session_id: str,
    *,
    items: list[dict[str, Any]],
    db_path: str | os.PathLike | None = None,
) -> None:
    now = utc_now()
    normalized_items = _normalize_plan_items(items)
    with _connect_agent(user_id, session_id, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_session_todos (
                user_id, session_id, items_json, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, session_id) DO UPDATE SET
                items_json=excluded.items_json,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                session_id,
                _json_dumps(normalized_items),
                now,
                now,
            ),
        )
        conn.commit()


def get_session_todos(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
) -> dict[str, Any] | None:
    with _connect_agent(user_id, session_id, db_path) as conn:
        row = conn.execute(
            """
            SELECT items_json, updated_at, created_at
            FROM webot_session_todos
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        ).fetchone()
    if row is None:
        return None
    try:
        items = json.loads(row["items_json"] or "[]")
    except json.JSONDecodeError:
        items = []
    return {
        "items": _normalize_plan_items(items),
        "updated_at": row["updated_at"],
        "created_at": row["created_at"],
    }


def delete_session_todos(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
) -> int:
    with _connect_agent(user_id, session_id, db_path) as conn:
        cursor = conn.execute(
            """
            DELETE FROM webot_session_todos
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        )
        conn.commit()
        return cursor.rowcount


def _default_claude_keepalive(user_id: str, session_id: str) -> ClaudeKeepaliveRecord:
    now = utc_now()
    return ClaudeKeepaliveRecord(
        user_id=user_id,
        session_id=session_id or "default",
        enabled=False,
        tool="claude",
        prompt="ping",
        model="",
        timezone="",
        start_time="06:00",
        sleep_time="23:00",
        weekdays="MTWRFSU",
        use_caffeinate=False,
        force_sleep_at_quiet_hours=False,
        monitor_command="claude-monitor --clear",
        timeout_seconds=90,
        next_run_at="",
        last_run_at="",
        last_status="idle",
        last_error="",
        last_result="",
        reset_at="",
        metadata={},
        updated_at=now,
        created_at=now,
    )


def get_claude_keepalive_state(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
) -> ClaudeKeepaliveRecord:
    with _connect_agent(user_id, session_id, db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_claude_keepalive
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id or "default"),
        ).fetchone()
    return _row_to_claude_keepalive(row) or _default_claude_keepalive(user_id, session_id or "default")


def save_claude_keepalive_state(
    user_id: str,
    session_id: str,
    *,
    enabled: bool = False,
    tool: str = "claude",
    prompt: str = "ping",
    model: str = "",
    timezone_name: str = "",
    start_time: str = "06:00",
    sleep_time: str = "23:00",
    weekdays: str = "MTWRFSU",
    use_caffeinate: bool = False,
    force_sleep_at_quiet_hours: bool = False,
    monitor_command: str = "claude-monitor --clear",
    timeout_seconds: int = 90,
    next_run_at: str = "",
    last_run_at: str = "",
    last_status: str = "idle",
    last_error: str = "",
    last_result: str = "",
    reset_at: str = "",
    metadata: dict[str, Any] | None = None,
    db_path: str | os.PathLike | None = None,
) -> ClaudeKeepaliveRecord:
    existing = get_claude_keepalive_state(user_id, session_id, db_path=db_path)
    now = utc_now()
    record = ClaudeKeepaliveRecord(
        user_id=user_id,
        session_id=session_id or "default",
        enabled=bool(enabled),
        tool=(tool or "claude").strip() or "claude",
        prompt=(prompt or existing.prompt or "ping").strip() or "ping",
        model=(model if model is not None else existing.model).strip(),
        timezone=(timezone_name if timezone_name is not None else existing.timezone).strip(),
        start_time=(start_time or existing.start_time or "06:00").strip(),
        sleep_time=(sleep_time or existing.sleep_time or "23:00").strip(),
        weekdays=(weekdays or existing.weekdays or "MTWRFSU").strip().upper() or "MTWRFSU",
        use_caffeinate=bool(use_caffeinate),
        force_sleep_at_quiet_hours=bool(force_sleep_at_quiet_hours),
        monitor_command=(monitor_command or existing.monitor_command or "claude-monitor --clear").strip(),
        timeout_seconds=max(10, min(int(timeout_seconds or existing.timeout_seconds or 90), 600)),
        next_run_at=next_run_at if next_run_at is not None else existing.next_run_at,
        last_run_at=last_run_at if last_run_at is not None else existing.last_run_at,
        last_status=last_status if last_status is not None else existing.last_status,
        last_error=last_error if last_error is not None else existing.last_error,
        last_result=last_result if last_result is not None else existing.last_result,
        reset_at=reset_at if reset_at is not None else existing.reset_at,
        metadata=dict(metadata if metadata is not None else existing.metadata),
        updated_at=now,
        created_at=existing.created_at or now,
    )
    with _connect_agent(user_id, session_id, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_claude_keepalive (
                user_id, session_id, enabled, tool, prompt, model, timezone,
                start_time, sleep_time, weekdays, use_caffeinate,
                force_sleep_at_quiet_hours, monitor_command, timeout_seconds,
                next_run_at, last_run_at, last_status, last_error, last_result,
                reset_at, metadata_json, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, session_id) DO UPDATE SET
                enabled=excluded.enabled,
                tool=excluded.tool,
                prompt=excluded.prompt,
                model=excluded.model,
                timezone=excluded.timezone,
                start_time=excluded.start_time,
                sleep_time=excluded.sleep_time,
                weekdays=excluded.weekdays,
                use_caffeinate=excluded.use_caffeinate,
                force_sleep_at_quiet_hours=excluded.force_sleep_at_quiet_hours,
                monitor_command=excluded.monitor_command,
                timeout_seconds=excluded.timeout_seconds,
                next_run_at=excluded.next_run_at,
                last_run_at=excluded.last_run_at,
                last_status=excluded.last_status,
                last_error=excluded.last_error,
                last_result=excluded.last_result,
                reset_at=excluded.reset_at,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                record.user_id,
                record.session_id,
                1 if record.enabled else 0,
                record.tool,
                record.prompt,
                record.model,
                record.timezone,
                record.start_time,
                record.sleep_time,
                record.weekdays,
                1 if record.use_caffeinate else 0,
                1 if record.force_sleep_at_quiet_hours else 0,
                record.monitor_command,
                record.timeout_seconds,
                record.next_run_at,
                record.last_run_at,
                record.last_status,
                record.last_error,
                record.last_result,
                record.reset_at,
                _json_dumps(record.metadata),
                record.updated_at,
                record.created_at,
            ),
        )
        conn.commit()
    return get_claude_keepalive_state(user_id, session_id, db_path=db_path)


def record_claude_keepalive_result(
    user_id: str,
    session_id: str,
    *,
    status: str,
    result: str = "",
    error: str = "",
    reset_at: str = "",
    next_run_at: str = "",
    metadata: dict[str, Any] | None = None,
    db_path: str | os.PathLike | None = None,
) -> ClaudeKeepaliveRecord:
    current = get_claude_keepalive_state(user_id, session_id, db_path=db_path)
    merged_metadata = dict(current.metadata)
    if metadata:
        merged_metadata.update(metadata)
    return save_claude_keepalive_state(
        user_id,
        session_id,
        enabled=current.enabled,
        tool=current.tool,
        prompt=current.prompt,
        model=current.model,
        timezone_name=current.timezone,
        start_time=current.start_time,
        sleep_time=current.sleep_time,
        weekdays=current.weekdays,
        use_caffeinate=current.use_caffeinate,
        force_sleep_at_quiet_hours=current.force_sleep_at_quiet_hours,
        monitor_command=current.monitor_command,
        timeout_seconds=current.timeout_seconds,
        next_run_at=next_run_at if next_run_at is not None else current.next_run_at,
        last_run_at=utc_now(),
        last_status=(status or "unknown").strip().lower() or "unknown",
        last_error=error or "",
        last_result=result or "",
        reset_at=reset_at if reset_at is not None else current.reset_at,
        metadata=merged_metadata,
        db_path=db_path,
    )


def add_verification_record(
    user_id: str,
    session_id: str,
    *,
    verification_id: str,
    title: str,
    status: str,
    details: str,
    db_path: str | os.PathLike | None = None,
) -> None:
    with _connect_agent(user_id, session_id, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_verifications (
                verification_id, user_id, session_id, title, status, details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                verification_id,
                user_id,
                session_id,
                title.strip(),
                status.strip().lower(),
                details,
                utc_now(),
            ),
        )
        conn.commit()


def list_verification_records(
    user_id: str,
    session_id: str,
    *,
    limit: int = 20,
    db_path: str | os.PathLike | None = None,
) -> list[dict[str, str]]:
    with _connect_agent(user_id, session_id, db_path) as conn:
        rows = conn.execute(
            """
            SELECT verification_id, title, status, details, created_at
            FROM webot_verifications
            WHERE user_id = ? AND session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, session_id, max(1, limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def create_tool_approval_request(
    user_id: str,
    session_id: str,
    *,
    approval_id: str,
    tool_name: str,
    args: dict[str, Any],
    request_reason: str,
    db_path: str | os.PathLike | None = None,
    expiry_hours: int = 12,
) -> ToolApprovalRecord:
    now = utc_now()
    args_json = _json_dumps(args or {})
    args_hash = _stable_args_hash(tool_name, args or {})
    record = ToolApprovalRecord(
        approval_id=approval_id,
        user_id=user_id,
        session_id=session_id,
        tool_name=tool_name,
        args_json=args_json,
        args_hash=args_hash,
        status="pending",
        request_reason=request_reason,
        resolution_reason="",
        created_at=now,
        updated_at=now,
        expires_at=_future_timestamp(hours=expiry_hours),
    )
    with _connect_agent(user_id, session_id, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_tool_approvals (
                approval_id, user_id, session_id, tool_name, args_json, args_hash,
                status, request_reason, resolution_reason, created_at, updated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.approval_id,
                record.user_id,
                record.session_id,
                record.tool_name,
                record.args_json,
                record.args_hash,
                record.status,
                record.request_reason,
                record.resolution_reason,
                record.created_at,
                record.updated_at,
                record.expires_at,
            ),
        )
        conn.commit()
    return record


def list_tool_approvals(
    user_id: str,
    session_id: str | None = None,
    *,
    status: str | None = None,
    db_path: str | os.PathLike | None = None,
    limit: int = 50,
) -> list[ToolApprovalRecord]:
    query = [
        "SELECT * FROM webot_tool_approvals WHERE user_id = ?",
    ]
    params: list[Any] = [user_id]
    if session_id:
        query.append("AND session_id = ?")
        params.append(session_id)
    if session_id:
        with _connect_agent(user_id, session_id, db_path) as conn:
            rows = conn.execute(" ".join(query), params).fetchall()
    else:
        rows = _query_agent_rows(" ".join(query), params, db_path=db_path, identity="approval_id")
    records = [_row_to_approval(row) for row in rows]
    if status:
        records = [record for record in records if record.status == status]
    return sorted(records, key=lambda item: item.updated_at, reverse=True)[:max(1, limit)]


def find_active_approval_for_action(
    user_id: str,
    session_id: str,
    tool_name: str,
    args: dict[str, Any],
    db_path: str | os.PathLike | None = None,
) -> ToolApprovalRecord | None:
    args_hash = _stable_args_hash(tool_name, args or {})
    now = utc_now()
    with _connect_agent(user_id, session_id, db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_tool_approvals
            WHERE user_id = ?
              AND session_id = ?
              AND tool_name = ?
              AND args_hash = ?
              AND status = 'approved'
              AND expires_at >= ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id, session_id, tool_name, args_hash, now),
        ).fetchone()
    return _row_to_approval(row)


def find_pending_approval_for_action(
    user_id: str,
    session_id: str,
    tool_name: str,
    args: dict[str, Any],
    db_path: str | os.PathLike | None = None,
) -> ToolApprovalRecord | None:
    args_hash = _stable_args_hash(tool_name, args or {})
    now = utc_now()
    with _connect_agent(user_id, session_id, db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_tool_approvals
            WHERE user_id = ?
              AND session_id = ?
              AND tool_name = ?
              AND args_hash = ?
              AND status = 'pending'
              AND expires_at >= ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id, session_id, tool_name, args_hash, now),
        ).fetchone()
    return _row_to_approval(row)


def update_tool_approval_status(
    approval_id: str,
    user_id: str,
    *,
    status: str,
    resolution_reason: str = "",
    expected_status: str | None = None,
    db_path: str | os.PathLike | None = None,
) -> ToolApprovalRecord | None:
    expected_statuses = {
        "approved": ("pending",),
        "denied": ("pending", "approved"),
        "used": ("approved",),
        "expired": ("pending", "approved"),
    }
    if status not in expected_statuses:
        raise ValueError(f"Unsupported approval status: {status}")
    allowed_statuses = expected_statuses[status]
    if expected_status is not None:
        if expected_status not in allowed_statuses:
            raise ValueError(f"Invalid expected approval status: {expected_status}")
        allowed_statuses = (expected_status,)
    session_id = _record_session("webot_tool_approvals", "approval_id", approval_id, user_id, db_path=db_path)
    if session_id is None:
        return None
    with _connect_agent(user_id, session_id, db_path) as conn:
        updated_at = utc_now()
        placeholders = ",".join("?" for _ in allowed_statuses)
        expiry_condition = "" if status == "expired" else " AND expires_at > ?"
        params = [status, resolution_reason, resolution_reason, updated_at, approval_id, user_id, *allowed_statuses]
        if status != "expired":
            params.append(updated_at)
        cursor = conn.execute(
            f"""
            UPDATE webot_tool_approvals
            SET status = ?, resolution_reason = CASE WHEN ? = '' THEN resolution_reason ELSE ? END, updated_at = ?
            WHERE approval_id = ? AND user_id = ?
              AND status IN ({placeholders}){expiry_condition}
            """,
            params,
        )
        conn.commit()
        if cursor.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT * FROM webot_tool_approvals WHERE approval_id = ? AND user_id = ?",
            (approval_id, user_id),
        ).fetchone()
    return _row_to_approval(row)


def get_tool_approval(
    approval_id: str,
    user_id: str,
    db_path: str | os.PathLike | None = None,
) -> ToolApprovalRecord | None:
    session_id = _record_session("webot_tool_approvals", "approval_id", approval_id, user_id, db_path=db_path)
    if session_id is None:
        return None
    with _connect_agent(user_id, session_id, db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_tool_approvals
            WHERE approval_id = ? AND user_id = ?
            """,
            (approval_id, user_id),
        ).fetchone()
    return _row_to_approval(row)


def set_approval_review_metadata(approval_id: str, user_id: str, metadata: dict) -> None:
    session_id = _record_session("webot_tool_approvals", "approval_id", approval_id, user_id)
    if session_id is None:
        return
    with _connect_agent(user_id, session_id) as conn:
        conn.execute(
            "UPDATE webot_tool_approvals SET review_metadata_json = ? WHERE approval_id = ? AND user_id = ? AND status IN ('pending', 'approved')",
            (_json_dumps(metadata), approval_id, user_id),
        )
        conn.commit()


def record_tool_execution(approval_id: str, user_id: str, *, status: str, detail: str = "") -> None:
    if not approval_id:
        return
    session_id = _record_session("webot_tool_approvals", "approval_id", approval_id, user_id)
    if session_id is None:
        return
    with _connect_agent(user_id, session_id) as conn:
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT review_metadata_json FROM webot_tool_approvals WHERE approval_id = ? AND user_id = ? AND status = 'used'", (approval_id, user_id)).fetchone()
        if row is None:
            return
        metadata = json.loads(row[0])
        metadata["execution"] = {"status": status, "at": utc_now(), "detail": detail[:500]}
        conn.execute("UPDATE webot_tool_approvals SET review_metadata_json = ? WHERE approval_id = ? AND user_id = ?", (json.dumps(metadata, ensure_ascii=False), approval_id, user_id))


def issue_execution_permit(user_id: str, session_id: str, tool_name: str, args: dict, binding_hash: str) -> None:
    from datetime import timedelta
    import uuid
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    with _connect_agent(user_id, session_id) as conn:
        conn.execute("DELETE FROM webot_execution_permits WHERE expires_at <= ? OR consumed = 1", (utc_now(),))
        conn.execute(
            "INSERT INTO webot_execution_permits VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (uuid.uuid4().hex, user_id, session_id, tool_name, _stable_args_hash(tool_name, args), binding_hash, expires_at),
        )
        conn.commit()


def consume_execution_permit(user_id: str, session_id: str, tool_name: str, args: dict, binding_hash: str) -> bool:
    with _connect_agent(user_id, session_id) as conn:
        cursor = conn.execute("""
            UPDATE webot_execution_permits SET consumed = 1 WHERE permit_id = (
                SELECT permit_id FROM webot_execution_permits
                WHERE user_id = ? AND session_id = ? AND tool_name = ? AND args_hash = ?
                  AND binding_hash = ? AND consumed = 0 AND expires_at > ? LIMIT 1
            ) AND consumed = 0
        """, (user_id, session_id, tool_name, _stable_args_hash(tool_name, args), binding_hash, utc_now()))
        conn.commit()
        return cursor.rowcount == 1


def save_memory_state(
    user_id: str,
    session_id: str,
    *,
    project_slug: str = "",
    memory_dir: str = "",
    index_path: str = "",
    kairos_enabled: bool = False,
    dream_status: str = "idle",
    active_run_id: str = "",
    last_dream_at: str = "",
    daily_log_path: str = "",
    metadata: dict[str, Any] | None = None,
    db_path: str | os.PathLike | None = None,
) -> MemoryStateRecord:
    now = utc_now()
    with _connect_agent(user_id, session_id, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_memory_state (
                user_id, session_id, project_slug, memory_dir, index_path,
                kairos_enabled, dream_status, active_run_id, last_dream_at,
                daily_log_path, metadata_json, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, session_id) DO UPDATE SET
                project_slug=excluded.project_slug,
                memory_dir=excluded.memory_dir,
                index_path=excluded.index_path,
                kairos_enabled=excluded.kairos_enabled,
                dream_status=excluded.dream_status,
                active_run_id=excluded.active_run_id,
                last_dream_at=excluded.last_dream_at,
                daily_log_path=excluded.daily_log_path,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                session_id,
                project_slug,
                memory_dir,
                index_path,
                1 if kairos_enabled else 0,
                dream_status,
                active_run_id,
                last_dream_at,
                daily_log_path,
                _json_dumps(metadata or {}),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM webot_memory_state WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
    return _row_to_memory_state(row)  # type: ignore[arg-type]


def get_memory_state(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
) -> MemoryStateRecord:
    with _connect_agent(user_id, session_id, db_path) as conn:
        row = conn.execute(
            "SELECT * FROM webot_memory_state WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
    record = _row_to_memory_state(row)
    if record is not None:
        return record
    now = utc_now()
    return MemoryStateRecord(
        user_id=user_id,
        session_id=session_id,
        project_slug="",
        memory_dir="",
        index_path="",
        kairos_enabled=False,
        dream_status="idle",
        active_run_id="",
        last_dream_at="",
        daily_log_path="",
        metadata={},
        updated_at=now,
        created_at=now,
    )


def save_voice_state(
    user_id: str,
    session_id: str,
    *,
    enabled: bool = False,
    auto_read_aloud: bool = False,
    recording_supported: bool = True,
    tts_model: str = "",
    tts_voice: str = "",
    stt_model: str = "",
    last_transcript: str = "",
    metadata: dict[str, Any] | None = None,
    db_path: str | os.PathLike | None = None,
) -> VoiceStateRecord:
    now = utc_now()
    with _connect_agent(user_id, session_id, db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_voice_state (
                user_id, session_id, enabled, auto_read_aloud, recording_supported,
                tts_model, tts_voice, stt_model, last_transcript, metadata_json,
                updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, session_id) DO UPDATE SET
                enabled=excluded.enabled,
                auto_read_aloud=excluded.auto_read_aloud,
                recording_supported=excluded.recording_supported,
                tts_model=excluded.tts_model,
                tts_voice=excluded.tts_voice,
                stt_model=excluded.stt_model,
                last_transcript=excluded.last_transcript,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                session_id,
                1 if enabled else 0,
                1 if auto_read_aloud else 0,
                1 if recording_supported else 0,
                tts_model,
                tts_voice,
                stt_model,
                last_transcript,
                _json_dumps(metadata or {}),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM webot_voice_state WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
    return _row_to_voice_state(row)  # type: ignore[arg-type]


def get_voice_state(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
) -> VoiceStateRecord:
    with _connect_agent(user_id, session_id, db_path) as conn:
        row = conn.execute(
            "SELECT * FROM webot_voice_state WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        ).fetchone()
    record = _row_to_voice_state(row)
    if record is not None:
        return record
    now = utc_now()
    return VoiceStateRecord(
        user_id=user_id,
        session_id=session_id,
        enabled=False,
        auto_read_aloud=False,
        recording_supported=True,
        tts_model="",
        tts_voice="",
        stt_model="",
        last_transcript="",
        metadata={},
        updated_at=now,
        created_at=now,
    )
