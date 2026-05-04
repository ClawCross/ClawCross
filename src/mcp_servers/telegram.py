import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

#!/usr/bin/env python3

# -*- coding: utf-8 -*-
"""
MCP Telegram 推送通知服务

功能说明：
- Agent 可通过此工具向用户的 Telegram 发送消息
- 用户的 chat_id 存储在 data/user_files/<username>/tg_chat_id.txt
- 设置 chat_id 时自动同步到全局白名单 data/telegram_whitelist.json
- 使用 .env 中的 TELEGRAM_BOT_TOKEN 发送
"""

import os
import json
import httpx
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

mcp = FastMCP("TelegramPush")

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(dotenv_path=os.path.join(root_dir, "config", ".env"))

def _resolve_telegram_token() -> str:
    """读 TELEGRAM_BOT_TOKEN；为空时尝试从 TELEGRAM_BOTS JSON 取第一个 bot 的 token。

    新版 chatbot 走 NoneBot 桥接，使用 TELEGRAM_BOTS=[{"token":"..."}] 作为标准配置。
    本 outbound MCP 优先沿用旧的 TELEGRAM_BOT_TOKEN，fallback 到新格式以保持兼容。
    """
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


TELEGRAM_BOT_TOKEN = _resolve_telegram_token()
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
USER_DATA_DIR = os.path.join(root_dir, "data", "user_files")
WHITELIST_FILE = os.path.join(root_dir, "data", "telegram_whitelist.json")

def _get_chat_id_path(username: str) -> str:
    """获取用户 chat_id 文件路径"""
    return os.path.join(USER_DATA_DIR, username, "tg_chat_id.txt")

def _read_chat_id(username: str) -> str | None:
    """读取用户的 Telegram chat_id"""
    chat_id_path = _get_chat_id_path(username)
    if os.path.exists(chat_id_path):
        with open(chat_id_path, "r", encoding="utf-8") as f:
            chat_id_val = f.read().strip()
            return chat_id_val if chat_id_val else None
    return None

# ── 白名单管理 ──

def _load_whitelist() -> dict:
    """加载白名单文件。

    标准 schema（新版 chatbot 桥接读它）：
        {"entries": {"<chat_id>": {"username": ..., "tg_username": ...}},
         "tg_name_map": {"<tg_username>": {"username": ...}}}

    旧 schema 仍可读：{"allowed": [{"username", "chat_id", "tg_username"}]}
    读到旧版时会就地转换为新 schema 返回。
    """
    if not os.path.exists(WHITELIST_FILE):
        return {"entries": {}, "tg_name_map": {}}
    try:
        with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"entries": {}, "tg_name_map": {}}

    if isinstance(data, dict) and "entries" in data:
        data.setdefault("tg_name_map", {})
        return data

    # 兼容旧 schema -> 转为新 schema
    new_data = {"entries": {}, "tg_name_map": {}}
    for entry in (data or {}).get("allowed", []) or []:
        username = entry.get("username")
        chat_id = entry.get("chat_id")
        tg_username = entry.get("tg_username") or ""
        if not username or not chat_id:
            continue
        new_data["entries"][str(chat_id)] = {"username": username, "tg_username": tg_username}
        if tg_username:
            new_data["tg_name_map"][tg_username] = {"username": username}
    return new_data


def _save_whitelist(whitelist: dict):
    """保存白名单到磁盘（统一以新 schema 写入）。"""
    os.makedirs(os.path.dirname(WHITELIST_FILE), exist_ok=True)
    with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
        json.dump(whitelist, f, ensure_ascii=False, indent=2)


def _sync_to_whitelist(username: str, chat_id: str, tg_username: str = ""):
    """添加 / 更新 username -> (chat_id, tg_username) 到白名单。

    新 schema 的索引主键是 chat_id；同 username 在不同 chat_id 切换时
    会清掉旧 chat_id 条目，避免悬挂数据。
    """
    whitelist = _load_whitelist()
    entries = whitelist.setdefault("entries", {})
    name_map = whitelist.setdefault("tg_name_map", {})

    # 先清理同 username 的旧 chat_id 条目
    stale_keys = [k for k, v in entries.items() if v.get("username") == username and k != str(chat_id)]
    for k in stale_keys:
        entries.pop(k, None)

    entries[str(chat_id)] = {"username": username, "tg_username": tg_username}
    if tg_username:
        name_map[tg_username] = {"username": username}

    _save_whitelist(whitelist)


def _remove_from_whitelist(username: str):
    """从白名单中移除某 username 的所有条目（含 entries / tg_name_map 中的引用）。"""
    whitelist = _load_whitelist()
    entries = whitelist.setdefault("entries", {})
    name_map = whitelist.setdefault("tg_name_map", {})

    entries_to_drop = [k for k, v in entries.items() if v.get("username") == username]
    for k in entries_to_drop:
        entries.pop(k, None)

    names_to_drop = [k for k, v in name_map.items() if v.get("username") == username]
    for k in names_to_drop:
        name_map.pop(k, None)

    _save_whitelist(whitelist)

