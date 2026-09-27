"""A team as a view over the agent registry.

The manifest files keep their format. Reading a team reconciles it:

* an internal role written without a ``session`` (by hand, or by team-builder
  via ``write_file``) is a new agent: a session is stamped and written back in
  the same format ``_ia_save`` uses, so the role becomes usable everywhere;
* a role that names a ``session`` / ``global_name`` refers to that agent, so the
  same agent can serve several teams under different role names.

Membership is answered as agent records with their role name, tag and whether
they lead the team (``is_primary``).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from agents.registry import AgentRecord, AgentRegistry, get_registry, new_webot_session_id

INTERNAL_MANIFEST = "internal_agents.json"
EXTERNAL_MANIFEST = "external_agents.json"


@dataclass(slots=True)
class TeamMember:
    role_name: str
    kind: str  # "internal" | "external"
    tag: str
    is_lead: bool
    entry: dict[str, Any] = field(default_factory=dict)
    agent: AgentRecord | None = None

    @property
    def binding_ref(self) -> str:
        """The session or global_name this role is bound through."""
        key = "session" if self.kind == "internal" else "global_name"
        return str(self.entry.get(key) or "").strip()


class TeamNotFound(LookupError):
    pass


class TeamHasNoLead(LookupError):
    pass


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Serialize manifest write-backs between the ClawCross processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        try:
            import fcntl
        except ImportError:  # Windows
            try:
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            except (ImportError, OSError):
                pass
            yield
            return
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read_entries(path: Path) -> list[Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _write_entries(path: Path, entries: list[Any]) -> None:
    """Replace the manifest atomically, formatted exactly like ``_ia_save``."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _is_lead(entry: dict) -> bool:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    return bool(entry.get("is_primary") or meta.get("is_primary"))


class TeamView:
    def __init__(self, registry: AgentRegistry | None = None):
        self.registry = registry or get_registry()
        self.user_files_dir = Path(self.registry.user_files_dir)

    # ── folders ──────────────────────────────────────────────────────────

    def team_dir(self, owner: str, team: str) -> Path:
        """A team's folder; team "" is the owner's personal (root) scope."""
        base = self.user_files_dir / owner
        return base / "teams" / team if team else base

    def teams(self, owner: str) -> list[str]:
        root = self.user_files_dir / owner / "teams"
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))

    def exists(self, owner: str, team: str) -> bool:
        return bool(team) and self.team_dir(owner, team).is_dir()

    # ── manifest ─────────────────────────────────────────────────────────

    def reconcile(self, owner: str, team: str) -> None:
        """Give every named internal role a session, writing it back to the manifest."""
        path = self.team_dir(owner, team) / INTERNAL_MANIFEST
        if not path.is_file():
            return
        if all(
            not isinstance(e, dict) or "name" not in e or str(e.get("session") or "").strip()
            for e in _read_entries(path)
        ):
            return
        with _file_lock(path.with_name(f".{INTERNAL_MANIFEST}.lock")):
            entries = _read_entries(path)  # re-read: another process may have stamped it
            changed = False
            for entry in entries:
                if isinstance(entry, dict) and "name" in entry and not str(entry.get("session") or "").strip():
                    entry.pop("session", None)
                    entry["session"] = new_webot_session_id()  # appended last, as _ia_save does
                    changed = True
            if changed:
                _write_entries(path, entries)

    def entries(self, owner: str, team: str, kind: str) -> list[dict]:
        """The manifest's named entries of *kind* ("internal" / "external"), reconciled."""
        if kind == "internal":
            self.reconcile(owner, team)
            filename = INTERNAL_MANIFEST
        else:
            filename = EXTERNAL_MANIFEST
        return [
            e for e in _read_entries(self.team_dir(owner, team) / filename)
            if isinstance(e, dict) and "name" in e
        ]

    # ── membership ───────────────────────────────────────────────────────

    def members(self, owner: str, team: str) -> list[TeamMember]:
        """Every role in the team, internal first, in manifest order."""
        result: list[TeamMember] = []
        for kind in ("internal", "external"):
            for entry in self.entries(owner, team, kind):
                member = TeamMember(
                    role_name=str(entry.get("name") or "").strip(),
                    kind=kind,
                    tag=str(entry.get("tag") or "").strip(),
                    is_lead=_is_lead(entry),
                    entry=entry,
                )
                ref = member.binding_ref
                if ref:
                    member.agent = (
                        self.registry.webot_session(owner, ref) if kind == "internal"
                        else self.registry.external(owner, ref)
                    )
                result.append(member)
        return result

    def member(self, owner: str, team: str, ref: str) -> TeamMember:
        """A member by role name, agent handle, agent id, session or global_name."""
        wanted = (ref or "").strip().lstrip("@")
        lowered = wanted.lower()
        for member in self.members(owner, team):
            agent = member.agent
            if (
                member.role_name.lower() == lowered
                or member.binding_ref == wanted
                or (agent and wanted in (agent.agent_id, agent.handle, agent.address))
            ):
                return member
        raise LookupError(f"{ref!r} is not a member of team {team!r}")

    def lead(self, owner: str, team: str) -> TeamMember:
        """The member who speaks for the team (``is_primary``)."""
        if not self.exists(owner, team):
            raise TeamNotFound(f"no team {team!r} for {owner}")
        for member in self.members(owner, team):
            if member.is_lead and member.agent is not None:
                return member
        raise TeamHasNoLead(f"team {team!r} has no lead agent; mark one member as primary")

    def teams_of(self, owner: str, agent_id: str) -> list[str]:
        """The teams (by folder name) whose manifests include this agent."""
        return [
            team for team in self.teams(owner)
            if any(m.agent and m.agent.agent_id == agent_id for m in self.members(owner, team))
        ]

    @staticmethod
    def context(team: str) -> dict[str, Any]:
        """What callers pass as ``context`` when acting inside a team."""
        return {"team": team} if team else {}


def get_team_view(user_files_dir: str | os.PathLike | None = None) -> TeamView:
    return TeamView(get_registry(user_files_dir))
