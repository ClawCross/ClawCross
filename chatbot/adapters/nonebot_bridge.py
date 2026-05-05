"""
NoneBot 桥接适配器 - 一份代码覆盖 NoneBot 全部 30+ 平台

NoneBot 已经实现了 30+ 平台的协议解析、事件分发、回复 API。
此 bridge 把 NoneBot 当作内嵌 runtime，把它收到的所有 Event 统一转成 ChatMessage，
经我们标准的 verify_permission / build_content / call_ai 流程处理后通过
NoneBot 的 bot.send 回写。

使用方式：
    1. pip install nonebot2[fastapi,httpx,websockets]
    2. pip install nonebot-adapter-<platform>   # 例如 nonebot-adapter-ding
    3. .env 中配置：
        NONEBOT_ADAPTERS=ding,villa,yunhu,onebot.v11,mail
        NONEBOT_HOST=127.0.0.1
        NONEBOT_PORT=8120
        WHITELIST_FILE=data/whitelist.json    # 中心化白名单，按 channel 分段
    4. 各平台自身的 env 变量按 NoneBot 适配器文档配（如 DING_ACCESS_TOKEN, ONEBOT_ACCESS_TOKEN 等）
    5. 启动 chatbot 即可

冲突保护：
    Slack 和通用 Webhook 由本地手写 adapter 覆盖（NoneBot 无 Slack 适配器）。
    若用户在 NONEBOT_ADAPTERS 中设置了同名条目会跳过；可设 NONEBOT_ALLOW_OVERLAP=1 强制。
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import threading
from typing import Any

from dotenv import load_dotenv

_chatbot_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_project_root = os.path.dirname(_chatbot_dir)
load_dotenv(dotenv_path=os.path.join(_project_root, "config", ".env"))

from .base import ChannelAdapter

logger = logging.getLogger("chatbot.nonebot_bridge")

# 与现有手写 adapter 冲突的 NoneBot 模块短名（目前只有 Slack 不在 NoneBot 列表）
_OVERLAPPING_ADAPTERS: set[str] = set()


def _resolve_adapter_module(name: str):
    """支持两种命名：nonebot.adapters.<name> 或 nonebot_adapter_<name>。"""
    name = name.strip().lower()
    candidates = [
        f"nonebot.adapters.{name}",
        f"nonebot_adapter_{name.replace('.', '_').replace('-', '_')}",
    ]
    last_err = None
    for mod_path in candidates:
        try:
            return importlib.import_module(mod_path)
        except ImportError as e:
            last_err = e
    raise ImportError(
        f"找不到 NoneBot 适配器 '{name}'，尝试过: {candidates}。"
        f"请先 pip install nonebot-adapter-{name.replace('.', '-')}。原始错误: {last_err}"
    )


class NoneBotBridgeAdapter(ChannelAdapter):
    """单适配器桥接 NoneBot 全部平台事件。"""

    channel = "nonebot"  # 默认；实际每条消息会按真实 NB 适配器名覆盖

    def __init__(self):
        super().__init__()
        raw = os.getenv("NONEBOT_ADAPTERS", "")
        requested = [n.strip() for n in raw.split(",") if n.strip()]

        allow_overlap = os.getenv("NONEBOT_ALLOW_OVERLAP", "").strip().lower() in ("1", "true", "yes", "on")
        if allow_overlap:
            self._adapter_names = requested
        else:
            self._adapter_names = []
            for n in requested:
                base = n.split(".")[0].lower()
                if base in _OVERLAPPING_ADAPTERS:
                    logger.warning(
                        f"NoneBot adapter '{n}' 与本地手写 adapter 冲突，已跳过。"
                        f"如确需用 NoneBot 版本请设 NONEBOT_ALLOW_OVERLAP=1 并清空对应 env。"
                    )
                    continue
                self._adapter_names.append(n)

        # 中心化白名单由 base.__init__ 处理（WHITELIST_FILE 或 data/whitelist.json）
        self._host = os.getenv("NONEBOT_HOST", "127.0.0.1")
        self._port = int(os.getenv("NONEBOT_PORT", "8120"))
        self._driver = os.getenv("NONEBOT_DRIVER", "~fastapi+~httpx+~websockets")

    # ── 适配器抽象方法实现 ────────────────────────────────────────────

    async def verify_permission(self, event: Any, channel: str | None = None) -> tuple[bool, str | None]:
        """从 NB 事件提取 user_id，按真实平台 channel 查中心化白名单。"""
        try:
            user_id = str(event.get_user_id())
        except Exception:
            user_id = ""
        username_hint = self._extract_username_hint(event)

        entry = self._find_whitelist_entry(user_id, username_hint, channel=channel)
        if entry:
            return True, entry.get("username")

        if not self._whitelist_file or not os.path.exists(self._whitelist_file):
            return True, username_hint or user_id or "anonymous"

        logger.warning(f"NoneBot bridge 未授权: channel={channel} user_id={user_id} hint={username_hint}")
        return False, None

    async def build_content(self, event: Any) -> list[dict]:
        """转换 NB Message 为 OpenAI 多模态 content 列表。

        多模态原则：text 段拼成一段；image 段尝试取 url，转 image_url。
        音频/视频段当前作为说明文落入文本（各 NB 适配器音频结构差异大，单独处理收益小）。
        """
        content_list: list[dict] = []

        text = ""
        try:
            text = event.get_plaintext() or ""
        except Exception:
            pass

        image_urls: list[str] = []
        non_text_summary: list[str] = []
        try:
            for seg in event.get_message():
                seg_type = getattr(seg, "type", "") or ""
                data = getattr(seg, "data", {}) or {}
                if seg_type == "image":
                    url = data.get("url") or data.get("file") or data.get("src")
                    if url:
                        image_urls.append(str(url))
                elif seg_type in ("voice", "audio", "record"):
                    non_text_summary.append("[语音]")
                elif seg_type == "video":
                    non_text_summary.append("[视频]")
                elif seg_type in ("file", "document"):
                    fname = data.get("name") or data.get("file_name") or "文件"
                    non_text_summary.append(f"[文件: {fname}]")
        except Exception as e:
            logger.debug(f"消息段提取异常: {e}")

        full_text = " ".join(filter(None, [text.strip()] + non_text_summary)).strip()
        content_list.append({"type": "text", "text": full_text or "请分析此内容"})

        for url in image_urls:
            content_list.append({"type": "image_url", "image_url": {"url": url}})

        return content_list

    async def handle_message(self, bot: Any, event: Any) -> None:
        """统一消息处理入口（被 NoneBot matcher 调用）。"""
        adapter_name = self._extract_adapter_channel(bot)

        allowed, username = await self.verify_permission(event, channel=adapter_name)
        if not allowed:
            return
        if not username:
            return

        content_list = await self.build_content(event)

        # /cross 命令：直接返回 magic link，跳过 AI
        text = self.extract_text(content_list)
        if self.is_cross_command(text):
            link = await self.generate_magic_link(username)
            reply = self.format_cross_reply(link)
            try:
                await bot.send(event, reply)
            except Exception as e:
                logger.error(f"回复 /cross 失败 ({adapter_name}): {e}")
            return

        # 按消息真实平台动态构造 api_key（不修改 self.channel，避免并发竞争）
        api_key = f"{self._internal_token}:{username}:{adapter_name.upper()}"

        result = await self.call_ai(content_list, api_key, model=self._llm_model)
        reply = result.content if result.ok else f"AI 错误: {result.error}"

        try:
            await bot.send(event, reply)
        except Exception as e:
            logger.error(f"回复失败 ({adapter_name}): {e}")

    # ── 启动入口 ──────────────────────────────────────────────────────

    async def run(self) -> None:
        """启动 NoneBot 主循环（阻塞）。

        直接用 NoneBot 的 driver.run()，因为它内部会正确启动 lifespan 和 polling。
        """
        if not self._adapter_names:
            logger.info("NONEBOT_ADAPTERS 为空，bridge 不启动")
            return

        try:
            self._bootstrap_nonebot()
        except Exception as e:
            logger.error(f"NoneBot bridge 初始化失败: {e}", exc_info=True)
            return

        try:
            import nonebot
        except ImportError as e:
            logger.error(f"NoneBot 未安装: {e}")
            return

        driver = nonebot.get_driver()
        asgi_app = getattr(driver, "server_app", None) or getattr(driver, "asgi", None)
        if asgi_app is None:
            logger.error("当前 NoneBot driver 未提供 ASGI app（请使用 ~fastapi 系列驱动）")
            return

        logger.info(
            f"NoneBot bridge 启动 ({len(self._adapter_names)} 个平台: "
            f"{', '.join(self._adapter_names)}) on {self._host}:{self._port}"
        )

        # uvicorn.run() 需要自己的 event loop，不能在已有 loop 的协程里调用
        # 因此在 daemon 线程里运行，保持 bridge 生命周期直到进程退出
        import threading
        import uvicorn

        def _run_server():
            LOGGING_CONFIG = {
                "version": 1,
                "disable_existing_loggers": False,
                "handlers": {
                    "default": {
                        "class": "nonebot.log.LoguruHandler",
                    },
                },
                "loggers": {
                    "uvicorn.error": {"handlers": ["default"], "level": "INFO"},
                    "uvicorn.access": {"handlers": ["default"], "level": "INFO"},
                },
            }
            uvicorn.run(
                asgi_app,
                host=self._host,
                port=self._port,
                log_config=LOGGING_CONFIG,
            )

        thread = threading.Thread(target=_run_server, daemon=False, name="nonebot-bridge")
        thread.daemon = True
        thread.start()

        # 等待 NoneBot server 完全启动（监听端口可用）
        import time
        deadline = time.monotonic() + 15
        import socket
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self._port), timeout=1):
                    logger.info(f"NoneBot bridge 已就绪 on {self._host}:{self._port}")
                    break
            except OSError:
                time.sleep(0.2)
        else:
            logger.warning("NoneBot bridge 启动超时（端口未就绪）")

        # 在 daemon 线程里运行 asyncio.run() 会报错，用 sleep 阻塞
        await asyncio.sleep(float('inf'))

    # ── 内部辅助 ──────────────────────────────────────────────────────

    def _bootstrap_nonebot(self) -> None:
        try:
            import nonebot
        except ImportError as e:
            raise ImportError(
                "未安装 nonebot2，请先 `pip install nonebot2[fastapi,httpx,websockets]`"
            ) from e

        # 把 host/port/driver 推到 NB 初始化参数（NB 读 env 也行，但显式更可控）
        nonebot.init(
            driver=self._driver,
            host=self._host,
            port=self._port,
            log_level=os.getenv("NONEBOT_LOG_LEVEL", "INFO"),
        )
        driver = nonebot.get_driver()

        # NoneBot Config 有 extra='allow'，但 <NAME>_BOTS 等适配器专属 env 不会被自动读取
        # 需要手动从 env 读 <NAME>_BOTS（如 TELEGRAM_BOTS, QQ_BOTS）并设置到 config。
        # 带点平台（如 onebot.v11）使用 ONEBOTV11_BOTS；同时兼容旧的 ONEBOT.V11_BOTS。
        import json
        for name in self._adapter_names:
            name_lower = name.lower().replace(' ', '')
            std_name = name_lower.replace('-', '').replace('_', '').replace('.', '')
            env_key = f"{std_name.upper()}_BOTS"
            legacy_env_key = f"{name.upper()}_BOTS"
            bots_json = os.getenv(env_key, "") or os.getenv(legacy_env_key, "")
            if not bots_json:
                continue

            try:
                bots = json.loads(bots_json)
            except Exception as e:
                logger.warning(f"解析 {env_key} 失败: {e}")
                continue

            # 标准化字段名：telegram, qq, discord, dingtalk, onebotv11, onebotv12, ...
            field_name = f"{std_name}_bots"

            # 直接设置，不判断 hasattr（Config 有 extra='allow'，可以任意添加字段）
            setattr(driver.config, field_name, bots)

            try:
                module = _resolve_adapter_module(name)
                adapter_cls = getattr(module, "Adapter", None)
                if adapter_cls is None:
                    logger.error(f"模块 {module.__name__} 没有 Adapter 类，跳过")
                    continue
                driver.register_adapter(adapter_cls)
                logger.info(f"已注册 NoneBot 适配器: {name}")

            except Exception as e:
                logger.error(f"加载 NoneBot 适配器 {name} 失败: {e}")

        # 注册全局 message matcher，路由到我们的 handle_message
        from nonebot import on_message

        matcher = on_message(priority=10, block=False)

        bridge_self = self

        @matcher.handle()
        async def _route(bot, event):
            await bridge_self.handle_message(bot, event)

    def _run_nonebot_blocking(self) -> None:
        import nonebot
        nonebot.run()

    def _extract_adapter_channel(self, bot: Any) -> str:
        """从 NB Bot 拿到平台名作为 channel 后缀。"""
        try:
            adapter = getattr(bot, "adapter", None)
            if adapter is None:
                return "nonebot"
            name = adapter.get_name()
            # NB 适配器名通常类似 "OneBot V11", "Telegram", "DingTalk"
            # 取首单词 + 去空格作为 channel
            return name.split()[0].replace("-", "_").lower() if name else "nonebot"
        except Exception:
            return "nonebot"

    def _extract_username_hint(self, event: Any) -> str | None:
        """尽力从 event 提取一个 username 提示。"""
        for attr in ("get_user_name", "user_name"):
            v = getattr(event, attr, None)
            if callable(v):
                try:
                    return str(v())
                except Exception:
                    pass
            elif isinstance(v, str) and v:
                return v

        sender = getattr(event, "sender", None)
        if sender is not None:
            for attr in ("nickname", "card", "name", "username"):
                v = getattr(sender, attr, None)
                if isinstance(v, str) and v:
                    return v
        return None
