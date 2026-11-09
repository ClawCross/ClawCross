"""
Persistent runtime primitives for WeBot.

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

from utils.runtime_paths import DATA_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = DATA_DIR / "webot_runtime.db"


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    explicit.parent.mkdir(parents=True, exist_ok=True)
    return explicit


def _connect(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(get_runtime_db_path(db_path), factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
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
            delivered_at TEXT NOT NULL DEFAULT ''
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
        CREATE TABLE IF NOT EXISTS webot_bridge_sessions (
            bridge_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            label TEXT NOT NULL DEFAULT '',
            attach_code TEXT NOT NULL DEFAULT '',
            websocket_path TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'detached',
            connection_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            last_error TEXT NOT NULL DEFAULT '',
            last_attached_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_bridge_sessions_lookup
        ON webot_bridge_sessions(user_id, session_id, updated_at DESC)
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
        CREATE TABLE IF NOT EXISTS webot_buddy_state (
            user_id TEXT PRIMARY KEY,
            seed TEXT NOT NULL DEFAULT '',
            species TEXT NOT NULL DEFAULT '',
            rarity TEXT NOT NULL DEFAULT '',
            shiny INTEGER NOT NULL DEFAULT 0,
            eye TEXT NOT NULL DEFAULT '',
            hat TEXT NOT NULL DEFAULT '',
            stats_json TEXT NOT NULL DEFAULT '{}',
            soul_name TEXT NOT NULL DEFAULT '',
            soul_personality TEXT NOT NULL DEFAULT '',
            reaction TEXT NOT NULL DEFAULT '',
            hatched_at TEXT NOT NULL DEFAULT '',
            last_interaction_at TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL
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
        CREATE TABLE IF NOT EXISTS webot_session_goals (
            goal_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            priority TEXT NOT NULL DEFAULT 'normal',
            parent_goal_id TEXT NOT NULL DEFAULT '',
            owner_session TEXT NOT NULL DEFAULT '',
            metrics_json TEXT NOT NULL DEFAULT '{}',
            budget_tokens INTEGER NOT NULL DEFAULT 0,
            spent_tokens INTEGER NOT NULL DEFAULT 0,
            budget_usd REAL NOT NULL DEFAULT 0,
            spent_usd REAL NOT NULL DEFAULT 0,
            heartbeat_status TEXT NOT NULL DEFAULT 'idle',
            heartbeat_at TEXT NOT NULL DEFAULT '',
            last_report TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webot_session_goals_lookup
        ON webot_session_goals(user_id, session_id, status, updated_at DESC)
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
    conn.execute(
        """
        UPDATE webot_session_inbox
        SET delivery_status = 'queued'
        WHERE delivery_status = 'pending'
        """
    )
    return conn


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

    @property
    def body(self) -> str:
        return self.content

    @property
    def status(self) -> str:
        return self.delivery_status

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
class BridgeSessionRecord:
    bridge_id: str
    user_id: str
    session_id: str
    role: str
    label: str
    attach_code: str
    websocket_path: str
    status: str
    connection_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""
    last_attached_at: str = ""
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
class BuddyStateRecord:
    user_id: str
    seed: str
    species: str
    rarity: str
    shiny: bool
    eye: str
    hat: str
    stats: dict[str, int] = field(default_factory=dict)
    soul_name: str = ""
    soul_personality: str = ""
    reaction: str = ""
    hatched_at: str = ""
    last_interaction_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""
    created_at: str = ""

    @property
    def stats_json(self) -> str:
        return _json_dumps(self.stats)

    @property
    def metadata_json(self) -> str:
        return _json_dumps(self.metadata)


@dataclass(frozen=True)
class SessionGoalRecord:
    goal_id: str
    user_id: str
    session_id: str
    title: str
    description: str
    status: str
    priority: str
    parent_goal_id: str
    owner_session: str
    metrics: dict[str, Any] = field(default_factory=dict)
    budget_tokens: int = 0
    spent_tokens: int = 0
    budget_usd: float = 0.0
    spent_usd: float = 0.0
    heartbeat_status: str = "idle"
    heartbeat_at: str = ""
    last_report: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""
    created_at: str = ""

    @property
    def metrics_json(self) -> str:
        return _json_dumps(self.metrics)

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


def _row_to_bridge_session(row: sqlite3.Row | None) -> BridgeSessionRecord | None:
    if row is None:
        return None
    data = dict(row)
    data["metadata"] = _json_loads_dict(data.pop("metadata_json", ""))
    return BridgeSessionRecord(**data)


def _row_to_voice_state(row: sqlite3.Row | None) -> VoiceStateRecord | None:
    if row is None:
        return None
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    data["auto_read_aloud"] = bool(data["auto_read_aloud"])
    data["recording_supported"] = bool(data["recording_supported"])
    data["metadata"] = _json_loads_dict(data.pop("metadata_json", ""))
    return VoiceStateRecord(**data)


def _row_to_buddy_state(row: sqlite3.Row | None) -> BuddyStateRecord | None:
    if row is None:
        return None
    data = dict(row)
    data["shiny"] = bool(data["shiny"])
    data["stats"] = _json_loads_dict(data.pop("stats_json", ""))
    data["metadata"] = _json_loads_dict(data.pop("metadata_json", ""))
    stats = {
        str(key): int(value)
        for key, value in data["stats"].items()
        if isinstance(key, str)
    }
    data["stats"] = stats
    return BuddyStateRecord(**data)


def _row_to_session_goal(row: sqlite3.Row | None) -> SessionGoalRecord | None:
    if row is None:
        return None
    data = dict(row)
    data["metrics"] = _json_loads_dict(data.pop("metrics_json", ""))
    data["metadata"] = _json_loads_dict(data.pop("metadata_json", ""))
    data["budget_tokens"] = int(data.get("budget_tokens") or 0)
    data["spent_tokens"] = int(data.get("spent_tokens") or 0)
    data["budget_usd"] = float(data.get("budget_usd") or 0)
    data["spent_usd"] = float(data.get("spent_usd") or 0)
    return SessionGoalRecord(**data)


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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM webot_runs WHERE run_id = ? AND user_id = ?",
            (run_id, user_id),
        ).fetchone()
    return _row_to_run(row)


def list_runs_for_agent(
    user_id: str,
    agent_id: str,
    db_path: str | os.PathLike | None = None,
    limit: int = 20,
) -> list[WeBotRunRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM webot_runs
            WHERE user_id = ? AND agent_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (user_id, agent_id, max(1, limit)),
        ).fetchall()
    return [_row_to_run(row) for row in rows if row is not None]


def list_runs_for_session(
    user_id: str,
    session_id: str,
    db_path: str | os.PathLike | None = None,
    limit: int = 20,
) -> list[WeBotRunRecord]:
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
        if run_kind:
            rows = conn.execute(
                """
                SELECT * FROM webot_runs
                WHERE user_id = ? AND parent_session = ? AND run_kind = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, parent_session, run_kind, max(1, limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM webot_runs
                WHERE user_id = ? AND parent_session = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, parent_session, max(1, limit)),
            ).fetchall()
    return [_row_to_run(row) for row in rows if row is not None]


