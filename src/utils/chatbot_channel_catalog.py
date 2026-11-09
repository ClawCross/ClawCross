"""Shared chatbot channel catalog loader.

The source of truth is ``config/chatbot_channels.json`` so the mobile UI,
settings API, and CLI all describe the same channel-specific fields.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    from utils.runtime_paths import PROJECT_ROOT
except ModuleNotFoundError:  # CLI imports this module as src.utils.*
    from src.utils.runtime_paths import PROJECT_ROOT


CATALOG_PATH = PROJECT_ROOT / "config" / "chatbot_channels.json"


@lru_cache(maxsize=1)
def load_chatbot_channel_catalog() -> dict[str, Any]:
    try:
        with open(CATALOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    channels = data.get("channels")
    if not isinstance(channels, list):
        channels = []
    common_keys = data.get("common_keys")
    if not isinstance(common_keys, list):
        common_keys = ["NONEBOT_ADAPTERS", "NONEBOT_HOST", "NONEBOT_PORT", "WHITELIST_FILE"]
    return {
        "common_keys": [str(k) for k in common_keys if k],
        "channels": [ch for ch in channels if isinstance(ch, dict) and ch.get("id")],
    }


def get_chatbot_channels() -> list[dict[str, Any]]:
    return list(load_chatbot_channel_catalog()["channels"])


def get_chatbot_channel(channel_id_or_adapter: str) -> dict[str, Any] | None:
    key = str(channel_id_or_adapter or "").strip().lower()
    if not key:
        return None
    for channel in get_chatbot_channels():
        channel_id = str(channel.get("id") or "").strip().lower()
        adapter = str(channel.get("adapter") or channel_id).strip().lower()
        aliases = [str(x).strip().lower() for x in channel.get("aliases") or []]
        if key in {channel_id, adapter, *aliases}:
            return dict(channel)
    return None


def get_chatbot_common_keys() -> list[str]:
    return list(load_chatbot_channel_catalog()["common_keys"])


def get_chatbot_env_keys() -> list[str]:
    keys: set[str] = set(get_chatbot_common_keys())
    for channel in get_chatbot_channels():
        env_key = str(channel.get("env_key") or "").strip()
        if env_key:
            keys.add(env_key)
        for alias in channel.get("env_aliases") or []:
            alias_key = str(alias or "").strip()
            if alias_key:
                keys.add(alias_key)
        for field in channel.get("fields") or []:
            if not isinstance(field, dict):
                continue
            target = str(field.get("target") or "").strip()
            key = str(field.get("env_key") or field.get("name") or "").strip()
            if target == "env" and key:
                keys.add(key)
    return sorted(keys)


def get_nonebot_adapter_meta(adapter_name: str) -> dict[str, Any]:
    channel = get_chatbot_channel(adapter_name) or {}
    adapter = str(channel.get("adapter") or adapter_name or "").strip()
    normalized = adapter.lower().replace(" ", "")
    flat = normalized.replace("-", "_").replace(".", "_")
    compact = normalized.replace("-", "").replace("_", "").replace(".", "")
    if "env_key" in channel:
        env_key = str(channel.get("env_key") or "").strip()
    else:
        env_key = f"{compact.upper()}_BOTS"
    config_field = str(channel.get("config_field") or env_key.lower()).strip()
    package = str(channel.get("package") or f"nonebot-adapter-{normalized.split('.', 1)[0].replace('_', '-')}").strip()
    module = str(channel.get("module") or f"nonebot.adapters.{normalized.replace('-', '_')}").strip()
    aliases = [str(x).strip() for x in channel.get("env_aliases") or [] if str(x or "").strip()]
    required_env = [str(x).strip() for x in channel.get("required_env") or [] if str(x or "").strip()]
    for field in channel.get("fields") or []:
        if not isinstance(field, dict):
            continue
        if str(field.get("target") or "").strip() != "env" or not field.get("required"):
            continue
        key = str(field.get("env_key") or field.get("name") or "").strip()
        if key and key not in required_env:
            required_env.append(key)
    return {
        "id": str(channel.get("id") or adapter).strip(),
        "adapter": adapter,
        "module": module,
        "package": package,
        "env_key": env_key,
        "env_aliases": aliases,
        "config_field": config_field,
        "requires_config": bool(channel.get("requires_config", bool(env_key))),
        "required_env": required_env,
        "package_required": bool(channel.get("package_required", True)),
    }
