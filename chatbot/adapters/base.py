"""
Chatbot base handler - shared logic for all channel adapters.

统一处理：
1. 权限验证（各渠道白名单）
2. 消息内容构建（多模态）
3. AI 调用（走 Agent API）
4. 统一回复格式

注意：channel 是社交媒体渠道（如 Telegram、QQ），与 agent connector 的 platform（openclaw、claude）是不同概念。
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("chatbot.base")

CROSS_COMMAND = "/cross"


@dataclass
class ChatMessage:
    """统一的消息格式"""
    channel: str                # "telegram", "qq", "discord" 等社交渠道
    user_id: str                # 渠道用户 ID
    username: str | None         # 用户名
    text: str                   # 文本内容
    content_list: list[dict]    # OpenAI 多模态 content 列表
    session_key: str            # 用于 Agent session


@dataclass
class AIResponse:
    """AI 回复"""
    ok: bool
    content: str | None = None
    error: str | None = None


class ChannelAdapter(ABC):
    """社交渠道适配器基类"""

    channel: str = "unknown"  # 社交渠道名称（telegram, qq, discord 等）

    def __init__(self):
        self._agent_url = os.getenv("AI_API_URL", f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}/v1/chat/completions")
        self._internal_token = os.getenv("INTERNAL_TOKEN", "")
        self._llm_model = os.getenv("LLM_MODEL", "")
        _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._whitelist_file = os.getenv("WHITELIST_FILE") or os.path.join(_project_root, "data", "whitelist.json")

    @abstractmethod
    async def handle_message(self, message: ChatMessage) -> str:
        """处理消息，返回回复文本"""
        pass

    @abstractmethod
    async def verify_permission(self, raw_message: Any) -> tuple[bool, str | None]:
        """验证用户权限，返回 (允许, username)"""
        pass

    @abstractmethod
    async def build_content(self, raw_message: Any) -> list[dict]:
        """构建 OpenAI 多模态 content 列表"""
        pass

    def _load_whitelist(self) -> dict:
        """加载中心化白名单文件，返回完整字典 {channel: {entries, name_map}}"""
        if not self._whitelist_file or not os.path.exists(self._whitelist_file):
            return {}
        try:
            with open(self._whitelist_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载白名单失败: {e}")
            return {}

    def _find_whitelist_entry(
        self,
        user_id: str,
        username: str | None = None,
        channel: str | None = None,
    ) -> dict | None:
        """在指定 channel 段下查找白名单条目。channel 默认 self.channel。"""
        whitelist = self._load_whitelist()
        section = whitelist.get(channel or self.channel, {})
        entries = section.get("entries", {})
        name_map = section.get("name_map", {})

        if user_id in entries:
            return entries[user_id]
        if username and username in name_map:
            return name_map[username]
        return None

    async def call_ai(self, content_list: list[dict], api_key: str, model: str | None = None) -> AIResponse:
        """调用 AI 服务（走 Agent HTTP API）"""
        if not self._internal_token:
            return AIResponse(ok=False, error="INTERNAL_TOKEN 未配置")

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    self._agent_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": model or self._llm_model,
                        "messages": [{"role": "user", "content": content_list}]
                    }
                )

            if response.status_code != 200:
                return AIResponse(ok=False, error=f"AI 接口报错 ({response.status_code}): {response.text[:200]}")

            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return AIResponse(ok=True, content=content)

        except httpx.ConnectError:
            return AIResponse(ok=False, error="无法连接 AI 服务")
        except Exception as e:
            return AIResponse(ok=False, error=str(e))

    def build_session_key(self, username: str) -> str:
        """构建 Agent session key"""
        return f"{self._internal_token}:{username}:{self.channel.upper()}"

    def build_api_key(self, username: str) -> str:
        """构建 API 认证 key"""
        return f"{self._internal_token}:{username}:{self.channel.upper()}"

    # ── /cross 命令：所有 adapter 共用 ─────────────────────────────────

    @staticmethod
    def is_cross_command(text: str) -> bool:
        return bool(text) and text.strip().lower().startswith(CROSS_COMMAND)

    @staticmethod
    def extract_text(content_list: list[dict]) -> str:
        """从 OpenAI 多模态 content 列表里取第一个 text 段。"""
        for part in content_list or []:
            if isinstance(part, dict) and part.get("type") == "text":
                return part.get("text", "") or ""
        return ""

    @staticmethod
    def format_cross_reply(link: str | None) -> str:
        if not link:
            return "❌ 生成登录链接失败，请检查前端服务（PORT_FRONTEND）是否就绪"
        return f"🔗 ClawCross 前端登录链接（24 小时有效）：\n{link}"

    async def generate_magic_link(self, user_id: str) -> str | None:
        port = os.getenv("PORT_FRONTEND", "51209")
        url = f"http://127.0.0.1:{port}/generate_login_link"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json={"user_id": user_id})
            if resp.status_code != 200:
                logger.warning(f"生成 magic link 失败: {resp.status_code} {resp.text[:200]}")
                return None
            return resp.json().get("link")
        except Exception as e:
            logger.warning(f"调用 generate_login_link 异常: {e}")
            return None