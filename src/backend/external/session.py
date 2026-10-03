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
from agents.store import ACPX, Agent, AgentStore, get_store
from external import history

logger = logging.getLogger(__name__)

from common.runtime_paths import PROJECT_ROOT  # noqa: E402

_turn_locks = weakref.WeakKeyDictionary()


def runtime_session(agent: Agent) -> str:
    """The agent's session inside an external runtime, named after its id.

    ``openclaw acp`` takes an OpenClaw gateway session key, which also names the
    OpenClaw agent that holds it: ``main``, or for an agent made before OpenClaw
    ran over ACP, the one kept in ``global_name``.
    """
    key = f"clawcross-{agent.owner}-{agent.agent_id}"
    if agent.runtime.get("session_generation"):
        key += "-" + str(agent.runtime["session_generation"])
    if agent.platform == "openclaw":
        return f"agent:{agent.config.get('global_name') or 'main'}:{key}"
    return key


def _prompt_file(name: str) -> str:
    try:
        return (PROJECT_ROOT / "data" / "prompts" / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def identity_sections(agent: Agent) -> dict[str, str]:
    """Read live identity sources; external sessions receive only changed blocks."""
    from webot.profiles import frame_session_identity
    from webot.skills import build_user_profile_block
    from webot.soul import build_soul_prompt
    from agents.gateway import cli_entry

    from common.agent_prompt import identity_sections as shared_identity
    sections = shared_identity(
        base=_prompt_file("base_system.txt"), conversation=_prompt_file("conversation_rules.txt"),
        persona=frame_session_identity(agent.name, "", str(agent.config.get("persona") or "").strip()),
        user_profile=build_user_profile_block(agent.owner), soul=build_soul_prompt(agent.owner),
        session=f"【ClawCross 会话】\nowner: {agent.owner}\nagent_id: {agent.agent_id}")
    return {"base_rules": sections.pop("base_rules"),
            "external_rules": _prompt_file("external_agent_system.txt"), **sections}


def _join_identity(sections: dict[str, str]) -> str:
    return "\n\n".join(p.strip() for p in sections.values() if p and p.strip())


def identity_prompt(agent: Agent, context: dict[str, Any] | None = None, instructions: str = "") -> str:
    return _join_identity(identity_sections(agent))


def delivered_context(agent: Agent) -> dict[str, str]:
    """One delivery state; migrate old identity snapshots without resetting native memory."""
    if agent.runtime.get('negotiation_session', runtime_session(agent)) != runtime_session(agent):
        return {}
    previous = dict(agent.runtime.get('dynamic_context') or {})
    if agent.runtime.get('prompt_context_version') != 2:
        previous.update({'identity_' + name: value for name, value in
                         (agent.runtime.get('identity_sections') or {}).items()})
    return previous


@asynccontextmanager
async def turn(store: AgentStore | None, agent: Agent):
    """Serialize a session's turns and read its persisted negotiation state afresh."""
    selected_store = store or get_store()
    locks = _turn_locks.setdefault(asyncio.get_running_loop(), {})
    key = (selected_store.db_path, agent.owner, agent.agent_id)
    async with locks.setdefault(key, asyncio.Lock()):
        yield selected_store.require(agent.owner, agent.agent_id)


def is_busy(store: AgentStore | None, agent: Agent) -> bool:
    """Inspect our turn lock without starting an external adapter."""
    selected_store = store or get_store()
    locks = _turn_locks.get(asyncio.get_running_loop(), {})
    lock = locks.get((selected_store.db_path, agent.owner, agent.agent_id))
    return bool(lock and lock.locked())


@dataclass(slots=True)
class PreparedTurn:
    text: str
    identity: str | None
    dynamic_context: dict[str, str]
    identity_sections: dict[str, str]
    runtime_context: str = ""
    user_input: str = ""


def build_dynamic_context(agent: Agent, msg: AgentMessage, *, context: dict[str, Any],
                          mode: str | None, enabled_tools: list[str] | None,
                          response_format: dict | None, identity: dict[str, str] | None = None) -> dict[str, str]:
    from webot.skills import build_user_skills_listing
    from webot.workflow_prompt import build_team_workflow_prompt
    from webot.runtime import build_session_mode_message
    from common.agent_prompt import render_team_skill_context
    from common.conversation_context import group_memberships, render_group_metadata, current_group_metadata
    from agents.gateway import cli_entry
    teams = sorted({str(team).strip() for team in agent.teams if str(team).strip()})
    connected = agent.driver == ACPX and ((agent.config.get('meta') or {}).get('acp') or {}).get('clawcross_tools', True)
    memberships = group_memberships(agent.owner, agent.agent_id)
    dynamic = {
        **{"identity_" + name: value for name, value in (identity if identity is not None else identity_sections(agent)).items()},
        "cli_entry": "" if connected else f"当前命令入口：{cli_entry(agent.owner)} --help；替代此前提供的旧命令路径。",
        "teams": render_team_skill_context(teams),
        "groups": render_group_metadata(current_group_metadata(context.get("groups") or [], memberships)),
        "group_memberships": render_group_metadata(memberships),
        "skills": build_user_skills_listing(agent.owner, teams=teams, tool_mode="mcp" if connected else "cli"),
        "workflows": "" if connected else "\n\n".join(filter(None, (build_team_workflow_prompt(agent.owner, team=team) for team in teams))),
        "instructions": msg.instructions.strip(),
        "mode": build_session_mode_message(mode) if mode else "",
        "tools": "" if enabled_tools is None else "本轮允许的工具：" + (", ".join(enabled_tools) or "无"),
        "tool_connector": ("ClawCross MCP 已开启：通过 tool_search 查询准确参数，再用 tool_call 调用；"
                           "身份由服务器注入，工具受当前模式、名单与审核约束。")
                          if connected
                          else 'ClawCross MCP 未开启；使用原生工具及明确提供的命令行入口。',
        "command_tools": ("以下定义是命令行工具的调用约定；使用已提供的命令，不编造命令，不输出 API tool_calls。\n"
                          + json.dumps(context["command_tools"], ensure_ascii=False, sort_keys=True)) if context.get("command_tools") else "",
        "reply_format": json.dumps(response_format, ensure_ascii=False, sort_keys=True) if response_format else "",
    }
    return dynamic


def prepare_turn(agent: Agent, msg: AgentMessage, *, context: dict[str, Any], mode: str | None,
                 enabled_tools: list[str] | None, response_format: dict | None,
                 plain_text: bool = True) -> PreparedTurn:
    """Only new information travels in this turn's user text; never replay replies."""
    from common.agent_prompt import render_section_updates
    sections = identity_sections(agent)
    previous = delivered_context(agent)
    dynamic = build_dynamic_context(agent, msg, context=context, mode=mode,
                                    enabled_tools=enabled_tools, response_format=response_format, identity=sections)
    delta = render_section_updates(previous, dynamic)
    text = compose_text_prompt(msg.text, msg.attachments) if plain_text else msg.text
    # Compatibility indicator only; identity content is sent through delta exactly once.
    identity = None if any(key.startswith('identity_') for key in previous) else _join_identity(sections)
    return PreparedTurn("\n\n".join(filter(None, [delta, text])), identity, dynamic, sections, delta, text)


def remember_turn(store: AgentStore | None, agent: Agent, prepared: PreparedTurn) -> None:
    changes = {"negotiation_sent": True, "negotiation_session": runtime_session(agent),
               "dynamic_context": prepared.dynamic_context, "prompt_context_version": 2}
    if prepared.identity is not None:
        changes.update(negotiated_at=time.time())
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
                   send: Callable[[], Awaitable[Sent]], prepared: PreparedTurn | None = None) -> AgentReply:
    """Send, and log both sides in the agent's exchange log (a failed log never fails the send)."""
    options = history.attach_history_context(
        {}, user_id=agent.owner, group_id=str(context.get("conversation_id") or ""), global_name=agent.agent_id)
    if prepared is not None:
        options.update(display_input=prepared.user_input, display_runtime_context=prepared.runtime_context)
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
    messages = []
    for row in rows[-limit:]:
        role = row.get('role') or ('user' if row.get('direction') == 'send' else 'assistant')
        content = row.get('content') or ''
        if row.get('direction') == 'error' and '{"jsonrpc"' in content:
            import re
            from external.acpx import public_command_error
            match = re.search(r'acpx failed \((\d+)\)', content)
            content = public_command_error(content[content.index('{"jsonrpc"'):],
                                           int(match[1]) if match else 1)
        if role == 'assistant' and content.lstrip().startswith('{"jsonrpc"'):
            # Older tool-only turns stored protocol output as assistant text.
            # Clean the presentation without changing the original audit log.
            from external.acpx import AcpxAdapter
            trace = AcpxAdapter._extract_trace(content)
            for call, result in zip(trace.tool_uses, trace.tool_results):
                messages.append({'role': 'tool', 'tool_name': call.get('name', ''),
                                 'content': result.get('content', ''), 'status': result.get('status')})
            content = trace.text
            if not content:
                continue
        if role == 'tool':
            try:
                item = json.loads(content)
            except (ValueError, TypeError):
                item = None
            if isinstance(item, dict) and row.get('direction') in ('tool_call', 'tool_result'):
                messages.append({'role': 'tool', 'tool_name': item.get('name') or item.get('tool_name') or '',
                                 'content': json.dumps(item.get('input', {}), ensure_ascii=False)
                                 if row['direction'] == 'tool_call' else str(item.get('content', '')),
                                 'status': item.get('status')})
                continue
        item = {'role': role, 'content': content}
        meta = row.get('meta') or {}
        if role == 'user' and isinstance(meta.get('display_input'), str):
            item.update(user_input=meta['display_input'], runtime_context=meta.get('display_runtime_context', ''))
        messages.append(item)
    return messages[-limit:]


async def drop_log(agent: Agent) -> None:
    await (await history.get_store()).delete_session(platform=agent.platform, session_key=runtime_session(agent))