def list_child_runs(
    user_id: str,
    parent_run_id: str,
    db_path: str | os.PathLike | None = None,
    limit: int = 50,
) -> list[WeBotRunRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM webot_runs
            WHERE user_id = ? AND parent_run_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (user_id, parent_run_id, max(1, limit)),
        ).fetchall()
    return [_row_to_run(row) for row in rows if row is not None]


def get_latest_run_for_agent(
    user_id: str,
    agent_id: str,
    db_path: str | os.PathLike | None = None,
) -> WeBotRunRecord | None:
    records = list_runs_for_agent(user_id, agent_id, db_path=db_path, limit=1)
    return records[0] if records else None


def list_recoverable_runs(db_path: str | os.PathLike | None = None) -> list[WeBotRunRecord]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM webot_runs
            WHERE status IN ('queued', 'running', 'cancelling')
              AND run_kind IN ('subagent', 'ultraplan')
            ORDER BY updated_at ASC
            """
        ).fetchall()
    return [_row_to_run(row) for row in rows if row is not None]


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
    with _connect(db_path) as conn:
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
    query.append("ORDER BY created_at DESC LIMIT ?")
    params.append(max(1, limit))
    with _connect(db_path) as conn:
        rows = conn.execute(" ".join(query), params).fetchall()
    return [_row_to_attempt(row) for row in rows if row is not None]


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
    normalized_mode = (mode or "execute").strip().lower()
    if normalized_mode not in {"execute", "agent", "plan", "review", "yolo"}:
        normalized_mode = "execute"
    normalized_status = (status or "active").strip().lower() or "active"
    now = utc_now()
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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
        title=title.strip(),
        content=normalized_content,
        delivery_status=normalized_status,
        wait_for_idle=wait_for_idle,
        metadata=merged_metadata,
        created_at=utc_now(),
        delivered_at="",
    )
    with _connect(db_path) as conn:
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
    limit: int = 50,
) -> list[InboxMessageRecord]:
    query = [
        "SELECT * FROM webot_session_inbox WHERE user_id = ? AND target_session = ?",
    ]
    params: list[Any] = [user_id, target_session]
    if status:
        query.append("AND delivery_status = ?")
        params.append(status)
    query.append("ORDER BY created_at DESC LIMIT ?")
    params.append(max(1, limit))
    with _connect(db_path) as conn:
        rows = conn.execute(" ".join(query), params).fetchall()
    return [_row_to_inbox_message(row) for row in rows if row is not None]


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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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
    return create_runtime_artifact(
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


def get_runtime_artifact(
    artifact_id: str,
    user_id: str,
    db_path: str | os.PathLike | None = None,
) -> RuntimeArtifactRecord | None:
    with _connect(db_path) as conn:
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
    query.append("ORDER BY created_at DESC LIMIT ?")
    params.append(max(1, limit))
    with _connect(db_path) as conn:
        rows = conn.execute(" ".join(query), params).fetchall()
    return [_row_to_artifact(row) for row in rows if row is not None]


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
    if status:
        query.append("AND delivery_status = ?")
        params.append(status)
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            DELETE FROM webot_session_todos
            WHERE user_id = ? AND session_id = ?
            """,
            (user_id, session_id),
        )
        conn.commit()
        return cursor.rowcount


