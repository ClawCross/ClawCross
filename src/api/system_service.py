"""
系统触发服务模块

处理来自定时任务、外部系统等触发源的请求：
- 系统触发消息处理
- 多模态附件（图片/音频/文件）处理
"""

import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from langchain_core.messages import HumanMessage

from utils.logging_utils import get_logger
from services.message_builder import build_human_message
from api.system_models import SystemTriggerRequest

logger = get_logger("system_service")
_GRAPH_RECURSION_LIMIT = 100


@dataclass(frozen=True)
class _QueuedSystemTrigger:
    req: SystemTriggerRequest
    message: HumanMessage
    received_at: str

# 可以直接 base64 解码为文本的 MIME 类型（前缀匹配）
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_EXACT = {
    "application/json", "application/xml", "application/javascript",
    "application/typescript", "application/x-yaml", "application/yaml",
    "application/toml", "application/x-toml",
    "application/sql", "application/graphql",
    "application/x-sh", "application/x-python",
    "application/csv", "application/x-csv",
    "application/ld+json", "application/manifest+json",
    "application/x-httpd-php",
}


def _is_text_mime(mime_type: str) -> bool:
    """判断 MIME 类型是否为文本类（可以 base64 解码为可读文本）。"""
    mime = mime_type.lower().strip()
    if any(mime.startswith(p) for p in _TEXT_MIME_PREFIXES):
        return True
    if mime in _TEXT_MIME_EXACT:
        return True
    # 常见文本后缀的通配：application/*+json, application/*+xml
    if mime.endswith("+json") or mime.endswith("+xml"):
        return True
    return False


def _try_decode_base64_text(data: str, max_chars: int = 50000) -> str | None:
    """尝试将 base64 数据解码为 UTF-8 文本。失败返回 None。"""
    try:
        raw = base64.b64decode(data)
        text = raw.decode("utf-8")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n... (文件过长，已截断，共 {len(raw)} 字节)"
        return text
    except Exception:
        return None


