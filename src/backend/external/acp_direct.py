"""Managed ACP stdio client using the official SDK.

One actor loop owns subprocesses, including calls made from group-worker threads.
The normal Agent turn lock still protects history, inbox and MCP authorization.
No prompt is retried after a timeout or transport failure. Native tool execution
inside a harness remains subject to that harness's permission implementation.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import signal
import threading
import time
from pathlib import Path

from acp import Client, PROTOCOL_VERSION, RequestError, connect_to_agent
from acp.schema import (AllowedOutcome, AudioContentBlock, ClientCapabilities, DeniedOutcome,
                        Implementation, McpServerStdio, RequestPermissionResponse,
                        TextContentBlock, ImageContentBlock)
from agents.store import get_store
from common.runtime_paths import STATE_DIR
from external import acp_events, session
from external.acpx import AcpxError, AcpxPromptTrace


class Lease:
    """An OS lease prevents another backend from controlling the same Agent."""
    def __init__(self, key):
        root = STATE_DIR / 'acp-leases'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = (root / (hashlib.sha256(repr(key).encode()).hexdigest() + '.lock')).open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.write(b'0'); self.file.flush(); self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise AcpxError('This Agent is controlled by another ClawCross process') from None

    def close(self):
        self.file.close()  # Do not unlink: waiters must see the same inode.


def dump(value):
    return value.model_dump(by_alias=True, exclude_none=True)


class Peer(Client):
    def __init__(self, adapter, fingerprint, lease):
        self.adapter, self.fingerprint, self.lease = adapter, fingerprint, lease
        self.process = self.connection = None
        self.stderr_task = None
        self.sid = None
        self.options = []
        self.capabilities = None
        self.callback = None
        self.items = []
        self.text = []
        self.output_size = 0
        self.overflow = None
        self.policy = {}
        self.last_used = time.monotonic()
        self.active = False
        self.in_use = True
        self.ttl = adapter.manager.idle_seconds if adapter else 300
        self.operation_task = asyncio.current_task()

    def on_connect(self, conn):
        pass

    async def session_update(self, session_id, update, **_):
        # load_session may replay history before its result. Never re-emit it.
        if session_id != self.sid:
            return
        packet = dump(update)
        if packet.get('sessionUpdate') == 'config_option_update':
            self.options = packet.get('configOptions', [])
            self.persist()
        if not self.active:
            return
        event = acp_events.normalize_event({'method':'session/update', 'params':{'update':packet}})
        if event:
            self.output_size += len(json.dumps(event, ensure_ascii=False).encode())
            if self.output_size > 8 * 1024 * 1024:
                # Closing cancels SDK handlers; schedule outside this handler
                # to avoid waiting for our own cancellation to complete.
                if self.overflow is None:
                    self.overflow = asyncio.create_task(self.adapter.manager.retire(self.adapter.key, expected=self))
                return
            acp_events.apply(self.items, event)
            if event['type'] == 'text':
                self.text.append(event['text'])
            if self.callback:
                await self.callback(event)

    async def request_permission(self, session_id, tool_call, options, **_):
        allowed = False
        if self.active and session_id == self.sid:
            title = tool_call.title or ''
            delegated = self.policy.get('bridge') and title in {
                'mcp__ClawCross__tool_search', 'mcp__ClawCross__tool_call',
                'mcp.ClawCross.tool_search', 'mcp.ClawCross.tool_call'}
            policy = self.policy.get('permission_policy') or ('approve-all' if self.policy.get('approve_all') else 'approve-reads')
            allowed = bool(delegated or policy == 'approve-all' or
                           policy == 'approve-reads' and tool_call.kind in {'read', 'search'})
        option = next((option for option in options if option.kind == 'allow_once'), None)
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome='selected', option_id=option.option_id)
                                         if allowed and option else DeniedOutcome(outcome='cancelled'))

    async def ext_method(self, method, params):
        raise RequestError(-32601, 'Client extension is not available')

    async def ext_notification(self, method, params):
        pass

    async def _unavailable(self, **_):
        raise RequestError(-32601, 'Client filesystem and terminal capabilities are disabled')

    async def read_text_file(self, **kwargs): return await self._unavailable(**kwargs)
    async def write_text_file(self, **kwargs): return await self._unavailable(**kwargs)
    async def create_terminal(self, **kwargs): return await self._unavailable(**kwargs)
    async def terminal_output(self, **kwargs): return await self._unavailable(**kwargs)
    async def release_terminal(self, **kwargs): return await self._unavailable(**kwargs)
    async def wait_for_terminal_exit(self, **kwargs): return await self._unavailable(**kwargs)
    async def kill_terminal(self, **kwargs): return await self._unavailable(**kwargs)
    async def create_elicitation(self, **kwargs): return await self._unavailable(**kwargs)
    async def complete_elicitation(self, **kwargs): pass

    def persist(self):
        self.adapter.store.patch_runtime(self.adapter.agent.owner, self.adapter.agent.agent_id,
            {'native_resume_id':self.sid, 'acp_cwd':self.adapter.cwd,
             'direct_config_options':self.options, 'acp_transport':'direct'})

    async def start(self, servers, resume):
        from external.acp_profiles import command
        argv = command(self.adapter.agent.config)
        private = {'INTERNAL_TOKEN', 'LLM_API_KEY', 'CLAWCROSS_BRIDGE_TOKEN', 'HUB_TOKEN',
                   'HUB_DEVICE_TOKEN', 'TELEGRAM_BOT_TOKEN', 'QQ_APP_SECRET'}
        env = {key:value for key,value in os.environ.items() if key not in private}
        self.process = await asyncio.create_subprocess_exec(*argv, cwd=self.adapter.cwd, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **({'start_new_session':True} if os.name != 'nt' else {}))
        async def drain():
            # Native stderr can contain credentials. Drain without logging it.
            while await self.process.stderr.read(8192):
                pass
        self.stderr_task = asyncio.create_task(drain())
        self.connection = connect_to_agent(self, self.process.stdin, self.process.stdout)
        initialized = await self.connection.initialize(protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(), client_info=Implementation(name='clawcross', version='0.0.29'))
        self.capabilities = initialized.agent_capabilities
        if resume:
            if not self.capabilities.load_session:
                raise AcpxError('Adapter cannot resume this Agent; its history was preserved')
            result = await self.connection.load_session(cwd=self.adapter.cwd, session_id=resume, mcp_servers=servers)
            self.sid = resume
        else:
            result = await self.connection.new_session(cwd=self.adapter.cwd, mcp_servers=servers)
            self.sid = result.session_id
        self.options = [dump(option) for option in result.config_options or []]
        # Identity is persisted before a model prompt. A failed prompt must never
        # silently create a replacement conversation on the next request.
        self.persist()

    async def close(self):
        stdout_task = None
        try:
            if self.connection:
                try:
                    await asyncio.wait_for(self.connection.close(), 2)
                except TimeoutError:
                    pass
        finally:
            if self.process:
                # SDK shutdown stops its stdout reader. Drain remaining pipe
                # bytes so a large output cannot leave a paused transport alive
                # after the actor loop exits.
                async def discard_stdout():
                    while await self.process.stdout.read(8192): pass
                stdout_task = asyncio.create_task(discard_stdout())
                if os.name != 'nt':
                    try: os.killpg(self.process.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                elif self.process.returncode is None:
                    self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 2)
                except TimeoutError:
                    if os.name != 'nt':
                        try: os.killpg(self.process.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                    else:
                        self.process.kill()
                    await self.process.wait()
            drains = [task for task in (stdout_task, self.stderr_task) if task]
            if drains:
                try:
                    await asyncio.wait_for(asyncio.gather(*drains, return_exceptions=True), 1)
                except TimeoutError:
                    pass
            self.lease.close()


class Manager:
    def __init__(self, max_processes=16, max_per_owner=4, idle_seconds=300):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name='clawcross-acp', daemon=True)
        self.max_processes, self.max_per_owner, self.idle_seconds = max_processes, max_per_owner, idle_seconds
        self.peers = {}
        import weakref
        self.locks = weakref.WeakValueDictionary()
        self.queued = {}
        self.operations = set()
        self.closed = False
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        self.loop.close()

    async def submit(self, factory, *, key=None):
        if self.closed:
            raise AcpxError('ACP manager is shutting down')
        started = concurrent.futures.Future()
        async def operation():
            started.set_result(asyncio.current_task())
            if self.closed:
                raise AcpxError('ACP manager is shutting down')
            if key is not None:
                if self.queued.get(key, 0) >= 64 or sum(self.queued.values()) >= 256:
                    raise AcpxError('ACP request queue is full; retry later')
                self.queued[key] = self.queued.get(key, 0) + 1
            self.operations.add(asyncio.current_task())
            try:
                return await factory()
            finally:
                self.operations.discard(asyncio.current_task())
                if key is not None:
                    self.queued[key] -= 1
                    if not self.queued[key]: self.queued.pop(key)
        future = asyncio.run_coroutine_threadsafe(operation(), self.loop)
        try:
            # Shield prevents caller cancellation releasing its session lock
            # before the actor has closed the cancelled native process.
            return await asyncio.shield(asyncio.wrap_future(future))
        except asyncio.CancelledError:
            if self.closed:
                raise
            async def stop():
                task = await asyncio.wrap_future(started)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            stopped = asyncio.run_coroutine_threadsafe(stop(), self.loop)
            await asyncio.shield(asyncio.wrap_future(stopped))
            raise

    async def retire(self, key, *, expected=None):
        peer = self.peers.get(key)
        if expected is not None and peer is not expected:
            return
        if peer:
            expiry = getattr(peer, 'expiry_task', None)
            if expiry and expiry is not asyncio.current_task():
                expiry.cancel()
            if not getattr(peer, 'closing', None):
                peer.closing = asyncio.create_task(peer.close())
            try:
                await asyncio.shield(peer.closing)
            finally:
                if peer.closing.done() and self.peers.get(key) is peer:
                    self.peers.pop(key, None)

    async def acquire(self, adapter, servers, fingerprint, resume):
        if self.closed:
            raise AcpxError('ACP manager is shutting down')
        key = adapter.key
        peer = self.peers.get(key)
        if peer and (peer.fingerprint != fingerprint or peer.process is None or peer.process.returncode is not None):
            await self.retire(key)
            peer = None
        if peer:
            peer.in_use = True
            return peer, False
        # Eviction is only of idle sessions; active work is never displaced.
        for other, candidate in list(self.peers.items()):
            if other != key and not candidate.in_use and time.monotonic() - candidate.last_used > candidate.ttl:
                await self.retire(other)
        if len(self.peers) >= self.max_processes or sum(k[:2] == key[:2] for k in self.peers) >= self.max_per_owner:
            raise AcpxError('ACP process limit reached; wait for an idle session to close')
        peer = Peer(adapter, fingerprint, Lease(key))
        self.peers[key] = peer
        await peer.start(servers, resume)
        peer.expiry_task = asyncio.create_task(self._expire(key, peer))
        return peer, not bool(resume)

    async def _expire(self, key, peer):
        while self.peers.get(key) is peer:
            await asyncio.sleep(min(peer.ttl, 30))
            if not peer.in_use and time.monotonic() - peer.last_used >= peer.ttl:
                async with self.lock(key):
                    if self.peers.get(key) is peer and not peer.in_use:
                        await self.retire(key)
                return

    def lock(self, key):
        # Only accessed by the actor loop. Remove inactive lock entries after use.
        lock = self.locks.get(key)
        if lock is None:
            lock = self.locks[key] = asyncio.Lock()
        return lock

    async def shutdown(self):
        if self.closed:
            return
        self.closed = True
        async def stop():
            # Cancel operations before closing peers, so a queued operation can
            # never start a replacement process during shutdown.
            operations = list(self.operations)
            for operation in operations: operation.cancel()
            await asyncio.gather(*operations, return_exceptions=True)
            await asyncio.gather(*(self.retire(key) for key in list(self.peers)), return_exceptions=True)
            current = asyncio.current_task()
            tasks = [t for t in asyncio.all_tasks() if t is not current]
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(stop(), self.loop))
        self.loop.call_soon_threadsafe(self.loop.stop)
        await asyncio.to_thread(self.thread.join, 5)


_manager = None
_manager_guard = threading.Lock()


def manager():
    global _manager
    with _manager_guard:
        if _manager is None or _manager.closed:
            _manager = Manager()
        return _manager


async def shutdown():
    if _manager:
        await _manager.shutdown()


class DirectAdapter:
    def __init__(self, agent, store=None, runtime=None):
        self.agent, self.store = agent, store or get_store()
        self.cwd = session.native_workspace_cwd(agent)
        self.key = (str(Path(self.store.db_path).resolve()), agent.owner, agent.agent_id)
        self.manager = runtime or manager()

    @staticmethod
    def to_acpx_session_name(*, tool, session_key):
        return session_key

    async def _operation(self, prompt=None, **options):
        key = options['session_key']
        mcp = options.get('mcp_config')
        servers = []
        if mcp:
            servers = [McpServerStdio.model_validate(item) for item in json.loads(Path(mcp).read_text()).get('mcpServers', [])]
        async with self.manager.lock(self.key):
            peer = None
            try:
                # Read AFTER queuing: the preceding turn may have created a
                # native identity, changed generation, or disabled execution.
                current = self.store.require(self.agent.owner, self.agent.agent_id)
                if session.runtime_session(current) != key:
                    raise AcpxError('Agent session changed; submit a new request')
                if options.get('resume_session_id') and options['resume_session_id'] != current.runtime.get('native_resume_id'):
                    raise AcpxError('Native session must be registered to this Agent before resuming')
                if options.get('reset_session'):
                    raise AcpxError('Use the Agent reset control to create a new conversation')
                from external.native_tool_policy import require_native_execution
                from external.acp_launcher import launch_settings
                require_native_execution(current.config)
                self.agent, self.cwd = current, session.native_workspace_cwd(current)
                fingerprint = (key, self.cwd, json.dumps(launch_settings(current.config), sort_keys=True),
                               json.dumps([dump(server) for server in servers], sort_keys=True))
                async with asyncio.timeout(options.get('timeout_sec') or None):
                    peer, created = await self.manager.acquire(self, servers, fingerprint,
                        current.runtime.get('native_resume_id'))
                    peer.ttl = max(.05, float(options.get('ttl_sec') or self.manager.idle_seconds))
                    peer.operation_task = asyncio.current_task()
                    peer.last_used = time.monotonic()
                    if prompt is None:
                        return created
                    config = dict(options.get('config_options') or {})
                    level = config.pop('_clawcross_reasoning_level', None)
                    if options.get('model'):
                        config['model'] = options['model']
                    if level:
                        from common.reasoning_levels import mapped_effort
                        for item in peer.options:
                            if item['id'] in {'reasoning_effort', 'effort'}:
                                mapped = mapped_effort([o.get('value') for o in item.get('options', [])], level)
                                if mapped: config[item['id']] = mapped
                    for name, value in config.items():
                        advertised = next((item for item in peer.options if item['id'] == name), None)
                        if advertised and advertised.get('currentValue') == value:
                            continue
                        if name == 'mode' and not advertised:
                            result = await peer.connection.set_session_mode(session_id=peer.sid, mode_id=str(value))
                        else:
                            result = await peer.connection.set_config_option(session_id=peer.sid, config_id=name, value=value)
                        if getattr(result, 'config_options', None):
                            peer.options = [dump(item) for item in result.config_options]
                    peer.persist()
                    blocks = [TextContentBlock(type='text', text=prompt)]
                    for attachment in options.get('attachments') or []:
                        kind, data = attachment.get('type'), attachment.get('data', '')
                        mime = attachment.get('mime_type', '')
                        if data.startswith('data:'):
                            header, data = data.split(',', 1)
                            mime = mime or header[5:].split(';')[0]
                        if len(data) > 20 * 1024 * 1024:
                            raise AcpxError('ACP attachment exceeds 20 MiB')
                        if kind == 'image' and peer.capabilities.prompt_capabilities.image:
                            blocks.append(ImageContentBlock(type='image', data=data, mime_type=mime or 'image/png'))
                        elif kind == 'audio' and peer.capabilities.prompt_capabilities.audio:
                            blocks.append(AudioContentBlock(type='audio', data=data, mime_type=mime or 'audio/wav'))
                        elif kind in {'image', 'audio'}:
                            raise AcpxError(f'Adapter does not support {kind} prompts')
                    peer.policy = {**options, 'bridge':any(s.name == 'ClawCross' for s in servers)}
                    peer.items, peer.text, peer.output_size = [], [], 0
                    peer.callback = options.get('on_event')
                    peer.active = True
                    try:
                        result = await peer.connection.prompt(session_id=peer.sid,
                            prompt=blocks)
                        if peer.output_size > 8 * 1024 * 1024:
                            raise AcpxError('ACP output exceeded the 8 MiB limit')
                        if result.stop_reason not in {'end_turn', 'max_tokens'}:
                            raise AcpxError(f'ACP turn stopped: {result.stop_reason}')
                        return AcpxPromptTrace(text=''.join(peer.text), items=peer.items, raw_output='')
                    finally:
                        peer.active = False
                        peer.callback = None
                        peer.last_used = time.monotonic()
            except BaseException as exc:
                peer = peer or self.manager.peers.get(self.key)
                if peer and peer.connection and peer.sid:
                    try:
                        await asyncio.wait_for(peer.connection.cancel(session_id=peer.sid), 1)
                    except Exception:
                        pass
                await self.manager.retire(self.key)
                if isinstance(exc, (asyncio.CancelledError, AcpxError)):
                    raise
                if isinstance(exc, TimeoutError):
                    raise AcpxError('ACP request timed out; no automatic retry was made') from None
                if peer and peer.output_size > 8 * 1024 * 1024:
                    raise AcpxError('ACP output exceeded the 8 MiB limit') from None
                # Adapter messages can contain prompt bodies or credentials.
                raise AcpxError(f'ACP transport failed ({type(exc).__name__}); no automatic retry was made') from None
            finally:
                if peer:
                    peer.in_use = False
                    peer.last_used = time.monotonic()

    async def _call(self, prompt=None, **options):
        callback = options.get('on_event')
        if callback:
            caller = asyncio.get_running_loop()
            async def emit(event):
                await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(callback(event), caller))
            options = {**options, 'on_event':emit}
        return await self.manager.submit(lambda:self._operation(prompt, **options), key=self.key)

    async def ensure_session(self, **options):
        return await self._call(**options)

    async def prompt_with_trace(self, *, prompt_text, **options):
        return await self._call(prompt_text, **options)

    async def ops_cancel(self, **_):
        async def stop():
            peer = self.manager.peers.get(self.key)
            if peer and peer.connection and peer.sid:
                await asyncio.wait_for(peer.connection.cancel(session_id=peer.sid), 2)
        return await self.manager.submit(stop)

    async def close_session(self, **_):
        async def stop():
            peer = self.manager.peers.get(self.key)
            if peer and peer.in_use and peer.operation_task and not peer.operation_task.done():
                peer.operation_task.cancel()
                await asyncio.gather(peer.operation_task, return_exceptions=True)
            async with self.manager.lock(self.key):
                await self.manager.retire(self.key)
        return await self.manager.submit(stop)

    async def ops_reset_session(self, **options):
        return await self.close_session(**options)

    async def read_session(self, *, tail, **_):
        entries = await session.log(self.store.require(self.agent.owner, self.agent.agent_id), tail)
        return {'entries':[{'role':item.get('role'), 'textPreview':item.get('content', '')} for item in entries]}
