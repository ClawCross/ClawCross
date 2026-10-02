"""A WeBot session's own operations, for its runtime (``webot.driver``): what a
session list shows of it, its messages, compaction, deletion and context use."""

import asyncio
import contextlib
import secrets
import time
from typing import Any, Callable

from webot.checkpoint_repository import delete_thread_records, fetch_thread_checkpoint_times, get_context_compaction
from common.logging_utils import get_logger
from webot.context_compressor import estimate_messages_tokens
from webot.context_limits import resolve_history_message_limits
from webot.session_summary import build_session_summary
from webot.compression import apply_compression, commit_prepared_compression, make_llm_summarizer, static_compression_view
from webot.profiles import is_subagent_session
from webot.runtime_settings import get_runtime_settings, resolve_context_window, resolve_context_history_budget, context_usage_with_window
from webot.runtime_store import delete_agent_runtime_db
from webot.subagents import delete_subagent_by_session

logger = get_logger("session_service")


class SessionService:
    def __init__(self, *, db_path: str, agent: Any, extract_text: Callable[[Any], str]):
        self.db_path = db_path
        self.agent = agent
        self.extract_text = extract_text
        self._compaction_jobs: dict[str, dict] = {}
        self._compaction_generations: dict[str, int] = {}

    def start_compaction(self, user_id: str, session_id: str) -> dict:
        thread_id = f"{user_id}#{session_id}"
        previous = self._compaction_jobs.get(thread_id)
        if previous and not previous["task"].done():
            return self.compaction_status(user_id, session_id)
        job = {"job_id": secrets.token_hex(8), "state": "running", "kind": "manual", "started_at": time.time()}
        self._compaction_jobs[thread_id] = job

        async def run():
            try:
                job["result"] = await self.compact(user_id, session_id)
                job["state"] = "completed"
            except asyncio.CancelledError:
                job["state"] = "cancelled"
            except Exception as exc:
                logger.exception("manual compaction job failed for %s", thread_id)
                job.update(state="failed", error=str(exc.__cause__ or exc))

        job["task"] = asyncio.create_task(run())
        return self.compaction_status(user_id, session_id)

    def compaction_status(self, user_id: str, session_id: str) -> dict:
        job = self._compaction_jobs.get(f"{user_id}#{session_id}")
        if not job:
            return {"state": "missing", "error": "压缩任务不存在或服务已重启，请重新发起。"}
        status = {key: value for key, value in job.items() if key != "task"}
        if status["state"] == "running":
            status["elapsed_seconds"] = max(0, int(time.time() - status["started_at"]))
        return status

    def visible_compaction_status(self, user_id: str, session_id: str) -> dict:
        thread_id = f"{user_id}#{session_id}"
        manual = self.compaction_status(user_id, session_id) if thread_id in self._compaction_jobs else {"state": "idle"}
        auto_status = getattr(self.agent, "get_background_compaction_status", None)
        automatic = auto_status(thread_id) if callable(auto_status) else {"state": "idle"}
        for status in (manual, automatic):
            if status.get("state") in {"checking", "running"}:
                return status
        return max((manual, automatic), key=lambda status: status.get("started_at", 0))

    async def cancel_compaction(self, user_id: str, session_id: str) -> None:
        thread_id = f"{user_id}#{session_id}"
        self._compaction_generations[thread_id] = self._compaction_generations.get(thread_id, 0) + 1
        job = self._compaction_jobs.pop(thread_id, None)
        if job and not job["task"].done():
            job["task"].cancel()
            await asyncio.gather(job["task"], return_exceptions=True)

    async def close(self) -> None:
        for thread_id in list(self._compaction_jobs):
            user_id, session_id = thread_id.split("#", 1)
            await self.cancel_compaction(user_id, session_id)

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
                if not self.agent.get_thread_context_usage(thread_id).get("tokens"):
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
        generation = self._compaction_generations.get(thread_id, 0)
        invalidate = getattr(self.agent, "invalidate_background_compression", None)
        if callable(invalidate):
            await invalidate(thread_id)
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
                    model=settings.summarizer_model or last_model or None, max_output_tokens=settings.summary_tokens,
                    input_token_budget=settings.summarizer_input_tokens,
                    preserve_instructions=settings.preserve_instructions,
                ),
                force=True,
                settings=settings,
                persist=False,
            )
            if self._compaction_generations.get(thread_id, 0) != generation:
                raise RuntimeError("会话已重置或删除，本次摘要未发布。")
            if result.triggered and getattr(result, "source_message_count", 0):
                commit_prepared_compression(store_path, thread_id, result)
                result.reason = "compressed"
            if result.reason == "persistence_failed":
                raise RuntimeError("compaction persistence failed")
        except Exception as exc:
            logger.exception("compact_session failed for %s", thread_id)
            raise RuntimeError("compaction failed") from exc

        after_tokens = result.view_tokens
        if result.triggered and hasattr(self.agent, "project_compacted_context_usage"):
            try:
                self.agent.project_compacted_context_usage(thread_id, get_context_compaction(store_path, thread_id), msgs)
            except Exception:
                # The summary is already committed. A display refresh must not
                # keep its task running or turn a successful commit into failure.
                logger.exception("post-compaction usage projection failed for %s", thread_id)
        elif not self.agent.get_thread_context_usage(thread_id).get("tokens"):
            self.agent.set_thread_context_usage(thread_id, after_tokens, budget, source="estimate")

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
        """Stop and delete one session: its task, checkpoints, runtime state and sub-agent record."""
        thread_id = f"{user_id}#{session_id}"
        await self.cancel_compaction(user_id, session_id)
        await self.agent.cancel_task(thread_id)
        await self._close_thread_checkpoints([thread_id])
        await delete_thread_records(self.db_path, thread_id)
        delete_agent_runtime_db(user_id, session_id)
        forget = getattr(self.agent, "forget_thread_state", None)
        if callable(forget):
            forget(thread_id)
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
        if hasattr(self.agent, "refresh_compacted_context_usage"):
            await self.agent.refresh_compacted_context_usage(f"{user_id}#{session_id}")
        usage = self._configured_context_usage(user_id, session_id)
        if not usage.get("tokens") and hasattr(self.agent, "restore_context_usage"):
            if await self.agent.restore_context_usage(f"{user_id}#{session_id}"):
                usage = self._configured_context_usage(user_id, session_id)
        return usage
