"""Codex, Claude Code, Gemini, OpenClaw and other ACP tools, through the acpx CLI.

Incoming messages use the shared durable inbox and wait for the session turn
lock. MCP results carry live inbox notices while a native turn is running.
A turn runs on when the request that started it goes away; ``events`` lets any
page follow the current turn item by item.
"""

from __future__ import annotations

from typing import Any
import asyncio
import time
import uuid

from agents.messages import ACPX_OVERRIDES_BY_MODE, AgentMessage, AgentReply
from agents.runtime import NO_TIMEOUT, ControlError
from agents.store import Agent, AgentStore, canonical_platform
from external import acp_events, session
from external.inbox import InboxRuntime


def adapter(agent: Agent | None = None):
    from external.acpx import AcpxError, get_acpx_adapter

    if agent and agent.config.get('connection_id'):
        from connections.acpx import SSHAcpxAdapter
        try:return SSHAcpxAdapter(agent)
        except (LookupError,RuntimeError) as exc:raise ControlError(str(exc)) from exc

    from ops.components import binary_path
    if not binary_path("acpx"):
        raise ControlError("acpx is not installed")
    try:
        from external.acp_launcher import launch_settings
        return get_acpx_adapter(cwd=session.native_workspace_cwd(agent), **launch_settings(agent.config)) if agent else get_acpx_adapter()
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


class _Turn:
    """An agent's current or last turn, as the pages following it see it."""

    def __init__(self, group_id):
        self.id = uuid.uuid4().hex
        self.group_id = group_id or ''
        self.started_at = time.time()
        self.finished_at = None
        self.items = []
        self.changed = {}  # item id -> sequence number of its last change
        self.cursor = 0

    def card(self):
        return {'id': self.id, 'group_id': self.group_id, 'running': self.finished_at is None,
                'started_at': self.started_at, 'finished_at': self.finished_at}


