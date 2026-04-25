from __future__ import annotations

import json
import os
import threading
from typing import Any

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_CACHE_LOCK = threading.Lock()
_OWNER_CACHE: dict[str, tuple[tuple[tuple[str, int, int], ...], dict[str, dict[str, Any]]]] = {}


def _canonical_external_platform(platform: str) -> str:
    pl = (platform or "").strip().lower()
    if pl in ("claude-code", "claudecode"):
        return "claude"
    if pl in ("gemini-cli", "geminicli"):
        return "gemini"
    return pl


def _public_external_agents_path(user_id: str) -> str:
    return os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "external_agents.json")


def _team_external_agents_path(user_id: str, team: str) -> str:
    return os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "teams", team, "external_agents.json")


def _collect_owner_external_agent_files(owner_uid: str) -> list[tuple[str, str]]:
    files: list[tuple[str, str]] = []
    if not owner_uid:
        return files

    public_path = _public_external_agents_path(owner_uid)
    if os.path.isfile(public_path):
        files.append((public_path, ""))

    teams_dir = os.path.join(_PROJECT_ROOT, "data", "user_files", owner_uid, "teams")
    if not os.path.isdir(teams_dir):
        return files

    for team_dir in sorted(os.listdir(teams_dir)):
        team_path = _team_external_agents_path(owner_uid, team_dir)
        if os.path.isfile(team_path):
            files.append((team_path, team_dir))
    return files


def _build_fingerprint(files: list[tuple[str, str]]) -> tuple[tuple[str, int, int], ...]:
    fingerprint: list[tuple[str, int, int]] = []
    for path, _team in files:
        try:
            stat = os.stat(path)
            fingerprint.append((path, stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            continue
    return tuple(fingerprint)


def _parse_external_agents_file(path: str, *, owner_user_id: str = "", team: str = "") -> list[dict[str, Any]]:
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
    except Exception:
        return []

    result: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or "name" not in item:
            continue
        ext_config = item.get("config") or item.get("meta") or {}
        if not isinstance(ext_config, dict):
            ext_config = {}
        name = str(item.get("name", "") or "")
        global_name = str(item.get("global_name", "") or "")
        result.append({
            "user_id": "ext",
            "owner_user_id": owner_user_id,
            "global_id": global_name,
            "short_name": name,
            "member_type": "ext",
            "tag": item.get("tag", ""),
            "global_name": global_name,
            "name": name,
            "team": team,
            "platform": _canonical_external_platform(str(item.get("platform", "") or "")),
            "api_url": ext_config.get("api_url", ""),
            "api_key": ext_config.get("api_key", ""),
            "model": ext_config.get("model", ""),
            "meta": ext_config if isinstance(ext_config, dict) else {},
        })
    return result


def build_external_agents_map_for_owner(owner_uid: str) -> dict[str, dict[str, Any]]:
    if not owner_uid:
        return {}

    files = _collect_owner_external_agent_files(owner_uid)
    fingerprint = _build_fingerprint(files)

    with _CACHE_LOCK:
        cached = _OWNER_CACHE.get(owner_uid)
        if cached and cached[0] == fingerprint:
            return dict(cached[1])

    external_agents_map: dict[str, dict[str, Any]] = {}
    for path, team in files:
        for agent in _parse_external_agents_file(path, owner_user_id=owner_uid, team=team):
            gid = str(agent.get("global_id") or agent.get("global_name") or "").strip()
            if gid:
                external_agents_map[gid] = agent

    with _CACHE_LOCK:
        _OWNER_CACHE[owner_uid] = (fingerprint, dict(external_agents_map))
    return external_agents_map


def invalidate_external_agents_cache(owner_uid: str = "") -> None:
    with _CACHE_LOCK:
        if owner_uid:
            _OWNER_CACHE.pop(owner_uid, None)
        else:
            _OWNER_CACHE.clear()
