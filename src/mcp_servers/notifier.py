import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

#!/usr/bin/env python3

# -*- coding: utf-8 -*-
"""
MCP 多通道推送通知服务

Agent 可通过此工具向用户的任意已配置社交通道（Telegram、未来扩展 WeClaw/Minecraft 等）
推送消息。通道存储在 data/user_files/<username>/notification_channels.json，
形如：
    {
      "telegram": {"target_id": "12345678", "display_name": "alice"},
      "weclaw":   {"target_id": "...",      "display_name": "..."}
    }

当前内置后端：telegram（沿用 .env 的 TELEGRAM_BOT_TOKEN / TELEGRAM_BOTS）。
要新增通道，只需在 _SENDERS / _STATUS_PROBES 注册函数并把它加入 _AVAILABLE_CHANNELS。
"""

import json
import os
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from utils.runtime_paths import DATA_DIR, ENV_FILE, USER_FILES_DIR

mcp = FastMCP("Notifier")

load_dotenv(dotenv_path=ENV_FILE)

USER_DATA_DIR = str(USER_FILES_DIR)
CHANNELS_FILENAME = "notification_channels.json"
LEGACY_TG_FILENAME = "tg_chat_id.txt"


# ── Whitelist (central) helpers ─────────────────────────────────────

def _resolve_whitelist_file() -> str:
    configured = (os.getenv("WHITELIST_FILE") or "").strip()
    if not configured:
        return os.path.join(str(DATA_DIR), "whitelist.json")
    expanded = os.path.expanduser(configured)
    if os.path.isabs(expanded):
        return expanded
    parts = expanded.replace("\\", "/").split("/")
    if parts and parts[0] == "data":
        expanded = "/".join(parts[1:]) or "whitelist.json"
    return os.path.join(str(DATA_DIR), expanded)


WHITELIST_FILE = _resolve_whitelist_file()


def _load_full_whitelist() -> dict:
    if not os.path.exists(WHITELIST_FILE):
        return {}
    try:
        with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _load_channel_section(channel: str) -> dict:
    full = _load_full_whitelist()
    section = full.get(channel) or {}
    return {
        "entries": section.get("entries", {}) or {},
        "name_map": section.get("name_map", {}) or {},
    }


def _save_channel_section(channel: str, section: dict) -> None:
    full = _load_full_whitelist()
    full[channel] = {
        "entries": section.get("entries", {}),
        "name_map": section.get("name_map", {}),
    }
    os.makedirs(os.path.dirname(WHITELIST_FILE), exist_ok=True)
    with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)


def _sync_to_whitelist(channel: str, username: str, target_id: str, display_name: str = "") -> None:
    section = _load_channel_section(channel)
    entries = section["entries"]
    name_map = section["name_map"]

    stale_keys = [k for k, v in entries.items() if v.get("username") == username and k != str(target_id)]
    for k in stale_keys:
        entries.pop(k, None)

    entry = {"username": username}
    if display_name:
        entry["tg_username" if channel == "telegram" else "display_name"] = display_name
    entries[str(target_id)] = entry
    if display_name:
        name_map[display_name] = {"username": username}

    _save_channel_section(channel, section)


def _remove_from_whitelist(channel: str, username: str) -> None:
    section = _load_channel_section(channel)
    entries = section["entries"]
    name_map = section["name_map"]
    for k in [k for k, v in entries.items() if v.get("username") == username]:
        entries.pop(k, None)
    for k in [k for k, v in name_map.items() if v.get("username") == username]:
        name_map.pop(k, None)
    _save_channel_section(channel, section)


# ── Per-user channel config ─────────────────────────────────────────

def _user_dir(username: str) -> str:
    path = os.path.join(USER_DATA_DIR, username)
    os.makedirs(path, exist_ok=True)
    return path


def _channels_path(username: str) -> str:
    return os.path.join(_user_dir(username), CHANNELS_FILENAME)


def _legacy_telegram_path(username: str) -> str:
    return os.path.join(_user_dir(username), LEGACY_TG_FILENAME)


def _load_channels(username: str) -> dict:
    """Read per-user channel map. Migrates legacy tg_chat_id.txt once."""
    path = _channels_path(username)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (OSError, json.JSONDecodeError):
            pass

    legacy_path = _legacy_telegram_path(username)
    if os.path.exists(legacy_path):
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                chat_id = f.read().strip()
            if chat_id:
                migrated = {"telegram": {"target_id": chat_id, "display_name": ""}}
                _save_channels(username, migrated)
                return migrated
        except OSError:
            pass

    return {}