def _normalize_goal_status(status: str | None) -> str:
    normalized = (status or "active").strip().lower() or "active"
    if normalized not in {"active", "blocked", "completed", "archived", "paused"}:
        normalized = "active"
    return normalized


def _normalize_goal_priority(priority: str | None) -> str:
    normalized = (priority or "normal").strip().lower() or "normal"
    if normalized not in {"low", "normal", "high", "critical"}:
        normalized = "normal"
    return normalized


def upsert_session_goal(
    user_id: str,
    session_id: str,
    *,
    goal_id: str = "",
    title: str,
    description: str = "",
    status: str = "active",
    priority: str = "normal",
    parent_goal_id: str = "",
    owner_session: str = "",
    metrics: dict[str, Any] | None = None,
    budget_tokens: int = 0,
    spent_tokens: int = 0,
    budget_usd: float = 0.0,
    spent_usd: float = 0.0,
    heartbeat_status: str = "idle",
    heartbeat_at: str = "",
    last_report: str = "",
    metadata: dict[str, Any] | None = None,
    db_path: str | os.PathLike | None = None,
) -> SessionGoalRecord:
    normalized_goal_id = (goal_id or "").strip() or f"goal-{uuid.uuid4().hex[:12]}"
    existing = get_session_goal(user_id, normalized_goal_id, db_path=db_path)
    now = utc_now()
    created_at = existing.created_at if existing is not None else now
    normalized_session = (session_id or (existing.session_id if existing is not None else "") or "default").strip()
    normalized_title = (title or (existing.title if existing is not None else "")).strip()
    if not normalized_title:
        normalized_title = "Untitled goal"
    record = SessionGoalRecord(
        goal_id=normalized_goal_id,
        user_id=user_id,
        session_id=normalized_session,
        title=normalized_title,
        description=description if description is not None else (existing.description if existing else ""),
        status=_normalize_goal_status(status if status is not None else (existing.status if existing else "active")),
        priority=_normalize_goal_priority(priority if priority is not None else (existing.priority if existing else "normal")),
        parent_goal_id=parent_goal_id if parent_goal_id is not None else (existing.parent_goal_id if existing else ""),
        owner_session=owner_session if owner_session is not None else (existing.owner_session if existing else ""),
        metrics=dict(metrics if metrics is not None else (existing.metrics if existing else {})),
        budget_tokens=max(0, int(budget_tokens if budget_tokens is not None else (existing.budget_tokens if existing else 0))),
        spent_tokens=max(0, int(spent_tokens if spent_tokens is not None else (existing.spent_tokens if existing else 0))),
        budget_usd=max(0.0, float(budget_usd if budget_usd is not None else (existing.budget_usd if existing else 0))),
        spent_usd=max(0.0, float(spent_usd if spent_usd is not None else (existing.spent_usd if existing else 0))),
        heartbeat_status=(heartbeat_status or (existing.heartbeat_status if existing else "idle")).strip().lower() or "idle",
        heartbeat_at=heartbeat_at if heartbeat_at is not None else (existing.heartbeat_at if existing else ""),
        last_report=last_report if last_report is not None else (existing.last_report if existing else ""),
        metadata=dict(metadata if metadata is not None else (existing.metadata if existing else {})),
        updated_at=now,
        created_at=created_at,
    )
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_session_goals (
                goal_id, user_id, session_id, title, description, status, priority,
                parent_goal_id, owner_session, metrics_json, budget_tokens, spent_tokens,
                budget_usd, spent_usd, heartbeat_status, heartbeat_at, last_report,
                metadata_json, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(goal_id) DO UPDATE SET
                session_id=excluded.session_id,
                title=excluded.title,
                description=excluded.description,
                status=excluded.status,
                priority=excluded.priority,
                parent_goal_id=excluded.parent_goal_id,
                owner_session=excluded.owner_session,
                metrics_json=excluded.metrics_json,
                budget_tokens=excluded.budget_tokens,
                spent_tokens=excluded.spent_tokens,
                budget_usd=excluded.budget_usd,
                spent_usd=excluded.spent_usd,
                heartbeat_status=excluded.heartbeat_status,
                heartbeat_at=excluded.heartbeat_at,
                last_report=excluded.last_report,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            WHERE webot_session_goals.user_id = excluded.user_id
            """,
            (
                record.goal_id,
                record.user_id,
                record.session_id,
                record.title,
                record.description,
                record.status,
                record.priority,
                record.parent_goal_id,
                record.owner_session,
                _json_dumps(record.metrics),
                record.budget_tokens,
                record.spent_tokens,
                record.budget_usd,
                record.spent_usd,
                record.heartbeat_status,
                record.heartbeat_at,
                record.last_report,
                _json_dumps(record.metadata),
                record.updated_at,
                record.created_at,
            ),
        )
        conn.commit()
    refreshed = get_session_goal(user_id, record.goal_id, db_path=db_path)
    return refreshed or record


def get_session_goal(
    user_id: str,
    goal_id: str,
    db_path: str | os.PathLike | None = None,
) -> SessionGoalRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_session_goals
            WHERE user_id = ? AND goal_id = ?
            """,
            (user_id, goal_id),
        ).fetchone()
    return _row_to_session_goal(row)


