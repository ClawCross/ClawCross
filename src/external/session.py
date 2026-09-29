"""What the external runtimes share: the session named after the agent's id, the
identity prompt they are told, what the runtime already knows, and the log of
what was said (``external.history``)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from agents.messages import AgentReply
from agents.store import OPENCLAW, Agent, AgentStore, get_store
from external import history

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_system_prompt: str | None = None


def runtime_session(agent: Agent) -> str:
    """The agent's session inside an external runtime, named after its id; OpenClaw's
    also names which of its agents holds it."""
    key = f"clawcross-{agent.owner}-{agent.agent_id}"
    if agent.driver == OPENCLAW:
        return f"agent:{agent.config.get('global_name') or 'main'}:{key}"
    return key


def _prompt_file(name: str) -> str:
    try:
        return (PROJECT_ROOT / "data" / "prompts" / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def identity_prompt(agent: Agent, context: dict[str, Any], instructions: str) -> str:
    """Who the agent is, as WeBot's system prompt says it: the chat rules, its own
    persona text, the owner's profile, its skills and the team's workflows, then
    the caller's instructions."""
    from webot.profiles import frame_session_identity
    from webot.skills import build_user_profile_block, build_user_skills_listing
    from webot.workflow_prompt import build_team_workflow_prompt

    global _system_prompt
    if _system_prompt is None:
        _system_prompt = "\n\n".join(p for p in (
            _prompt_file("external_agent_system.txt"), _prompt_file("conversation_rules.txt"),
        ) if p)
    team = str(context.get("team") or "")
    parts = [
        _system_prompt,
        frame_session_identity(agent.name, "", str(agent.config.get("persona") or "").strip()),
        build_user_profile_block(agent.owner),
        build_user_skills_listing(agent.owner, team=team, tool_mode="cli"),
        build_team_workflow_prompt(agent.owner, team=team),
        instructions,
    ]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


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


@dataclass(slots=True)
class Sent:
    """What came back from a runtime: its answer, or why there is none."""

    ok: bool
    content: str = ""
    error: str = ""
    raw: Any = None


async def exchange(agent: Agent, *, connect_type: str, prompt: Any, context: dict[str, Any],
                   send: Callable[[], Awaitable[Sent]]) -> AgentReply:
    """Send, and log both sides in the agent's exchange log (a failed log never fails the send)."""
    options = history.attach_history_context(
        {}, user_id=agent.owner, group_id=str(context.get("conversation_id") or ""), global_name=agent.agent_id)
    where = {"platform": agent.platform, "session_key": runtime_session(agent), "connect_type": connect_type}
    request_id = None
    try:
        request_id = await (await history.get_store()).record_send(prompt=prompt, options=options, **where)
    except Exception as exc:
        logger.warning("history record_send failed: %s", exc)
    try:
        sent = await send()
    except Exception as exc:
        sent = Sent(ok=False, error=str(exc))
    if request_id:
        try:
            await (await history.get_store()).record_recv(
                request_id=request_id, ok=sent.ok, content=sent.content, raw_response=sent.raw,
                error=sent.error or None, options=options, **where)
        except Exception as exc:
            logger.warning("history record_recv failed: %s", exc)
    return AgentReply(ok=sent.ok, content=sent.content, error=sent.error)


async def log(agent: Agent, limit: int) -> list[dict[str, Any]]:
    """What was said in the agent's session, oldest first: ``[{role, content}]``."""
    rows = await (await history.get_store()).list_messages(
        platform=agent.platform, session_key=runtime_session(agent), limit=5000)
    return [
        {"role": row.get("role") or ("user" if row.get("direction") == "send" else "assistant"),
         "content": row.get("content") or ""}
        for row in rows[-limit:]
    ]


async def drop_log(agent: Agent) -> None:
    await (await history.get_store()).delete_session(platform=agent.platform, session_key=runtime_session(agent))