def _save_channels(username: str, channels: dict) -> None:
    path = _channels_path(username)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(channels, f, ensure_ascii=False, indent=2)


def _set_channel(username: str, channel: str, target_id: str, display_name: str = "") -> None:
    channels = _load_channels(username)
    channels[channel] = {"target_id": target_id, "display_name": display_name}
    _save_channels(username, channels)


def _delete_channel(username: str, channel: str) -> bool:
    channels = _load_channels(username)
    if channel not in channels:
        return False
    channels.pop(channel, None)
    _save_channels(username, channels)
    return True


# ── Channel backends ────────────────────────────────────────────────

@dataclass
class SendResult:
    ok: bool
    detail: str


def _resolve_telegram_token() -> str:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if token:
        return token
    raw = (os.getenv("TELEGRAM_BOTS") or "").strip()
    if not raw:
        return ""
    try:
        bots = json.loads(raw)
        if isinstance(bots, list) and bots:
            first = bots[0]
            if isinstance(first, dict):
                return str(first.get("token") or "").strip()
    except Exception:
        pass
    return ""


async def _send_telegram(target_id: str, text: str, parse_mode: str) -> SendResult:
    token = _resolve_telegram_token()
    if not token:
        return SendResult(False, "未配置 TELEGRAM_BOT_TOKEN / TELEGRAM_BOTS")

    api = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": target_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(api, json=payload, timeout=15.0)
            data = resp.json()
            if data.get("ok"):
                return SendResult(True, "已发送")
            err = data.get("description", "未知错误")
            # Markdown 解析失败时回退纯文本
            if parse_mode and "parse" in err.lower():
                payload["parse_mode"] = ""
                retry = await client.post(api, json=payload, timeout=15.0)
                if retry.json().get("ok"):
                    return SendResult(True, "已发送（降级为纯文本）")
            return SendResult(False, f"Telegram 拒绝: {err}")
        except httpx.ConnectError:
            return SendResult(False, "无法连接 Telegram API")
        except Exception as exc:
            return SendResult(False, f"Telegram 发送异常: {exc}")


def _telegram_status() -> tuple[bool, str]:
    token = _resolve_telegram_token()
    if not token:
        return False, "Bot Token 未配置（.env 中 TELEGRAM_BOT_TOKEN/TELEGRAM_BOTS 都为空）"
    masked = token[:8] + "****" if len(token) > 8 else "****"
    return True, f"Bot Token: {masked}"


Sender = Callable[[str, str, str], Awaitable[SendResult]]
StatusProbe = Callable[[], tuple[bool, str]]

_SENDERS: dict[str, Sender] = {
    "telegram": _send_telegram,
}

_STATUS_PROBES: dict[str, StatusProbe] = {
    "telegram": _telegram_status,
}

_AVAILABLE_CHANNELS = tuple(sorted(_SENDERS.keys()))


def _normalize_channel(channel: str, *, default: str = "telegram") -> str:
    value = (channel or default).strip().lower()
    aliases = {"tg": "telegram"}
    return aliases.get(value, value)


def _pick_default_channel(username: str) -> str | None:
    channels = _load_channels(username)
    if "telegram" in channels:
        return "telegram"
    for ch in _AVAILABLE_CHANNELS:
        if ch in channels:
            return ch
    return None


# ── MCP tools ───────────────────────────────────────────────────────

@mcp.tool()
async def list_notification_channels() -> str:
    """
    列出当前部署支持的推送通道及其后端配置状态。

    :return: 多行文本，标注每个通道是否可用以及原因。
    """
    lines = ["📣 可用推送通道："]
    for channel in _AVAILABLE_CHANNELS:
        probe = _STATUS_PROBES.get(channel)
        if probe is None:
            lines.append(f"  • {channel}: ⚠️ 无状态探针")
            continue
        ok, detail = probe()
        icon = "✅" if ok else "❌"
        lines.append(f"  • {channel}: {icon} {detail}")
    return "\n".join(lines)


@mcp.tool()
async def set_notification_channel(
    username: str,
    channel: str,
    target_id: str,
    display_name: str = "",
) -> str:
    """
    为用户设置某个推送通道的目标地址，并同步到中心化白名单。

    :param username: 用户标识符（系统自动注入，无需手动传递）
    :param channel: 通道名（如 "telegram"）。可用值见 list_notification_channels。
    :param target_id: 渠道用户 ID（如 Telegram chat_id）
    :param display_name: 渠道显示名 / @用户名（不要带 @），可选
    :return: 操作结果描述
    """
    ch = _normalize_channel(channel, default="")
    if not ch:
        return "❌ channel 不能为空。"
    if ch not in _SENDERS:
        return f"❌ 不支持的通道: {ch}（当前支持：{', '.join(_AVAILABLE_CHANNELS)}）"
    if not target_id or not target_id.strip():
        return "❌ target_id 不能为空。"

    target_id = target_id.strip()
    display = display_name.strip().lstrip("@") if display_name else ""

    _set_channel(username, ch, target_id, display)
    _sync_to_whitelist(ch, username, target_id, display)

    return (
        f"✅ 已保存 {ch} 推送目标：{target_id}\n"
        f"✅ 已加入 {ch} 白名单。"
    )


