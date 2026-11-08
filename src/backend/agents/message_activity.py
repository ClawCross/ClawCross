"""Read message times for the directory without loading histories or waking Agents."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import math
import sqlite3

from agents.store import Agent, WEBOT
from common.runtime_paths import DATA_DIR
from webot.checkpoint_paths import checkpoint_db_name_for_thread, legacy_hashed_checkpoint_db_name_for_thread


def _stamp(value) -> float:
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            value = parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp()
        stamp = float(value or 0)
        return stamp if math.isfinite(stamp) and stamp > 0 else 0.0
    except (ValueError, TypeError, OverflowError):
        return 0.0


@lru_cache(maxsize=1024)
def _read(path: str, query: str, identity: str, signature: tuple) -> float:
    # signature includes the WAL: committed messages need not change the main DB.
    try:
        with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
            row = db.execute(query, (identity,)).fetchone()
        return _stamp(row[0]) if row else 0.0
    except (OSError, sqlite3.Error):
        return 0.0


def _message_time(path: Path, query: str, identity: str) -> float:
    try:
        stat = path.stat()
        try:
            wal = Path(str(path) + '-wal').stat()
            wal_signature = (wal.st_ino, wal.st_size, wal.st_mtime_ns)
        except FileNotFoundError:
            wal_signature = ()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns, wal_signature)
        return _read(str(path.resolve()), query, identity, signature)
    except OSError:
        return 0.0


def message_times(agents: list[Agent], *, checkpoint_dir: Path | None = None,
                  history_dir: Path | None = None) -> dict[str, float]:
    """Only already-owned Agents and existing databases are read; no paths are created."""
    checkpoints = checkpoint_dir or DATA_DIR / 'agent_checkpoints'
    histories = history_dir or DATA_DIR / 'external_agent_history'
    times = {}
    for agent in agents:
        if agent.driver == WEBOT:
            thread = f'{agent.owner}#{agent.agent_id}'
            query = ("SELECT created_at FROM context_messages WHERE thread_id=? "
                     "AND json_valid(message_json) AND json_extract(message_json,'$.type') IN ('human','ai','AIMessageChunk','HumanMessageChunk') "
                     "ORDER BY sequence DESC LIMIT 1")
            times[agent.agent_id] = max(
                _message_time(checkpoints / name, query, thread)
                for name in (checkpoint_db_name_for_thread(thread), legacy_hashed_checkpoint_db_name_for_thread(thread)))
        else:
            from external.history import history_db_name_for
            from external.session import runtime_session
            path = histories / history_db_name_for(agent.platform, runtime_session(agent))
            query = ("SELECT ts FROM messages WHERE user_id=? AND role IN ('user','assistant') "
                     "AND direction IN ('send','recv','error') ORDER BY rowid DESC LIMIT 1")
            times[agent.agent_id] = _message_time(path, query, agent.owner)
    return times
