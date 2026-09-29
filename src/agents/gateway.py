"""Talking to one agent, whatever runtime it lives in.

The gateway finds the runtime of an agent's driver (``agents.runtime``: WeBot in
``webot.driver``, the others in ``external``) and hands it the call:

* ``ask``     — send and wait for the reply;
* ``trigger`` — the system trigger: hand it over to be handled now, return at once;
* ``inbox``   — queue it; the runtime takes it when it can.

The agent answers, if at all, through the conversation it was told about. Each
runtime's control plane (``status``, ``control``, ``history``) is its own.

``persona_agent`` is a single model call with a persona (nothing stored);
``temp_session_agent`` a throwaway WeBot session with tools, deleted with ``discard``.
"""

from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Any, Callable

from agents.messages import AgentMessage, AgentReply, DeliveryReceipt, normalize_run_mode
from agents.runtime import ControlError, Runtime
from agents.store import ACPX, HTTP, LLM, OPENCLAW, TEMP_SESSION_PREFIX, WEBOT, Agent, AgentStore, get_store

logger = logging.getLogger(__name__)

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])


def persona_agent(owner: str, name: str, *, persona: str = "", team: str = "", llm: dict | None = None) -> Agent:
    """A persona for one call: no tools, no memory. *llm*: model, api_key, base_url, provider, temperature, max_tokens."""
    return Agent(agent_id="", owner=owner, name=name, driver=LLM,
                 config={"persona": persona, "team": team, "llm": dict(llm or {})})


def temp_session_agent(owner: str, name: str, session: str, *, persona: str = "", team: str = "",
                       llm: dict | None = None) -> Agent:
    """A throwaway WeBot session (``tmp__…``) for a persona that needs tools; *llm* overrides its model."""
    if not session.startswith(TEMP_SESSION_PREFIX) or len(session) <= len(TEMP_SESSION_PREFIX):
        raise ValueError(f"not a temporary session: {session!r}")
    config: dict[str, Any] = {"persona": persona, "team": team}
    if llm:
        config["llm"] = dict(llm)
    return Agent(agent_id=session, owner=owner, name=name, driver=WEBOT, config=config)


def reply_channel(agent: Agent, conversation_id: str) -> str:
    """How this agent posts into a ClawCross conversation: a tool for WeBot, the CLI otherwise."""
    if agent.driver == WEBOT:
        return (f'send_to_group(group_id="{conversation_id}", content="你的回复")'
                "（username 与 source_session 自动注入，不要手动填写）")
    return (f"cd {shlex.quote(_PROJECT_ROOT)} && uv run scripts/cli.py -u {shlex.quote(agent.owner)} "
            f"groups send --group-id {shlex.quote(conversation_id)} --agent {shlex.quote(agent.agent_id)} "
            "--message '你的回复'")