class AcpRuntime(InboxRuntime):
    controls = ("cancel", "reset")

    def __init__(self, store: AgentStore | None = None) -> None:
        super().__init__(store)
        self._turns = {}
        self._followers = {}
        self._sequence = 0
        self._control_stopping = set()

    def _changed(self, key, turn, item=None):
        self._sequence += 1
        turn.cursor = self._sequence
        if item is not None:
            turn.changed[item['id']] = self._sequence
        follower = self._followers.pop(key, None)
        if follower:
            follower.set()

    async def events(self, agent, after=0, wait=0.0):
        """The agent's current (or last) turn and its items changed after *after*.

        With *wait*, the call waits that long for a change, so a page that opens
        while the turn runs follows it as the page that sent it does.
        """
        key = (agent.owner, agent.agent_id)
        if after > self._sequence:
            after = 0  # a cursor from before a restart
        turn = self._turns.get(key)
        if wait and (turn is None or turn.cursor <= after):
            follower = self._followers.setdefault(key, asyncio.Event())
            try:
                await asyncio.wait_for(follower.wait(), wait)
                await asyncio.sleep(0.08)  # gather the next few chunks into this reply
            except TimeoutError:
                pass
            turn = self._turns.get(key)
        if turn is None:
            return {'turn': None, 'items': [], 'cursor': self._sequence}
        return {'turn': turn.card(), 'cursor': turn.cursor,
                'items': [dict(item) for item in turn.items if turn.changed[item['id']] > after]}

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
            reply = await asyncio.shield(self._detach(self.ask(agent, message, context=context, **kwargs)))
            if not reply.ok:
                raise HTTPException(502, reply.error or 'Agent call failed')
            return response(reply.content, model=model)

        async def generate():
            queue = asyncio.Queue()
            context['_acp_event_sink'] = queue.put_nowait
            cid = completion_id()

            async def run():
                try:
                    reply = await self.ask(agent, message, context=context, **kwargs)
                except asyncio.CancelledError:
                    reply = AgentReply(ok=False, error='Agent turn cancelled')
                except Exception as exc:
                    reply = AgentReply(ok=False, error=str(exc))
                queue.put_nowait(('reply', reply))

            self._detach(run())
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
                # Like a message typed into WeBot, the turn runs on when the reader leaves;
                # only cancel stops it.
                context['_acp_event_sink'] = None
        return streaming(generate())

    def _detach(self, coro):
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

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

    async def _resolve_remote_workspace(self, agent):
        if not agent.config.get('connection_id') or agent.runtime.get('acp_cwd'):return agent
        from connections.ssh import get_connections
        cwd=await get_connections().resolve_workspace(agent.owner,agent.config['connection_id'],session.native_workspace_cwd(agent))
        if self._store:
            self._store.patch_runtime(agent.owner,agent.agent_id,{'acp_cwd':cwd})
            return self._store.require(agent.owner,agent.agent_id)
        from dataclasses import replace
        return replace(agent,runtime={**agent.runtime,'acp_cwd':cwd})

    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, enabled_tools, response_format, timeout) -> AgentReply:
        try:
            async with session.turn(self._store, agent) as current:
                current=await self._resolve_remote_workspace(current)
                if not current.config.get('connection_id') and current.runtime.get('native_resume_id') and current.runtime.get('acp_cwd'):
                    from webot.workspace import set_cli_workspace
                    set_cli_workspace(current.owner, current.agent_id, current.runtime['acp_cwd'])
                return await self._ask_turn(current, msg, context=context, mode=mode, enabled_tools=enabled_tools,
                                            response_format=response_format, timeout=timeout)
        except asyncio.CancelledError:
            if (agent.owner, agent.agent_id) in self._control_stopping:
                return AgentReply(ok=False, error='Agent turn cancelled')
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

        key = (agent.owner, agent.agent_id)
        turn = _Turn(context.get('group_id') or context.get('conversation_id'))

        async def on_event(event):
            item = acp_events.apply(turn.items, event)
            if item is not None:
                self._changed(key, turn, item)
            sink = context.get('_acp_event_sink')
            if sink:
                sink(event)

        async def send() -> session.Sent:
            # The prompt is in the history now: followers show this turn after it.
            self._turns[key] = turn
            self._changed(key, turn)
            try:
                with active_turn(agent, msg, context, mode, enabled_tools, prepared=prepared, response_format=response_format):
                    selected = adapter(agent)
                    trace = await selected.prompt_with_trace(
                        tool=canonical_platform(agent.platform),
                        session_key=session.runtime_session(agent),
                        prompt_text=prompt, reset_session=False, system_prompt=None,
                        attachments=[*prepared.profile_images, *[dict(a) for a in msg.attachments]] or None,
                        model=model, mcp_config=connector, config_options=native_config,
                        on_event=on_event, **run,
                        **({'resume_session_id': agent.runtime['native_resume_id']} if agent.runtime.get('native_resume_id') else {}),
                    )
            except asyncio.CancelledError:
                # Cancel the native turn while its ClawCross lock is still held.
                try:
                    await self._control(agent, 'cancel')
                except (ControlError, RuntimeError):
                    pass
                raise
            except (AcpxError, RuntimeError) as exc:
                return session.Sent(ok=False, error=str(exc), raw={"items": turn.items})
            return session.Sent(ok=True, content=trace.text or "", raw={"items": trace.items})

        try:
            reply = await session.exchange(agent, connect_type="acp", prompt=prompt, context=context, send=send,
                                           prepared=prepared, partial=lambda: {"items": turn.items})
        finally:
            if self._turns.get(key) is turn:
                turn.finished_at = time.time()  # after its history is written
                self._changed(key, turn)
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
            current=await self._resolve_remote_workspace(current)
            options = acpx_options_from_agent(current.config, default_timeout_sec=60)
            options['timeout_sec'] = min(options.get('timeout_sec') or 60, 90)
            model, connector, _ = self._native_session(current)
            try:
                selected = adapter(current)
                await selected.ensure_session(
                    tool=canonical_platform(current.platform), session_key=session.runtime_session(current),
                    acpx_session=session.runtime_session(current), system_prompt=None,
                    model=model, mcp_config=connector, **options,
                    **({'resume_session_id': current.runtime['native_resume_id']} if current.runtime.get('native_resume_id') else {}))
                session.remember(self._store, current, acp_cwd=session.native_workspace_cwd(current))
                if current.config.get('connection_id') and self._store:
                    choices=await selected.refresh_options(current.platform,session.runtime_session(current))
                    self._store.patch_runtime(current.owner,current.agent_id,{'remote_config_options':choices})
            except (AcpxError, RuntimeError) as exc:
                raise ControlError(str(exc)) from exc

    async def status(self, agent: Agent) -> dict[str, Any]:
        # Availability and open transport sessions are separate from an active
        # turn. Polling status must not spawn npx/adapters or enumerate histories.
        return {"state": "running" if self.is_busy(agent) else "idle"}

    def is_busy(self, agent: Agent) -> bool:
        return session.is_busy(self._store, agent)

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        async with session.control_lock(self._store,agent):
            return await self._control_action(agent,action)

    async def _control_action(self, agent, action):
        if action == "reset":
            key = (agent.owner, agent.agent_id)
            self._inbox_paused.add(key)
            try:
                await self._stop_turn(agent)
                async with session.turn(self._store, agent,cancellable=False) as current:
                    return await self._control(current, action)
            finally:
                self._inbox_paused.discard(key)
        if action == 'cancel' and session.request_tasks(self._store,agent):
            await self._stop_turn(agent)
            return {'cancel': True}
        return await self._control(agent, action)

    async def _stop_turn(self, agent):
        current = asyncio.current_task()
        tasks = session.request_tasks(self._store,agent) | {self._inbox_tasks.get((agent.owner,agent.agent_id))}
        tasks.discard(None)
        tasks.discard(current)
        key = (agent.owner, agent.agent_id)
        self._control_stopping.add(key)
        for task in tasks:
            task.cancel()
        try:
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 15)
        except TimeoutError as exc:
            raise ControlError('Agent cancellation did not finish; retry after its native turn stops') from exc
        finally:
            self._control_stopping.discard(key)

    async def _control(self, agent: Agent, action: str) -> dict[str, Any]:
        from external.acpx import AcpxError

        acpx, key = adapter(agent), session.runtime_session(agent)
        try:
            if agent.config.get('connection_id'):
                from connections.store import get_connection_store
                connection=get_connection_store().get(agent.owner,agent.config['connection_id'])
                if not connection['enabled']:
                    if action=='reset':session.forget(self._store,agent,new_session=True)
                    return {'action':action,'state':'idle','detail':'本机请求已停止；SSH 已暂停，未确认远端原生进程状态。'}
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
        async with session.control_lock(self._store,agent):
            return await self._destroy_owned(agent)

    async def _destroy_owned(self, agent):
        identity = (agent.owner, agent.agent_id)
        self._inbox_paused.add(identity)
        try:
            was_busy = self.is_busy(agent)
            await self._stop_turn(agent)
            async with session.turn(self._store, agent,cancellable=False) as current:
                remote_paused=False
                if current.config.get('connection_id'):
                    from connections.store import get_connection_store
                    remote_paused=not get_connection_store().get(current.owner,current.config['connection_id'])['enabled']
                if not remote_paused and (was_busy or current.runtime.get('acp_cwd') or current.runtime.get('native_resume_id')):
                    acpx, key = adapter(current), session.runtime_session(current)
                    await acpx.close_session(tool=current.platform, session_key=key,
                        acpx_session=acpx.to_acpx_session_name(tool=current.platform, session_key=key),
                        **command_options(current, long=False))
                await session.drop_log(current)
            from external.tool_bridge import revoke_connector
            revoke_connector(agent)
            self._turns.pop(identity, None)
        except Exception:
            self._inbox_paused.discard(identity)
            raise