def list_session_goals(
    user_id: str,
    session_id: str | None = None,
    *,
    status: str | None = None,
    db_path: str | os.PathLike | None = None,
    limit: int = 50,
) -> list[SessionGoalRecord]:
    query = ["SELECT * FROM webot_session_goals WHERE user_id = ?"]
    params: list[Any] = [user_id]
    if session_id:
        query.append("AND session_id = ?")
        params.append(session_id)
    if status:
        query.append("AND status = ?")
        params.append(_normalize_goal_status(status))
    query.append("ORDER BY updated_at DESC LIMIT ?")
    params.append(max(1, min(limit, 200)))
    with _connect(db_path) as conn:
        rows = conn.execute(" ".join(query), params).fetchall()
    return [_row_to_session_goal(row) for row in rows if row is not None]


def record_goal_heartbeat(
    user_id: str,
    goal_id: str,
    *,
    session_id: str = "",
    heartbeat_status: str = "active",
    report: str = "",
    spent_tokens_delta: int = 0,
    spent_usd_delta: float = 0.0,
    metadata: dict[str, Any] | None = None,
    db_path: str | os.PathLike | None = None,
) -> SessionGoalRecord | None:
    record = get_session_goal(user_id, goal_id, db_path=db_path)
    if record is None:
        return None
    merged_metadata = dict(record.metadata)
    if metadata:
        merged_metadata.update(metadata)
    heartbeat_at = utc_now()
    return upsert_session_goal(
        user_id,
        session_id or record.session_id,
        goal_id=record.goal_id,
        title=record.title,
        description=record.description,
        status=record.status,
        priority=record.priority,
        parent_goal_id=record.parent_goal_id,
        owner_session=record.owner_session,
        metrics=record.metrics,
        budget_tokens=record.budget_tokens,
        spent_tokens=record.spent_tokens + max(0, int(spent_tokens_delta or 0)),
        budget_usd=record.budget_usd,
        spent_usd=record.spent_usd + max(0.0, float(spent_usd_delta or 0)),
        heartbeat_status=(heartbeat_status or "active").strip().lower() or "active",
        heartbeat_at=heartbeat_at,
        last_report=report if report is not None else record.last_report,
        metadata=merged_metadata,
        db_path=db_path,
    )


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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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
    if status:
        query.append("AND status = ?")
        params.append(status)
    query.append("ORDER BY updated_at DESC LIMIT ?")
    params.append(max(1, limit))
    with _connect(db_path) as conn:
        rows = conn.execute(" ".join(query), params).fetchall()
    return [_row_to_approval(row) for row in rows if row is not None]


