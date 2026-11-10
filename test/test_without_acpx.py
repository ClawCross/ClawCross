"""Runtime, migration and native import must work with no acpx executable/package."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
from unittest.mock import AsyncMock

import pytest
from agents.store import AgentStore, ACPX
from external import session
from external.acp_direct import Manager, DirectAdapter
from external.acp_migration import snapshot, migrate
from external.acpx import AcpxError

SERVER=Path(__file__).parent/'fixtures/acp_agent.py'


@pytest.fixture(autouse=True)
def prohibit_old_runtime(monkeypatch):
    monkeypatch.setattr('external.acpx.get_acpx_adapter',lambda **_:(_ for _ in ()).throw(AssertionError('acpx must not run')))
    monkeypatch.setattr('ops.components.binary_path',lambda _:None)


def make(tmp_path,owner='alice',aid='existing'):
    cwd=tmp_path/owner;cwd.mkdir(exist_ok=True)
    store=AgentStore(tmp_path/'agents.db')
    agent=store.create(owner,agent_id=aid,driver=ACPX,config={'platform':'acp',
        'workspaces':{'companion':False,'user_shared':False,'cli':False,'teams':False,'paths':[str(cwd)]},
        'meta':{'acp':{'agent_command':shlex.join([sys.executable,str(SERVER)])}}})
    return store,agent,cwd


@pytest.mark.asyncio
async def test_old_agent_is_direct_even_without_transport_flag(tmp_path,monkeypatch):
    from external.acp import adapter
    store,agent,cwd=make(tmp_path)
    runtime=Manager()
    monkeypatch.setattr('external.acp_direct.manager',lambda:runtime)
    monkeypatch.setattr('external.acp_direct.STATE_DIR',tmp_path)
    selected=adapter(agent,store=store)
    assert isinstance(selected,DirectAdapter)
    try:
        reply=await selected.prompt_with_trace(tool='acp',session_key=session.runtime_session(agent),prompt_text='works',timeout_sec=5)
        assert reply.text=='works'
    finally:await runtime.shutdown()


def test_migration_restores_native_id_and_never_changes_generation(tmp_path,monkeypatch):
    store,agent,cwd=make(tmp_path)
    root=tmp_path/'home';records=root/'.acpx/sessions';records.mkdir(parents=True)
    store.patch_runtime('alice','existing',{'negotiation_sent':True,'session_generation':'kept','acp_cwd':str(cwd)})
    agent=store.require('alice','existing')
    (records/'record.json').write_text(json.dumps({'name':session.runtime_session(agent),'cwd':str(cwd),
        'acp_session_id':'same-native','acpx_record_id':'record','acpx':{'config_options':[]}}))
    monkeypatch.setattr('external.acp_migration.Path.home',lambda:root)
    current=migrate(store,agent)
    assert current.runtime['native_resume_id']=='same-native'
    assert current.runtime['session_generation']=='kept'
    assert current.runtime['negotiation_sent'] is True
    assert current.agent_id==agent.agent_id


def test_live_old_controller_or_missing_history_fails_closed(tmp_path):
    store,agent,cwd=make(tmp_path)
    root=tmp_path/'legacy';records=root/'sessions';records.mkdir(parents=True)
    (root/'queues').mkdir()
    (records/'r.json').write_text(json.dumps({'name':session.runtime_session(agent),'cwd':str(cwd),
        'acp_session_id':'preserve','acpx_record_id':'record'}))
    (root/'queues'/(hashlib.sha256(b'record').hexdigest()[:24]+'.lock')).write_text(json.dumps({'pid':os.getpid()}))
    with pytest.raises(AcpxError,match='旧 ACP 进程'):
        snapshot(agent,root=root)
    assert snapshot(agent,root=root,check_owner=False)['native_resume_id']=='preserve'


@pytest.mark.asyncio
async def test_native_list_and_history_use_sdk_without_new_session_or_prompt(tmp_path,monkeypatch):
    from external.acp_catalog import inspect_native
    store,agent,cwd=make(tmp_path)
    runtime=Manager()
    monkeypatch.setattr('external.acp_direct.STATE_DIR',tmp_path)
    monkeypatch.setattr('external.acp_catalog.STATE_DIR',tmp_path)
    try:
        result=await inspect_native('alice',agent.config,cwd=str(cwd),runtime=runtime)
        assert result['sessions'][0]['session_id']=='fixture-native'
        history=await inspect_native('alice',agent.config,cwd=str(cwd),session_id='fixture-native',runtime=runtime)
        assert history['status']=='loaded'
        assert history['messages'][0]['content']=='OLD REPLAY MUST NOT APPEAR'
        events=[json.loads(line)['event'] for line in (cwd/'wire.jsonl').read_text().splitlines()]
        assert 'session/new' not in events and 'begin' not in events
        assert events.count('initialize')==2
    finally:await runtime.shutdown()


def test_all_platforms_and_named_argv_resolve_without_discovery_cli(tmp_path,monkeypatch):
    from agents.platforms import acpx_agent_command_names
    from external import acp_profiles as profiles
    assert {'codex','claude','gemini','kimi','openclaw','antigravity'} <= acpx_agent_command_names()
    monkeypatch.setattr(profiles,'CONFIG_DIR',tmp_path)
    (tmp_path/'acp-launchers.json').write_text(json.dumps({'agents':{'private':{'command':[sys.executable,'my server.py','--acp']}}}))
    assert profiles.command({'platform':'acp','meta':{'acp':{'agent_name':'private'}}}) == [sys.executable,'my server.py','--acp']
    for platform in profiles.LAUNCHERS:
        assert profiles.launch_spec({'platform':platform})


@pytest.mark.asyncio
async def test_session_tracking_and_foreign_owner_rejection_are_passive(tmp_path,monkeypatch):
    from agents.acp_sessions import list_all_sessions,close_acp_session
    from fastapi import HTTPException
    store,agent,cwd=make(tmp_path)
    monkeypatch.setattr('agents.store.get_store',lambda:store)
    result=await list_all_sessions('alice',None)
    assert result['sessions'][0]['agent_id']==agent.agent_id
    assert result['sessions'][0]['transport']=='direct'
    with pytest.raises(HTTPException):await close_acp_session('acp',session.runtime_session(agent),user_id='bob')
