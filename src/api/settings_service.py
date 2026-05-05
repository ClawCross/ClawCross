"""
设置管理服务模块

提供系统配置的管理功能：
- 读取/更新白名单配置
- 读取/更新完整配置（含敏感信息自动脱敏）
- 重启服务信号
"""

import os
import json
from typing import Callable

from fastapi import HTTPException

from utils.env_settings import (
    SETTINGS_WHITELIST,
    filter_updates_skip_mask,
    filter_whitelisted_updates,
    mask_all_sensitive,
    mask_sensitive,
    read_env_all,
    read_env_settings,
    write_env_settings,
)
from api.settings_models import ChatbotWhitelistUpdateRequest, SettingsUpdateRequest


CHATBOT_WHITELIST_CHANNELS = ("telegram", "qq", "weclaw", "webhook")
CHATBOT_CHANNEL_CATALOG = [
    {"id": "telegram", "label": "Telegram", "kind": "nonebot", "adapter": "telegram", "env_key": "TELEGRAM_BOTS"},
    {"id": "qq", "label": "QQ", "kind": "nonebot", "adapter": "qq", "env_key": "QQ_BOTS"},
    {"id": "onebotv11", "label": "OneBot V11", "kind": "nonebot", "adapter": "onebot.v11", "env_key": "ONEBOTV11_BOTS"},
    {"id": "onebotv12", "label": "OneBot V12", "kind": "nonebot", "adapter": "onebot.v12", "env_key": "ONEBOTV12_BOTS"},
    {"id": "discord", "label": "Discord", "kind": "nonebot", "adapter": "discord", "env_key": "DISCORD_BOTS"},
    {"id": "dingtalk", "label": "DingTalk", "kind": "nonebot", "adapter": "dingtalk", "env_key": "DINGTALK_BOTS"},
    {"id": "feishu", "label": "Feishu", "kind": "nonebot", "adapter": "feishu", "env_key": "FEISHU_BOTS"},
    {"id": "kaiheila", "label": "Kaiheila / KOOK", "kind": "nonebot", "adapter": "kaiheila", "env_key": "KAIHEILA_BOTS"},
    {"id": "mail", "label": "Mail", "kind": "nonebot", "adapter": "mail", "env_key": "MAIL_BOTS"},
    {"id": "minecraft", "label": "Minecraft", "kind": "nonebot", "adapter": "minecraft", "env_key": "MINECRAFT_BOTS"},
    {"id": "github", "label": "GitHub", "kind": "nonebot", "adapter": "github", "env_key": "GITHUB_BOTS"},
    {"id": "villa", "label": "Villa", "kind": "nonebot", "adapter": "villa", "env_key": "VILLA_BOTS"},
    {"id": "yunhu", "label": "Yunhu", "kind": "nonebot", "adapter": "yunhu", "env_key": "YUNHU_BOTS"},
    {"id": "heybox", "label": "Heybox", "kind": "nonebot", "adapter": "heybox", "env_key": "HEYBOX_BOTS"},
    {"id": "console", "label": "Console", "kind": "nonebot", "adapter": "console", "env_key": "CONSOLE_BOTS"},
    {"id": "weclaw", "label": "WeClaw / WeChat", "kind": "weclaw", "adapter": "weclaw", "env_key": ""},
    {"id": "webhook", "label": "Custom Webhook", "kind": "webhook", "adapter": "webhook", "env_key": ""},
]


