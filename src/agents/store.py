"""Every agent on this machine, one record each.

An agent has an ``ag_…`` id that never changes, a handle unique per owner
(its address is ``owner/handle``), a display name, and a driver with the
driver's own config. ``runtime`` is what the agent's runtime already knows —
the identity prompt it was sent, when it was last used — whatever the driver.
Only this package reads ``driver``, ``config`` and ``runtime``; everything
above it knows an agent by its id.

Drivers and their config:

* ``webot``    — ClawCross's own agent runtime: ``session``, ``persona`` (tag),
  ``team`` (whose skills and personas it loads), ``tools``.
* ``acpx``     — codex, claude code, gemini … over ACP: ``platform``, ``global_name``, ….
* ``openclaw`` — an OpenClaw agent: ``global_name``, ``api_url``, ….
* ``http``     — any OpenAI-compatible endpoint: ``api_url``, ``api_key``, ``model``, ….
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AGENT_ID_PREFIX = "ag_"
_ID_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # base32 without look-alikes
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

WEBOT = "webot"
ACPX = "acpx"
OPENCLAW = "openclaw"
HTTP = "http"
DRIVERS = (WEBOT, ACPX, OPENCLAW, HTTP)
# A single model call with a persona: a temporary agent, never stored.
LLM = "llm"

# Temporary WeBot sessions (OASIS personas with tools) start with this; they are
# not agents and are discarded when their task ends.
TEMP_SESSION_PREFIX = "tmp__"


class AgentNotFound(LookupError):
    pass


class AgentExists(ValueError):
    """The runtime this record would point at already belongs to an agent."""

    def __init__(self, agent: "Agent"):
        super().__init__(f"{agent.address} already uses this runtime")
        self.agent = agent


@dataclass(frozen=True, slots=True)
class Agent:
    agent_id: str
    owner: str
    handle: str
    name: str
    driver: str
    config: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def address(self) -> str:
        return f"{self.owner}/{self.handle}"

    @property
    def platform(self) -> str:
        """What the agent is, for people: webot, codex, claude, openclaw, …."""
        return str(self.config.get("platform") or self.driver)

    @property
    def persona(self) -> str:
        """The persona tag it speaks with, if any."""
        return str(self.config.get("persona") or "")

    @property
    def temporary(self) -> bool:
        """Made for one task and discarded after it (never stored)."""
        return not self.agent_id

    @property
    def remembers(self) -> bool:
        """Whether it keeps the conversation between messages."""
        return self.driver != LLM


def new_agent_id() -> str:
    return AGENT_ID_PREFIX + "".join(secrets.choice(_ID_ALPHABET) for _ in range(10))


def new_webot_session() -> str:
    """A WeBot session id: base36 milliseconds + 4 random base36 digits."""
    def base36(n: int) -> str:
        digits = "0123456789abcdefghijklmnopqrstuvwxyz"
        out = ""
        while True:
            n, r = divmod(n, 36)
            out = digits[r] + out
            if n == 0:
                return out
    return base36(int(time.time() * 1000)) + base36(secrets.randbelow(36 ** 4)).zfill(4)


def canonical_platform(platform: str) -> str:
    pl = (platform or "").strip().lower()
    return {"claude-code": "claude", "claudecode": "claude", "gemini-cli": "gemini", "geminicli": "gemini"}.get(pl, pl)


def driver_for_platform(platform: str) -> str:
    """The driver that reaches an agent of *platform*."""
    pl = canonical_platform(platform)
    if pl in ("", WEBOT):
        return WEBOT
    if pl == OPENCLAW:
        return OPENCLAW
    from integrations.acpx_cli_tools import acpx_agent_tags_with_legacy
    if pl in {canonical_platform(t) for t in acpx_agent_tags_with_legacy()}:
        return ACPX
    return HTTP


def runtime_key(driver: str, config: dict[str, Any]) -> str:
    """The runtime a record points at; two agents never share one."""
    if driver == WEBOT:
        ident = str(config.get("session") or "").strip()
    else:
        ident = str(config.get("global_name") or "").strip()
    if not ident:
        raise ValueError(f"{driver} agent needs {'a session' if driver == WEBOT else 'a global_name'}")
    return f"{driver}:{ident}"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", (text or "").strip().lower()).strip("-_")
    return slug[:32].rstrip("-_")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    agent_id    TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    handle      TEXT NOT NULL,
    name        TEXT NOT NULL,
    driver      TEXT NOT NULL,
    runtime_key TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    runtime_json TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE (owner, handle),
    UNIQUE (owner, runtime_key)
);
"""


