"""External agents of a user, keyed by global_name, in group-member shape.

A view over the L1 agent registry, which indexes every external_agents.json
(user root first, then teams in sorted order; the last entry wins).
"""

from __future__ import annotations

from typing import Any

from agents.registry import DRIVER_WEBOT, get_registry
from utils.runtime_paths import USER_FILES_DIR


def build_external_agents_map_for_owner(owner_uid: str) -> dict[str, dict[str, Any]]:
    if not owner_uid:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for record in get_registry(USER_FILES_DIR).list(owner_uid):
        if record.driver == DRIVER_WEBOT:
            continue
        binding = record.binding
        global_name = str(binding.get("global_name") or "")
        name = str(binding.get("name") or "")
        meta = binding.get("meta") if isinstance(binding.get("meta"), dict) else {}
        result[global_name] = {
            "user_id": "ext",
            "owner_user_id": owner_uid,
            "global_id": global_name,
            "short_name": name,
            "member_type": "ext",
            "tag": binding.get("tag", ""),
            "global_name": global_name,
            "name": name,
            "team": binding.get("team", ""),
            "platform": binding.get("platform", ""),
            "api_url": binding.get("api_url", ""),
            "api_key": binding.get("api_key", ""),
            "model": binding.get("model", ""),
            "meta": meta,
            "agent_id": record.agent_id,
        }
    return result


def invalidate_external_agents_cache(owner_uid: str = "") -> None:
    """Kept for callers of the old cache; the registry tracks file changes itself."""
    return None
