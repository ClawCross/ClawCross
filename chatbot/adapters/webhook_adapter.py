"""
通用 Webhook 适配器

支持任何通过 HTTP POST 发送消息的平台。
只需配置 Webhook URL，即可接收消息并回复。

功能：
- 接收任意 HTTP POST Webhook 的消息
- 权限验证
- 多模态内容构建
- 调用 AI 服务
- 回复到指定 URL
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

import httpx
from dotenv import load_dotenv

_chatbot_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_project_root = os.path.dirname(_chatbot_dir)
load_dotenv(dotenv_path=os.path.join(_project_root, "config", ".env"))

from .base import ChannelAdapter

logger = logging.getLogger("chatbot.webhook")


class WebhookAdapter(ChannelAdapter):
    """通用 Webhook 适配器"""

    channel = "webhook"

    def __init__(self, platform_name: str = "webhook"):
        super().__init__()
        self._platform_name = platform_name
        self._webhook_url = os.getenv(f"{platform_name.upper()}_WEBHOOK_URL")
        self._webhook_secret = os.getenv(f"{platform_name.upper()}_WEBHOOK_SECRET")
        self._reply_url = os.getenv(f"{platform_name.upper()}_REPLY_URL", "")
        self._default_allow = os.getenv(f"{platform_name.upper()}_DEFAULT_ALLOW", "false").lower() == "true"

    def _verify_signature(self, payload: str, signature: str) -> bool:
        """验证签名"""
        if not self._webhook_secret or not signature:
            return True

        try:
            expected = hmac.new(
                self._webhook_secret.encode(),
                payload.encode(),
                hashlib.sha256
            ).hexdigest()
            return hmac.compare_digest(expected, signature)
        except Exception:
            return False

    async def verify_permission(self, event: dict) -> tuple[bool, str | None]:
        """验证用户权限"""
        user_id = str(event.get("user_id", "") or event.get("from_user_id", "") or "")
        username = str(event.get("username", "") or event.get("user_name", "") or user_id)

        entry = self._find_whitelist_entry(user_id, username, channel=self._platform_name)
        if entry:
            return True, entry.get("username")

        # 检查是否默认允许
        if self._default_allow:
            return True, username or "anonymous"

        return False, None

    async def build_content(self, event: dict) -> list[dict]:
        """构建 OpenAI 多模态 content 列表"""
        content_list = []

        # 尝试从多个字段提取文本
        text = (
            event.get("text") or
            event.get("content") or
            event.get("message") or
            event.get("msg") or
            ""
        )

        if isinstance(text, str) and text:
            content_list.append({"type": "text", "text": text})
        elif isinstance(text, dict):
            content = text.get("content", "") or text.get("text", "")
            if content:
                content_list.append({"type": "text", "text": content})

        # 处理附件
        attachments = event.get("attachments", []) or []
        for att in attachments:
            url = att.get("url", "") or att.get("file_url", "")
            if url:
                content_list.append({"type": "text", "text": f"[附件: {att.get('name', 'file')}]"})

        # 处理图片
        images = event.get("images", []) or event.get("photos", []) or []
        for img in images:
            if isinstance(img, str):
                content_list.append({"type": "text", "text": f"[图片: {img}]"})
            elif isinstance(img, dict):
                url = img.get("url", "") or img.get("src", "")
                if url:
                    content_list.append({"type": "text", "text": f"[图片: {url}]"})

        if not content_list:
            content_list.append({"type": "text", "text": json.dumps(event, ensure_ascii=False)})

        return content_list

    async def handle_message(self, event: dict) -> str | None:
        """处理消息"""
        allowed, username = await self.verify_permission(event)
        if not allowed:
            return None

        content_list = await self.build_content(event)

        # /cross 命令：直接返回 magic link，跳过 AI
        text = self.extract_text(content_list)
        if self.is_cross_command(text):
            link = await self.generate_magic_link(username)
            reply = self.format_cross_reply(link)
            await self._send_reply(event, reply)
            return reply

        api_key = self.build_api_key(username)
        result = await self.call_ai(content_list, api_key)

        reply = result.content if result.ok else f"发生错误: {result.error}"

        # 回复到 webhook
        await self._send_reply(event, reply)

        return reply

    async def _send_reply(self, original_event: dict, content: str):
        """发送回复"""
        if not self._reply_url:
            return

        try:
            # 构造回复消息
            reply_data = {"content": content}

            # 尝试保留消息 ID
            msg_id = original_event.get("message_id") or original_event.get("msg_id") or original_event.get("id", "")
            if msg_id:
                reply_data["reference"] = {"message_id": msg_id}

            async with httpx.AsyncClient() as client:
                await client.post(self._reply_url, json=reply_data)
        except Exception as e:
            logger.warning(f"回复失败: {e}")

    async def handle_webhook(self, request_body: str, signature: str = "") -> str | None:
        """处理 Webhook 请求"""
        if not self._verify_signature(request_body, signature):
            logger.warning("签名验证失败")
            return "signature verification failed"

        try:
            event = json.loads(request_body)
        except json.JSONDecodeError:
            return "invalid json"

        return await self.handle_message(event)


def create_webhook_adapter(platform_name: str) -> WebhookAdapter:
    """创建指定平台的 Webhook 适配器"""
    adapter = WebhookAdapter(platform_name)
    adapter.channel = platform_name
    return adapter