class AgentStore:
    """SQLite-backed; shared safely by the ClawCross processes."""

    def __init__(self, db_path: str | os.PathLike):
        self.db_path = str(db_path)
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        # Teams and conversations reference agents; deleting one cascades there.
        conn.execute("PRAGMA foreign_keys = ON")
        if not self._ready:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            self._add_missing_columns(conn)
            self._ready = True
        return conn

    @staticmethod
    def _add_missing_columns(conn: sqlite3.Connection) -> None:
        """Columns added after a database was created (``runtime_json``)."""
        columns = {row[1] for row in conn.execute("PRAGMA table_info(agents)")}
        if "runtime_json" not in columns:
            try:
                conn.execute("ALTER TABLE agents ADD COLUMN runtime_json TEXT NOT NULL DEFAULT '{}'")
            except sqlite3.OperationalError as exc:  # another process added it first
                if "duplicate column" not in str(exc):
                    raise

    @staticmethod
    def _agent(row: sqlite3.Row) -> Agent:
        return Agent(
            agent_id=row["agent_id"], owner=row["owner"], handle=row["handle"], name=row["name"],
            driver=row["driver"], config=json.loads(row["config_json"] or "{}"),
            runtime=json.loads(row["runtime_json"] or "{}"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def _one(self, where: str, params: tuple) -> Agent | None:
        conn = self._connect()
        try:
            row = conn.execute(f"SELECT * FROM agents WHERE {where}", params).fetchone()
        finally:
            conn.close()
        return self._agent(row) if row else None

    @staticmethod
    def _free_handle(conn: sqlite3.Connection, owner: str, wanted: str, name: str, agent_id: str) -> str:
        base = next((s for s in (_slug(wanted), _slug(name)) if _HANDLE_RE.match(s)), "agent")
        handle, n = base, 1
        while True:
            row = conn.execute("SELECT agent_id FROM agents WHERE owner = ? AND handle = ?", (owner, handle)).fetchone()
            if row is None or row["agent_id"] == agent_id:
                return handle
            n += 1
            handle = f"{base[: 32 - len(str(n)) - 1]}-{n}"

    # ── writes ───────────────────────────────────────────────────────────

    def create(self, owner: str, *, name: str, driver: str, config: dict[str, Any], handle: str = "") -> Agent:
        if driver not in DRIVERS:
            raise ValueError(f"unknown driver {driver!r}")
        owner, name = owner.strip(), (name or "").strip()
        if not owner or not name:
            raise ValueError("an agent needs an owner and a name")
        key = runtime_key(driver, config)
        now = time.time()
        agent_id = new_agent_id()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            taken = conn.execute(
                "SELECT * FROM agents WHERE owner = ? AND runtime_key = ?", (owner, key),
            ).fetchone()
            if taken is not None:
                conn.execute("ROLLBACK")
                raise AgentExists(self._agent(taken))
            handle = self._free_handle(conn, owner, handle, name, agent_id)
            conn.execute(
                "INSERT INTO agents (agent_id, owner, handle, name, driver, runtime_key, config_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (agent_id, owner, handle, name, driver, key, json.dumps(config, ensure_ascii=False), now, now),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()
        return self.get(agent_id)  # type: ignore[return-value]

    def update(self, agent_id: str, *, name: str | None = None, config: dict[str, Any] | None = None) -> Agent:
        agent = self.get(agent_id)
        if agent is None:
            raise AgentNotFound(agent_id)
        new_name = (name if name is not None else agent.name).strip() or agent.name
        new_config = agent.config if config is None else config
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE agents SET name = ?, runtime_key = ?, config_json = ?, updated_at = ? WHERE agent_id = ?",
                (new_name, runtime_key(agent.driver, new_config), json.dumps(new_config, ensure_ascii=False),
                 time.time(), agent_id),
            )
        except sqlite3.IntegrityError:
            raise AgentExists(self.find(agent.owner, agent.driver, new_config) or agent) from None
        finally:
            conn.close()
        return self.get(agent_id)  # type: ignore[return-value]

    def set_runtime(self, agent_id: str, runtime: dict[str, Any]) -> None:
        """Record what the agent's runtime now knows (not a settings change)."""
        conn = self._connect()
        try:
            conn.execute("UPDATE agents SET runtime_json = ? WHERE agent_id = ?",
                         (json.dumps(runtime, ensure_ascii=False), agent_id))
        finally:
            conn.close()

    def delete(self, agent_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        finally:
            conn.close()

    # ── reads ────────────────────────────────────────────────────────────

    def get(self, agent_id: str) -> Agent | None:
        return self._one("agent_id = ?", (agent_id,))

    def list(self, owner: str) -> list[Agent]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM agents WHERE owner = ? ORDER BY handle", (owner,)).fetchall()
        finally:
            conn.close()
        return [self._agent(row) for row in rows]

    def find(self, owner: str, driver: str, config: dict[str, Any]) -> Agent | None:
        """The agent that owns this runtime (a WeBot session, an external global_name)."""
        try:
            key = runtime_key(driver, config)
        except ValueError:
            return None
        return self._one("owner = ? AND runtime_key = ?", (owner, key))

    def resolve(self, owner: str, ref: str) -> Agent:
        """*owner*'s agent by ``ag_…`` id, ``owner/handle`` address or handle."""
        text = (ref or "").strip().lstrip("@")
        if text.startswith(AGENT_ID_PREFIX):
            agent = self.get(text)
        else:
            prefix, _, handle = text.rpartition("/")
            if prefix and prefix != owner:
                raise AgentNotFound(f"{ref!r} is not one of {owner}'s agents")
            agent = self._one("owner = ? AND handle = ?", (owner, handle.lower()))
        if agent is None or agent.owner != owner:
            raise AgentNotFound(f"no agent {ref!r} for {owner}")
        return agent


_STORES: dict[str, AgentStore] = {}
_LOCK = threading.Lock()


def default_db_path() -> Path:
    from utils.runtime_paths import DATA_DIR
    return Path(DATA_DIR) / "clawcross.db"


def get_store(db_path: str | os.PathLike | None = None) -> AgentStore:
    path = str(db_path or default_db_path())
    with _LOCK:
        store = _STORES.get(path)
        if store is None:
            store = _STORES[path] = AgentStore(path)
        return store
