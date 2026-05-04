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
        self._whitelist_file = None  # 子类设置

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
        """加载白名单"""
        if not self._whitelist_file or not os.path.exists(self._whitelist_file):
            return {"entries": {}, "tg_name_map": {}}
        try:
            with open(self._whitelist_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载白名单失败: {e}")
            return {"entries": {}, "tg_name_map": {}}

    def _find_whitelist_entry(self, user_id: str, username: str | None = None) -> dict | None:
        """查找白名单条目"""
        whitelist = self._load_whitelist()
        entries = whitelist.get("entries", {})
        name_map = whitelist.get("tg_name_map", {})

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