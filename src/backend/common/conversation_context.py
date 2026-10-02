"""Conversation metadata only; message bodies and digests belong in the inbox."""
from __future__ import annotations

import json
from typing import Callable

_FIELDS = ("group_id", "title", "kind", "owner", "identity", "role", "delivery", "reply_channel", "primary_agent", "server_url", "remote_group_id")
_membership_provider: Callable[[str, str], list[dict]] | None = None


def set_group_membership_provider(provider: Callable[[str, str], list[dict]]) -> None:
    """The service composition supplies live facts without coupling runtimes to groups."""
    global _membership_provider
    _membership_provider = provider


def group_memberships(user_id: str, agent_id: str) -> list[dict]:
    return normalize_group_metadata(_membership_provider(user_id, agent_id)) if _membership_provider else []


def normalize_group_metadata(groups: list[dict]) -> list[dict]:
    latest = {}
    for group in groups:
        if not isinstance(group, dict) or not group.get("group_id"):
            continue
        item = {key: str(group[key]) for key in _FIELDS if key in group}
        item["members"] = [
            {key: member[key] for key in ("name", "kind", "muted", "agent_id", "user_id", "node_id") if key in member}
            for member in group.get("members") or [] if isinstance(member, dict)
        ]
        latest[item["group_id"]] = item
    return list(latest.values())


def render_group_metadata(groups: list[dict]) -> str:
    current = normalize_group_metadata(groups)
    return json.dumps(current, ensure_ascii=False, sort_keys=True) if current else ""


def current_group_metadata(sources: list[dict], memberships: list[dict] | None = None) -> list[dict]:
    """Resolve source channels against live membership, never stale delivery details."""
    sources = normalize_group_metadata(sources)
    if memberships is None:
        return sources
    source_ids = {group['group_id'] for group in sources}
    return [group for group in normalize_group_metadata(memberships) if group['group_id'] in source_ids]
