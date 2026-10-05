"""Codex, Claude Code, Gemini, OpenClaw and other ACP tools, through the acpx CLI.

acpx keeps a queue per session, so a message sent while the agent is busy waits
its turn: the runtime needs no inbox of its own.
"""

from __future__ import annotations

from typing import Any
import asyncio
from collections import deque
import time

from agents.messages import ACPX_OVERRIDES_BY_MODE, AgentMessage, AgentReply
from agents.runtime import NO_TIMEOUT, ControlError, Runtime
from agents.store import Agent, AgentStore, canonical_platform
from external import session


def adapter(agent: Agent | None = None):
    from external.acpx import AcpxError, get_acpx_adapter

    from ops.components import binary_path
    if not binary_path("acpx"):
        raise ControlError("acpx is not installed")
    try:
        return get_acpx_adapter(cwd=session.native_workspace_cwd(agent)) if agent else get_acpx_adapter()
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
        self._events = {}
        self._sequence = 0

    def events(self, agent, after=0):
        events = self._events.get((agent.owner, agent.agent_id), ())
        return {'events': [event for event in events if event['seq'] > after],
                'cursor': events[-1]['seq'] if events else 0}

    async def chat(self, agent, req):
        from agents.openai import chunk, completion_id, response, streaming
        from agents.messages import parse_openai_content, normalize_run_mode
        from fastapi import HTTPException
        text, attachments = next((parse_openai_content(m.content) for m in reversed(req.messages)
                                  if m.role == 'user'), ('', []))
        instructions = '\n\n'.join(parse_openai_content(m.content)[0] for m in req.messages
                                   if m.role in ('system', 'developer'))
        message = AgentMessage(text=text, attachments=attachments, sender=f'u:{agent.owner}',
                               instructions=instructions)
        model = req.model or agent.platform
        kwargs = dict(mode=normalize_run_mode(req.session_mode), enabled_tools=req.enabled_tools,
                      response_format=req.response_format, timeout=None)
        context = {'teams': agent.teams, 'command_tools': req.tools or []}
        if not req.stream:
            reply = await self.ask(agent, message, context=context, **kwargs)
            if not reply.ok:
                raise HTTPException(502, reply.error or 'Agent call failed')
            return response(reply.content, model=model)

        async def generate():
            queue = asyncio.Queue(maxsize=256)
            started = asyncio.Event()
            context['_acp_event_sink'] = queue.put
            context['_acp_turn_started'] = started
            cid = completion_id()

            async def run():
                try:
                    reply = await self.ask(agent, message, context=context, **kwargs)
                    await queue.put(('reply', reply))
                except Exception as exc:
                    await queue.put(('reply', AgentReply(ok=False, error=str(exc))))

            task = asyncio.create_task(run())
            emitted_text = False
            try:
                yield chunk(model=model, completion_id=cid)
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ': keepalive\n\n'
                        continue
                    if isinstance(event, tuple):
                        reply = event[1]
                        if reply.ok and reply.content and not emitted_text:
                            yield chunk(content=reply.content, model=model, completion_id=cid)
                        if not reply.ok:
                            yield chunk(meta={'type': 'error', 'message': reply.error}, model=model, completion_id=cid)
                        yield chunk(model=model, finish_reason='stop' if reply.ok else 'error', completion_id=cid)
                        yield 'data: [DONE]\n\n'
                        break
                    if event['type'] == 'text':
                        emitted_text = emitted_text or bool(event['text'])
                        yield chunk(content=event['text'], model=model, completion_id=cid)
                    else:
                        yield chunk(meta=event, model=model, completion_id=cid)
            finally:
                if not task.done():
                    # Cancelling a queued request must not cancel somebody else's turn.
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        return streaming(generate())

    def _native_session(self, agent: Agent) -> tuple[str | None, str | None, dict]:
        """``(model, MCP connector file, native config options)`` of the agent's ACP session."""
        from external.acp_settings import initial_config_options
        from external.tool_bridge import connector_file

        acp_meta = (agent.config.get('meta') or {}).get('acp') or {}
        native_config = {**initial_config_options(agent), **(acp_meta.get('config_options') or {})}
        if 'reasoning_level' in acp_meta:
            native_config['_clawcross_reasoning_level'] = acp_meta['reasoning_level']
        model = str(agent.config.get("model") or native_config.get("model") or "").strip() or None
        return model, connector_file(agent), native_config

    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, enabled_tools, response_format, timeout) -> AgentReply:
        async with session.turn(self._store, agent) as current:
            if current.runtime.get('native_resume_id') and current.runtime.get('acp_cwd'):
                from webot.workspace import set_cli_workspace
                set_cli_workspace(current.owner, current.agent_id, current.runtime['acp_cwd'])
            if context.get('_acp_turn_started'):
                context['_acp_turn_started'].set()
            try:
                return await self._ask_turn(current, msg, context=context, mode=mode, enabled_tools=enabled_tools,
                                            response_format=response_format, timeout=timeout)
            except asyncio.CancelledError:
                # Still holding the turn lock: cancel cannot hit the next request.
                try:
                    await self._control(current, 'cancel')
                except (ControlError, RuntimeError):
                    pass
                raise

    async def _ask_turn(self, agent: Agent, msg: AgentMessage, *, context, mode, enabled_tools, response_format, timeout) -> AgentReply:
        from external.acpx import AcpxError, acpx_options_from_agent, get_acpx_adapter
        from webot.runtime import effective_session_mode

        mode = mode or effective_session_mode(agent.owner, agent.agent_id)

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
        model, connector, native_config = self._native_session(agent)
        from external.tool_bridge import active_turn

        tool_output = {}
        async def on_event(event):
            tool_id = event.get('tool_call_id')
            if tool_id and 'content_delta' in event:
                tool_output[tool_id] = (tool_output.get(tool_id, '') + event.pop('content_delta'))[-32000:]
                event['content_text'] = tool_output[tool_id]
            if event['type'] != 'text':
                self._sequence += 1
                self._events.setdefault((agent.owner, agent.agent_id), deque(maxlen=256)).append(
                    {**event, 'seq': self._sequence, 'at': time.time(),
                     'group_id': context.get('group_id') or context.get('conversation_id')})
            sink = context.get('_acp_event_sink')
            if sink:
                await sink(event)

        async def send() -> session.Sent:
            try:
                with active_turn(agent, msg, context, mode, enabled_tools, prepared=prepared, response_format=response_format):
                    selected = get_acpx_adapter(cwd=session.native_workspace_cwd(agent))
                    trace = await selected.prompt_with_trace(
                        tool=canonical_platform(agent.platform),
                        session_key=session.runtime_session(agent),
                        prompt_text=prompt, reset_session=False, system_prompt=None,
                        attachments=[dict(a) for a in msg.attachments] or None,
                        model=model, mcp_config=connector, config_options=native_config,
                        on_event=on_event, **run,
                        **({'resume_session_id': agent.runtime['native_resume_id']} if agent.runtime.get('native_resume_id') else {}),
                    )
            except (AcpxError, RuntimeError) as exc:
                return session.Sent(ok=False, error=str(exc))
            return session.Sent(ok=True, content=trace.text or "", raw={
                "messages": trace.messages, "tool_uses": trace.tool_uses, "tool_results": trace.tool_results,
            })

        reply = await session.exchange(agent, connect_type="acp", prompt=prompt, context=context, send=send, prepared=prepared)
        if reply.ok:
            session.remember(self._store, agent, acp_cwd=session.native_workspace_cwd(agent))
            session.remember_turn(self._store, agent, prepared)
        return reply

    async def test_connection(self, agent: Agent) -> None:
        """Initialize/resume this owned session without prompting or negotiating identity."""
        if self.is_busy(agent):
            raise ControlError('Agent 正在运行，请在本轮结束后测试连接')
        from external.acpx import AcpxError, acpx_options_from_agent, get_acpx_adapter
        async with session.turn(self._store, agent) as current:
            options = acpx_options_from_agent(current.config, default_timeout_sec=60)
            options['timeout_sec'] = min(options.get('timeout_sec') or 60, 90)
            model, connector, _ = self._native_session(current)
            try:
                selected = get_acpx_adapter(cwd=session.native_workspace_cwd(current))
                await selected.ensure_session(
                    tool=canonical_platform(current.platform), session_key=session.runtime_session(current),
                    acpx_session=session.runtime_session(current), system_prompt=None,
                    model=model, mcp_config=connector, **options,
                    **({'resume_session_id': current.runtime['native_resume_id']} if current.runtime.get('native_resume_id') else {}))
                session.remember(self._store, current, acp_cwd=session.native_workspace_cwd(current))
            except (AcpxError, RuntimeError) as exc:
                raise ControlError(str(exc)) from exc

    async def status(self, agent: Agent) -> dict[str, Any]:
        # Availability and open transport sessions are separate from an active
        # turn. Polling status must not spawn npx/adapters or enumerate histories.
        return {"state": "running" if self.is_busy(agent) else "idle"}

    def is_busy(self, agent: Agent) -> bool:
        return session.is_busy(self._store, agent)

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        if action == "reset":
            async with session.turn(self._store, agent) as current:
                return await self._control(current, action)
        return await self._control(agent, action)

    async def _control(self, agent: Agent, action: str) -> dict[str, Any]:
        from external.acpx import AcpxError

        acpx, key = adapter(agent), session.runtime_session(agent)
        try:
            if action == "cancel":
                await acpx.ops_cancel(
                    tool=agent.platform, session_key=key, **command_options(agent, long=False))
            elif action == "reset":
                await acpx.ops_reset_session(
                    tool=agent.platform, session_key=key, **command_options(agent, long=True))
                # A new session key: OpenClaw keeps a conversation per gateway key,
                # so reopening the same key would resume it.
                session.forget(self._store, agent, new_session=True)
            else:
                return await super().control(agent, action)
        except AcpxError as exc:
            raise ControlError(str(exc)) from exc
        return {action: True}

    async def history(self, agent: Agent, limit: int) -> list[dict[str, Any]]:
        return await session.log(agent, limit)

    async def transport_history(self, agent: Agent, limit: int) -> list[dict[str, Any]]:
        """Read captured acpx text locally, without loading or prompting the provider."""
        from external.acpx import AcpxError
        acpx = adapter(agent)
        name = acpx.to_acpx_session_name(tool=agent.platform, session_key=session.runtime_session(agent))
        try:
            result = await acpx.read_session(tool=agent.platform, name=name, tail=limit)
        except AcpxError as exc:
            raise ControlError(str(exc)) from exc
        entries = result.get('entries')
        if not isinstance(entries, list):
            raise ControlError('acpx did not return captured conversation entries')
        return [{'role': item['role'], 'content': item.get('textPreview', '')}
                for item in entries[-limit:] if isinstance(item, dict) and item.get('role') in {'user', 'assistant'}]

    async def destroy(self, agent: Agent) -> None:
        acpx, key = adapter(agent), session.runtime_session(agent)
        await acpx.close_session(
            tool=agent.platform, session_key=key, acpx_session=acpx.to_acpx_session_name(tool=agent.platform, session_key=key),
            **command_options(agent, long=False),
        )
        await session.drop_log(agent)
        self._events.pop((agent.owner, agent.agent_id), None)