@mcp.tool()
async def remove_notification_channel(username: str, channel: str = "") -> str:
    """
    移除用户在指定通道的推送配置；channel 为空时清空所有通道。

    :param username: 用户标识符（系统自动注入，无需手动传递）
    :param channel: 通道名，可选。空 = 全部移除。
    :return: 操作结果描述
    """
    channels = _load_channels(username)
    if not channels:
        return "ℹ️ 该用户未配置任何推送通道。"

    targets: list[str]
    if channel:
        ch = _normalize_channel(channel, default="")
        if ch not in channels:
            return f"ℹ️ 该用户未配置 {ch} 通道，无需移除。"
        targets = [ch]
    else:
        targets = list(channels.keys())

    removed: list[str] = []
    for ch in targets:
        if _delete_channel(username, ch):
            _remove_from_whitelist(ch, username)
            removed.append(ch)

    return "✅ 已移除通道：" + ", ".join(removed) if removed else "ℹ️ 没有可移除的通道。"


@mcp.tool()
async def send_notification(
    username: str,
    text: str,
    channel: str = "",
    source_session: str = "",
    parse_mode: str = "Markdown",
) -> str:
    """
    向用户某个通道推送消息。channel 留空时优先 telegram，再回退到任一已配置通道。

    :param username: 用户标识符（系统自动注入，无需手动传递）
    :param text: 消息内容
    :param channel: 通道名，可选。可用值见 list_notification_channels。
    :param source_session: （自动注入）触发此通知的会话 ID
    :param parse_mode: "Markdown" / "HTML" / "" （仅 telegram 等支持的通道生效）
    :return: 发送结果描述
    """
    ch = _normalize_channel(channel, default="") if channel else _pick_default_channel(username) or ""
    if not ch:
        return "❌ 用户未配置任何推送通道，无法发送。先调用 set_notification_channel。"
    if ch not in _SENDERS:
        return f"❌ 不支持的通道: {ch}（当前支持：{', '.join(_AVAILABLE_CHANNELS)}）"

    channels = _load_channels(username)
    cfg = channels.get(ch)
    if not cfg or not cfg.get("target_id"):
        return f"❌ 用户未配置 {ch} 通道，请先调用 set_notification_channel。"

    body = text
    if source_session and source_session != ch:
        body = f"[来自会话: {source_session}]\n" + body

    sender = _SENDERS[ch]
    result = await sender(cfg["target_id"], body, parse_mode)
    icon = "✅" if result.ok else "❌"
    return f"{icon} [{ch}] {result.detail}"


@mcp.tool()
async def get_notification_status(username: str, channel: str = "") -> str:
    """
    查询用户的推送通道配置 + 后端状态。channel 留空时显示所有通道。

    :param username: 用户标识符（系统自动注入，无需手动传递）
    :param channel: 通道名，可选
    :return: 状态文本
    """
    channels = _load_channels(username)
    if channel:
        wanted = [_normalize_channel(channel, default="")]
    else:
        wanted = list(_AVAILABLE_CHANNELS)

    lines = ["📱 推送通道配置状态："]
    for ch in wanted:
        if ch not in _SENDERS:
            lines.append(f"  • {ch}: ❌ 未注册的通道")
            continue
        cfg = channels.get(ch) or {}
        target = cfg.get("target_id")
        display = cfg.get("display_name") or ""
        if target:
            label = f"target_id: {target}"
            if display:
                label += f"  (@{display})"
            lines.append(f"  • {ch}: ✅ {label}")
        else:
            lines.append(f"  • {ch}: ❌ 未配置")

        probe = _STATUS_PROBES.get(ch)
        if probe:
            ok, detail = probe()
            lines.append(f"      后端: {'✅' if ok else '❌'} {detail}")

        section = _load_channel_section(ch)
        in_whitelist = any(v.get("username") == username for v in section["entries"].values())
        lines.append(f"      白名单: {'✅ 已加入' if in_whitelist else '⚠️ 未加入'}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
