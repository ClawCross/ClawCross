"""A team is a named set of agents, each playing a role; at most one leads.

Membership lives in the database next to the agents it references, so deleting
an agent takes it out of every team. The team's folder
(``user_files/<owner>/teams/<team>``) holds the team's assets — persona
templates, workflows, skills, settings — and never its agents.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.store import Agent, AgentStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_members (
    owner      TEXT NOT NULL,
    team       TEXT NOT NULL,
    agent_id   TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    is_lead    INTEGER NOT NULL DEFAULT 0,
    position   INTEGER NOT NULL DEFAULT 0,
    extra_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (owner, team, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_team_members_agent ON team_members(agent_id);
"""


class TeamNotFound(LookupError):
    pass


def valid_team_name(team: str) -> bool:
    team = (team or "").strip()
    return bool(team) and "/" not in team and "\\" not in team and not team.startswith(".")


@dataclass(frozen=True, slots=True)
class Member:
    agent: Agent
    role: str
    is_lead: bool
    extra: dict[str, Any] = field(default_factory=dict)  # manifest fields kept for export


class TeamStore:
    def __init__(self, agents: AgentStore, user_files_dir: str | os.PathLike):
        self.agents = agents
        self.user_files_dir = Path(user_files_dir)
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        conn = self.agents._connect()  # same database: membership references agents
        if not self._ready:
            conn.executescript(_SCHEMA)
            self._ready = True
        return conn

    # ── folders ──────────────────────────────────────────────────────────

    def folder(self, owner: str, team: str) -> Path:
        if not valid_team_name(team):
            raise ValueError(f"invalid team name {team!r}")
        return self.user_files_dir / owner / "teams" / team

    def teams(self, owner: str) -> list[str]:
        root = self.user_files_dir / owner / "teams"
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir() and valid_team_name(p.name))

    def exists(self, owner: str, team: str) -> bool:
        return valid_team_name(team) and self.folder(owner, team).is_dir()

    def require(self, owner: str, team: str) -> None:
        if not self.exists(owner, team):
            raise TeamNotFound(f"no team {team!r}")

    def create(self, owner: str, team: str) -> None:
        self.folder(owner, team).mkdir(parents=True, exist_ok=True)

    def delete(self, owner: str, team: str) -> None:
        """Remove the team and its assets; its agents stay."""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM team_members WHERE owner = ? AND team = ?", (owner, team))
        finally:
            conn.close()
        shutil.rmtree(self.folder(owner, team), ignore_errors=True)

    def rename(self, owner: str, old: str, new: str) -> None:
        src, dst = self.folder(owner, old), self.folder(owner, new)
        if dst.exists():
            raise FileExistsError(f"team {new!r} already exists")
        src.rename(dst)
        conn = self._connect()
        try:
            conn.execute("UPDATE team_members SET team = ? WHERE owner = ? AND team = ?", (new, owner, old))
        finally:
            conn.close()

    # ── membership ───────────────────────────────────────────────────────

    def members(self, owner: str, team: str) -> list[Member]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT agent_id, role, is_lead, extra_json FROM team_members"
                " WHERE owner = ? AND team = ? ORDER BY position, rowid",
                (owner, team),
            ).fetchall()
        finally:
            conn.close()
        result = []
        for row in rows:
            agent = self.agents.get(row["agent_id"])
            if agent is not None:
                result.append(Member(agent, row["role"], bool(row["is_lead"]), json.loads(row["extra_json"] or "{}")))
        return result

    def member(self, owner: str, team: str, ref: str) -> Member:
        """A member by agent id, address, handle or role name."""
        wanted = (ref or "").strip().lstrip("@")
        for m in self.members(owner, team):
            if wanted in (m.agent.agent_id, m.agent.address, m.agent.handle) or m.role.casefold() == wanted.casefold():
                return m
        raise LookupError(f"{ref!r} is not in team {team!r}")

    def lead(self, owner: str, team: str) -> Member | None:
        return next((m for m in self.members(owner, team) if m.is_lead), None)

    def teams_of(self, owner: str, agent_id: str) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT team FROM team_members WHERE owner = ? AND agent_id = ? ORDER BY team", (owner, agent_id),
            ).fetchall()
        finally:
            conn.close()
        return [row["team"] for row in rows]

    def add(self, owner: str, team: str, agent_id: str, *, role: str = "", is_lead: bool = False,
            extra: dict[str, Any] | None = None) -> Member:
        self.require(owner, team)
        agent = self.agents.get(agent_id)
        if agent is None or agent.owner != owner:
            raise LookupError(f"no agent {agent_id!r} for {owner}")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if is_lead:
                conn.execute("UPDATE team_members SET is_lead = 0 WHERE owner = ? AND team = ?", (owner, team))
            position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM team_members WHERE owner = ? AND team = ?", (owner, team),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO team_members (owner, team, agent_id, role, is_lead, position, extra_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(owner, team, agent_id) DO UPDATE SET role = excluded.role,"
                " is_lead = excluded.is_lead, extra_json = excluded.extra_json",
                (owner, team, agent_id, role.strip() or agent.name, int(is_lead), position,
                 json.dumps(extra or {}, ensure_ascii=False)),
            )
            conn.execute("COMMIT")
        finally:
            conn.close()
        return self.member(owner, team, agent_id)

    def update(self, owner: str, team: str, agent_id: str, *, role: str | None = None,
               is_lead: bool | None = None) -> Member:
        current = self.member(owner, team, agent_id)
        return self.add(
            owner, team, agent_id,
            role=current.role if role is None else role,
            is_lead=current.is_lead if is_lead is None else is_lead,
            extra=current.extra,
        )

    def remove(self, owner: str, team: str, agent_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM team_members WHERE owner = ? AND team = ? AND agent_id = ?", (owner, team, agent_id),
            )
        finally:
            conn.close()


def get_team_store(agents: AgentStore | None = None, user_files_dir: str | os.PathLike | None = None) -> TeamStore:
    from agents.store import get_store
    if user_files_dir is None:
        from utils.runtime_paths import USER_FILES_DIR
        user_files_dir = USER_FILES_DIR
    return TeamStore(agents or get_store(), user_files_dir)
