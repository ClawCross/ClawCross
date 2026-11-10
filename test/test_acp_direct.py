"""Exercise real stdio processes and cross-loop callers without model spending."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import sys
import threading
import time

import pytest
import pytest_asyncio

from agents.store import AgentStore, ACPX
from external.acp_direct import DirectAdapter, Manager, Lease, Peer
from external.acpx import AcpxError
from external import session
from external.async_lock import AsyncLock, cancel_tasks

SERVER = Path(__file__).parent / 'fixtures/acp_agent.py'


@pytest_asyncio.fixture
async def setup(tmp_path, monkeypatch):
    monkeypatch.setattr('external.acp_direct.STATE_DIR', tmp_path)
    store = AgentStore(tmp_path/'agents.db')
    monkeypatch.setattr('webot.workspace._cli_directories', {})
    runtime = Manager()
    def create(owner='alice', aid='agent'):
        cwd = tmp_path/owner/aid
        cwd.mkdir(parents=True, exist_ok=True)
        agent = store.create(owner, driver=ACPX, agent_id=aid, config={
            'platform':'acp', 'workspace_root':str(cwd),
            'workspaces':{'companion':False,'user_shared':False,'teams':False,'cli':False,'paths':[str(cwd)]},
            'meta':{'acp':{'transport':'direct',
            'agent_command':shlex.join([sys.executable, str(SERVER)])}}})
        return DirectAdapter(agent, store, runtime)
    yield create, store, runtime
    await runtime.shutdown()


def options(adapter, **extra):
    return {'tool':'acp','session_key':session.runtime_session(adapter.store.require(adapter.agent.owner, adapter.agent.agent_id)),
            'timeout_sec':5, **extra}


def wire(adapter):
    return [json.loads(line) for line in (Path(adapter.cwd)/'wire.jsonl').read_text().splitlines()]


@pytest.mark.asyncio
async def test_fifo_single_start_and_real_event_callbacks(setup):
    create, store, runtime = setup
    adapter = create()
    received = []
    caller = threading.get_ident()
    async def event(item):
        assert threading.get_ident() == caller
        received.append(item['text'])
    replies = await asyncio.gather(*(adapter.prompt_with_trace(prompt_text=str(i), **options(adapter, on_event=event)) for i in range(20)))
    assert [r.text for r in replies] == [str(i) for i in range(20)] == received
    rows = wire(adapter)
    assert sum(row['event']=='initialize' for row in rows) == 1
    assert sum(row['event']=='session/new' for row in rows) == 1
    assert [row['event'] for row in rows if row['event'] in {'begin','end'}] == ['begin','end']*20
    assert store.require('alice','agent').runtime['native_resume_id']


@pytest.mark.asyncio
async def test_different_agents_and_owners_run_in_parallel(setup):
    create, _, _ = setup
    adapters = [create('alice','one'), create('alice','two'), create('bob','one')]
    await asyncio.gather(*(a.ensure_session(**options(a)) for a in adapters))
    await asyncio.gather(*(a.prompt_with_trace(prompt_text=a.agent.owner+a.agent.agent_id, **options(a)) for a in adapters))
    rows = [wire(a) for a in adapters]
    assert max(next(r['time'] for r in rows_ if r['event']=='begin') for rows_ in rows) < min(
        next(r['time'] for r in rows_ if r['event']=='end') for rows_ in rows)
    assert len({a.store.require(a.agent.owner,a.agent.agent_id).runtime['native_resume_id'] for a in adapters}) == 3


@pytest.mark.asyncio
async def test_cross_thread_callers_share_one_process(setup):
    create, _, _ = setup
    adapter = create()
    def run(i):
        return asyncio.run(adapter.prompt_with_trace(prompt_text=str(i), **options(adapter))).text
    results = await asyncio.gather(*(asyncio.to_thread(run, i) for i in range(12)))
    assert len(set(results)) == 12
    rows = wire(adapter)
    assert sum(r['event']=='initialize' for r in rows) == 1
    assert [r['event'] for r in rows if r['event'] in {'begin','end'}] == ['begin','end']*12


@pytest.mark.asyncio
async def test_cancel_closes_before_return_and_resume_does_not_replay(setup):
    create, store, runtime = setup
    adapter = create()
    task = asyncio.create_task(adapter.prompt_with_trace(prompt_text='slow', **options(adapter, timeout_sec=60)))
    while not runtime.peers or not any(p.active for p in list(runtime.peers.values())):
        await asyncio.sleep(.01)
    sid = store.require('alice','agent').runtime['native_resume_id']
    process = next(iter(runtime.peers.values())).process
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert process.returncode is not None and not runtime.peers
    result = await adapter.prompt_with_trace(prompt_text='next', **options(adapter))
    assert result.text == 'next'
    assert store.require('alice','agent').runtime['native_resume_id'] == sid
    assert sum(r['event']=='session/new' for r in wire(adapter)) == 1
    assert sum(r['event']=='session/load' for r in wire(adapter)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('text,timeout,error', [('slow',.2,'timed out'), ('crash',5,'transport failed')])
async def test_timeout_and_disconnect_are_never_retried(setup,text,timeout,error):
    create, store, runtime = setup
    adapter = create()
    # Measure prompt timeout independently of subprocess startup under load.
    await adapter.ensure_session(**options(adapter))
    with pytest.raises(AcpxError, match=error):
        await adapter.prompt_with_trace(prompt_text=text, **options(adapter, timeout_sec=timeout))
    assert not runtime.peers
    assert sum(r['event']=='begin' for r in wire(adapter)) == 1
    assert store.require('alice','agent').runtime['native_resume_id']
    assert (await adapter.prompt_with_trace(prompt_text='recovered', **options(adapter))).text == 'recovered'
    assert sum(r['event']=='session/new' for r in wire(adapter)) == 1


@pytest.mark.asyncio
async def test_initialization_is_free_and_attachment_and_config_supported(setup):
    create, _, _ = setup
    adapter = create()
    await adapter.ensure_session(**options(adapter))
    assert not any(r['event']=='begin' for r in wire(adapter))
    reply = await adapter.prompt_with_trace(prompt_text='image', **options(adapter,
        config_options={'mode':'agent'}, attachments=[{'type':'image','data':'data:image/png;base64,YQ=='}]))
    assert reply.text == 'image'
    rows = wire(adapter)
    assert next(r for r in rows if r['event']=='config')['value'] == 'agent'
    assert next(r for r in rows if r['event']=='begin')['blocks'][1]['data'] == 'YQ=='
    # SDK omits capabilities that equal the protocol's false defaults.
    caps = rows[0]['capabilities'] or {}
    assert not caps.get('terminal') and not caps.get('fs', {}).get('readTextFile')


@pytest.mark.asyncio
async def test_process_budget_and_idle_cleanup(setup):
    create, _, runtime = setup
    runtime.max_per_owner = 1
    runtime.idle_seconds = .15
    one, two, bob = create('alice','one'), create('alice','two'), create('bob','one')
    await one.ensure_session(**options(one))
    with pytest.raises(AcpxError, match='process limit'):
        await two.ensure_session(**options(two))
    await bob.ensure_session(**options(bob))
    await asyncio.sleep(.4)
    assert not runtime.peers
    await two.ensure_session(**options(two))


@pytest.mark.asyncio
async def test_cancelled_queued_call_does_not_cancel_active_turn(setup):
    create, _, _ = setup
    adapter = create()
    active = asyncio.create_task(adapter.prompt_with_trace(prompt_text='first', **options(adapter)))
    queued = asyncio.create_task(adapter.prompt_with_trace(prompt_text='never', **options(adapter)))
    await asyncio.sleep(.04)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError): await queued
    assert (await active).text == 'first'
    assert not any(r.get('text')=='never' for r in wire(adapter))


@pytest.mark.asyncio
async def test_queued_message_reads_identity_after_previous_disconnect(setup):
    create, _, _ = setup
    adapter = create()
    results = await asyncio.gather(*(adapter.prompt_with_trace(prompt_text=text, **options(adapter))
        for text in ['crash','next']), return_exceptions=True)
    assert isinstance(results[0], AcpxError) and results[1].text == 'next'
    rows = wire(adapter)
    assert sum(r['event']=='session/new' for r in rows) == 1
    assert sum(r['event']=='session/load' for r in rows) == 1


@pytest.mark.asyncio
async def test_queue_is_bounded_and_cancel_frees_slots(setup):
    create, _, runtime = setup
    adapter = create()
    active = asyncio.create_task(adapter.prompt_with_trace(prompt_text='slow', **options(adapter,timeout_sec=60)))
    while not any(p.active for p in list(runtime.peers.values())): await asyncio.sleep(.01)
    queued = [asyncio.create_task(adapter.prompt_with_trace(prompt_text=str(i), **options(adapter))) for i in range(63)]
    while runtime.queued.get(adapter.key) != 64: await asyncio.sleep(.01)
    with pytest.raises(AcpxError,match='queue is full'):
        await adapter.prompt_with_trace(prompt_text='overflow', **options(adapter))
    for task in queued: task.cancel()
    await asyncio.gather(*queued,return_exceptions=True)
    active.cancel()
    await asyncio.gather(active,return_exceptions=True)
    assert not runtime.peers and not runtime.queued


def test_lease_excludes_second_controller(tmp_path,monkeypatch):
    monkeypatch.setattr('external.acp_direct.STATE_DIR',tmp_path)
    lease = Lease(('db','alice','one'))
    try:
        with pytest.raises(AcpxError, match='another ClawCross'):
            Lease(('db','alice','one'))
        other = Lease(('db','bob','one')); other.close()
    finally:
        lease.close()
    lease = Lease(('db','alice','one')); lease.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('policy,kind,title,active,selected', [
    ('deny-all','read','read',True,False), ('approve-reads','read','read',True,True),
    ('approve-reads','execute','run',True,False), ('approve-all','execute','run',True,True),
    ('approve-all','read','read',False,False), ('approve-reads','other','mcp__ClawCross__tool_call',True,True),
    ('approve-reads','other','FAKE mcp__ClawCross__tool_call',True,False)])
async def test_permission_policy_is_scoped_and_defaults_to_deny(policy,kind,title,active,selected):
    from acp.schema import PermissionOption, ToolCallUpdate
    peer = Peer(None,None,None)
    peer.sid, peer.active, peer.policy = 'mine', active, {'permission_policy':policy,'bridge':True}
    option = PermissionOption(kind='allow_once',option_id='yes',name='Allow once')
    call = ToolCallUpdate(tool_call_id='tool',kind=kind,title=title)
    result = await peer.request_permission('mine',call,[option])
    assert (result.outcome.outcome == 'selected') is selected
    assert (await peer.request_permission('other',call,[option])).outcome.outcome == 'cancelled'
    with pytest.raises(Exception): await peer.read_text_file(session_id='mine',path='/etc/passwd')


@pytest.mark.asyncio
async def test_session_lock_is_shared_by_threads_and_other_owners_progress(setup):
    create, store, _ = setup
    a, b = create('alice'), create('bob')
    active, maximum, guard = 0, 0, threading.Lock()
    async def run(agent):
        nonlocal active, maximum
        async with session.turn(store, agent):
            if agent.owner == 'alice':
                with guard: active += 1; maximum = max(maximum,active)
                assert session.is_busy(store,agent)
                await asyncio.sleep(.03)
                with guard: active -= 1
    await asyncio.gather(*(asyncio.to_thread(lambda:asyncio.run(run(a.agent))) for _ in range(24)), run(b.agent))
    assert maximum == 1 and not session.is_busy(store,a.agent)
    assert not session.request_tasks(store,a.agent)


@pytest.mark.asyncio
async def test_fifo_lock_cancellation_race_does_not_leak():
    lock = AsyncLock()
    for _ in range(100):
        async with lock:
            task = asyncio.create_task(lock.__aenter__())
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        async with lock: pass
    assert not lock.locked()


@pytest.mark.asyncio
async def test_api_concurrency_uses_normal_agent_identity_and_history(setup,monkeypatch,tmp_path):
    import httpx
    from fastapi import FastAPI
    from agents.gateway import AgentGateway
    from agents.routes import create_agents_router
    from external.acp import AcpRuntime
    from external import history
    create, store, actor = setup
    adapter = create()
    agent = store.update('alice','agent',config={**adapter.agent.config,'meta':{'acp':{
        **adapter.agent.config['meta']['acp'],'clawcross_tools':False}}})
    monkeypatch.setattr('external.acp_direct.manager',lambda:actor)
    monkeypatch.setattr(history,'_STORE',history.ExternalAgentHistoryStore(tmp_path/'history'))
    monkeypatch.setattr(session,'identity_sections',lambda _:{})
    monkeypatch.setattr(session,'build_dynamic_context',lambda *a,**k:{})
    runtime = AcpRuntime(store)
    gateway = AgentGateway(store=store,runtimes={ACPX:runtime})
    app = FastAPI()
    app.include_router(create_agents_router(internal_token='private-test',verify_password=lambda *_:False,
                                           store=store,gateway=gateway))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test',
                                headers={'Authorization':'Bearer private-test:alice'}) as client:
        replies = await asyncio.gather(*(client.post('/v1/agents/agent/messages',json={'text':str(i),'mode':'chat'}) for i in range(12)))
        assert all(r.status_code==200 and r.json()['ok'] for r in replies)
        assert [r.json()['content'] for r in replies] == [str(i) for i in range(12)]
        forbidden = await client.get('/v1/agents/agent/capabilities',
                                      headers={'Authorization':'Bearer private-test:bob'})
        assert forbidden.status_code == 404
    messages = await session.log(agent,100)
    assert len([m for m in messages if m['role']=='assistant']) == 12
    assert sum(r['event']=='session/new' for r in wire(adapter)) == 1


@pytest.mark.asyncio
async def test_normal_durable_group_inbox_deduplicates_and_replies_to_same_group(setup,monkeypatch,tmp_path):
    from agents.gateway import AgentGateway
    from agents.messages import AgentMessage
    from external.acp import AcpRuntime
    from external import history
    from webot import runtime_store
    create, store, actor = setup
    adapter = create()
    agent = store.update('alice','agent',config={**adapter.agent.config,'meta':{'acp':{
        **adapter.agent.config['meta']['acp'],'clawcross_tools':False}}})
    monkeypatch.setattr('external.acp_direct.manager',lambda:actor)
    monkeypatch.setattr(runtime_store,'DEFAULT_DB_PATH',tmp_path/'inbox.db')
    monkeypatch.setattr(history,'_STORE',history.ExternalAgentHistoryStore(tmp_path/'history'))
    monkeypatch.setattr(session,'identity_sections',lambda _:{})
    monkeypatch.setattr(session,'build_dynamic_context',lambda *a,**k:{})
    runtime = AcpRuntime(store)
    gateway = AgentGateway(store=store,runtimes={ACPX:runtime})
    replies = []
    async def reply(current, groups, content):
        replies.append((current.agent_id,groups,content))
    gateway.group_reply_handler = reply
    monkeypatch.setattr('agents.gateway.get_gateway',lambda:gateway)
    groups = [{'group_id':'normal-group','automatic_reply_id':'normal-reply:1:agent'}]
    context = {'delivery_id':'same-delivery','groups':groups,'conversation_id':'normal-group'}
    await asyncio.gather(*(runtime.inbox(agent,AgentMessage(text='hello',sender='u:friend'),
                                       context=context,mode='chat',on_complete=None) for _ in range(20)))
    await asyncio.gather(*list(runtime._inbox_tasks.values()))
    assert replies == [('agent',groups,'hello')]
    assert sum(r['event']=='begin' for r in wire(adapter)) == 1
    assert runtime_store.count_inbox_messages('alice','agent',status='queued') == 0


@pytest.mark.asyncio
async def test_cross_loop_cancel_joins_running_and_waiting_tasks(setup):
    create, store, _ = setup
    adapter = create()
    ready = threading.Event()
    def run():
        async def wait():
            async with session.turn(store,adapter.agent):
                ready.set()
                await asyncio.sleep(30)
        return asyncio.run(wait())
    worker = asyncio.create_task(asyncio.to_thread(run))
    await asyncio.to_thread(ready.wait)
    await asyncio.wait_for(cancel_tasks(session.request_tasks(store,adapter.agent)),2)
    await asyncio.gather(worker,return_exceptions=True)
    assert not session.is_busy(store,adapter.agent)
    assert not session.request_tasks(store,adapter.agent)


def test_mcp_credentials_are_published_atomically(tmp_path,monkeypatch):
    from external import tool_bridge
    from agents.store import Agent
    monkeypatch.setattr(tool_bridge,'STATE_DIR',tmp_path)
    agent = Agent('same','alice','test',ACPX,{'platform':'codex'})
    with ThreadPoolExecutor(max_workers=20) as workers:
        paths = list(workers.map(lambda _:tool_bridge.connector_file(agent), range(60)))
    assert len(set(paths)) == 1
    config = json.loads(Path(paths[0]).read_text())
    token = next(e['value'] for e in config['mcpServers'][0]['env'] if e['name']=='CLAWCROSS_BRIDGE_TOKEN')
    assert len(token) >= 32
    assert len(list((tmp_path/'acp-connectors').glob('*.token'))) == 1
    tool_bridge.revoke_connector(agent)


@pytest.mark.asyncio
async def test_explicit_close_stops_an_active_native_turn(setup):
    create, _, runtime = setup
    adapter = create()
    running = asyncio.create_task(adapter.prompt_with_trace(prompt_text='slow',**options(adapter,timeout_sec=60)))
    while not any(p.active for p in list(runtime.peers.values())): await asyncio.sleep(.01)
    process = next(iter(runtime.peers.values())).process
    await asyncio.wait_for(adapter.close_session(),3)
    await asyncio.gather(running,return_exceptions=True)
    assert process.returncode is not None and not runtime.peers


@pytest.mark.asyncio
async def test_shutdown_reaps_active_processes_and_queued_callers(setup):
    create, _, runtime = setup
    adapter = create()
    running = asyncio.create_task(adapter.prompt_with_trace(prompt_text='slow',**options(adapter,timeout_sec=60)))
    while not any(p.active for p in list(runtime.peers.values())): await asyncio.sleep(.01)
    queued = asyncio.create_task(adapter.prompt_with_trace(prompt_text='queued',**options(adapter)))
    process = next(iter(runtime.peers.values())).process
    await asyncio.wait_for(runtime.shutdown(),5)
    await asyncio.gather(running,queued,return_exceptions=True)
    assert process.returncode is not None and not runtime.thread.is_alive()


@pytest.mark.asyncio
async def test_mcp_change_reconnects_same_native_identity(setup,tmp_path):
    create, store, _ = setup
    adapter = create()
    connector = tmp_path/'mcp.json'
    connector.write_text(json.dumps({'mcpServers':[]}))
    await adapter.ensure_session(**options(adapter,mcp_config=str(connector)))
    sid = store.require('alice','agent').runtime['native_resume_id']
    connector.write_text(json.dumps({'mcpServers':[{'name':'ClawCross','command':sys.executable,'args':[],'env':[]}]}))
    await adapter.ensure_session(**options(adapter,mcp_config=str(connector)))
    assert store.require('alice','agent').runtime['native_resume_id'] == sid
    assert sum(r['event']=='session/new' for r in wire(adapter)) == 1
    assert sum(r['event']=='session/load' for r in wire(adapter)) == 1


@pytest.mark.asyncio
async def test_unregistered_resume_and_stale_generation_never_create_a_replacement(setup):
    create, store, runtime = setup
    adapter = create()
    with pytest.raises(AcpxError,match='registered'):
        await adapter.ensure_session(**options(adapter,resume_session_id='unregistered'))
    stale = options(adapter)
    session.forget(store,adapter.agent,new_session=True)
    with pytest.raises(AcpxError,match='session changed'):
        await adapter.ensure_session(**stale)
    assert not runtime.peers and not (Path(adapter.cwd)/'wire.jsonl').exists()


@pytest.mark.asyncio
async def test_session_admission_is_bounded_before_history_and_prompt(setup):
    from agents.runtime import ControlError
    create, store, _ = setup
    adapter = create()
    async def request():
        async with session.turn(store,adapter.agent): pass
    async with session.turn(store,adapter.agent):
        waiting = [asyncio.create_task(request()) for _ in range(63)]
        while len(session.request_tasks(store,adapter.agent)) != 64: await asyncio.sleep(0)
        with pytest.raises(ControlError,match='queue is full'):
            await request()
        for task in waiting: task.cancel()
        await asyncio.gather(*waiting,return_exceptions=True)
    assert not session.request_tasks(store,adapter.agent)


@pytest.mark.asyncio
async def test_large_stderr_cannot_block_and_output_overflow_is_closed(setup):
    create, _, runtime = setup
    adapter = create()
    assert (await adapter.prompt_with_trace(prompt_text='stderr',**options(adapter))).text == 'stderr'
    with pytest.raises(AcpxError,match='8 MiB'):
        await adapter.prompt_with_trace(prompt_text='overflow',**options(adapter))
    assert not runtime.peers