def find_active_approval_for_action(
    user_id: str,
    session_id: str,
    tool_name: str,
    args: dict[str, Any],
    db_path: str | os.PathLike | None = None,
) -> ToolApprovalRecord | None:
    args_hash = _stable_args_hash(tool_name, args or {})
    now = utc_now()
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_tool_approvals
            WHERE user_id = ?
              AND session_id = ?
              AND tool_name = ?
              AND args_hash = ?
              AND status IN ('approved', 'used')
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
    with _connect(db_path) as conn:
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
    db_path: str | os.PathLike | None = None,
) -> ToolApprovalRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_tool_approvals
            WHERE approval_id = ? AND user_id = ?
            """,
            (approval_id, user_id),
        ).fetchone()
        if row is None:
            return None
        updated_at = utc_now()
        conn.execute(
            """
            UPDATE webot_tool_approvals
            SET status = ?, resolution_reason = ?, updated_at = ?
            WHERE approval_id = ? AND user_id = ?
            """,
            (status, resolution_reason, updated_at, approval_id, user_id),
        )
        conn.commit()
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
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM webot_tool_approvals
            WHERE approval_id = ? AND user_id = ?
            """,
            (approval_id, user_id),
        ).fetchone()
    return _row_to_approval(row)


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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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


def upsert_bridge_session(
    *,
    bridge_id: str,
    user_id: str,
    session_id: str,
    role: str = "viewer",
    label: str = "",
    attach_code: str = "",
    websocket_path: str = "",
    status: str = "detached",
    connection_count: int = 0,
    metadata: dict[str, Any] | None = None,
    last_error: str = "",
    last_attached_at: str = "",
    db_path: str | os.PathLike | None = None,
) -> BridgeSessionRecord:
    now = utc_now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_bridge_sessions (
                bridge_id, user_id, session_id, role, label, attach_code,
                websocket_path, status, connection_count, metadata_json,
                last_error, last_attached_at, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bridge_id) DO UPDATE SET
                role=excluded.role,
                label=excluded.label,
                attach_code=excluded.attach_code,
                websocket_path=excluded.websocket_path,
                status=excluded.status,
                connection_count=excluded.connection_count,
                metadata_json=excluded.metadata_json,
                last_error=excluded.last_error,
                last_attached_at=excluded.last_attached_at,
                updated_at=excluded.updated_at
            """,
            (
                bridge_id,
                user_id,
                session_id,
                role,
                label,
                attach_code,
                websocket_path,
                status,
                connection_count,
                _json_dumps(metadata or {}),
                last_error,
                last_attached_at,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM webot_bridge_sessions WHERE bridge_id = ?",
            (bridge_id,),
        ).fetchone()
    return _row_to_bridge_session(row)  # type: ignore[arg-type]


def get_bridge_session(
    bridge_id: str,
    user_id: str,
    db_path: str | os.PathLike | None = None,
) -> BridgeSessionRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM webot_bridge_sessions WHERE bridge_id = ? AND user_id = ?",
            (bridge_id, user_id),
        ).fetchone()
    return _row_to_bridge_session(row)


def list_bridge_sessions(
    user_id: str,
    session_id: str | None = None,
    *,
    db_path: str | os.PathLike | None = None,
    limit: int = 20,
) -> list[BridgeSessionRecord]:
    query = ["SELECT * FROM webot_bridge_sessions WHERE user_id = ?"]
    params: list[Any] = [user_id]
    if session_id:
        query.append("AND session_id = ?")
        params.append(session_id)
    query.append("ORDER BY updated_at DESC LIMIT ?")
    params.append(max(1, limit))
    with _connect(db_path) as conn:
        rows = conn.execute(" ".join(query), params).fetchall()
    return [_row_to_bridge_session(row) for row in rows if row is not None]


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
    with _connect(db_path) as conn:
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
    with _connect(db_path) as conn:
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


def save_buddy_state(
    *,
    user_id: str,
    seed: str,
    species: str,
    rarity: str,
    shiny: bool,
    eye: str,
    hat: str,
    stats: dict[str, int],
    soul_name: str = "",
    soul_personality: str = "",
    reaction: str = "",
    hatched_at: str = "",
    last_interaction_at: str = "",
    metadata: dict[str, Any] | None = None,
    db_path: str | os.PathLike | None = None,
) -> BuddyStateRecord:
    now = utc_now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO webot_buddy_state (
                user_id, seed, species, rarity, shiny, eye, hat, stats_json,
                soul_name, soul_personality, reaction, hatched_at,
                last_interaction_at, metadata_json, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                seed=excluded.seed,
                species=excluded.species,
                rarity=excluded.rarity,
                shiny=excluded.shiny,
                eye=excluded.eye,
                hat=excluded.hat,
                stats_json=excluded.stats_json,
                soul_name=excluded.soul_name,
                soul_personality=excluded.soul_personality,
                reaction=excluded.reaction,
                hatched_at=excluded.hatched_at,
                last_interaction_at=excluded.last_interaction_at,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                user_id,
                seed,
                species,
                rarity,
                1 if shiny else 0,
                eye,
                hat,
                _json_dumps(stats),
                soul_name,
                soul_personality,
                reaction,
                hatched_at,
                last_interaction_at,
                _json_dumps(metadata or {}),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM webot_buddy_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return _row_to_buddy_state(row)  # type: ignore[arg-type]


def get_buddy_state(
    user_id: str,
    db_path: str | os.PathLike | None = None,
) -> BuddyStateRecord | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM webot_buddy_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return _row_to_buddy_state(row)
