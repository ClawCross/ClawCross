"""Small, purpose-built runtime for ClawCross's two-step agent loop.

The application only needs a deterministic ``model -> tools -> model`` loop.
This module provides the tiny subset of the former LangGraph compiled-graph API
used by the HTTP services, without pulling in the LangGraph execution engine.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessageChunk


class AgentRecursionError(RuntimeError):
    """Raised when an agent run exceeds its configured node-step limit."""


@dataclass(frozen=True)
class StateSnapshot:
    values: dict[str, Any]


def _merge_state(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Apply LangGraph-compatible state semantics: append messages, replace scalars."""
    merged = dict(base)
    for key, value in update.items():
        if key == "messages":
            merged[key] = list(merged.get(key) or []) + list(value or [])
        else:
            merged[key] = value
    return merged


class _EventCallback(AsyncCallbackHandler):
    def __init__(self, emit: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
        self._emit = emit
        self._tool_names: dict[Any, str] = {}

    async def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        # LangChain passes a ChatGenerationChunk here (fields: text/message/
        # generation_info/type), not the AIMessageChunk itself — the actual
        # message content lives at .message. Passing the wrapper straight
        # through silently drops every token (no .content attribute), which
        # is why streaming looked like it was firing (on_llm_new_token called
        # per token) but no text ever reached the consumer.
        raw_chunk = kwargs.get("chunk")
        message_chunk = getattr(raw_chunk, "message", None)
        if message_chunk is None:
            message_chunk = raw_chunk if hasattr(raw_chunk, "content") else AIMessageChunk(content=token or "")
        await self._emit({
            "event": "on_chat_model_stream",
            "name": kwargs.get("name", "chat_model"),
            "data": {"chunk": message_chunk},
        })

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        **kwargs: Any,
    ) -> None:
        name = serialized.get("name") or kwargs.get("name", "tool")
        run_id = kwargs.get("run_id")
        if run_id is not None:
            self._tool_names[run_id] = name
        await self._emit({
            "event": "on_tool_start",
            "name": name,
            "data": {"input": kwargs.get("inputs", input_str)},
        })

    async def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        run_id = kwargs.get("run_id")
        name = self._tool_names.pop(run_id, kwargs.get("name", "tool"))
        await self._emit({
            "event": "on_tool_end",
            "name": name,
            "data": {"output": output},
        })


class LightweightAgentRuntime:
    """Compiled-agent compatible facade backed by an append-only context store."""

    def __init__(
        self,
        *,
        call_model: Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]],
        call_tools: Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]],
        should_continue: Callable[[dict[str, Any]], bool],
        context_store: Any,
    ) -> None:
        self._call_model = call_model
        self._call_tools = call_tools
        self._should_continue = should_continue
        self._context_store = context_store

    @staticmethod
    def _thread_id(config: dict[str, Any] | None) -> str:
        configurable = (config or {}).get("configurable") or {}
        thread_id = str(configurable.get("thread_id") or "")
        if not thread_id:
            raise ValueError("config.configurable.thread_id is required")
        return thread_id

    async def _run(
        self,
        input_state: dict[str, Any],
        config: dict[str, Any],
        emit: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        thread_id = self._thread_id(config)
        history = await self._context_store.load_context(thread_id)
        state = _merge_state({"messages": history}, input_state)
        # Persist the incoming user/tool message before the potentially long LLM
        # call so cancellation and error recovery can still see the full turn.
        await self._context_store.append_messages(thread_id, input_state.get("messages") or [])
        recursion_limit = max(1, int(config.get("recursion_limit", 500)))
        steps = 0

        async def run_node(name: str, fn: Callable) -> None:
            nonlocal state, steps
            if steps >= recursion_limit:
                raise AgentRecursionError(f"agent recursion limit {recursion_limit} reached")
            steps += 1
            if emit:
                await emit({"event": "on_chain_start", "name": name, "data": {"input": state}})
            update = await fn(state, config)
            state = _merge_state(state, update or {})
            await self._context_store.append_messages(
                thread_id,
                (update or {}).get("messages") or [],
            )
            if emit:
                await emit({"event": "on_chain_end", "name": name, "data": {"output": update or {}}})

        while True:
            await run_node("chatbot", self._call_model)
            if not self._should_continue(state):
                return state
            await run_node("tools", self._call_tools)

    async def ainvoke(
        self,
        input_state: dict[str, Any],
        config: dict[str, Any],
        **_: Any,
    ) -> dict[str, Any]:
        return await self._run(input_state, config)

    async def astream_events(
        self,
        input_state: dict[str, Any],
        config: dict[str, Any],
        **_: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any] | BaseException | None] = asyncio.Queue()

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        callback = _EventCallback(emit)
        run_config = dict(config)
        callbacks = list(config.get("callbacks") or [])
        run_config["callbacks"] = [*callbacks, callback]

        async def produce() -> None:
            try:
                await self._run(input_state, run_config, emit)
            except BaseException as exc:
                await queue.put(exc)
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            if not producer.done():
                producer.cancel()
            try:
                await producer
            except asyncio.CancelledError:
                pass

    async def aget_state(self, config: dict[str, Any]) -> StateSnapshot:
        messages = await self._context_store.load_context(self._thread_id(config))
        return StateSnapshot(values={"messages": messages})

    async def aupdate_state(
        self,
        config: dict[str, Any],
        values: dict[str, Any],
    ) -> dict[str, Any]:
        thread_id = self._thread_id(config)
        state = _merge_state(
            {"messages": await self._context_store.load_context(thread_id)},
            values,
        )
        await self._context_store.append_messages(thread_id, values.get("messages") or [])
        return state