class SettingsService:
    def __init__(
        self,
        *,
        env_path: str,
        verify_auth_or_token: Callable[[str, str, str | None], None],
    ):
        self.env_path = env_path
        self.verify_auth_or_token = verify_auth_or_token
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.restart_flag = os.path.join(self.project_root, ".restart_flag")

    def _chatbot_whitelist_path(self) -> str:
        settings = read_env_settings(self.env_path, ["WHITELIST_FILE"])
        configured = (settings.get("WHITELIST_FILE") or "").strip()
        path = configured or os.path.join("data", "whitelist.json")
        if not os.path.isabs(path):
            path = os.path.join(self.project_root, path)
        return os.path.abspath(os.path.expanduser(path))

    def _normalize_chatbot_whitelist(self, raw: dict | None) -> dict:
        normalized = {}
        raw = raw if isinstance(raw, dict) else {}
        for channel in CHATBOT_WHITELIST_CHANNELS:
            section = raw.get(channel, {})
            if not isinstance(section, dict):
                section = {}
            entries = section.get("entries", {})
            name_map = section.get("name_map", {})
            if not isinstance(entries, dict):
                entries = {}
            if not isinstance(name_map, dict):
                name_map = {}
            normalized[channel] = {
                "entries": entries,
                "name_map": name_map,
            }
        for channel, section in raw.items():
            if channel in normalized or not isinstance(section, dict):
                continue
            entries = section.get("entries", {})
            name_map = section.get("name_map", {})
            normalized[channel] = {
                "entries": entries if isinstance(entries, dict) else {},
                "name_map": name_map if isinstance(name_map, dict) else {},
            }
        return normalized

    def _read_chatbot_whitelist(self) -> dict:
        path = self._chatbot_whitelist_path()
        if not os.path.exists(path):
            return self._normalize_chatbot_whitelist({})
        try:
            with open(path, "r", encoding="utf-8") as f:
                return self._normalize_chatbot_whitelist(json.load(f))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"读取 chatbot 白名单失败: {e}")

    def _write_chatbot_whitelist(self, whitelist: dict) -> None:
        path = self._chatbot_whitelist_path()
        normalized = self._normalize_chatbot_whitelist(whitelist)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(normalized, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp_path, path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"写入 chatbot 白名单失败: {e}")

    async def get_settings(self, user_id: str, password: str, x_internal_token: str | None):
        self.verify_auth_or_token(user_id, password, x_internal_token)
        raw = read_env_settings(self.env_path, SETTINGS_WHITELIST)
        masked = mask_sensitive(raw)
        return {"status": "success", "settings": masked}

    async def update_settings(self, req: SettingsUpdateRequest, x_internal_token: str | None):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        filtered = filter_whitelisted_updates(req.settings, SETTINGS_WHITELIST)

        if filtered:
            write_env_settings(self.env_path, filtered)
            return {
                "status": "success",
                "updated": list(filtered.keys()),
            }

        return {"status": "success", "updated": []}

    async def get_settings_full(self, user_id: str, password: str, x_internal_token: str | None):
        self.verify_auth_or_token(user_id, password, x_internal_token)
        raw = read_env_all(self.env_path)
        masked = mask_all_sensitive(raw)
        return {"status": "success", "settings": masked}

    async def update_settings_full(self, req: SettingsUpdateRequest, x_internal_token: str | None):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        updates = filter_updates_skip_mask(req.settings)

        if updates:
            write_env_settings(self.env_path, updates)
            return {
                "status": "success",
                "updated": list(updates.keys()),
            }

        return {"status": "success", "updated": []}

    async def restart_services(self, req: SettingsUpdateRequest, x_internal_token: str | None):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        try:
            with open(self.restart_flag, "w") as f:
                f.write("restart")
            return {"status": "success", "message": "重启信号已发送，服务将在数秒内重启"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"写入重启信号失败: {e}")

    async def get_chatbot_whitelist(self, user_id: str, password: str, x_internal_token: str | None):
        self.verify_auth_or_token(user_id, password, x_internal_token)
        path = self._chatbot_whitelist_path()
        return {
            "status": "success",
            "path": os.path.relpath(path, self.project_root) if path.startswith(self.project_root) else path,
            "channels": list(CHATBOT_WHITELIST_CHANNELS),
            "available_channels": CHATBOT_CHANNEL_CATALOG,
            "whitelist": self._read_chatbot_whitelist(),
        }

    async def update_chatbot_whitelist(self, req: ChatbotWhitelistUpdateRequest, x_internal_token: str | None):
        self.verify_auth_or_token(req.user_id, req.password, x_internal_token)
        self._write_chatbot_whitelist(req.whitelist)
        return {
            "status": "success",
            "channels": list(CHATBOT_WHITELIST_CHANNELS),
            "available_channels": CHATBOT_CHANNEL_CATALOG,
            "whitelist": self._read_chatbot_whitelist(),
        }
