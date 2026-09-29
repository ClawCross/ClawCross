"""Codex, Claude Code, Gemini and other ACP tools, through the acpx CLI.

acpx keeps a queue per session, so a message sent while the agent is busy waits
its turn: the runtime needs no inbox of its own.
"""

from __future__ import annotations

import shutil
from typing import Any

from agents.messages import ACPX_OVERRIDES_BY_MODE, AgentMessage, AgentReply, compose_text_prompt
from agents.runtime import NO_TIMEOUT, ControlError, Runtime
from agents.store import Agent, AgentStore
from external import session


def adapter():
    from integrations.acpx_adapter import AcpxError, get_acpx_adapter
    from utils.runtime_paths import WORKSPACE_DIR

    if not shutil.which("acpx"):
        raise ControlError("acpx is not installed")
    try:
        return get_acpx_adapter(cwd=str(WORKSPACE_DIR / "acpx"))
    except AcpxError as exc:
        raise ControlError(str(exc)) from exc


def command_options(agent: Agent, *, long: bool) -> dict[str, Any]:
    """acpx options for a control command; only *long* ones get the agent's full timeout."""
    from integrations.acpx_adapter import acpx_options_from_agent

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

    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, tools, response_format, timeout) -> AgentReply:
        from integrations.acpx_adapter import acpx_options_from_agent
        from integrations.agent_sender import SendToAgentRequest, send_to_agent
        from utils.external_agent_history import attach_history_context
        from utils.runtime_paths import WORKSPACE_DIR

        options: dict[str, Any] = {
            "cwd": str(WORKSPACE_DIR / "acpx"),
            **acpx_options_from_agent(
                agent.config,
                overrides=ACPX_OVERRIDES_BY_MODE.get(mode) if mode else None,
                default_timeout_sec=int(timeout) if timeout and timeout != NO_TIMEOUT else 180,
            ),
            "reset_session": False,
            "identity_prompt": session.identity_prompt(agent, context, msg.instructions),
            "attachments": [dict(a) for a in msg.attachments] or None,
            "return_trace": True,
        }
        if timeout == NO_TIMEOUT:
            options["timeout_sec"] = None
        options = attach_history_context(
            options, user_id=agent.owner, group_id=str(context.get("conversation_id") or ""),
            global_name=agent.agent_id,
        )
        result = await send_to_agent(SendToAgentRequest(
            prompt=compose_text_prompt(msg.text, msg.attachments),
            connect_type="acp",
            platform=agent.platform,
            session=session.runtime_session(agent),
            options=options,
        ))
        if result.ok:
            session.remember(self._store, agent)  # acpx itself sends the identity to a new session
        return session.reply_of(result)

    async def status(self, agent: Agent) -> dict[str, Any]:
        sessions = await adapter().list_sessions(tool=agent.platform)
        key = session.runtime_session(agent)
        live = [s for s in sessions if s.get("name") == key and not s.get("closed")]
        return {"state": "online" if live else "idle", "sessions": live}

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        from integrations.acpx_adapter import AcpxError

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
        return await session.history(agent, limit)

    async def destroy(self, agent: Agent) -> None:
        acpx, key = adapter(), session.runtime_session(agent)
        await acpx.close_session(
            tool=agent.platform, session_key=key, acpx_session=acpx.to_acpx_session_name(tool=agent.platform, session_key=key),
            **command_options(agent, long=False),
        )
        await session.drop_history(agent)
