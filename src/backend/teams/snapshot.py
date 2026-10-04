"""Install a snapshot as a fresh team, with explicit replacement of name conflicts."""

from __future__ import annotations

import json
import secrets
import shutil
import tempfile
from pathlib import Path

from teams.manifest import EXTERNAL_FILE, INTERNAL_FILE, import_entries, read_folder
from teams.snapshot_skills import _team_skills_dir, restore_skills_from_team_dir
from teams.store import MEMBERS_FILE, TeamStore


class TeamExistsError(ValueError):
    pass


def install_snapshot(teams: TeamStore, owner: str, team: str, assets: Path, skills: Path,
                     *, replace: bool = False):
    """Validate first; replace team assets/members/skills together, restoring them on failure.

    Snapshot members always get new agent ids. Existing agents remain in the
    owner's agent registry, following TeamStore.delete's ownership semantics.
    """
    internal, external = read_folder(assets)
    names = [entry["name"].strip().casefold() for entry in internal + external]
    if len(names) != len(set(names)):
        raise ValueError("Team member names must be unique")
    experts = assets / "oasis_experts.json"
    if experts.exists():
        data = json.loads(experts.read_text(encoding="utf-8"))
        if not isinstance(data, list) or any(not isinstance(entry, dict) for entry in data):
            raise ValueError("oasis_experts.json: expected a JSON array of personas")
    if (assets / MEMBERS_FILE).exists():
        raise ValueError("Use internal_agents.json / external_agents.json, not runtime members.json")

    folder = teams.folder(owner, team)
    existed = folder.exists()
    if existed and not replace:
        raise TeamExistsError(f"Team '{team}' already exists")
    previous = teams.members(owner, team) if existed else []
    # Assign fresh ids even when a legacy package contains this machine's ids.
    created_ids = []
    for entries, key in ((internal, "session"), (external, "global_name")):
        for entry in entries:
            entry.pop("session_id", None)
            entry.pop("session", None)
            entry.pop("global_name", None)
            entry[key] = "ag_" + secrets.token_hex(12)
            created_ids.append(entry[key])

    skill_folder = _team_skills_dir(owner, team)
    folder.parent.mkdir(parents=True, exist_ok=True)
    skill_folder.parent.mkdir(parents=True, exist_ok=True)
    original_members = (folder / MEMBERS_FILE).read_bytes() if (folder / MEMBERS_FILE).is_file() else None
    with tempfile.TemporaryDirectory(prefix=".team-import-", dir=folder.parent) as team_backup, \
            tempfile.TemporaryDirectory(prefix=".skill-import-", dir=skill_folder.parent) as skill_backup:
        saved_team = Path(team_backup) / "team"
        saved_skills = Path(skill_backup) / "skills"
        try:
            for member in previous:
                teams.remove(owner, team, member.agent.agent_id)
            if folder.exists():
                folder.rename(saved_team)
                if original_members is not None:
                    (saved_team / MEMBERS_FILE).write_bytes(original_members)
            if skill_folder.exists():
                skill_folder.rename(saved_skills)
            shutil.copytree(assets, folder)
            members = import_entries(teams, owner, team, internal, external)
            for name in (INTERNAL_FILE, EXTERNAL_FILE):
                (folder / name).unlink(missing_ok=True)
            restored_skills = restore_skills_from_team_dir(skills, owner, team)
        except Exception:
            if saved_team.exists() or not existed:
                shutil.rmtree(folder, ignore_errors=True)
            if saved_skills.exists():
                shutil.rmtree(skill_folder, ignore_errors=True)
            for agent_id in created_ids:
                teams.agents.delete(owner, agent_id)
            if saved_team.exists():
                saved_team.rename(folder)
            if saved_skills.exists():
                saved_skills.rename(skill_folder)
            for member in previous:
                teams.add(owner, team, member.agent.agent_id, role=member.role,
                          is_lead=member.is_lead, extra=member.extra)
            raise
    return members, restored_skills
