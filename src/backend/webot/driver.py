"""WeBot as a runtime of the agent layer.

WeBot runs in the Agent service, and its runtime with it. ``ask``, ``trigger`` and
``inbox`` hand the session a message through its system trigger (``system.run``):
``ask`` is a turn after the session's current one, and waits for its reply;
``trigger`` a turn now; ``inbox`` an entry that names its sender, taken when the
session is free. ``chat`` is the chat window's OpenAI completion, streamed or not,
which takes over from the current turn (``chat_service.complete``). The control
plane reads the engine and the session's own operations (``sessions``: its
summary, messages, compaction, deletion). An agent's session is the WeBot thread
``<owner>#<agent_id>``.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from agents.messages import AgentMessage, AgentReply, DeliveryReceipt
from agents.runtime import NO_TIMEOUT, ControlError, Runtime
from agents.store import Agent

# How long ``ask`` waits when the caller does not say.
_DEFAULT_TIMEOUT = 500


def _fields(mode: str | None, enabled_tools: list[str] | None) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if enabled_tools is not None:
        fields["enabled_tools"] = list(enabled_tools)
    if mode:
        fields["session_mode"] = mode
        if mode == "chat":
            fields["enabled_tools"] = []  # chat: no tool calls at all
    return fields


class WebotRuntime(Runtime):
    controls = ("cancel", "reset", "compact", "deliver_inbox", "compact_async", "compact_status")

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

    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, enabled_tools, response_format, timeout) -> AgentReply:
        from webot.api.system_models import SystemTriggerRequest

        text = f"[来自调度方的指令]\n{msg.instructions}\n\n---\n{msg.text}" if msg.instructions else msg.text
        req = SystemTriggerRequest(
            user_id=agent.owner, session_id=agent.agent_id, text=text, attachments=list(msg.attachments) or None,
            response_format=response_format, llm_override=agent.config.get("llm") or None, wait_reply=True,
            groups=context.get("groups") or [],
            **_fields(mode, enabled_tools),
        )
        turn = asyncio.ensure_future(self.system.run(req))
        turn.add_done_callback(lambda t: t.cancelled() or t.exception())  # a turn outliving its caller
        wait = None if timeout == NO_TIMEOUT else (timeout if timeout is not None else _DEFAULT_TIMEOUT)
        try:
            # Waiting ends; the turn itself goes on, like any turn of the session.
            result = await asyncio.wait_for(asyncio.shield(turn), wait)
        except TimeoutError:
            return AgentReply(ok=False, error=f"no reply within {wait:g}s")
        return AgentReply(ok=True, content=str(result.get("reply") or ""))

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
            groups=context.get("groups") or [],
            **_fields(mode, None),
        ))
        return DeliveryReceipt(accepted=True)

    async def inbox(self, agent: Agent, msg: AgentMessage, *, context, mode, on_complete) -> DeliveryReceipt:
        """Taken when the session is free, in the session's own mode. *context* may name
        the sender's user (``source_user``, when another) and a label (``source_label``)."""
        from webot.api.system_models import SystemTriggerRequest

        await self.system.run(SystemTriggerRequest(
            user_id=agent.owner, session_id=agent.agent_id, text=msg.text,
            inbox_source_session=msg.sender or "system", inbox_summary=msg.summary,
            inbox_source_user=str(context.get("source_user") or ""),
            inbox_source_label=str(context.get("source_label") or ""),
            attachments=list(msg.attachments) or None,
            groups=context.get("groups") or [],
        ))
        return DeliveryReceipt(accepted=True)

    # ── control plane ────────────────────────────────────────────────────

    def _thread_state(self, agent: Agent) -> dict[str, Any]:
        return self.engine.get_all_thread_status(f"{agent.owner}#").get(self.thread(agent), {})

    def is_busy(self, agent: Agent) -> bool:
        running = self.engine.list_active_task_keys(f"{agent.owner}#")
        return bool(self._thread_state(agent).get("busy")) or self.thread(agent) in set(running)

    async def fork(self, parent: Agent, child: Agent) -> int:
        """Copy completed conversation turns, the session's setting overrides and its
        mode into a new session.

        The child gets its own system prompt on first inference and starts with
        empty inbox, approvals, permits, runs, and compaction state.
        """
        from webot.profiles import is_subagent_session
        from webot.runtime_settings import runtime_settings_payload, save_runtime_settings
        from webot.runtime_store import get_session_mode, save_session_mode

        if is_subagent_session(parent.agent_id):
            raise ControlError("Fork of isolated subagents is not supported")
        source_thread = self.thread(parent)
        target_thread = self.thread(child)
        history: list[BaseMessage] = await self.engine._context_store.snapshot_context(source_thread)
        last_complete = 0
        for index, message in enumerate(history, start=1):
            if isinstance(message, AIMessage) and not message.tool_calls:
                last_complete = index
        if not last_complete:
            raise ValueError("The source agent has no completed conversation turn")
        await self.engine._context_store.append_messages(
            target_thread, [message.model_copy(deep=True) for message in history[:last_complete]],
        )
        try:
            overrides = runtime_settings_payload(parent.owner, parent.agent_id)["session_overrides"]
            if overrides:
                save_runtime_settings(child.owner, session_id=child.agent_id, settings=overrides)
            mode = get_session_mode(parent.owner, parent.agent_id).get("mode") or "execute"
            save_session_mode(child.owner, child.agent_id, mode=mode, reason=f"Fork of {parent.agent_id}")
        except Exception:
            # ``destroy`` does not reach the overrides: they live in the owner's settings file.
            with suppress(Exception):
                save_runtime_settings(child.owner, session_id=child.agent_id, settings={}, reset=True)
            raise
        return last_complete

    async def status(self, agent: Agent) -> dict[str, Any]:
        """Running or idle, and who started the running turn (``source``: user / system);
        the system messages waiting; the session's mode and context use; and what a
        session list shows of it (``title``, ``last_message``, ``message_count``, times),
        none before anyone has written to it."""
        from webot.runtime import effective_session_mode

        state = self._thread_state(agent)
        busy = self.is_busy(agent)
        compaction_status = getattr(self.sessions, "visible_compaction_status", None)
        return {
            "state": "running" if busy else "idle",
            "source": state.get("source", "") if busy else "",
            "pending": state.get("pending_system", 0),
            "mode": effective_session_mode(agent.owner, agent.agent_id),
            "context": await self.sessions.context_usage(agent.owner, agent.agent_id),
            "compaction": compaction_status(agent.owner, agent.agent_id) if callable(compaction_status) else {"state": "idle"},
            **await self.sessions.summary(agent.owner, agent.agent_id),
        }

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        engine, thread = self.engine, self.thread(agent)
        if action == "cancel":
            return {"cancelled": bool(await engine.cancel_task(thread))}
        if action == "compact":
            return await self.sessions.compact(agent.owner, agent.agent_id)
        if action == "compact_async":
            return self.sessions.start_compaction(agent.owner, agent.agent_id)
        if action == "compact_status":
            return self.sessions.compaction_status(agent.owner, agent.agent_id)
        if action == "deliver_inbox":
            # The session's inbox worker takes what is queued once the current turn ends.
            from webot.api.system_models import SystemTriggerRequest

            await self.system.run(SystemTriggerRequest(user_id=agent.owner, session_id=agent.agent_id, text="",
                                                       drain_inbox=True))
            return {"scheduled": True}
        if action != "reset":
            return await super().control(agent, action)
        from webot.checkpoint_repository import delete_thread_records
        from webot.runtime_store import delete_agent_runtime_db, get_session_mode, save_session_mode

        cancel_delivery = getattr(self.system, "cancel_session", None)
        if callable(cancel_delivery):
            await cancel_delivery(thread)
        cancel_compaction = getattr(self.sessions, "cancel_compaction", None)
        if callable(cancel_compaction):
            await cancel_compaction(agent.owner, agent.agent_id)
        await engine.cancel_task(thread)
        close = getattr(engine, "close_thread_checkpoint", None)
        if callable(close):
            await close(thread)
        if getattr(engine, "_db_path", ""):
            await delete_thread_records(engine._db_path, thread)
            mode = get_session_mode(agent.owner, agent.agent_id)["mode"]
            delete_agent_runtime_db(agent.owner, agent.agent_id)
            save_session_mode(agent.owner, agent.agent_id, mode=mode)
        forget = getattr(engine, "forget_thread_state", None)
        if callable(forget):
            forget(thread)
        return {"reset": True}

    async def history(self, agent: Agent, limit: int) -> list[dict[str, Any]]:
        """The session's messages: a user's as sent (images too), tool calls and results."""
        return (await self.sessions.messages(agent.owner, agent.agent_id))[-limit:]

    async def destroy(self, agent: Agent) -> None:
        cancel_delivery = getattr(self.system, "cancel_session", None)
        if callable(cancel_delivery):
            await cancel_delivery(self.thread(agent))
        await self.sessions.delete(agent.owner, agent.agent_id)
