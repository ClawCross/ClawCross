"""Talking to one agent, whatever runtime it lives in.

The gateway finds the runtime of an agent's driver (``agents.runtime``: WeBot in
``webot.driver``, the others in ``external``) and hands it the call:

* ``ask``     — send and wait for the reply;
* ``trigger`` — the system trigger: hand it over to be handled now, return at once;
* ``inbox``   — queue it; the runtime takes it when it can.

``chat`` is the OpenAI chat-completions call (``/v1/chat/completions``): the
runtime answers it when it speaks the protocol, otherwise the agent is asked.

The agent answers, if at all, through the conversation it was told about. Each
runtime's control plane (``status``, ``control``, ``history``, ``destroy``) is its own.

The runtimes live in the Agent service; other processes reach agents over its
HTTP entrances (``agents.client``).
"""

from __future__ import annotations

import logging
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Callable

from agents.messages import AgentMessage, AgentReply, DeliveryReceipt, normalize_run_mode
from agents.runtime import ControlError, Runtime
from agents.store import ACPX, HTTP, LLM, WEBOT, Agent, AgentStore

logger = logging.getLogger(__name__)

from common.runtime_paths import PROJECT_ROOT  # noqa: E402

_PROJECT_ROOT = str(PROJECT_ROOT)


def cli_entry(user: str) -> str:
    """Use the running project's Python; no uv cache writes in native sandboxes."""
    return f'{shlex.quote(sys.executable)} {shlex.quote(str(PROJECT_ROOT / "src" / "cli" / "cli.py"))} -u {shlex.quote(user)}'


def reply_channel(agent: Agent, conversation_id: str) -> str:
    """How this agent posts into a ClawCross conversation: a tool for WeBot, the CLI otherwise."""
    if agent.driver == WEBOT:
        return (f'send_to_group(group_id="{conversation_id}", content="你的回复")'
                "（username 与 source_session 自动注入，不要手动填写）")
    if agent.driver == ACPX and ((agent.config.get('meta') or {}).get('acp') or {}).get('clawcross_tools', True):
        args = json.dumps({'group_id': conversation_id, 'content': '你的回复'}, ensure_ascii=False)
        return f'通过 ClawCross MCP tool_call 调用 send_to_group，arguments_json={args}；身份自动注入。'
    return (f'{cli_entry(agent.owner)} '
            f"groups send --group-id {shlex.quote(conversation_id)} --agent {shlex.quote(agent.agent_id)} "
            "--message '你的回复'")


class AgentGateway:
    """``runtimes`` adds or replaces runtimes by driver: WeBot's is the Agent service's,
    which runs WeBot."""

    def __init__(self, *, store: AgentStore | None = None, runtimes: dict[str, Runtime] | None = None):
        from external.acp import AcpRuntime
        from external.http import HttpRuntime
        from external.llm import LlmRuntime

        self.runtimes: dict[str, Runtime] = {
            ACPX: AcpRuntime(store),
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
        enabled_tools: list[str] | None = None,
        response_format: dict | None = None,
        timeout: float | None = None,
    ) -> AgentReply:
        """Send *msg* and wait for the reply. ``timeout`` in seconds; ``NO_TIMEOUT`` waits indefinitely.

        ``response_format`` is an OpenAI ``response_format``; each runtime enforces
        it as it can, or not at all.
        """
        context = {"teams": agent.teams, **(context or {})}
        try:
            return await self.runtime(agent).ask(agent, msg, context=context, mode=normalize_run_mode(mode),
                                                 enabled_tools=enabled_tools, response_format=response_format,
                                                 timeout=timeout)
        except Exception as exc:
            logger.exception("ask %s failed", agent.agent_id)
            return AgentReply(ok=False, error=f"{type(exc).__name__}: {exc}")

    async def chat(self, agent: Agent, request: Any) -> Any:
        """An OpenAI chat completion (``agents.openai.ChatCompletionRequest``), streamed or
        not: the runtime's own when it speaks the protocol, otherwise the agent is asked."""
        runtime = self.runtime(agent)
        if runtime.chat is not None:
            return await runtime.chat(agent, request)
        from agents.openai import answer_by_asking

        return await answer_by_asking(self, agent, request)

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
        context = {"teams": agent.teams, **(context or {})}
        return await self.runtime(agent).trigger(agent, msg, context=context, mode=normalize_run_mode(mode),
                                                 coalesce_key=coalesce_key, on_complete=on_complete)

    async def inbox(
        self,
        agent: Agent,
        msg: AgentMessage,
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        on_complete: Callable[[AgentReply], Any] | None = None,
    ) -> DeliveryReceipt:
        """Queue *msg* (from ``msg.sender``): WeBot takes it when the session is free and
        runs it in the session's own mode; a runtime without an inbox is handed it at
        once, in *mode*."""
        context = {"teams": agent.teams, **(context or {})}
        return await self.runtime(agent).inbox(agent, msg, context=context, mode=normalize_run_mode(mode),
                                               on_complete=on_complete)

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

    async def history(self, agent: Agent, limit: int = 200, *, source: str = 'clawcross') -> list[dict[str, Any]]:
        """The agent's own conversation, oldest first: ``[{role, content, tool_calls?}]``."""
        if source == 'acpx':
            read = getattr(self.runtime(agent), 'transport_history', None)
            if read is None:
                raise ControlError('This Agent does not use acpx')
            return await read(agent, limit)
        if source != 'clawcross':
            raise ControlError('Unknown history source')
        return await self.runtime(agent).history(agent, limit)

    async def fork(self, parent: Agent, child: Agent) -> int:
        """Start *child* from *parent*'s completed conversation; how many messages it got."""
        return await self.runtime(parent).fork(parent, child)

    async def destroy(self, agent: Agent) -> None:
        """Release what the runtime holds for an agent that is being deleted."""
        try:
            await self.runtime(agent).destroy(agent)
        except Exception as exc:
            logger.exception("cleanup of %s failed", agent.agent_id)
            if agent.driver == WEBOT:
                raise ControlError(f"WeBot cleanup failed: {exc}") from exc


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
