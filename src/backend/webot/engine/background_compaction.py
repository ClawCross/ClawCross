"""Prepare summaries between model calls and after turns; wait at the window limit."""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from webot.checkpoint_repository import fetch_thread_message_count, get_context_compaction
from webot.compression import apply_compression, commit_prepared_compression, make_llm_summarizer, compression_view_from_record
from webot.context_compressor import estimate_messages_tokens
from webot.policy import get_tool_policy, run_tool_policy_hooks
from webot.runtime import effective_session_mode
from webot.runtime_settings import ContextSettings, resolve_compaction_target, resolve_compaction_summary_budget

logger = logging.getLogger("webot.background_compaction")


class BackgroundCompressionManager:
    """Keep one preparation task per session and serialize commits with reset."""

    def __init__(self, checkpoint_store_path: str) -> None:
        self.checkpoint_store_path = checkpoint_store_path
        self._tasks: dict[str, asyncio.Task] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._generation: dict[str, int] = {}
        self._commit_locks: dict[str, asyncio.Lock] = {}
        self._statuses: dict[str, dict] = {}

    def status(self, thread_id: str) -> dict:
        status = dict(self._statuses.get(thread_id) or {"state": "idle", "kind": "automatic"})
        if status.get("state") in {"checking", "running"}:
            status["elapsed_seconds"] = max(0, int(time.time() - status["started_at"]))
        return status

    def schedule(
        self, *, user_id: str, session_id: str, messages: list[BaseMessage],
        history_token_budget: int, preserve_recent: int, settings: ContextSettings,
        measured_input_tokens: int = 0, measured_budget: int = 0,
        emergency: bool = False, model: str = "",
        queue_latest: bool = True,
    ) -> asyncio.Task | None:
        if not settings.auto_compact or not messages or history_token_budget <= 0:
            return
        thread_id = f"{user_id}#{session_id}"
        request = {
            "user_id": user_id, "session_id": session_id,
            "messages": copy.deepcopy(messages), "history_token_budget": history_token_budget,
            "preserve_recent": preserve_recent, "settings": settings,
            "measured_input_tokens": measured_input_tokens,
            "measured_budget": measured_budget,
            "emergency": emergency, "model": model,
        }
        current = self._tasks.get(thread_id)
        if current is not None and not current.done():
            if queue_latest:
                self._pending[thread_id] = request
            return current
        generation = self._generation.get(thread_id, 0)
        status = {"state": "checking", "kind": "automatic", "started_at": time.time(), "job_id": str(time.time_ns())}
        self._statuses[thread_id] = status
        task = asyncio.create_task(self._prepare_and_commit(
            thread_id=thread_id, generation=generation, **request,
        ))
        self._tasks[thread_id] = task

        def finished(done: asyncio.Task) -> None:
            if self._tasks.get(thread_id) is done:
                self._tasks.pop(thread_id, None)
                pending = self._pending.pop(thread_id, None)
                if pending is not None:
                    self.schedule(**pending)
            if not done.cancelled():
                error = done.exception()
                if error is not None:
                    status.update(state="failed", error=str(error))
                    logger.warning("background compression failed for %s: %s", thread_id, error)

        task.add_done_callback(finished)
        return task

    async def prepare_for_model(
        self, *, user_id: str, session_id: str, messages: list[BaseMessage],
        history_token_budget: int, preserve_recent: int, settings: ContextSettings,
        prefix_tokens: int, output_reserve: int, context_window: int,
        model: str = "", measured_input_tokens: int = 0,
    ):
        """Use fresh summaries mid-turn, and never send an overflowing turn.

        The caller supplies API usage only when it measured this compaction
        version. After a new summary, occupancy is estimated until recalibrated.
        """
        thread_id = f"{user_id}#{session_id}"
        record = await asyncio.to_thread(get_context_compaction, self.checkpoint_store_path, thread_id)
        if not settings.auto_compact:
            return record
        view = compression_view_from_record(record, messages)
        occupancy = max(measured_input_tokens, prefix_tokens + estimate_messages_tokens(view))
        critical = occupancy + output_reserve >= int(context_window * .95)
        trigger = min(settings.trigger_tokens or int(history_token_budget * .70),
                      int(context_window * .80))
        if occupancy < trigger and not critical:
            return record
        # Summarize an oversized tool loop before the hard watermark, even
        # when normal recent-turn retention protects the entire loop.
        last_user = max((i for i, item in enumerate(view) if isinstance(item, HumanMessage)), default=0)
        long_turn = estimate_messages_tokens(view[last_user:]) >= int(history_token_budget * .80)
        request = dict(user_id=user_id, session_id=session_id, messages=messages,
            history_token_budget=history_token_budget, preserve_recent=preserve_recent,
            settings=settings, measured_input_tokens=occupancy,
            measured_budget=context_window, model=model, queue_latest=False)
        task = self.schedule(**request, emergency=critical or long_turn)
        if not critical:
            return record
        # An existing normal job may preserve this entire long turn. If it
        # cannot free enough space, follow it with one tool-boundary job.
        for attempt in range(2):
            if task is not None:
                await asyncio.shield(task)
            record = await asyncio.to_thread(get_context_compaction, self.checkpoint_store_path, thread_id)
            view = compression_view_from_record(record, messages)
            if prefix_tokens + estimate_messages_tokens(view) + output_reserve < int(context_window * .95):
                return record
            if attempt == 0:
                task = self.schedule(**request, emergency=True)
        raise RuntimeError("上下文压缩后仍超过安全窗口；当前输入或最新工具结果过大，请缩小输入或调整上下文设置。")

    async def _prepare_and_commit(
        self, *, thread_id: str, user_id: str, session_id: str,
        messages: list[BaseMessage], history_token_budget: int,
        preserve_recent: int, settings: ContextSettings,
        measured_input_tokens: int, measured_budget: int, generation: int,
        emergency: bool = False, model: str = "",
    ) -> None:
        status = self._statuses.get(thread_id, {})
        loop = asyncio.get_running_loop()
        # Start before the foreground 90% watermark so the next user turn can
        # use an already finished summary. Clamp late triggers to 80% of the
        # complete window, since tools keep returning while we summarize.
        early_trigger = min(settings.trigger_tokens or max(1, int(history_token_budget * 0.70)),
                            max(1, int(measured_budget * .80)) if measured_budget else history_token_budget)
        target = resolve_compaction_target(settings, history_token_budget)
        target = min(target, max(1, early_trigger - 1))
        prepared_settings = settings.model_copy(update={
            "trigger_tokens": early_trigger, "target_tokens": target,
        })

        def prepare():
            snapshot = copy.deepcopy(messages)

            def before_summary() -> None:
                loop.call_soon_threadsafe(lambda: status.update(state="running"))
                run_tool_policy_hooks(
                    get_tool_policy(user_id), event="pre_compact",
                    user_id=user_id, session_id=session_id, tool_name="__session__",
                    args={"mode": effective_session_mode(user_id, session_id),
                          "message_count": len(snapshot)},
                    result={"context_token_budget": history_token_budget},
                )

            return apply_compression(
                user_id=user_id,
                session_id=session_id,
                messages=snapshot,
                history_token_budget=history_token_budget,
                checkpoint_store_path=self.checkpoint_store_path,
                preserve_recent=preserve_recent,
                summarizer=make_llm_summarizer(
                    model=settings.summarizer_model or model or None,
                    max_output_tokens=resolve_compaction_summary_budget(prepared_settings, target),
                    input_token_budget=settings.summarizer_input_tokens,
                    preserve_instructions=settings.preserve_instructions,
                ),
                measured_input_tokens=measured_input_tokens,
                measured_budget=measured_budget,
                settings=prepared_settings,
                persist=False,
                before_summary=before_summary,
                emergency=emergency,
            )

        result = await asyncio.to_thread(prepare)
        if not result.triggered:
            status.update(state="idle", reason=result.reason)
            return

        lock = self._commit_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            if self._generation.get(thread_id, 0) != generation:
                return
            # New turns may append messages. The original prefix must still
            # exist; a reset or deletion must not be resurrected by this task.
            count = await fetch_thread_message_count(self.checkpoint_store_path, thread_id)
            if count < result.source_message_count:
                status.update(state="cancelled")
                return
            try:
                await asyncio.to_thread(
                    commit_prepared_compression,
                    self.checkpoint_store_path, thread_id, result,
                )
            except RuntimeError as exc:
                if "version changed" not in str(exc):
                    raise
                status.update(state="cancelled")
                return
        status.update(state="completed", result={"saved_tokens": result.metadata.get("before_tokens", 0) - result.view_tokens})
        logger.info("background compression committed for %s through message %s",
                    thread_id, result.compacted_until)

    async def invalidate(self, thread_id: str) -> None:
        """Prevent an unfinished summary from recreating a reset session."""
        lock = self._commit_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            self._generation[thread_id] = self._generation.get(thread_id, 0) + 1
        task = self._tasks.pop(thread_id, None)
        self._pending.pop(thread_id, None)
        self._statuses.pop(thread_id, None)
        if task is not None:
            task.cancel()

    async def close(self) -> None:
        for thread_id in list(self._tasks):
            await self.invalidate(thread_id)
