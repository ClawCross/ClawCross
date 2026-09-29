"""What a runtime offers the agent layer.

A runtime runs the sessions of agents: WeBot (``webot.driver``) and the external
ones (``external``: acp, openclaw, http, llm). The calls are the same for every
runtime:

* ``ask``     — send and wait for the reply;
* ``trigger`` — hand it over to be handled now, return at once;
* ``inbox``   — queue it; the runtime takes it when it can.

A runtime that speaks the OpenAI chat-completions protocol itself (streaming,
the caller's tools) also has ``chat``; any other is asked instead.

Each runtime's control plane is its own: ``controls`` names the actions it has,
``control`` runs one; ``status``, ``history`` and ``destroy`` (release everything
when the agent is deleted) complete it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from agents.messages import AgentMessage, AgentReply, DeliveryReceipt
from agents.store import Agent

logger = logging.getLogger(__name__)

# Pass as ``timeout`` to wait for as long as the agent takes (long execution tasks).
NO_TIMEOUT = float("inf")


class ControlError(RuntimeError):
    pass


class Runtime:
    controls: tuple[str, ...] = ()
    # ``async chat(agent, request)``: an OpenAI chat completion answered by the runtime
    # itself, streamed or not. None: it is asked the last user message (agents.openai).
    chat = None

    def __init__(self) -> None:
        self._background: set[asyncio.Task] = set()

    # ── calls ────────────────────────────────────────────────────────────

    async def ask(self, agent: Agent, msg: AgentMessage, *, context: dict[str, Any], mode: str | None,
                  enabled_tools: list[str] | None, response_format: Any, timeout: float | None) -> AgentReply:
        raise NotImplementedError

    async def trigger(self, agent: Agent, msg: AgentMessage, *, context: dict[str, Any], mode: str | None,
                      coalesce_key: str | None, on_complete: Callable[[AgentReply], Any] | None) -> DeliveryReceipt:
        """Without a queue of its own, a runtime is asked in the background; the direct
        reply goes to *on_complete* (the agent answers through its own channel)."""

        async def send() -> None:
            reply = AgentReply(ok=False, error="delivery did not complete")
            try:
                reply = await self.ask(agent, msg, context=context, mode=mode, enabled_tools=None,
                                       response_format=None, timeout=None)
                if not reply.ok:
                    logger.warning("trigger %s failed: %s", agent.agent_id, reply.error)
            except Exception as exc:
                logger.exception("trigger %s failed", agent.agent_id)
                reply = AgentReply(ok=False, error=f"{type(exc).__name__}: {exc}")
            finally:
                if on_complete is not None:
                    try:
                        result = on_complete(reply)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception("on_complete for %s failed", agent.agent_id)

        task = asyncio.create_task(send())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return DeliveryReceipt(accepted=True)

    async def inbox(self, agent: Agent, msg: AgentMessage, *, context: dict[str, Any], mode: str | None,
                    on_complete: Callable[[AgentReply], Any] | None) -> DeliveryReceipt:
        """Without an inbox of its own, a runtime is handed the message at once."""
        return await self.trigger(agent, msg, context=context, mode=mode, coalesce_key=None, on_complete=on_complete)

    # ── control plane ────────────────────────────────────────────────────

    async def status(self, agent: Agent) -> dict[str, Any]:
        return {"state": "unknown"}

    def is_busy(self, agent: Agent) -> bool:
        """Whether it is still working; a runtime that cannot tell counts as busy."""
        return True

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        raise ControlError(f"{agent.platform} agents do not support {action}")

    async def history(self, agent: Agent, limit: int) -> list[dict[str, Any]]:
        return []

    async def destroy(self, agent: Agent) -> None:
        """Release what the runtime holds for an agent that is being deleted."""
