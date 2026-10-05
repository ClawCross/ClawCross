"""Every agent on this machine, one record each: the table of all sessions.

An agent is a session, of any runtime — a WeBot session, a codex / claude /
gemini / openclaw session over ACP, a service over HTTP. Its
``agent_id`` is its session number: unique within its owner's space, the way an
address is unique within one network. A number that is not there yet is a new
agent. What the agent is inside is its runtime's business: this table only
records its runtime (``driver``), the runtime's config, and what the runtime
already knows (``runtime``).

Drivers and their config:

* ``webot``    — ClawCross's own agent runtime.
* ``acpx``     — codex, claude code, gemini, openclaw … over ACP: ``platform``, ….
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
_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

WEBOT = "webot"
ACPX = "acpx"
HTTP = "http"
# A model call with a persona: no tools, remembers nothing between messages.
LLM = "llm"
DRIVERS = (WEBOT, ACPX, HTTP, LLM)

# Agents made for one task (OASIS personas) start with this; they are deleted,
# record and all, when their task ends.
TEMP_SESSION_PREFIX = "tmp__"


class AgentNotFound(LookupError):
    pass


class AgentExists(ValueError):
    def __init__(self, agent: "Agent"):
        super().__init__(f"{agent.agent_id} already exists")
        self.agent = agent


@dataclass(frozen=True, slots=True)
class Agent:
    agent_id: str
    owner: str
    name: str
    driver: str
    config: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def platform(self) -> str:
        """What the agent is, for people: webot, codex, claude, openclaw, …."""
        return str(self.config.get("platform") or self.driver)

    @property
    def teams(self) -> list[str]:
        """The teams it is in; only the team layer sets them."""
        return [str(t) for t in self.config.get("teams") or []]

    @property
    def temporary(self) -> bool:
        """Made for one task and discarded after it."""
        return not self.agent_id or self.agent_id.startswith(TEMP_SESSION_PREFIX)

    @property
    def remembers(self) -> bool:
        """Whether it keeps the conversation between messages."""
        return self.driver != LLM


def new_agent_id() -> str:
    return AGENT_ID_PREFIX + "".join(secrets.choice(_ID_ALPHABET) for _ in range(10))


def valid_agent_id(agent_id: str) -> bool:
    return bool(_AGENT_ID_RE.match(agent_id or ""))


def canonical_platform(platform: str) -> str:
    pl = (platform or "").strip().lower()
    return {"claude-code": "claude", "claudecode": "claude", "gemini-cli": "gemini", "geminicli": "gemini"}.get(pl, pl)


def driver_for_platform(platform: str) -> str:
    """The driver that reaches an agent of *platform*."""
    pl = canonical_platform(platform)
    if pl in ("", WEBOT):
        return WEBOT
    if pl == LLM:
        return pl
    from agents.platforms import acpx_agent_tags_with_legacy
    if pl in {canonical_platform(t) for t in acpx_agent_tags_with_legacy()}:
        return ACPX
    return HTTP


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    owner        TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    name         TEXT NOT NULL,
    driver       TEXT NOT NULL,
    config_json  TEXT NOT NULL DEFAULT '{}',
    runtime_json TEXT NOT NULL DEFAULT '{}',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    PRIMARY KEY (owner, agent_id)
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
        if not self._ready:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            # OpenClaw agents once had their own driver over the OpenClaw HTTP
            # gateway; they are ACP agents now. Their sessions keep their keys.
            conn.execute(
                "UPDATE agents SET driver = 'acpx', config_json = json_set("
                "json_remove(config_json, '$.api_url', '$.api_key', '$.headers', '$.model'),"
                " '$.platform', 'openclaw') WHERE driver = 'openclaw'"
            )
            self._ready = True
        return conn

    @staticmethod
    def _agent(row: sqlite3.Row) -> Agent:
        return Agent(
            agent_id=row["agent_id"], owner=row["owner"], name=row["name"], driver=row["driver"],
            config=json.loads(row["config_json"] or "{}"), runtime=json.loads(row["runtime_json"] or "{}"),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def _run(self, sql: str, params: tuple) -> list[sqlite3.Row]:
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    # ── writes ───────────────────────────────────────────────────────────

    def create(self, owner: str, *, driver: str, config: dict[str, Any] | None = None, name: str = "",
               agent_id: str = "") -> Agent:
        """A new agent: *agent_id* when given (a session number), else a fresh ``ag_…``."""
        if driver not in DRIVERS:
            raise ValueError(f"unknown driver {driver!r}")
        owner, agent_id = owner.strip(), (agent_id or "").strip() or new_agent_id()
        if not owner:
            raise ValueError("an agent needs an owner")
        if not valid_agent_id(agent_id):
            raise ValueError(f"invalid agent id {agent_id!r}: letters, digits, '_' and '-', at most 64")
        from webot.workspace import normalize_workspace_config
        config = dict(config or {})
        config['workspaces'] = normalize_workspace_config(config.get('workspaces'), user_id=owner, legacy_root=config.get('workspace_root',''))
        now = time.time()
        try:
            self._run(
                "INSERT INTO agents (owner, agent_id, name, driver, config_json, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (owner, agent_id, (name or "").strip() or agent_id, driver,
                 json.dumps(config or {}, ensure_ascii=False), now, now),
            )
        except sqlite3.IntegrityError:
            raise AgentExists(self.get(owner, agent_id)) from None
        return self.get(owner, agent_id)  # type: ignore[return-value]

    def ensure(self, owner: str, agent_id: str, *, driver: str = WEBOT, config: dict[str, Any] | None = None,
               name: str = "") -> Agent:
        """The agent with this number; a number not seen before is a new agent."""
        found = self.get(owner, agent_id)
        if found is not None:
            return found
        try:
            return self.create(owner, driver=driver, config=config, name=name, agent_id=agent_id)
        except AgentExists as exc:  # made by another request meanwhile
            return exc.agent

    def update(self, owner: str, agent_id: str, *, name: str | None = None,
               config: dict[str, Any] | None = None) -> Agent:
        agent = self.require(owner, agent_id)
        self._run(
            "UPDATE agents SET name = ?, config_json = ?, updated_at = ? WHERE owner = ? AND agent_id = ?",
            ((name if name is not None else agent.name).strip() or agent.name,
             json.dumps(agent.config if config is None else config, ensure_ascii=False), time.time(),
             owner, agent_id),
        )
        return self.require(owner, agent_id)

    def set_teams(self, owner: str, agent_id: str, teams: list[str]) -> None:
        """Record the teams the agent is in."""
        agent = self.require(owner, agent_id)
        if agent.teams != teams:
            self.update(owner, agent_id, config={**agent.config, "teams": list(teams)})

    def set_runtime(self, owner: str, agent_id: str, runtime: dict[str, Any]) -> None:
        """Record what the agent's runtime now knows (not a settings change)."""
        self._run("UPDATE agents SET runtime_json = ? WHERE owner = ? AND agent_id = ?",
                  (json.dumps(runtime, ensure_ascii=False), owner, agent_id))

    def patch_runtime(self, owner: str, agent_id: str, changes: dict[str, Any]) -> None:
        """Merge runtime state atomically without overwriting another writer's fields."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT runtime_json FROM agents WHERE owner = ? AND agent_id = ?",
                               (owner, agent_id)).fetchone()
            if row is None:
                raise AgentNotFound(f"no agent {agent_id!r} for {owner}")
            runtime = {**json.loads(row["runtime_json"] or "{}"), **changes}
            conn.execute("UPDATE agents SET runtime_json = ? WHERE owner = ? AND agent_id = ?",
                         (json.dumps(runtime, ensure_ascii=False), owner, agent_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete(self, owner: str, agent_id: str) -> None:
        self._run("DELETE FROM agents WHERE owner = ? AND agent_id = ?", (owner, agent_id))

    # ── reads ────────────────────────────────────────────────────────────

    def get(self, owner: str, agent_id: str) -> Agent | None:
        rows = self._run("SELECT * FROM agents WHERE owner = ? AND agent_id = ?", (owner, (agent_id or "").strip()))
        return self._agent(rows[0]) if rows else None

    def require(self, owner: str, agent_id: str) -> Agent:
        agent = self.get(owner, agent_id)
        if agent is None:
            raise AgentNotFound(f"no agent {agent_id!r} for {owner}")
        return agent

    def list(self, owner: str) -> list[Agent]:
        rows = self._run("SELECT * FROM agents WHERE owner = ? ORDER BY created_at, agent_id", (owner,))
        return [self._agent(row) for row in rows]


_STORES: dict[str, AgentStore] = {}
_LOCK = threading.Lock()


def default_db_path() -> Path:
    from common.runtime_paths import DATA_DIR
    return Path(DATA_DIR) / "agents.db"


def get_store(db_path: str | os.PathLike | None = None) -> AgentStore:
    path = str(db_path or default_db_path())
    with _LOCK:
        store = _STORES.get(path)
        if store is None:
            store = _STORES[path] = AgentStore(path)
        return store
