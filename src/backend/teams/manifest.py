"""The team file format: ``internal_agents.json`` and ``external_agents.json``.

This is the only place that reads or writes them. They exist in a team package
(a preset, a snapshot zip, a folder team-builder just wrote); importing turns
their entries into agents and memberships, and the files are then removed from
the team folder. Exporting writes them again in the same shape.

    internal entry: {"name", "tag", "persona"?, "session"?, "is_primary"?, …}  — ``session`` is the agent's id
    external entry: {"name", "tag", "persona"?, "platform", "global_name", "meta": {api_url, api_key, model,
                     headers, …}, "is_primary"?}  — ``global_name`` is the agent's id; other keys stay
                     with the membership and are exported as is

An entry whose name is already a member of the team is that member; one that
names an agent of this owner is that agent; any other becomes a new agent.

A new agent gets its own copy of its persona: the entry's ``persona`` text, or
the team's persona ``tag`` looked up in the persona library (the team's own
first). The tag stays with the membership, and both are exported again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agents.store import WEBOT, Agent, canonical_platform, driver_for_platform, valid_agent_id
from teams.store import Member, TeamStore

INTERNAL_FILE = "internal_agents.json"
EXTERNAL_FILE = "external_agents.json"

_INTERNAL_KEYS = {"name", "persona", "session", "session_id", "is_primary"}
_EXTERNAL_KEYS = {"name", "persona", "platform", "global_name", "meta", "is_primary"}
_EXTERNAL_CONFIG = ("api_url", "api_key", "model", "headers")
_SECRET_KEYS = {
    "apikey", "authorization", "proxyauthorization", "xapikey", "cookie", "setcookie",
    "password", "secret", "token", "accesstoken", "refreshtoken", "idtoken", "clientsecret",
    "xauthtoken",
}


def without_secrets(value: Any) -> Any:
    """Remove explicit credential fields from portable JSON, preserving public settings."""
    if isinstance(value, dict):
        return {key: without_secrets(item) for key, item in value.items()
                if re.sub(r"[^a-z0-9]", "", key.lower()) not in _SECRET_KEYS}
    if isinstance(value, list):
        return [without_secrets(item) for item in value]
    return value


def _validate_entries(data: Any, label: str) -> list[dict]:
    if not isinstance(data, list):
        raise ValueError(f"{label}: expected a JSON array of members")
    for index, entry in enumerate(data):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
            raise ValueError(f"{label}: member {index + 1} requires a non-empty name")
        if "meta" in entry and not isinstance(entry["meta"], dict):
            raise ValueError(f"{label}: member {index + 1} meta must be an object")
    return data


def _entries(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except ValueError as exc:
        raise ValueError(f"{path.name}: invalid JSON") from exc
    return _validate_entries(data, path.name)


def read_folder(folder: Path) -> tuple[list[dict], list[dict]]:
    return _entries(folder / INTERNAL_FILE), _entries(folder / EXTERNAL_FILE)


def _is_lead(entry: dict) -> bool:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    return bool(entry.get("is_primary") or meta.get("is_primary"))


def _member(teams: TeamStore, owner: str, team: str, entry: dict) -> Agent | None:
    try:
        return teams.member(owner, team, str(entry["name"]).strip()).agent
    except LookupError:
        return None


def persona_of(owner: str, team: str, entry: dict) -> str:
    """The persona text an entry's agent gets: its own, or its tag's in the persona library."""
    if str(entry.get("persona") or "").strip():
        return str(entry["persona"]).strip()
    tag = str(entry.get("tag") or "").strip()
    if not tag:
        return ""
    from oasis.experts import get_all_experts

    found = next((e for e in get_all_experts(owner, team=team) if str(e.get("tag") or "").strip() == tag), None)
    return str((found or {}).get("persona") or "").strip()


def agent_for_internal_entry(teams: TeamStore, owner: str, team: str, entry: dict) -> Agent:
    session = str(entry.get("session") or entry.get("session_id") or "").strip()
    found = _member(teams, owner, team, entry) or (teams.agents.get(owner, session) if session else None)
    if found is not None:
        return found
    config: dict[str, Any] = {"persona": persona_of(owner, team, entry)}
    if entry.get("tools") is not None:
        config["tools"] = entry["tools"]
    return teams.agents.create(owner, driver=WEBOT, config=config, name=str(entry["name"]).strip(),
                               agent_id=session if valid_agent_id(session) else "")


def agent_for_external_entry(teams: TeamStore, owner: str, team: str, entry: dict) -> Agent:
    platform = canonical_platform(str(entry.get("platform") or entry.get("tag") or ""))
    driver = driver_for_platform(platform)
    ref = str(entry.get("global_name") or "").strip()
    meta = entry.get("meta") or {}
    meta = dict(meta) if isinstance(meta, dict) else {}
    config: dict[str, Any] = {
        "platform": platform,
        "persona": persona_of(owner, team, entry),
        **{key: meta.pop(key) for key in _EXTERNAL_CONFIG if key in meta},
        "meta": meta,
    }
    found = _member(teams, owner, team, entry) or (teams.agents.get(owner, ref) if ref else None)
    if found is not None:
        return teams.agents.update(owner, found.agent_id, config={**found.config, **config})
    return teams.agents.create(owner, driver=driver, config=config, name=str(entry["name"]).strip(),
                               agent_id=ref if valid_agent_id(ref) else "")


def import_entries(teams: TeamStore, owner: str, team: str, internal: list[dict], external: list[dict]) -> list[Member]:
    """Make the team's membership exactly what the entries say.

    Members the entries no longer list leave the team; their agents stay.
    """
    _validate_entries(internal, INTERNAL_FILE)
    _validate_entries(external, EXTERNAL_FILE)
    names = [entry["name"].strip().casefold() for entry in internal + external]
    if len(names) != len(set(names)):
        raise ValueError("Team member names must be unique")
    teams.create(owner, team)
    wanted: list[tuple[Agent, dict, dict]] = []
    for entry in internal:
        extra = {k: v for k, v in entry.items() if k not in _INTERNAL_KEYS}
        wanted.append((agent_for_internal_entry(teams, owner, team, entry), entry, extra))
    for entry in external:
        extra = {k: v for k, v in entry.items() if k not in _EXTERNAL_KEYS}
        wanted.append((agent_for_external_entry(teams, owner, team, entry), entry, extra))

    keep = {agent.agent_id for agent, _e, _x in wanted}
    for member in teams.members(owner, team):
        if member.agent.agent_id not in keep:
            teams.remove(owner, team, member.agent.agent_id)
    for agent, entry, extra in wanted:
        teams.add(owner, team, agent.agent_id, role=str(entry["name"]).strip(), is_lead=_is_lead(entry), extra=extra)
    return teams.members(owner, team)


def import_folder(teams: TeamStore, owner: str, team: str) -> list[Member]:
    """Import the manifest files in the team's folder, then remove them."""
    folder = teams.folder(owner, team)
    if not any((folder / name).is_file() for name in (INTERNAL_FILE, EXTERNAL_FILE)):
        return teams.members(owner, team)
    internal, external = read_folder(folder)
    members = import_entries(teams, owner, team, internal, external)
    for name in (INTERNAL_FILE, EXTERNAL_FILE):
        (folder / name).unlink(missing_ok=True)
    return members


