"""What the external runtimes share: the session named after the agent's id, the
identity prompt they are told, what the runtime already knows, and the log of
what was said (``utils.external_agent_history``)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from agents.messages import AgentReply
from agents.store import OPENCLAW, Agent, AgentStore, get_store

logger = logging.getLogger(__name__)

PROJECT_ROOT = str(Path(__file__).resolve().parents[2])

_system_prompt: str | None = None


def runtime_session(agent: Agent) -> str:
    """The agent's session inside an external runtime, named after its id; OpenClaw's
    also names which of its agents holds it."""
    key = f"clawcross-{agent.owner}-{agent.agent_id}"
    if agent.driver == OPENCLAW:
        return f"agent:{agent.config.get('global_name') or 'main'}:{key}"
    return key


def identity_prompt(agent: Agent, context: dict[str, Any], instructions: str) -> str:
    """Who the agent is: the chat rules WeBot carries in its system prompt, its persona
    and the caller's instructions."""
    from integrations.acpx_adapter import load_external_agent_prompt_file, load_external_agent_system_prompt
    from integrations.external_persona import build_external_persona_prompt

    global _system_prompt
    if _system_prompt is None:
        _system_prompt = "\n\n".join(p for p in (
            load_external_agent_system_prompt(PROJECT_ROOT),
            load_external_agent_prompt_file(PROJECT_ROOT, "conversation_rules.txt"),
        ) if p)
    parts = [
        _system_prompt,
        build_external_persona_prompt(
            str(agent.config.get("persona") or ""), user_id=agent.owner, team=str(context.get("team") or ""),
        ),
        instructions,
    ]
    return "\n\n".join(p for p in parts if p).strip()


def remember(store: AgentStore | None, agent: Agent, **runtime: Any) -> None:
    """Record on the agent what its runtime now knows; temporary agents keep nothing."""
    if agent.temporary:
        return
    try:
        (store or get_store()).set_runtime(
            agent.owner, agent.agent_id, {**agent.runtime, **runtime, "last_used_at": time.time()})
    except Exception:
        logger.exception("could not record the runtime state of %s", agent.agent_id)


def forget(store: AgentStore | None, agent: Agent) -> None:
    """The runtime starts over: it has been told nothing (the identity is sent again)."""
    (store or get_store()).set_runtime(agent.owner, agent.agent_id, {})


def reply_of(result: Any) -> AgentReply:
    return AgentReply(ok=result.ok, content=result.content or "", error=result.error or "", meta=result.meta or {})


async def history(agent: Agent, limit: int) -> list[dict[str, Any]]:
    """What was said in the agent's session, oldest first: ``[{role, content}]``."""
    from utils.external_agent_history import get_store as history_store

    rows = await (await history_store()).list_messages(
        platform=agent.platform, session_key=runtime_session(agent), limit=5000)
    return [
        {"role": row.get("role") or ("user" if row.get("direction") == "send" else "assistant"),
         "content": row.get("content") or ""}
        for row in rows[-limit:]
    ]


async def drop_history(agent: Agent) -> None:
    from utils.external_agent_history import get_store as history_store

    await (await history_store()).delete_session(platform=agent.platform, session_key=runtime_session(agent))
