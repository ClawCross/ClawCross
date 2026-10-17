"""Custom ACP launch targets keep one identity across prompt and control commands."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.routes import AgentCreate, new_agent_config
from agents.store import ACPX, Agent
from external.acp_launcher import launch_settings
from external.acp_settings import validate_settings
from external.acpx import AcpxAdapter, get_acpx_adapter


def test_new_agent_accepts_presets_raw_command_and_named_alias():
    for platform in ('codebuddy', 'zcode'):
        driver, config = new_agent_config(AgentCreate(platform=platform))
        assert driver == ACPX and launch_settings(config)['agent_command']
    for launch in ({'agent_command':'node "/Apps/My ACP/Server.js" --stdio'}, {'agent_name':'My-Private-Agent'}):
        driver, config = new_agent_config(AgentCreate(platform='acp', meta={'acp':launch}))
        assert driver == ACPX
        assert all(launch_settings(config)[key] == value for key,value in launch.items())
    for launch in ({}, {'agent_command':'bad\ncommand'}, {'agent_name':'-x'},
                   {'agent_name':'named','agent_command':'raw'}):
        with pytest.raises(ValueError):
            new_agent_config(AgentCreate(platform='acp',meta={'acp':launch}))


def test_connected_session_keeps_launch_identity(monkeypatch):
    monkeypatch.setattr('external.acp_settings.native_options',lambda _:[])
    agent=Agent('a','alice','Custom',ACPX,config={'platform':'acp','meta':{'acp':{'agent_command':'MyAgent --acp'}}},
                runtime={'acp_cwd':'/project','native_resume_id':'native-thread'})
    assert validate_settings(agent,{'agent_command':'MyAgent --acp'})['agent_command']=='MyAgent --acp'
    with pytest.raises(ValueError,match='新建 Agent'):
        validate_settings(agent,{'agent_command':'OtherAgent --acp'})
    assert validate_settings(agent,{'clawcross_tools':False})['clawcross_tools'] is False


def test_adapter_cache_separates_commands_without_changing_case(tmp_path,monkeypatch):
    monkeypatch.setattr('ops.components.binary_path',lambda _: 'acpx')
    first=get_acpx_adapter(cwd=str(tmp_path),agent_command='MyAgent --acp')
    assert first is get_acpx_adapter(cwd=str(tmp_path),agent_command='MyAgent --acp')
    assert first is not get_acpx_adapter(cwd=str(tmp_path),agent_command='OtherAgent --acp')
    assert first._command_prefix(tool='acp',session_key='one')==['--agent','MyAgent --acp']


@pytest.mark.asyncio
async def test_custom_launcher_used_for_start_stop_history_and_discovery():
    for launch, prefix in [({'_agent_command':'node "/My Agent/Server.js"'},['--agent','node "/My Agent/Server.js"']),
                           ({'_agent_name':'configured-bot'},['configured-bot'])]:
        adapter=AcpxAdapter.__new__(AcpxAdapter)
        adapter.__dict__.update(launch)
        adapter._pending_initial_prompt={}
        adapter._session_exists=AsyncMock(return_value=False)
        adapter._run_json=AsyncMock(return_value=json.dumps({'sessions':[]}))
        await adapter.ensure_session(tool='acp',session_key='one',acpx_session='one')
        await adapter.cancel_session(tool='acp',session_key='one',acpx_session='one')
        await adapter.close_session(tool='acp',session_key='one',acpx_session='one')
        await adapter.read_session(tool='acp',name='one')
        await adapter.list_sessions(tool='acp')
        await adapter.list_native_sessions(tool='acp')
        assert all(call.args[0][:len(prefix)]==prefix for call in adapter._run_json.await_args_list)


def test_ssh_uses_same_per_agent_launcher(tmp_path):
    from connections.acpx import SSHAcpxAdapter
    agent=Agent('a','alice','Custom',ACPX,config={'platform':'acp','connection_id':'device',
        'remote_cwd':'/project','meta':{'acp':{'agent_name':'RemoteAlias'}}})
    manager=SimpleNamespace(store=SimpleNamespace(get=lambda *_:{'acpx':'acpx','node':'node'}))
    adapter=SSHAcpxAdapter(agent,manager)
    assert adapter._command_prefix(tool='acp',session_key='one')==['RemoteAlias']


@pytest.mark.asyncio
@pytest.mark.parametrize('launch,prefix', [({'agent_command':'MyAgent --acp'},['--agent','MyAgent --acp']),
                                          ({'agent_name':'MyAlias'},['MyAlias'])])
async def test_session_tracking_and_close_keep_custom_launcher(tmp_path,monkeypatch,launch,prefix):
    from agents.acp_sessions import list_all_sessions, close_acp_session
    from external.session import runtime_session
    agent=Agent('a','alice','Custom',ACPX,config={'platform':'acp','meta':{'acp':launch}},
                runtime={'acp_cwd':str(tmp_path)})
    name=runtime_session(agent)
    adapter=AcpxAdapter.__new__(AcpxAdapter)
    adapter._cwd=str(tmp_path)
    adapter._agent_name=launch.get('agent_name','')
    adapter._agent_command=launch.get('agent_command','')
    adapter._run_json=AsyncMock(return_value=json.dumps([{'name':name,'acpxRecordId':'record-1','cwd':str(tmp_path)}]))
    monkeypatch.setattr('agents.store.get_store',lambda:SimpleNamespace(list=lambda _: [agent]))
    monkeypatch.setattr('external.acpx.get_acpx_adapter',lambda **_:adapter)
    monkeypatch.setattr('external.acp.adapter',lambda _:adapter)
    rows=await list_all_sessions('alice','/acpx')
    assert rows['acpx_sessions'][0]['session_id']=='record-1'
    assert adapter._run_json.call_args.args[0]==prefix+['sessions','list','--local']
    spawn=AsyncMock(return_value=SimpleNamespace(returncode=0,communicate=AsyncMock(return_value=(b'',b''))))
    monkeypatch.setattr('agents.acp_sessions.asyncio.create_subprocess_exec',spawn)
    result=await close_acp_session('acp',name,user_id='alice',acpx_bin='/acpx')
    assert result['status']=='success'
    assert list(spawn.call_args.args)==['/acpx','--cwd',str(tmp_path)]+prefix+['sessions','close',name]
