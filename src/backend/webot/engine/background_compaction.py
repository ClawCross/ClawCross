"""Prepare conversation summaries after a turn without delaying the next reply."""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any

from langchain_core.messages import BaseMessage

from webot.checkpoint_repository import fetch_thread_message_count
from webot.compression import apply_compression, commit_prepared_compression, make_llm_summarizer
from webot.policy import get_tool_policy, run_tool_policy_hooks
from webot.runtime import effective_session_mode
from webot.runtime_settings import ContextSettings

logger = logging.getLogger("webot.background_compaction")


class BackgroundCompressionManager:
    """Keep one preparation task per session and serialize commits with reset."""

    def __init__(self, checkpoint_store_path: str) -> None:
        self.checkpoint_store_path = checkpoint_store_path
        self._tasks: dict[str, asyncio.Task] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._generation: dict[str, int] = {}
        self._commit_locks: dict[str, asyncio.Lock] = {}

    def schedule(
        self, *, user_id: str, session_id: str, messages: list[BaseMessage],
        history_token_budget: int, preserve_recent: int, settings: ContextSettings,
        measured_input_tokens: int = 0, measured_budget: int = 0,
    ) -> None:
        if not settings.auto_compact or not messages or history_token_budget <= 0:
            return
        thread_id = f"{user_id}#{session_id}"
        request = {
            "user_id": user_id, "session_id": session_id,
            "messages": copy.deepcopy(messages), "history_token_budget": history_token_budget,
            "preserve_recent": preserve_recent, "settings": settings,
            "measured_input_tokens": measured_input_tokens,
            "measured_budget": measured_budget,
        }
        current = self._tasks.get(thread_id)
        if current is not None and not current.done():
            self._pending[thread_id] = request
            return
        generation = self._generation.get(thread_id, 0)
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
                    logger.warning("background compression failed for %s: %s", thread_id, error)

        task.add_done_callback(finished)

    async def _prepare_and_commit(
        self, *, thread_id: str, user_id: str, session_id: str,
        messages: list[BaseMessage], history_token_budget: int,
        preserve_recent: int, settings: ContextSettings,
        measured_input_tokens: int, measured_budget: int, generation: int,
    ) -> None:
        # Start before the foreground 90% watermark so the next user turn can
        # use an already finished summary. Explicit trigger settings win.
        early_trigger = settings.trigger_tokens or max(1, int(history_token_budget * 0.70))
        target = settings.target_tokens or max(1, int(history_token_budget * 0.55))
        target = min(target, max(1, early_trigger - 1))
        prepared_settings = settings.model_copy(update={
            "trigger_tokens": early_trigger, "target_tokens": target,
        })

        def prepare():
            snapshot = copy.deepcopy(messages)

            def before_summary() -> None:
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
                    model=settings.summarizer_model or None,
                    max_output_tokens=settings.summary_tokens,
                    input_token_budget=settings.summarizer_input_tokens,
                    preserve_instructions=settings.preserve_instructions,
                ),
                measured_input_tokens=measured_input_tokens,
                measured_budget=measured_budget,
                settings=prepared_settings,
                persist=False,
                before_summary=before_summary,
            )

        result = await asyncio.to_thread(prepare)
        if not result.triggered:
            return

        lock = self._commit_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            if self._generation.get(thread_id, 0) != generation:
                return
            # New turns may append messages. The original prefix must still
            # exist; a reset or deletion must not be resurrected by this task.
            count = await fetch_thread_message_count(self.checkpoint_store_path, thread_id)
            if count < result.source_message_count:
                return
            try:
                await asyncio.to_thread(
                    commit_prepared_compression,
                    self.checkpoint_store_path, thread_id, result,
                )
            except RuntimeError as exc:
                if "version changed" not in str(exc):
                    raise
                return
        logger.info("background compression committed for %s through message %s",
                    thread_id, result.compacted_until)

    async def invalidate(self, thread_id: str) -> None:
        """Prevent an unfinished summary from recreating a reset session."""
        lock = self._commit_locks.setdefault(thread_id, asyncio.Lock())
        async with lock:
            self._generation[thread_id] = self._generation.get(thread_id, 0) + 1
        task = self._tasks.pop(thread_id, None)
        self._pending.pop(thread_id, None)
        if task is not None:
            task.cancel()

    async def close(self) -> None:
        for thread_id in list(self._tasks):
            await self.invalidate(thread_id)
