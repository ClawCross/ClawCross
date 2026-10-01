"""Codex, Claude Code, Gemini and other ACP tools, through the acpx CLI.

acpx keeps a queue per session, so a message sent while the agent is busy waits
its turn: the runtime needs no inbox of its own.
"""

from __future__ import annotations

import shutil
from typing import Any

from agents.messages import ACPX_OVERRIDES_BY_MODE, AgentMessage, AgentReply
from agents.runtime import NO_TIMEOUT, ControlError, Runtime
from agents.store import Agent, AgentStore, canonical_platform
from external import session


def adapter():
    from external.acpx import AcpxError, get_acpx_adapter

    if not shutil.which("acpx"):
        raise ControlError("acpx is not installed")
    try:
        return get_acpx_adapter()
    except AcpxError as exc:
        raise ControlError(str(exc)) from exc


def command_options(agent: Agent, *, long: bool) -> dict[str, Any]:
    """acpx options for a control command; only *long* ones get the agent's full timeout."""
    from external.acpx import acpx_options_from_agent

    policy = acpx_options_from_agent(agent.config, default_timeout_sec=180)
    return {
        "timeout_sec": policy["timeout_sec"] if long else min(policy["timeout_sec"], 60),
        "ttl_sec": policy["ttl_sec"],
        "approve_all": policy["approve_all"],
        "non_interactive_permissions": policy["non_interactive_permissions"],
    }


class AcpRuntime(Runtime):
    controls = ("cancel", "reset")

    def __init__(self, store: AgentStore | None = None) -> None:
        super().__init__()
        self._store = store

    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, enabled_tools, response_format, timeout) -> AgentReply:
        async with session.turn(self._store, agent) as current:
            return await self._ask_turn(current, msg, context=context, mode=mode, enabled_tools=enabled_tools,
                                        response_format=response_format, timeout=timeout)

    async def _ask_turn(self, agent: Agent, msg: AgentMessage, *, context, mode, enabled_tools, response_format, timeout) -> AgentReply:
        from external.acpx import AcpxError, acpx_options_from_agent, get_acpx_adapter

        run = acpx_options_from_agent(
            agent.config,
            overrides=ACPX_OVERRIDES_BY_MODE.get(mode) if mode else None,
            default_timeout_sec=int(timeout) if timeout and timeout != NO_TIMEOUT else 180,
        )
        if timeout == NO_TIMEOUT:
            run["timeout_sec"] = None
        prepared = session.prepare_turn(agent, msg, context=context, mode=mode,
                                        enabled_tools=enabled_tools, response_format=response_format)
        prompt = prepared.text

        async def send() -> session.Sent:
            try:
                trace = await get_acpx_adapter().prompt_with_trace(
                    tool=canonical_platform(agent.platform),
                    session_key=session.runtime_session(agent),
                    prompt_text=prompt,
                    reset_session=False,
                    system_prompt=None,
                    attachments=[dict(a) for a in msg.attachments] or None,
                    **run,
                )
            except (AcpxError, RuntimeError) as exc:
                return session.Sent(ok=False, error=str(exc))
            return session.Sent(ok=True, content=trace.text or "", raw={
                "messages": trace.messages, "tool_uses": trace.tool_uses, "tool_results": trace.tool_results,
            })

        reply = await session.exchange(agent, connect_type="acp", prompt=prompt, context=context, send=send)
        if reply.ok:
            session.remember_turn(self._store, agent, prepared)
        return reply

    async def status(self, agent: Agent) -> dict[str, Any]:
        sessions = await adapter().list_sessions(tool=agent.platform)
        key = session.runtime_session(agent)
        live = [s for s in sessions if s.get("name") == key and not s.get("closed")]
        return {"state": "online" if live else "idle", "sessions": live}

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        if action == "reset":
            async with session.turn(self._store, agent) as current:
                return await self._control(current, action)
        return await self._control(agent, action)

    async def _control(self, agent: Agent, action: str) -> dict[str, Any]:
        from external.acpx import AcpxError

        acpx, key = adapter(), session.runtime_session(agent)
        try:
            if action == "cancel":
                await acpx.ops_non_openclaw_cancel(
                    tool=agent.platform, session_key=key, **command_options(agent, long=False))
            elif action == "reset":
                await acpx.ops_non_openclaw_reset_session(
                    tool=agent.platform, session_key=key, **command_options(agent, long=True))
                session.forget(self._store, agent)
            else:
                return await super().control(agent, action)
        except AcpxError as exc:
            raise ControlError(str(exc)) from exc
        return {action: True}

    async def history(self, agent: Agent, limit: int) -> list[dict[str, Any]]:
        return await session.log(agent, limit)

    async def destroy(self, agent: Agent) -> None:
        acpx, key = adapter(), session.runtime_session(agent)
        await acpx.close_session(
            tool=agent.platform, session_key=key, acpx_session=acpx.to_acpx_session_name(tool=agent.platform, session_key=key),
            **command_options(agent, long=False),
        )
        await session.drop_log(agent)
