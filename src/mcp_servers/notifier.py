#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP 多通道推送通知服务

Agent 可通过此工具向用户的任意已配置社交通道推送消息。通道存储在
data/user_files/<username>/notification_channels.json，形如：
    {
      "_default": "telegram",
      "telegram": {"target_id": "12345678", "display_name": "alice"},
      "webhook":  {"target_id": "https://example.com/hook"},
      "console":  {"target_id": "notifications.log"}
    }

当前内置后端：
  - telegram: 走 .env 的 TELEGRAM_BOT_TOKEN / TELEGRAM_BOTS
  - webhook:  POST JSON 到用户配置的 URL（无第三方依赖）
  - console:  追加到 user_files/<username>/<target_id> 文件（调试/审计用）
要新增通道，只需在 _SENDERS / _STATUS_PROBES 注册函数。
"""

import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

import datetime
import json
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlparse

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


async def _send_webhook(target_id: str, text: str, parse_mode: str) -> SendResult:
    payload = {"text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(target_id, json=payload, timeout=15.0)
        if 200 <= resp.status_code < 300:
            return SendResult(True, f"已发送 (HTTP {resp.status_code})")
        return SendResult(False, f"Webhook 拒绝: HTTP {resp.status_code} {resp.text[:120]}")
    except httpx.ConnectError as exc:
        return SendResult(False, f"无法连接 webhook: {exc}")
    except Exception as exc:
        return SendResult(False, f"Webhook 发送异常: {exc}")


def _webhook_status() -> tuple[bool, str]:
    return True, "无后端依赖，POST 到用户配置的 URL"


def _resolve_console_path(target_id: str) -> str:
    name = (target_id or "notifications.log").strip() or "notifications.log"
    return os.path.join(USER_DATA_DIR, "_console", name)


async def _send_console(target_id: str, text: str, parse_mode: str) -> SendResult:
    path = _resolve_console_path(target_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] {text}\n"
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        return SendResult(True, f"已写入 {path}")
    except OSError as exc:
        return SendResult(False, f"console 写入失败: {exc}")


def _console_status() -> tuple[bool, str]:
    return True, f"日志根目录: {os.path.join(USER_DATA_DIR, '_console')}"


# ── WeClaw (WeChat via weclaw binary) ──────────────────────────────

import asyncio  # noqa: E402
import shutil  # noqa: E402

_WECLAW_BIN_ENV = "WECLAW_BIN"
_WECLAW_ACCOUNTS_DIR = os.path.expanduser("~/.weclaw/accounts")


def _resolve_weclaw_bin() -> str | None:
    name = (os.getenv(_WECLAW_BIN_ENV, "") or "weclaw").strip() or "weclaw"
    path = shutil.which(name)
    if path:
        return path
    if os.path.isfile(name) and os.access(name, os.X_OK):
        return name
    return None


async def _send_weclaw(target_id: str, text: str, parse_mode: str) -> SendResult:
    del parse_mode  # weclaw 不支持 markdown / html
    binary = _resolve_weclaw_bin()
    if not binary:
        return SendResult(False, "未找到 weclaw 二进制（设置 WECLAW_BIN 或 PATH）")
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "send", "--to", target_id, "--text", text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            proc.kill()
            return SendResult(False, "weclaw send 超时 (30s)")
        body = (out or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode == 0:
            return SendResult(True, f"已发送 (weclaw rc=0) {body[:120]}".rstrip())
        return SendResult(False, f"weclaw send 失败 rc={proc.returncode}: {body[:200]}")
    except FileNotFoundError:
        return SendResult(False, f"weclaw 二进制不可执行: {binary}")
    except Exception as exc:
        return SendResult(False, f"weclaw 发送异常: {exc}")


def _weclaw_status() -> tuple[bool, str]:
    binary = _resolve_weclaw_bin()
    if not binary:
        return False, "未找到 weclaw 二进制（设置 WECLAW_BIN 或 PATH）"
    if not os.path.isdir(_WECLAW_ACCOUNTS_DIR):
        return False, f"无 weclaw 账号目录 {_WECLAW_ACCOUNTS_DIR}（请 weclaw login）"
    accounts = [
        f for f in os.listdir(_WECLAW_ACCOUNTS_DIR)
        if f.endswith(".json") and not f.endswith(".sync.json")
    ]
    if not accounts:
        return False, "weclaw 账号目录为空（需 weclaw login 扫码）"
    return True, f"二进制: {binary}; 已登录账号: {len(accounts)}"


Sender = Callable[[str, str, str], Awaitable[SendResult]]
StatusProbe = Callable[[], tuple[bool, str]]

_SENDERS: dict[str, Sender] = {
    "telegram": _send_telegram,
    "webhook": _send_webhook,
    "console": _send_console,
    "weclaw": _send_weclaw,
}

_STATUS_PROBES: dict[str, StatusProbe] = {
    "telegram": _telegram_status,
    "webhook": _webhook_status,
    "console": _console_status,
    "weclaw": _weclaw_status,
}

_AVAILABLE_CHANNELS = tuple(sorted(_SENDERS.keys()))


def _normalize_channel(channel: str, *, default: str = "telegram") -> str:
    value = (channel or "").strip().lower()
    if not value:
        value = (default or "").strip().lower()
    aliases = {
        "tg": "telegram", "log": "console", "file": "console", "http": "webhook",
        "wechat": "weclaw", "wx": "weclaw", "微信": "weclaw",
    }
    return aliases.get(value, value)


_TELEGRAM_TARGET_RE = re.compile(r"^(-?\d+|@[A-Za-z][A-Za-z0-9_]{3,})$")


def _validate_target(channel: str, target_id: str) -> str:
    """Return '' if OK, otherwise human-readable error. Pure local checks, no network."""
    t = (target_id or "").strip()
    if not t:
        return "target_id 不能为空"
    if channel == "telegram":
        if not _TELEGRAM_TARGET_RE.match(t):
            return "telegram target_id 必须是数字 chat_id（可负）或 @channelname"
    elif channel == "webhook":
        parsed = urlparse(t)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return "webhook target_id 必须是 http(s):// 开头的 URL"
    elif channel == "console":
        if any(ch in t for ch in ("..", "\x00")) or t.startswith("/") or "\\" in t:
            return "console target_id 必须是 user 目录下的相对文件名（无 .. 无绝对路径）"
    elif channel == "weclaw":
        # ilink user_id：宽松校验，只禁 shell 危险字符（subprocess 已经走 argv 不经 shell，这层是保险）
        if any(c in t for c in ("\x00", "\n", "\r")):
            return "weclaw target_id 不能包含控制字符"
        if len(t) > 256:
            return "weclaw target_id 太长 (>256)"
    return ""


def _pick_default_channel(username: str) -> str | None:
    channels = _load_channels(username)
    explicit = channels.get("_default")
    if isinstance(explicit, str) and explicit in _SENDERS and explicit in channels:
        return explicit
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
    target_id = (target_id or "").strip()
    err = _validate_target(ch, target_id)
    if err:
        return f"❌ {err}"

    display = display_name.strip().lstrip("@") if display_name else ""

    _set_channel(username, ch, target_id, display)
    _sync_to_whitelist(ch, username, target_id, display)

    return (
        f"✅ 已保存 {ch} 推送目标：{target_id}\n"
        f"✅ 已加入 {ch} 白名单。"
    )


_META_KEYS = ("_default",)


def _user_channel_names(channels: dict) -> list[str]:
    return [k for k in channels.keys() if k not in _META_KEYS]


@mcp.tool()
async def set_default_notification_channel(username: str, channel: str) -> str:
    """
    设置该用户的默认推送通道（send_notification 不指定 channel 时使用）。

    :param username: 用户标识符（系统自动注入）
    :param channel: 必须是已配置且受支持的通道
    """
    ch = _normalize_channel(channel, default="")
    if not ch:
        return "❌ channel 不能为空。"
    if ch not in _SENDERS:
        return f"❌ 不支持的通道: {ch}（当前支持：{', '.join(_AVAILABLE_CHANNELS)}）"
    channels = _load_channels(username)
    if ch not in channels:
        return f"❌ 用户尚未配置 {ch} 通道，请先调用 set_notification_channel。"
    channels["_default"] = ch
    _save_channels(username, channels)
    return f"✅ 默认推送通道已设为 {ch}。"


@mcp.tool()
async def remove_notification_channel(username: str, channel: str = "") -> str:
    """
    移除用户在指定通道的推送配置；channel 为空时清空所有通道。

    :param username: 用户标识符（系统自动注入，无需手动传递）
    :param channel: 通道名，可选。空 = 全部移除。
    :return: 操作结果描述
    """
    channels = _load_channels(username)
    user_channels = _user_channel_names(channels)
    if not user_channels:
        return "ℹ️ 该用户未配置任何推送通道。"

    targets: list[str]
    if channel and channel.strip():
        ch = _normalize_channel(channel, default="")
        if ch in _META_KEYS or ch not in channels:
            return f"ℹ️ 该用户未配置 {ch} 通道，无需移除。"
        targets = [ch]
    else:
        targets = list(user_channels)

    removed: list[str] = []
    for ch in targets:
        if _delete_channel(username, ch):
            _remove_from_whitelist(ch, username)
            removed.append(ch)

    # 默认通道被删了的话清掉指针
    channels = _load_channels(username)
    if channels.get("_default") in removed:
        channels.pop("_default", None)
        _save_channels(username, channels)

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
    if channel and channel.strip():
        wanted = [_normalize_channel(channel, default="")]
    else:
        wanted = list(_AVAILABLE_CHANNELS)

    default_ch = channels.get("_default") or _pick_default_channel(username)
    lines = ["📱 推送通道配置状态："]
    if default_ch:
        lines.append(f"  (默认通道: {default_ch})")
    for ch in wanted:
        if not ch:
            lines.append("  • <空>: ❌ 通道名不能为空")
            continue
        if ch not in _SENDERS:
            lines.append(f"  • {ch}: ❌ 未注册的通道（已支持: {', '.join(_AVAILABLE_CHANNELS)}）")
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
