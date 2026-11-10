"""Workflow-local names for existing Agents. Bindings never create Agents."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import yaml


def validate_agent_map(value) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("agent_map must be a name -> Agent ID mapping")
    result, seen = {}, set()
    for name, agent_id in value.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_map names and Agent IDs must be non-empty strings")
        name, agent_id = name.strip().lstrip("@"), agent_id.strip()
        if not name or name.casefold() in seen:
            raise ValueError(f"duplicate or empty agent_map name: {name!r}")
        seen.add(name.casefold())
        result[name] = agent_id
    return result


def merge_agent_maps(*maps) -> dict[str, str]:
    result = {}
    for mapping in maps:
        for name, agent_id in validate_agent_map(mapping).items():
            previous = next((key for key in result if key.casefold() == name.casefold()), None)
            if previous is not None:
                del result[previous]
            result[name] = agent_id
    return result


def load_agent_map(filename: str | Path) -> dict[str, str]:
    """Load a plain name/ID table, or an ``agent_map`` block, from YAML or JSON."""
    data = yaml.safe_load(Path(filename).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("agent_map"), dict):
        data = data["agent_map"]
    return validate_agent_map(data)


def team_agent_map(user_id: str, team: str) -> dict[str, str]:
    if not team:
        return {}
    from teams.store import get_team_store

    result, seen = {}, set()
    for member in get_team_store().members(user_id, team):
        # Match TeamStore.member's existing first-match semantics, including
        # the virtual user Team where display names need not be unique.
        if member.role.casefold() not in seen:
            result[member.role] = member.agent.agent_id
            seen.add(member.role.casefold())
    return validate_agent_map(result)


def mapped_agent(user_id: str, ref: str, mapping: dict[str, str]):
    """Return a bound Agent, or None for an ordinary unbound reference."""
    key = ref.strip().lstrip("@").casefold()
    for name, agent_id in mapping.items():
        if name.casefold() == key:
            from agents.store import get_store

            agent = get_store().get(user_id, agent_id)
            if agent is None:
                raise ValueError(f"agent_map[{name!r}] points to a missing Agent for this user: {agent_id}")
            return agent, name
    return None


def add_agent_map_arguments(parser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--agent-map", help="Runtime name -> Agent ID mapping as JSON.")
    group.add_argument("--agent-map-file", help="Read this run's Agent mapping from a YAML/JSON file.")


def runtime_agent_map(args) -> dict[str, str]:
    if args.agent_map_file is not None:
        return load_agent_map(args.agent_map_file)
    if args.agent_map is not None:
        return validate_agent_map(json.loads(args.agent_map))
    return {}
