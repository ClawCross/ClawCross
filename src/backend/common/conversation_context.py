"""Conversation metadata only; message bodies and digests belong in the inbox."""
from __future__ import annotations

import json

_FIELDS = ("group_id", "title", "kind", "identity", "role", "delivery", "reply_channel")


def normalize_group_metadata(groups: list[dict]) -> list[dict]:
    latest = {}
    for group in groups:
        if not isinstance(group, dict) or not group.get("group_id"):
            continue
        item = {key: str(group[key]) for key in _FIELDS if key in group}
        item["members"] = [
            {key: member[key] for key in ("name", "kind", "muted") if key in member}
            for member in group.get("members") or [] if isinstance(member, dict)
        ]
        latest[item["group_id"]] = item
    return list(latest.values())


def render_group_metadata(groups: list[dict]) -> str:
    current = normalize_group_metadata(groups)
    return json.dumps(current, ensure_ascii=False, sort_keys=True) if current else ""