class SystemService:
    def __init__(
        self,
        *,
        agent: Any,
        verify_internal_token: Callable[[str | None], None],
        coalesce_debounce_seconds: float = 0.75,
    ):
        self.agent = agent
        self.verify_internal_token = verify_internal_token
        self.coalesce_debounce_seconds = max(0.0, coalesce_debounce_seconds)
        self._coalesce_lock = asyncio.Lock()
        self._coalesce_queues: dict[str, list[_QueuedSystemTrigger]] = {}
        self._coalesce_tasks: dict[str, asyncio.Task] = {}

    @staticmethod
    def _thread_id(req: SystemTriggerRequest) -> str:
        return f"{req.user_id}#{req.session_id}"

    @staticmethod
    def _queue_key(thread_id: str, coalesce_key: str) -> str:
        return f"{thread_id}\0{coalesce_key}"

    @staticmethod
    def _received_at() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _message_text_and_parts(message: HumanMessage) -> tuple[str, list[Any]]:
        content = message.content
        if isinstance(content, str):
            return content, []
        if not isinstance(content, list):
            return str(content), []

        text_parts: list[str] = []
        non_text_parts: list[Any] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text_parts.append(str(part.get("text") or ""))
            else:
                non_text_parts.append(part)
        return "\n".join(item for item in text_parts if item).strip(), non_text_parts

    @staticmethod
    def _format_batch_header(index: int, total: int, item: _QueuedSystemTrigger) -> str:
        return (
            f"==================== 群聊消息 {index}/{total} 开始 ====================\n"
            f"received_at: {item.received_at}\n"
            f"session_id: {item.req.session_id}\n"
            f"coalesce_key: {item.req.coalesce_key}\n"
            "-------------------- 内容 --------------------"
        )

    @staticmethod
    def _format_batch_footer(index: int, total: int) -> str:
        return f"==================== 群聊消息 {index}/{total} 结束 ===================="

    def _build_coalesced_message(self, batch: list[_QueuedSystemTrigger]) -> HumanMessage:
        if len(batch) == 1:
            return batch[0].message

        total = len(batch)
        has_multimodal_parts = False
        content_parts: list[Any] = [
            {
                "type": "text",
                "text": (
                    "[群聊未读消息批量投递]\n"
                    "你处理期间，同一个群聊目标累积了多条消息。请像查看微信群未读消息一样，"
                    "按下面的时间顺序阅读完整上下文后再决定是否回复。"
                ),
            }
        ]
        text_sections: list[str] = [content_parts[0]["text"]]

        for index, item in enumerate(batch, start=1):
            message_text, non_text_parts = self._message_text_and_parts(item.message)
            header = self._format_batch_header(index, total, item)
            footer = self._format_batch_footer(index, total)
            section = f"{header}\n{message_text or '(空消息)'}\n{footer}"
            text_sections.append(section)
            if non_text_parts:
                has_multimodal_parts = True
                content_parts.append({
                    "type": "text",
                    "text": f"{header}\n{message_text or '(空消息)'}\n\n[本条消息的多模态附件紧随其后]",
                })
                content_parts.extend(non_text_parts)
                content_parts.append({"type": "text", "text": footer})
            else:
                content_parts.append({"type": "text", "text": section})

        if has_multimodal_parts:
            return HumanMessage(content=content_parts)
        return HumanMessage(content="\n\n".join(text_sections))

    def _build_message_from_trigger(self, req: SystemTriggerRequest) -> HumanMessage:
        """将 SystemTriggerRequest 转为 HumanMessage，支持多模态附件。"""
        if not req.attachments:
            return HumanMessage(content=req.text)

        images: list[str] = []
        audios: list[dict] = []
        files: list[dict] = []

        for att in req.attachments:
            if att.type == "image":
                # build_human_message 期望 data URI 格式
                data_uri = f"data:{att.mime_type};base64,{att.data}"
                images.append(data_uri)
            elif att.type == "audio":
                # 提取格式后缀 (e.g. "audio/mp3" → "mp3")
                fmt = att.mime_type.split("/")[-1] if "/" in att.mime_type else "wav"
                audios.append({
                    "base64": att.data,
                    "format": fmt,
                    "name": att.name,
                })
            else:
                # file 类型 — 按 MIME 分类处理
                if att.mime_type == "application/pdf":
                    # PDF: 传 base64 给 build_human_message，它会调用 _extract_pdf_text
                    files.append({
                        "name": att.name,
                        "type": "pdf",
                        "content": att.data,
                    })
                elif _is_text_mime(att.mime_type):
                    # 文本类文件 (json/txt/csv/xml/yaml/...): 解码 base64 为可读文本
                    decoded = _try_decode_base64_text(att.data)
                    if decoded is not None:
                        files.append({
                            "name": att.name,
                            "type": "text",
                            "content": decoded,
                        })
                    else:
                        files.append({
                            "name": att.name,
                            "type": "text",
                            "content": f"(文件 {att.name} 解码失败，MIME: {att.mime_type})",
                        })
                else:
                    # 真正的二进制文件 (zip/docx/xlsx 等): 降级为描述
                    files.append({
                        "name": att.name,
                        "type": "text",
                        "content": f"(二进制文件: {att.name}, 类型: {att.mime_type}，无法直接展示内容)",
                    })

        return build_human_message(
            text=req.text,
            images=images or None,
            files=files or None,
            audios=audios or None,
        )

    def _build_system_input(self, req: SystemTriggerRequest, human_msg: HumanMessage) -> dict[str, Any]:
        return {
            "messages": [human_msg],
            "trigger_source": "system",
            "enabled_tools": None,
            "user_id": req.user_id,
            "session_id": req.session_id,
            "max_turns": None,
            "turn_count": 0,
        }

    async def _invoke_system_message_locked(
        self,
        *,
        req: SystemTriggerRequest,
        human_msg: HumanMessage,
        thread_id: str,
        config: dict[str, Any],
        batch_count: int,
    ) -> None:
        task_key = thread_id
        system_input = self._build_system_input(req, human_msg)
        # 与用户 openai 流式任务共用 task_key：必须在持有锁后才 register，
        # 否则会在等锁阶段覆盖 registry 中仍活跃的用户任务。
        self.agent.register_task(task_key, asyncio.current_task())
        self.agent.set_thread_busy_source(thread_id, "system")
        logger.info("Acquired lock on %s, invoking graph with %s system trigger(s) ...", thread_id, batch_count)
        try:
            async for _ in self.agent.agent_app.astream_events(system_input, config, version="v2", durability="exit"):
                pass
            self.agent.add_pending_system_message(thread_id)
            logger.info("Done for %s (%s system trigger(s))", thread_id, batch_count)
        except asyncio.CancelledError:
            logger.info("Cancelled for %s", thread_id)
            try:
                snapshot = await self.agent.agent_app.aget_state(config)
                last_msgs = snapshot.values.get("messages", [])
                if last_msgs:
                    last_msg = last_msgs[-1]
                    tool_messages = self.agent.cancelled_tool_messages_for_last_ai(last_msg)
                    if tool_messages:
                        await self.agent.agent_app.aupdate_state(config, {"messages": tool_messages})
            except Exception:
                pass
        except Exception as e:
            logger.exception("Error for %s: %s", thread_id, e)
        finally:
            self.agent.clear_thread_busy_source(thread_id)
            await self.agent.purge_checkpoints(thread_id)
            self.agent.unregister_task(task_key)

    async def _run_single_trigger(self, req: SystemTriggerRequest, human_msg: HumanMessage) -> None:
        thread_id = self._thread_id(req)
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": _GRAPH_RECURSION_LIMIT,
        }
        lock = await self.agent.get_thread_lock(thread_id)
        logger.info(
            "system_trigger waiting for lock on %s (will not cancel in-flight user run)",
            thread_id,
        )
        async with lock:
            await self._invoke_system_message_locked(
                req=req,
                human_msg=human_msg,
                thread_id=thread_id,
                config=config,
                batch_count=1,
            )

    async def _drain_coalesced_batch(self, queue_key: str) -> list[_QueuedSystemTrigger]:
        async with self._coalesce_lock:
            return self._coalesce_queues.pop(queue_key, [])

    async def _coalesced_worker(self, *, queue_key: str, thread_id: str) -> None:
        while True:
            if self.coalesce_debounce_seconds:
                await asyncio.sleep(self.coalesce_debounce_seconds)

            lock = await self.agent.get_thread_lock(thread_id)
            logger.info(
                "coalesced system_trigger waiting for lock on %s",
                thread_id,
            )
            async with lock:
                batch = await self._drain_coalesced_batch(queue_key)
                if batch:
                    req = batch[0].req
                    config = {
                        "configurable": {"thread_id": thread_id},
                        "recursion_limit": _GRAPH_RECURSION_LIMIT,
                    }
                    human_msg = self._build_coalesced_message(batch)
                    await self._invoke_system_message_locked(
                        req=req,
                        human_msg=human_msg,
                        thread_id=thread_id,
                        config=config,
                        batch_count=len(batch),
                    )

            async with self._coalesce_lock:
                if not self._coalesce_queues.get(queue_key):
                    if self._coalesce_tasks.get(queue_key) is asyncio.current_task():
                        self._coalesce_tasks.pop(queue_key, None)
                    return

    async def _enqueue_coalesced_trigger(self, req: SystemTriggerRequest, human_msg: HumanMessage) -> int:
        thread_id = self._thread_id(req)
        queue_key = self._queue_key(thread_id, req.coalesce_key)
        queued = _QueuedSystemTrigger(
            req=req,
            message=human_msg,
            received_at=self._received_at(),
        )
        async with self._coalesce_lock:
            queue = self._coalesce_queues.setdefault(queue_key, [])
            queue.append(queued)
            queued_count = len(queue)
            task = self._coalesce_tasks.get(queue_key)
            if task is None or task.done():
                self._coalesce_tasks[queue_key] = asyncio.create_task(
                    self._coalesced_worker(queue_key=queue_key, thread_id=thread_id)
                )
        return queued_count

    async def system_trigger(self, req: SystemTriggerRequest, x_internal_token: str | None):
        self.verify_internal_token(x_internal_token)
        thread_id = self._thread_id(req)

        human_msg = self._build_message_from_trigger(req)
        logger.info("system_trigger for %s, has_attachments=%s, content_type=%s",
                     thread_id, bool(req.attachments),
                     type(human_msg.content).__name__)

        if req.coalesce_key:
            queued_count = await self._enqueue_coalesced_trigger(req, human_msg)
            return {
                "status": "received",
                "message": f"系统触发已收到，用户 {req.user_id}",
                "coalesced": True,
                "queued_count": queued_count,
            }

        asyncio.create_task(self._run_single_trigger(req, human_msg))
        return {"status": "received", "message": f"系统触发已收到，用户 {req.user_id}", "coalesced": False}