def export_entries(teams: TeamStore, owner: str, team: str, *, portable: bool) -> tuple[list[dict], list[dict]]:
    """The team as manifest entries. ``portable`` leaves out this machine's agent ids
    and secrets, so importing elsewhere creates new agents."""
    internal: list[dict] = []
    external: list[dict] = []
    for m in teams.members(owner, team):
        config = m.agent.config
        if m.agent.driver == WEBOT:
            entry: dict[str, Any] = {"name": m.role, **m.extra}
            if config.get("persona"):
                entry["persona"] = config["persona"]
            if config.get("tools") is not None:
                entry["tools"] = config["tools"]
            if m.is_lead:
                entry["is_primary"] = True
            if not portable:
                entry["session"] = m.agent.agent_id
            internal.append(without_secrets(entry) if portable else entry)
            continue
        meta = dict(config.get("meta") or {})
        for key in _EXTERNAL_CONFIG:
            if config.get(key) not in (None, "", {}):
                meta[key] = config[key]
        entry = {"name": m.role, "platform": config.get("platform", ""), **m.extra}
        if config.get("persona"):
            entry["persona"] = config["persona"]
        if not portable:
            entry["global_name"] = m.agent.agent_id
        entry["meta"] = meta
        if m.is_lead:
            entry["is_primary"] = True
        external.append(without_secrets(entry) if portable else entry)
    return internal, external


def dumps(entries: list[dict]) -> str:
    return json.dumps(entries, ensure_ascii=False, indent=2)


def _ascii_slug(text: str, fallback: str, limit: int) -> str:
    """Lowercase ``a-z0-9`` only, starting with a letter; *fallback* plus a number when nothing is left."""
    slug = re.sub(r"[^a-z0-9]+", "", (text or "").strip().lower())
    if not slug:
        slug = f"{fallback}{abs(hash(text)) % 900_000 + 100_000}"
    if slug[0].isdigit():
        slug = fallback[0] + slug
    return slug[:limit]


def imported_agent_id(team: str, entry: dict, external: list[dict]) -> str:
    """A stable ASCII id for an external member imported from a package:
    ``<team>_<name>``, numbered when several entries share the name."""
    base = f"{_ascii_slug(team, 'team', 48)}_{_ascii_slug(entry.get('name', ''), 'agent', 32)}"
    name = (entry.get("name", "") or "").strip()
    same_name = [e for e in external if isinstance(e, dict) and (e.get("name", "") or "").strip() == name]
    if len(same_name) < 2:
        return base
    try:
        return f"{base}_{same_name.index(entry) + 1}"
    except ValueError:
        return f"{base}_1"
