"""
Chatbot 渠道适配器包

架构：
- NoneBotBridgeAdapter: 一份代码桥接 NoneBot 全部 30+ 平台
  （Telegram, Discord, QQ/OneBot V11/V12, Mirai, Feishu, Kaiheila, DingTalk,
   Mail, Minecraft, Console, GitHub, RocketChat, Villa, Yunhu, Heybox, ...）
- WebhookAdapter: 通用 HTTP 入站，应付 NoneBot 没有 / 自定义协议的场景

使用：
    from chatbot.adapters import NoneBotBridgeAdapter, WebhookAdapter
    from chatbot.adapters import AdapterManager, create_manager_from_env

    manager = create_manager_from_env()
    await manager.run_all()
"""

from __future__ import annotations

import asyncio
import logging

from .base import ChannelAdapter

logger = logging.getLogger("chatbot.adapters")

__all__ = [
    "ChannelAdapter",
    "WebhookAdapter",
    "NoneBotBridgeAdapter",
    "WeClawAdapter",
    "AdapterManager",
    "create_webhook_adapter",
    "create_manager_from_env",
]

from .webhook_adapter import WebhookAdapter, create_webhook_adapter
from .nonebot_bridge import NoneBotBridgeAdapter
from .weclaw_adapter import WeClawAdapter


class AdapterManager:
    """多渠道适配器管理器"""

    def __init__(self):
        self._adapters: list[tuple[str, ChannelAdapter]] = []
        self._tasks: list[asyncio.Task] = []

    def register_adapter(self, adapter: ChannelAdapter, enabled: bool = True) -> None:
        status = "enabled" if enabled else "disabled"
        self._adapters.append((status, adapter))
        logger.info(f"注册渠道: {adapter.channel} ({status})")

    def register_webhook(self, platform: str = "webhook", enabled: bool = True) -> WebhookAdapter:
        adapter = create_webhook_adapter(platform)
        self.register_adapter(adapter, enabled)
        return adapter

    def register_nonebot_bridge(self, enabled: bool = True) -> NoneBotBridgeAdapter:
        adapter = NoneBotBridgeAdapter()
        self.register_adapter(adapter, enabled)
        return adapter

    def register_weclaw(self, enabled: bool = True) -> WeClawAdapter:
        adapter = WeClawAdapter()
        self.register_adapter(adapter, enabled)
        return adapter

    async def run_all(self):
        coros = []
        for status, adapter in self._adapters:
            if status == "disabled":
                continue
            if adapter.channel == "webhook":
                logger.info(f"{adapter.channel}: 请使用 Webhook 模式（被动接受 HTTP POST）")
                continue
            coros.append(self._safe_run(adapter))

        if coros:
            await asyncio.gather(*coros)
        else:
            await asyncio.sleep(0)

    async def _safe_run(self, adapter: ChannelAdapter):
        try:
            await adapter.run()
        except Exception as e:
            logger.error(f"适配器 {adapter.channel} 异常退出: {e}", exc_info=True)

    def get_adapter(self, channel: str) -> ChannelAdapter | None:
        for _status, adapter in self._adapters:
            if adapter.channel == channel:
                return adapter
        return None


def create_manager_from_env() -> AdapterManager:
    """根据环境变量创建适配器管理器。"""
    import os

    manager = AdapterManager()

    # 通用 Webhook（任何 *_WEBHOOK_URL）
    for key, val in os.environ.items():
        if key.endswith("_WEBHOOK_URL") and val:
            platform = key.replace("_WEBHOOK_URL", "").lower()
            manager.register_webhook(platform=platform, enabled=True)
            logger.info(f"Webhook 渠道 [{platform}] 已启用")

    # NoneBot 桥接（覆盖其余所有平台）
    if os.getenv("NONEBOT_ADAPTERS", "").strip():
        manager.register_nonebot_bridge(enabled=True)
        logger.info("NoneBot 桥接渠道已启用")

    # WeClaw（微信桥接，托管外部 weclaw 二进制）
    if os.getenv("WECLAW_ENABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        manager.register_weclaw(enabled=True)
        logger.info("WeClaw 渠道已启用")

    return manager
