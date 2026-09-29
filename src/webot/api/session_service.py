"""A WeBot session's own operations, for its runtime (``webot.driver``): what a
session list shows of it, its messages, compaction, deletion and context use."""

import asyncio
import contextlib
from typing import Any, Callable

from utils.checkpoint_repository import delete_thread_records, fetch_thread_checkpoint_times
from utils.logging_utils import get_logger
from utils.context_compressor import estimate_messages_tokens
from utils.context_limits import resolve_history_message_limits
from utils.session_summary import build_session_summary
from webot.compression import apply_compression, make_llm_summarizer, static_compression_view
from webot.profiles import is_subagent_session
from webot.runtime_settings import get_runtime_settings, resolve_context_window, resolve_context_history_budget, context_usage_with_window
from webot.subagents import delete_subagent_by_session

logger = get_logger("session_service")


class SessionService:
    def __init__(self, *, db_path: str, agent: Any, extract_text: Callable[[Any], str]):
        self.db_path = db_path
        self.agent = agent
        self.extract_text = extract_text

    async def _close_thread_checkpoints(self, thread_ids: list[str]) -> None:
        close_checkpoint = getattr(self.agent, "close_thread_checkpoint", None)
        if not callable(close_checkpoint):
            return
        for thread_id in thread_ids:
            await close_checkpoint(thread_id)

    async def summary(self, user_id: str, session_id: str) -> dict:
        """What a session list shows: its first and last user messages, how many messages,
        when. Nothing for a sub-agent's session or one nobody has written to yet."""
        if is_subagent_session(session_id):
            return {}
        thread_id = f"{user_id}#{session_id}"
        snapshot = await self.agent.agent_app.aget_state({"configurable": {"thread_id": thread_id}})
        msgs = snapshot.values.get("messages", []) if snapshot and snapshot.values else []
        summary = build_session_summary(
            msgs,
            skip_prefixes=("[系统触发]", "[外部学术会议邀请]"),
            title_len=50,
            last_len=50,
            list_fallback="(图片消息)",
        )
        if not summary["first_human"]:
            return {}
        times = await fetch_thread_checkpoint_times(self.db_path, thread_id)
        return {
            "title": summary["first_human"],
            "last_message": summary["last_human"],
            "message_count": summary["msg_count"],
            "created_at": times.get("created_at", ""),
            "updated_at": times.get("updated_at", ""),
            "created_at_ts": times.get("created_at_ts", 0),
            "updated_at_ts": times.get("updated_at_ts", 0),
        }

    async def messages(self, user_id: str, session_id: str) -> list[dict]:
        """The session's messages, oldest first: a user's as sent (images too), the
        assistant's text and tool calls, each tool's result. Reading them also brings the
        session's context use up to date."""
        thread_id = f"{user_id}#{session_id}"
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.agent.agent_app.aget_state(config)

        if not snapshot or not snapshot.values:
            return []

        msgs = snapshot.values.get("messages", [])

        # 上下文占用与实时推理路径口径对齐：优先用上一轮 API 真实占用 (input+output)
        # 相对整窗口；只有从未推理过的会话（没有真值）才回退到「压缩视图字数估算 ÷
        # 历史预算」。这样刷新页面/切会话与实时轮询显示的占比一致，不会跳变。
        try:
            # 优先用该 thread 上次推理实际使用的模型；没有就回退到 LLM_MODEL env。
            last_model = ""
            if hasattr(self.agent, "get_thread_model"):
                last_model = self.agent.get_thread_model(thread_id)

            real_ctx = 0
            if hasattr(self.agent, "get_thread_last_context_tokens"):
                real_ctx = int(self.agent.get_thread_last_context_tokens(thread_id) or 0)
            if real_ctx <= 0 and hasattr(self.agent, "restore_context_usage"):
                # 服务重启后内存里没有真值：读回落盘的上一轮 API 用量
                await self.agent.restore_context_usage(thread_id)
                real_ctx = int(self.agent.get_thread_last_context_tokens(thread_id) or 0)

            if real_ctx > 0:
                # 推理/恢复路径已写入 API 实测值和分项，只在缺失时补一份，不用估算覆盖
                if self.agent.get_thread_context_usage(thread_id).get("source") != "api":
                    window = resolve_context_window(get_runtime_settings(user_id, session_id).context, last_model or None)
                    self.agent.set_thread_context_usage(
                        thread_id, real_ctx, max(window, real_ctx), source="api",
                    )
            else:
                # 数的是 apply_compression 真正看到的输入视图
                # = [已存的 summary] + messages[compacted_until:]，
                # 不是用户在前端看到的完整未压缩历史。
                compression_view = static_compression_view(
                    user_id=user_id,
                    session_id=session_id,
                    messages=msgs,
                    checkpoint_store_path=getattr(self.agent, "_db_path", None),
                )
                static_tokens = estimate_messages_tokens(compression_view)
                window = resolve_context_window(get_runtime_settings(user_id, session_id).context, last_model or None)
                self.agent.set_thread_context_usage(thread_id, static_tokens, window)
        except Exception:
            logger.exception("context usage estimation failed for %s", thread_id)
        result = []
        for msg in msgs:
            msg_type = type(msg).__name__
            if msg_type == "HumanMessage":
                result.append({"role": "user", "content": msg.content})
            elif msg_type in ("AIMessage", "AIMessageChunk"):
                content = self.extract_text(msg.content)
                tool_calls = []
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_calls.append({
                            "name": tc.get("name", ""),
                            "args": tc.get("args", {}),
                        })
                if content or tool_calls:
                    entry = {"role": "assistant", "content": content}
                    if tool_calls:
                        entry["tool_calls"] = tool_calls
                    result.append(entry)
            elif msg_type == "ToolMessage":
                content = self.extract_text(msg.content)
                tool_name = getattr(msg, "name", "")
                result.append({
                    "role": "tool",
                    "content": content,
                    "tool_name": tool_name,
                })
        return result

    async def compact(self, user_id: str, session_id: str) -> dict:
        """Compress the session's history now, whatever the threshold: early messages fold
        into a summary; the originals stay, and the next turn sees the compressed view."""
        logger.info("compact_session user=%s session=%s", user_id, session_id)

        thread_id = f"{user_id}#{session_id}"
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await self.agent.agent_app.aget_state(config)
        msgs = snapshot.values.get("messages", []) if snapshot and snapshot.values else []
        if not msgs:
            return {"triggered": False, "reason": "empty", "before_tokens": 0, "after_tokens": 0, "saved_tokens": 0}

        last_model = ""
        if hasattr(self.agent, "get_thread_model"):
            last_model = self.agent.get_thread_model(thread_id)
        settings = get_runtime_settings(user_id, session_id).context
        budget = resolve_context_history_budget(settings, is_subagent=is_subagent_session(session_id), model=last_model or None)
        store_path = getattr(self.agent, "_db_path", None) or self.db_path

        before_tokens = estimate_messages_tokens(
            static_compression_view(
                user_id=user_id,
                session_id=session_id,
                messages=msgs,
                checkpoint_store_path=store_path,
            )
        )
        try:
            _, preserve_recent = resolve_history_message_limits(
                is_subagent=is_subagent_session(session_id), token_budget=budget,
            )
            result = await asyncio.to_thread(
                apply_compression,
                user_id=user_id,
                session_id=session_id,
                messages=msgs,
                history_token_budget=budget,
                checkpoint_store_path=store_path,
                preserve_recent=preserve_recent,
                summarizer=make_llm_summarizer(
                    model=settings.summarizer_model or None, max_output_tokens=settings.summary_tokens,
                    input_token_budget=settings.summarizer_input_tokens,
                    preserve_instructions=settings.preserve_instructions,
                ),
                force=True,
                settings=settings,
            )
            if result.reason == "persistence_failed":
                raise RuntimeError("compaction persistence failed")
        except Exception as exc:
            logger.exception("compact_session failed for %s", thread_id)
            raise RuntimeError("compaction failed") from exc

        after_tokens = result.view_tokens
        # 已有 API 真值时不用字数估算覆盖；压缩效果在下一次调用后由真值体现
        has_real_usage = hasattr(self.agent, "get_thread_last_context_tokens") and int(
            self.agent.get_thread_last_context_tokens(thread_id) or 0
        ) > 0
        if not has_real_usage:
            with contextlib.suppress(Exception):
                self.agent.set_thread_context_usage(thread_id, after_tokens, budget)

        return {
            "triggered": result.triggered,
            "reason": result.reason,
            "before_tokens": int(before_tokens),
            "after_tokens": int(after_tokens),
            "saved_tokens": max(0, int(before_tokens) - int(after_tokens)),
            "summary_chars": len(result.summary or ""),
            "compacted_until": int(result.compacted_until or 0),
            "metadata": getattr(result, "metadata", None) or {},
        }

    async def delete(self, user_id: str, session_id: str) -> None:
        """Stop and delete one session: its task, checkpoints and sub-agent record."""
        thread_id = f"{user_id}#{session_id}"
        await self.agent.cancel_task(thread_id)
        await self._close_thread_checkpoints([thread_id])
        await delete_thread_records(self.db_path, thread_id)
        if is_subagent_session(session_id):
            delete_subagent_by_session(user_id, session_id)

    def _configured_context_usage(self, user_id: str, session_id: str) -> dict:
        thread_id = f"{user_id}#{session_id}"
        usage = self.agent.get_thread_context_usage(thread_id)
        model = self.agent.get_thread_model(thread_id) if hasattr(self.agent, "get_thread_model") else None
        window = resolve_context_window(get_runtime_settings(user_id, session_id).context, model)
        return context_usage_with_window(usage, window)

    async def context_usage(self, user_id: str, session_id: str) -> dict:
        """The session's context use against its window; read back from disk after a restart."""
        usage = self._configured_context_usage(user_id, session_id)
        if not usage.get("tokens") and hasattr(self.agent, "restore_context_usage"):
            if await self.agent.restore_context_usage(f"{user_id}#{session_id}"):
                usage = self._configured_context_usage(user_id, session_id)
        return usage