class AgentGateway:
    """``runtimes`` replaces the runtime of a driver (the Agent service gives WeBot its engine)."""

    def __init__(self, *, store: AgentStore | None = None, runtimes: dict[str, Runtime] | None = None):
        from external.acp import AcpRuntime
        from external.http import HttpRuntime
        from external.llm import LlmRuntime
        from external.openclaw import OpenclawRuntime
        from webot.driver import WebotRuntime

        self._store = store
        self.runtimes: dict[str, Runtime] = {
            WEBOT: WebotRuntime(),
            ACPX: AcpRuntime(store),
            OPENCLAW: OpenclawRuntime(store),
            HTTP: HttpRuntime(store),
            LLM: LlmRuntime(),
            **(runtimes or {}),
        }

    def runtime(self, agent: Agent) -> Runtime:
        try:
            return self.runtimes[agent.driver]
        except KeyError:
            raise ControlError(f"unsupported driver: {agent.driver}") from None

    # ── calls ────────────────────────────────────────────────────────────

    async def ask(
        self,
        agent: Agent,
        msg: AgentMessage,
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        tools: list[str] | None = None,
        response_format: dict | Any | None = None,
        timeout: float | None = None,
    ) -> AgentReply:
        """Send *msg* and wait for the reply. ``timeout`` in seconds; ``NO_TIMEOUT`` waits indefinitely.

        ``response_format`` is an OpenAI ``response_format`` dict or a Pydantic model;
        each runtime takes it in the form it can enforce, or not at all.
        """
        context = {"team": agent.config.get("team", ""), **(context or {})}
        try:
            return await self.runtime(agent).ask(agent, msg, context=context, mode=normalize_run_mode(mode),
                                                 tools=tools, response_format=response_format, timeout=timeout)
        except Exception as exc:
            logger.exception("ask %s failed", agent.agent_id)
            return AgentReply(ok=False, error=f"{type(exc).__name__}: {exc}")

    async def trigger(
        self,
        agent: Agent,
        msg: AgentMessage,
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        coalesce_key: str | None = None,
        on_complete: Callable[[AgentReply], Any] | None = None,
    ) -> DeliveryReceipt:
        """Hand *msg* over to be handled now, without waiting for an answer. A runtime
        without a queue of its own is asked in the background, its direct reply handed
        to *on_complete* (the agent speaks through the conversation's own channel)."""
        context = {"team": agent.config.get("team", ""), **(context or {})}
        return await self.runtime(agent).trigger(agent, msg, context=context, mode=normalize_run_mode(mode),
                                                 coalesce_key=coalesce_key, on_complete=on_complete)

    async def inbox(self, agent: Agent, msg: AgentMessage, *,
                    on_complete: Callable[[AgentReply], Any] | None = None) -> DeliveryReceipt:
        """Queue *msg* (from ``msg.sender``): WeBot takes it when the session is free; a
        runtime without an inbox is handed it at once."""
        context = {"team": agent.config.get("team", "")}
        return await self.runtime(agent).inbox(agent, msg, context=context, on_complete=on_complete)

    async def discard(self, agent: Agent) -> bool:
        """Delete a temporary session agent: its session and its record."""
        if not agent.agent_id.startswith(TEMP_SESSION_PREFIX):
            raise ValueError(f"{agent.name} is not temporary")
        try:
            await self.runtime(agent).destroy(agent)
        except Exception as exc:
            logger.warning("discarding %s#%s failed: %s", agent.owner, agent.agent_id, exc)
            return False
        (self._store or get_store()).delete(agent.owner, agent.agent_id)
        return True

    # ── control plane ────────────────────────────────────────────────────

    def actions(self, agent: Agent) -> list[str]:
        return ["status", *self.runtime(agent).controls]

    async def status(self, agent: Agent) -> dict[str, Any]:
        base = {"actions": self.actions(agent)}
        try:
            return {**base, **await self.runtime(agent).status(agent)}
        except Exception as exc:  # a status probe never fails the listing
            return {**base, "state": "unknown", "detail": str(exc)}

    def is_busy(self, agent: Agent) -> bool:
        """Whether the agent is still working; a runtime that cannot tell counts as busy."""
        return self.runtime(agent).is_busy(agent)

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        if action == "status":
            return await self.status(agent)
        if action not in self.runtime(agent).controls:
            raise ControlError(f"{agent.platform} agents do not support {action}")
        return await self.runtime(agent).control(agent, action)

    async def history(self, agent: Agent, limit: int = 200) -> list[dict[str, Any]]:
        """The agent's own conversation, oldest first: ``[{role, content, tool_calls?}]``."""
        return await self.runtime(agent).history(agent, limit)

    async def destroy(self, agent: Agent) -> None:
        """Release what the runtime holds for an agent that is being deleted."""
        try:
            await self.runtime(agent).destroy(agent)
        except Exception:
            logger.exception("cleanup of %s failed", agent.agent_id)


_GATEWAY: AgentGateway | None = None


def get_gateway() -> AgentGateway:
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = AgentGateway()
    return _GATEWAY


def set_gateway(gateway: AgentGateway) -> None:
    """The process's gateway: the Agent service's has WeBot's engine."""
    global _GATEWAY
    _GATEWAY = gateway
