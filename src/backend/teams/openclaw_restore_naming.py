"""
OpenClaw 恢复时的 id / 展示名策略：

- id（agent_name）：仅小写英文 + 数字，形如 {team_slug}_{序号}，避免 emoji 导致 CLI 截断、冲突。
- display_name：可含中文、emoji，写入 openclaw agents.list[].name；团队 JSON 的 global_name 存 id。
"""

from __future__ import annotations

import re


def team_slug_ascii(team: str) -> str:
    """团队名小写，只保留 a-z0-9；若为空则用 team + 数字后缀保证非空。"""
    s = (team or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "", s)
    if not slug:
        h = abs(hash(team)) % 900_000 + 100_000
        slug = f"team{h}"
    if slug[0].isdigit():
        slug = "t" + slug
    return slug[:48]


def name_slug_ascii(name: str) -> str:
    """成员名小写，只保留 a-z0-9；若为空则回退到 agent + 数字后缀。"""
    s = (name or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "", s)
    if not slug:
        h = abs(hash(name)) % 900_000 + 100_000
        slug = f"agent{h}"
    if slug[0].isdigit():
        slug = "a" + slug
    return slug[:32]


def openclaw_entries_ordered(entries: list) -> list:
    """团队中 openclaw 成员按列表顺序（与 JSON 一致）。"""
    return [e for e in entries if isinstance(e, dict) and (e.get("platform", "") or "").strip().lower() == "openclaw"]


def restore_agent_id(team: str, entry: dict, openclaw_ordered: list) -> str:
    """
    稳定序号：在 openclaw_ordered 中的 1-based 位置。
    entry 须为同一列表中的引用，以便 index 一致。
    """
    slug = team_slug_ascii(team)
    try:
        idx = openclaw_ordered.index(entry) + 1
    except ValueError:
        idx = len(openclaw_ordered)
    return f"{slug}_{idx}"


def restore_display_name(team: str, short_name: str) -> str:
    """OpenClaw 配置里的 name（展示用，可含任意 Unicode）。"""
    sn = (short_name or "").strip()
    t = (team or "").strip()
    if t and sn:
        return f"{t}_{sn}"
    return sn or t or "agent"


def restore_external_global_name(team: str, entry: dict, external_ordered: list) -> str:
    """为非 OpenClaw 外部 agent 生成稳定 ASCII global_name。"""
    team_slug = team_slug_ascii(team)
    base = f"{team_slug}_{name_slug_ascii(entry.get('name', ''))}"

    same_name_entries = [
        e for e in external_ordered
        if isinstance(e, dict) and (e.get("name", "") or "").strip() == (entry.get("name", "") or "").strip()
    ]
    try:
        dup_idx = same_name_entries.index(entry) + 1
    except ValueError:
        dup_idx = 1

    if len(same_name_entries) > 1:
        return f"{base}_{dup_idx}"
    return base
