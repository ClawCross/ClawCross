"""What the external runtimes share: the session named after the agent's id, the
identity prompt they are told, what the runtime already knows, and the log of
what was said (``external.history``)."""

from __future__ import annotations

import logging
import asyncio
import json
import shlex
import time
import uuid
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from agents.messages import AgentMessage, AgentReply, compose_text_prompt
from agents.store import OPENCLAW, Agent, AgentStore, get_store
from external import history

logger = logging.getLogger(__name__)

from common.runtime_paths import PROJECT_ROOT  # noqa: E402

_system_prompt: str | None = None
_turn_locks = weakref.WeakKeyDictionary()


def runtime_session(agent: Agent) -> str:
    """The agent's session inside an external runtime, named after its id; OpenClaw's
    also names which of its agents holds it."""
    key = f"clawcross-{agent.owner}-{agent.agent_id}"
    if agent.runtime.get("session_generation"):
        key += "-" + str(agent.runtime["session_generation"])
    if agent.driver == OPENCLAW:
        return f"agent:{agent.config.get('global_name') or 'main'}:{key}"
    return key


def _prompt_file(name: str) -> str:
    try:
        return (PROJECT_ROOT / "data" / "prompts" / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def identity_prompt(agent: Agent, context: dict[str, Any] | None = None, instructions: str = "") -> str:
    """The frozen first-turn identity, using the same fixed rules as WeBot."""
    from webot.profiles import frame_session_identity
    from webot.skills import build_user_profile_block

    global _system_prompt
    if _system_prompt is None:
        chat_rules = _prompt_file("conversation_rules.txt").replace(
            "具体用法见 send_to_group", "使用本轮提供的命令行发送方式")
        _system_prompt = "\n\n".join(p for p in (
            _prompt_file("base_system.txt").replace("{chat_rules}", chat_rules),
            _prompt_file("external_agent_system.txt"),
        ) if p)
    parts = [
        _system_prompt,
        frame_session_identity(agent.name, "", str(agent.config.get("persona") or "").strip()),
        build_user_profile_block(agent.owner),
        f"【ClawCross 会话】\nowner: {agent.owner}\nagent_id: {agent.agent_id}\n"
        f"命令行入口：cd {shlex.quote(str(PROJECT_ROOT))} && uv run scripts/cli.py -u {shlex.quote(agent.owner)} --help",
    ]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


@asynccontextmanager
async def turn(store: AgentStore | None, agent: Agent):
    """Serialize a session's turns and read its persisted negotiation state afresh."""
    selected_store = store or get_store()
    locks = _turn_locks.setdefault(asyncio.get_running_loop(), {})
    key = (selected_store.db_path, agent.owner, agent.agent_id)
    async with locks.setdefault(key, asyncio.Lock()):
        yield selected_store.require(agent.owner, agent.agent_id)


@dataclass(slots=True)
class PreparedTurn:
    text: str
    identity: str | None
    dynamic_context: dict[str, str]


def prepare_turn(agent: Agent, msg: AgentMessage, *, context: dict[str, Any], mode: str | None,
                 enabled_tools: list[str] | None, response_format: dict | None,
                 plain_text: bool = True) -> PreparedTurn:
    """Only new information travels in this turn's user text; never replay replies."""
    from webot.skills import build_user_skills_listing
    from webot.workflow_prompt import build_team_workflow_prompt

    teams = sorted({str(team).strip() for team in agent.teams if str(team).strip()})
    # Old successful sessions already received an identity before this state existed.
    known = bool(agent.runtime.get("negotiation_sent") or agent.runtime.get("identity_prompt")
                 or agent.runtime.get("last_used_at"))
    same_session = agent.runtime.get("negotiation_session", runtime_session(agent)) == runtime_session(agent)
    identity = None if known and same_session else identity_prompt(agent)
    rules = {
        "chat": "仅交流，不调用工具或命令。",
        "readonly": "只查看、读取和搜索；不修改文件、运行写入命令或发送消息。",
        "auto": "遵循原生工具审批与命令安全策略；不能自行绕过审批。",
        "bypass": "可使用本轮允许的工具；仍需遵循命令安全策略和用户授权范围。",
    }
    dynamic = {
        "teams": "\n".join(f"team: {team}" for team in teams),
        "skills": build_user_skills_listing(agent.owner, teams=teams, tool_mode="cli"),
        "workflows": "\n\n".join(filter(None, (build_team_workflow_prompt(agent.owner, team=team) for team in teams))),
        "instructions": msg.instructions.strip(),
        "mode": rules.get(mode or "", ""),
        "tools": "" if enabled_tools is None else "本轮允许的工具：" + (", ".join(enabled_tools) or "无"),
        "command_tools": ("以下定义是命令行工具的调用约定；使用已提供的命令，不编造命令，不输出 API tool_calls。\n"
                          + json.dumps(context["command_tools"], ensure_ascii=False, sort_keys=True)) if context.get("command_tools") else "",
        "reply_format": json.dumps(response_format, ensure_ascii=False, sort_keys=True) if response_format else "",
    }
    previous = agent.runtime.get("dynamic_context") or {}
    delta = [f"【本轮 {name}】\n{value or '此前提供的此项信息已撤销。'}"
             for name, value in dynamic.items() if previous.get(name, "") != value]
    text = compose_text_prompt(msg.text, msg.attachments) if plain_text else msg.text
    return PreparedTurn("\n\n".join(filter(None, [identity, *delta, text])), identity, dynamic)


def remember_turn(store: AgentStore | None, agent: Agent, prepared: PreparedTurn) -> None:
    changes = {"negotiation_sent": True, "negotiation_session": runtime_session(agent),
               "dynamic_context": prepared.dynamic_context}
    if prepared.identity is not None:
        changes.update(identity_prompt=prepared.identity, negotiated_at=time.time())
    remember(store, agent, **changes)


def remember(store: AgentStore | None, agent: Agent, **runtime: Any) -> None:
    """Record what this session knows on its Agent row, including temporary sessions."""
    if not agent.agent_id:
        return
    try:
        (store or get_store()).patch_runtime(
            agent.owner, agent.agent_id, {**runtime, "last_used_at": time.time()})
    except Exception:
        logger.exception("could not record the runtime state of %s", agent.agent_id)


def forget(store: AgentStore | None, agent: Agent, *, new_session: bool = False) -> None:
    """The runtime starts over: it has been told nothing (the identity is sent again)."""
    (store or get_store()).set_runtime(agent.owner, agent.agent_id,
                                     {"session_generation": uuid.uuid4().hex[:12]} if new_session else {})


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