@mcp.tool()
async def set_telegram_chat_id(username: str, chat_id: str, tg_username: str = "") -> str:
    """
    保存用户的 Telegram chat_id 用于推送通知。
    同时会自动将用户加入 Telegram bot 白名单。
    用户可以通过向 bot 发送 /start 或使用 @userinfobot 获取自己的 chat_id。

    :param username: 用户标识符（系统自动注入，无需手动传递）
    :param chat_id: Telegram chat ID（数字字符串，如 "123456789"）
    :param tg_username: 可选的 Telegram @用户名（不要加 @，如 "my_username"）
    :return: 操作结果描述
    """
    if not chat_id or not chat_id.strip():
        return "❌ chat_id 不能为空。"
    chat_id = chat_id.strip()

    user_dir = os.path.join(USER_DATA_DIR, username)
    os.makedirs(user_dir, exist_ok=True)

    with open(_get_chat_id_path(username), "w", encoding="utf-8") as f:
        f.write(chat_id)

    # 自动同步到全局白名单
    _sync_to_whitelist(username, chat_id, tg_username.strip().lstrip("@") if tg_username else "")

    return (
        f"✅ Telegram chat_id 已保存：{chat_id}，后续可通过 Telegram 接收通知。\n"
        f"✅ 已自动加入 Telegram Bot 白名单。"
    )

@mcp.tool()
async def remove_telegram_config(username: str) -> str:
    """
    移除用户的 Telegram 配置并撤销白名单访问权限。

    :param username: 用户标识符（系统自动注入，无需手动传递）
    :return: 操作结果描述
    """
    chat_id_path = _get_chat_id_path(username)
    removed_chat_id = False
    if os.path.exists(chat_id_path):
        os.remove(chat_id_path)
        removed_chat_id = True

    _remove_from_whitelist(username)

    if removed_chat_id:
        return "✅ 已移除 Telegram chat_id 并从白名单中删除。"
    else:
        return "ℹ️ 该用户未配置 Telegram chat_id，已确保从白名单中移除。"

@mcp.tool()
async def send_telegram_message(
    username: str, text: str, source_session: str = "", parse_mode: str = "Markdown"
) -> str:
    """
    通过 Telegram Bot 向用户发送文本消息。
    用于主动通知用户任务结果、提醒或重要更新。
    消息会自动标注来源会话。

    :param username: 用户标识符（系统自动注入，无需手动传递）
    :param text: 要发送的消息内容，支持 Markdown 格式
    :param source_session: （自动注入）触发此通知的会话 ID，请勿手动设置
    :param parse_mode: 文本格式模式："Markdown"、"HTML" 或 ""（纯文本），默认："Markdown"
    :return: 发送结果描述
    """
    if not TELEGRAM_BOT_TOKEN:
        return "❌ 未配置 TELEGRAM_BOT_TOKEN，无法发送 Telegram 消息。请在 .env 中设置。"

    chat_id = _read_chat_id(username)
    if not chat_id:
        return (
            "❌ 尚未配置 Telegram chat_id，无法发送消息。\n"
            "请让用户提供 Telegram chat_id（可通过 @userinfobot 获取）。"
        )

    # 自动在消息前标注来源 session
    if source_session and source_session != "tg":
        session_tag = f"[来自会话: {source_session}]\n"
        text = session_tag + text

    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{TELEGRAM_API}/sendMessage",
                json=payload,
                timeout=15.0,
            )
            response_data = resp.json()
            if response_data.get("ok"):
                return f"✅ Telegram 消息已发送！"
            else:
                error_desc = response_data.get("description", "未知错误")
                # Markdown 解析失败时自动降级为纯文本重试
                if "parse" in error_desc.lower() and parse_mode:
                    payload["parse_mode"] = ""
                    retry_resp = await client.post(
                        f"{TELEGRAM_API}/sendMessage",
                        json=payload,
                        timeout=15.0,
                    )
                    retry_data = retry_resp.json()
                    if retry_data.get("ok"):
                        return f"✅ Telegram 消息已发送（降级为纯文本格式）。"
                return f"❌ Telegram 发送失败: {error_desc}"
        except httpx.ConnectError:
            return "❌ 无法连接 Telegram API，请检查网络。"
        except Exception as e:
            return f"⚠️ Telegram 发送异常: {str(e)}"

@mcp.tool()
async def get_telegram_status(username: str) -> str:
    """
    查询用户的 Telegram 推送通知配置状态。

    :param username: 用户标识符（系统自动注入，无需手动传递）
    :return: 配置状态的详细描述
    """
    chat_id = _read_chat_id(username)
    status_lines = ["📱 Telegram 推送配置状态："]

    if chat_id:
        status_lines.append(f"  ✅ Chat ID: {chat_id}")
    else:
        status_lines.append("  ❌ Chat ID: 未配置")

    if TELEGRAM_BOT_TOKEN:
        masked_token = TELEGRAM_BOT_TOKEN[:8] + "****" if len(TELEGRAM_BOT_TOKEN) > 8 else "****"
        status_lines.append(f"  ✅ Bot Token: {masked_token}")
    else:
        status_lines.append("  ❌ Bot Token: 未配置（.env 中缺少 TELEGRAM_BOT_TOKEN）")

    if chat_id and TELEGRAM_BOT_TOKEN:
        status_lines.append("  ✅ 可正常发送 Telegram 通知")
    else:
        status_lines.append("  ⚠️ 配置不完整，无法发送通知")

    # 白名单状态
    whitelist = _load_whitelist()
    in_whitelist = any(entry.get("username") == username for entry in whitelist.get("allowed", []))
    if in_whitelist:
        status_lines.append("  ✅ 已在 Telegram Bot 白名单中")
    else:
        status_lines.append("  ⚠️ 未在 Telegram Bot 白名单中（设置 chat_id 后自动加入）")

    return "\n".join(status_lines)

if __name__ == "__main__":
    mcp.run()
