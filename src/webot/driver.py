"""WeBot as a runtime of the agent layer.

WeBot runs in the Agent service, and its runtime with it: ``ask`` runs a turn
(``chat_service.answer``) and ``chat`` a whole OpenAI chat completion, streamed
or not (``chat_service.complete``); ``trigger`` and ``inbox`` hand the session a system message
(``system.run``; an inbox entry names its sender and is taken when the session
is free); ``destroy`` deletes the session (``sessions.delete``). The control
plane reads the engine. An agent's session is the WeBot thread ``<owner>#<agent_id>``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agents.messages import AgentMessage, AgentReply, DeliveryReceipt, build_openai_content
from agents.runtime import NO_TIMEOUT, Runtime
from agents.store import Agent

# How long ``ask`` waits when the caller does not say.
_DEFAULT_TIMEOUT = 500


def _fields(mode: str | None, tools: list[str] | None) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if tools is not None:
        fields["enabled_tools"] = list(tools)
    if mode:
        fields["session_mode"] = mode
        if mode == "chat":
            fields["enabled_tools"] = []  # chat: no tool calls at all
    return fields


class WebotRuntime(Runtime):
    controls = ("cancel", "reset")

    def __init__(self, *, engine: Any, chat_service: Any, system: Any, sessions: Any):
        super().__init__()
        self.engine = engine
        self.chat_service = chat_service
        self.system = system
        self.sessions = sessions

    @staticmethod
    def thread(agent: Agent) -> str:
        return f"{agent.owner}#{agent.agent_id}"

    # ── calls ────────────────────────────────────────────────────────────

    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, tools, response_format, timeout) -> AgentReply:
        from fastapi import HTTPException

        from agents.openai import ChatCompletionRequest

        messages: list[dict] = []
        if msg.instructions:
            messages.append({"role": "system", "content": msg.instructions})
        messages.append({"role": "user", "content": build_openai_content(msg.text, msg.attachments)})
        req = ChatCompletionRequest(model="webot", messages=messages, response_format=response_format,
                                    llm_override=agent.config.get("llm") or None, **_fields(mode, tools))
        turn = asyncio.ensure_future(self.chat_service.answer(agent.owner, agent.agent_id, req))
        turn.add_done_callback(lambda t: t.cancelled() or t.exception())  # a turn outliving its caller
        wait = None if timeout == NO_TIMEOUT else (timeout if timeout is not None else _DEFAULT_TIMEOUT)
        try:
            # Waiting ends; the turn itself goes on, like any turn of the session.
            return AgentReply(ok=True, content=await asyncio.wait_for(asyncio.shield(turn), wait))
        except TimeoutError:
            return AgentReply(ok=False, error=f"no reply within {wait:g}s")
        except HTTPException as exc:
            return AgentReply(ok=False, error=str(exc.detail))

    async def chat(self, agent: Agent, request: Any) -> Any:
        """The chat window's call: streamed or not, with the caller's own tools; like a
        message typed in, it takes over from the session's current turn."""
        return await self.chat_service.complete(agent.owner, agent.agent_id, request)

    async def trigger(self, agent: Agent, msg: AgentMessage, *, context, mode, coalesce_key, on_complete) -> DeliveryReceipt:
        from webot.api.system_models import SystemTriggerRequest

        await self.system.run(SystemTriggerRequest(
            user_id=agent.owner,
            session_id=agent.agent_id,
            text=f"{msg.text}\n\n{msg.instructions}" if msg.instructions else msg.text,
            attachments=list(msg.attachments) or None,
            coalesce_key=coalesce_key or "",
            **_fields(mode, None),
        ))
        return DeliveryReceipt(accepted=True)

    async def inbox(self, agent: Agent, msg: AgentMessage, *, context, mode, on_complete) -> DeliveryReceipt:
        """Taken when the session is free, in the session's own mode."""
        from webot.api.system_models import SystemTriggerRequest

        await self.system.run(SystemTriggerRequest(
            user_id=agent.owner, session_id=agent.agent_id, text=msg.text,
            inbox_source_session=msg.sender or "system", inbox_summary=msg.summary,
            attachments=list(msg.attachments) or None,
        ))
        return DeliveryReceipt(accepted=True)

    # ── control plane ────────────────────────────────────────────────────

    def _thread_state(self, agent: Agent) -> dict[str, Any]:
        return self.engine.get_all_thread_status(f"{agent.owner}#").get(self.thread(agent), {})

    def is_busy(self, agent: Agent) -> bool:
        running = self.engine.list_active_task_keys(f"{agent.owner}#")
        return bool(self._thread_state(agent).get("busy")) or self.thread(agent) in set(running)

    async def status(self, agent: Agent) -> dict[str, Any]:
        return {
            "state": "running" if self.is_busy(agent) else "idle",
            "context": await self.sessions.context_usage(agent.owner, agent.agent_id),
            "pending": self._thread_state(agent).get("pending_system", 0),
        }

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        engine, thread = self.engine, self.thread(agent)
        if action == "cancel":
            return {"cancelled": bool(await engine.cancel_task(thread))}
        if action != "reset":
            return await super().control(agent, action)
        from utils.checkpoint_repository import delete_thread_records

        await engine.cancel_task(thread)
        close = getattr(engine, "close_thread_checkpoint", None)
        if callable(close):
            await close(thread)
        if getattr(engine, "_db_path", ""):
            await delete_thread_records(engine._db_path, thread)
        return {"reset": True}

    async def history(self, agent: Agent, limit: int) -> list[dict[str, Any]]:
        """The session's messages, tool calls included."""
        from services.llm_factory import extract_text

        snapshot = await self.engine.agent_app.aget_state({"configurable": {"thread_id": self.thread(agent)}})
        out: list[dict[str, Any]] = []
        for msg in (snapshot.values.get("messages", []) if snapshot and snapshot.values else []):
            kind = type(msg).__name__
            if kind == "HumanMessage":
                out.append({"role": "user", "content": extract_text(msg.content)})
            elif kind in ("AIMessage", "AIMessageChunk"):
                calls = [{"name": c.get("name", ""), "args": c.get("args", {})} for c in (getattr(msg, "tool_calls", None) or [])]
                content = extract_text(msg.content)
                if content or calls:
                    out.append({"role": "assistant", "content": content, **({"tool_calls": calls} if calls else {})})
            elif kind == "ToolMessage":
                out.append({"role": "tool", "content": extract_text(msg.content), "tool_name": getattr(msg, "name", "")})
        return out[-limit:]

    async def destroy(self, agent: Agent) -> None:
        await self.sessions.delete(agent.owner, agent.agent_id)
