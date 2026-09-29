"""A team is a namespace: a folder that brings agents, personas, skills,
workflows and alarms together.

The folder ``user_files/<owner>/teams/<team>`` holds the team's persona library,
skills, workflows, settings, and ``members.json`` — which agents are in it, the
name each goes by in the team, and which one leads. Inside the team an agent is
``<team>.<name>``; the agents themselves live in the agent table.

An agent is in at most one team, and its config ``team`` names it: this store
is the only writer of both, so ``members.json`` and the agent's ``team`` always
agree. Joining another team moves the agent there.
"""

from __future__ import annotations

import json
import os
import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from agents.store import Agent, AgentStore

MEMBERS_FILE = "members.json"


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
        """Remove the team and its assets; its agents stay, in no team."""
        for entry in self._read(owner, team):
            self._set_team(owner, entry["agent"], "")
        shutil.rmtree(self.folder(owner, team), ignore_errors=True)

    def rename(self, owner: str, old: str, new: str) -> None:
        src, dst = self.folder(owner, old), self.folder(owner, new)
        if dst.exists():
            raise FileExistsError(f"team {new!r} already exists")
        src.rename(dst)
        for entry in self._read(owner, new):
            self._set_team(owner, entry["agent"], new)

    def _set_team(self, owner: str, agent_id: str, team: str) -> None:
        if self.agents.get(owner, agent_id) is not None:
            self.agents.set_team(owner, agent_id, team)

    # ── membership ───────────────────────────────────────────────────────

    def _read(self, owner: str, team: str) -> list[dict]:
        try:
            data = json.loads((self.folder(owner, team) / MEMBERS_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [e for e in data if isinstance(e, dict) and e.get("agent")] if isinstance(data, list) else []

    @contextmanager
    def _editing(self, owner: str, team: str) -> Iterator[list[dict]]:
        """The member entries to change in place; written back when the block ends."""
        folder = self.folder(owner, team)
        with _exclusive(folder):
            entries = self._read(owner, team)
            yield entries
            tmp = folder / f".{MEMBERS_FILE}.tmp"
            tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, folder / MEMBERS_FILE)

    def members(self, owner: str, team: str) -> list[Member]:
        if not valid_team_name(team):
            return []
        result = []
        for entry in self._read(owner, team):
            agent = self.agents.get(owner, entry["agent"])
            if agent is not None:
                result.append(Member(agent, str(entry.get("name") or agent.name), bool(entry.get("lead")),
                                     dict(entry.get("extra") or {})))
        return result

    def member(self, owner: str, team: str, ref: str) -> Member:
        """A member by its name in the team or its agent id."""
        wanted = (ref or "").strip().lstrip("@")
        for m in self.members(owner, team):
            if m.role.casefold() == wanted.casefold() or m.agent.agent_id == wanted:
                return m
        raise LookupError(f"{ref!r} is not in team {team!r}")

    def lead(self, owner: str, team: str) -> Member | None:
        return next((m for m in self.members(owner, team) if m.is_lead), None)

    def address(self, owner: str, ref: str) -> Agent | None:
        """``<team>.<name>``: the agent that goes by *name* in *team*."""
        for team in self.teams(owner):
            if ref.startswith(team + ".") and len(ref) > len(team) + 1:
                try:
                    return self.member(owner, team, ref[len(team) + 1:]).agent
                except LookupError:
                    return None
        return None

    def teams_of(self, owner: str, agent_id: str) -> list[str]:
        return [t for t in self.teams(owner) if any(e["agent"] == agent_id for e in self._read(owner, t))]

    def add(self, owner: str, team: str, agent_id: str, *, role: str = "", is_lead: bool = False,
            extra: dict[str, Any] | None = None) -> Member:
        self.require(owner, team)
        agent = self.agents.get(owner, agent_id)
        if agent is None:
            raise LookupError(f"no agent {agent_id!r} for {owner}")
        entry: dict[str, Any] = {"agent": agent_id, "name": role.strip() or agent.name}
        if is_lead:
            entry["lead"] = True
        if extra:
            entry["extra"] = extra
        for other in self.teams_of(owner, agent_id):  # one team per agent: joining this one leaves the other
            if other != team:
                self.remove(owner, other, agent_id)
        with self._editing(owner, team) as entries:
            if is_lead:
                for e in entries:
                    e.pop("lead", None)
            at = next((i for i, e in enumerate(entries) if e["agent"] == agent_id), None)
            if at is None:
                entries.append(entry)
            else:
                entries[at] = entry
        self._set_team(owner, agent_id, team)
        return self.member(owner, team, agent_id)

    def update(self, owner: str, team: str, agent_id: str, *, role: str | None = None,
               is_lead: bool | None = None, tag: str | None = None) -> Member:
        """``tag``: the team persona the member wears ("" for none)."""
        current = self.member(owner, team, agent_id)
        extra = dict(current.extra)
        if tag is not None:
            extra.pop("tag", None)
            if tag:
                extra["tag"] = tag
        return self.add(
            owner, team, agent_id,
            role=current.role if role is None else role,
            is_lead=current.is_lead if is_lead is None else is_lead,
            extra=extra,
        )

    def remove(self, owner: str, team: str, agent_id: str) -> None:
        if not self.exists(owner, team):
            return
        with self._editing(owner, team) as entries:
            entries[:] = [e for e in entries if e["agent"] != agent_id]
        agent = self.agents.get(owner, agent_id)
        if agent is not None and agent.team == team:
            self._set_team(owner, agent_id, "")

    def forget_agent(self, owner: str, agent_id: str) -> None:
        """An agent was deleted: it leaves every team."""
        for team in self.teams_of(owner, agent_id):
            self.remove(owner, team, agent_id)


@contextmanager
def _exclusive(folder: Path) -> Iterator[None]:
    """One writer at a time across processes (where the OS can lock a folder)."""
    try:
        import fcntl
    except ImportError:  # Windows: writes still replace the file whole
        yield
        return
    fd = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def get_team_store(agents: AgentStore | None = None, user_files_dir: str | os.PathLike | None = None) -> TeamStore:
    from agents.store import get_store
    if user_files_dir is None:
        from utils.runtime_paths import USER_FILES_DIR
        user_files_dir = USER_FILES_DIR
    return TeamStore(agents or get_store(), user_files_dir)
