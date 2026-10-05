"""Workspace source changes must reach tools, prompts and OS isolation together."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agents.store import AgentStore, WEBOT
from teams.store import TeamStore, DEFAULT_TEAM
from webot import workspace, skills, skill_memory, command_sandbox
from webot.approval_actions import bind_file_target, file_target_outside_workspace
from webot.approval_review import policy_binding
from webot.mcp import filemanager


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for module in (workspace, skills):
        monkeypatch.setattr(module, 'WORKSPACE_DIR', tmp_path / 'workspace')
        monkeypatch.setattr(module, 'USER_FILES_DIR', tmp_path / 'data')
    monkeypatch.setattr(workspace, '_cli_directories', {})
    monkeypatch.delenv('CLAWCROSS_WORKSPACE_ORIGIN_SERVICE', raising=False)
    store = AgentStore(tmp_path / 'agents.db')
    monkeypatch.setattr('agents.store.get_store', lambda: store)
    monkeypatch.setattr('webot.runtime_settings.get_runtime_settings', lambda *_: SimpleNamespace(
        approval=SimpleNamespace(sandbox_security='standard', mode='bypass')))
    return store, TeamStore(store, tmp_path / 'data')


def test_automatic_sources_are_derived_and_custom_paths_are_persisted(setup, tmp_path, monkeypatch):
    store, teams = setup
    custom = tmp_path / 'custom'; custom.mkdir()
    origin = tmp_path / 'cli'; origin.mkdir()
    agent = store.create('alice', driver=WEBOT, config={'workspaces':workspace.normalize_workspace_config(
        {'user_shared':True, 'paths':[str(custom)]})})
    teams.create('alice', 'project'); teams.add('alice', 'project', agent.agent_id)
    workspace.set_cli_workspace('alice', agent.agent_id, str(origin))
    current = store.require('alice', agent.agent_id)
    state = workspace.resolve_session_workspace('alice', agent.agent_id)
    assert set(f['source'] for f in state.folders) == {'companion','user','cli','team','custom'}
    assert state.cwd == origin
    settings = current.config['workspaces']
    assert set(settings) == {'companion','user_shared','cli','teams','paths'}
    assert settings['paths'] == [str(custom)]
    assert str(origin) not in json.dumps(current.config)
    before = policy_binding('alice', agent.agent_id)
    teams.remove('alice', 'project', agent.agent_id)
    assert 'team' not in [f['source'] for f in workspace.resolve_session_workspace('alice', agent.agent_id).folders]
    assert policy_binding('alice', agent.agent_id) != before
    monkeypatch.setattr(workspace, 'WORKSPACE_DIR', tmp_path / 'relocated')
    moved = workspace.resolve_session_workspace('alice', agent.agent_id)
    assert moved.folders[0]['path'].startswith(str(tmp_path / 'relocated'))
    assert store.require('alice',agent.agent_id).config['workspaces'] == settings
    workspace.clear_cli_workspace('alice',agent.agent_id)
    assert 'cli' not in [f['source'] for f in workspace.resolve_session_workspace('alice',agent.agent_id).folders]


def test_default_project_contains_all_agents_without_granting_shared_access(setup):
    store, teams = setup
    first = store.create('alice', driver=WEBOT); second = store.create('alice',driver=WEBOT)
    assert DEFAULT_TEAM in teams.teams('alice')
    assert {m.agent.agent_id for m in teams.members('alice', DEFAULT_TEAM)} == {first.agent_id, second.agent_id}
    one = workspace.resolve_session_workspace('alice',first.agent_id)
    two = workspace.resolve_session_workspace('alice',second.agent_id)
    assert one.root != two.root
    assert not one.root.is_relative_to(workspace._user_root('alice'))
    assert not any(f['source']=='user' for f in one.folders)
    with pytest.raises(ValueError): teams.delete('alice',DEFAULT_TEAM)


def test_all_selected_roots_are_available_to_files_but_strict_denies_outside(setup, tmp_path, monkeypatch):
    store, _ = setup
    other = tmp_path / 'second'; other.mkdir(); inside = other / 'inside.txt'; inside.write_text('INSIDE')
    outside = tmp_path / 'outside.txt'; outside.write_text('OUTSIDE')
    agent = store.create('alice',driver=WEBOT,config={'workspaces':workspace.normalize_workspace_config({'paths':[str(other)]})})
    state = workspace.resolve_session_workspace('alice',agent.agent_id)
    bound = bind_file_target('read_file', {'filename':str(inside)},'alice',agent.agent_id,workspace=state)
    assert not file_target_outside_workspace(bound)
    monkeypatch.setattr('webot.runtime_settings.get_runtime_settings',lambda *_:SimpleNamespace(approval=SimpleNamespace(sandbox_security='strict', mode='bypass')))
    from webot.approval_actions import file_access_violation
    forbidden = bind_file_target('read_file', {'filename':str(outside)},'alice',agent.agent_id,workspace=state)
    assert file_access_violation(forbidden,'alice',agent.agent_id)
    assert asyncio.run(filemanager.read_file('alice',str(inside),session_id=agent.agent_id)).endswith('INSIDE')
    result = asyncio.run(filemanager.read_file('alice',str(outside),session_id=agent.agent_id))
    assert '❌' in result and 'OUTSIDE' not in result


def test_skills_and_supporting_files_follow_workspace_membership(setup, tmp_path):
    store, teams = setup
    agent = store.create('alice',driver=WEBOT)
    personal = skills.create_skill('alice', name='personal', content='---\nname: personal\ndescription: private user\n---\nShared')
    assert not skill_memory.list_memory('alice',session_id=agent.agent_id)
    entry = skill_memory.memory_target('alice','companion',create=True,session_id=agent.agent_id)
    entry['_path'].parent.mkdir(parents=True)
    entry['_path'].write_text('---\nname: companion\ndescription: Agent skill\n---\nInstructions')
    helper = entry['_path'].parent / 'helper.py'; helper.write_text('print("OK")')
    assert helper.is_relative_to(workspace.resolve_session_workspace('alice',agent.agent_id).root)
    teams.create('alice','project'); teams.add('alice','project',agent.agent_id)
    team_skill = skills.create_skill('alice',name='team-skill',team='project',content='---\nname: team-skill\ndescription: Team skill\n---\nTeam')
    catalog=skill_memory.list_memory('alice',session_id=agent.agent_id)
    assert {e['name'] for e in catalog} == {'companion','team-skill'}
    prompt=skills.build_user_skills_listing('alice',session_id=agent.agent_id)
    assert str(helper.parent/'SKILL.md') in prompt
    assert personal['path'] not in prompt
    assert Path(team_skill['path']).is_relative_to(workspace.team_workspace('alice','project'))
    teams.remove('alice','project',agent.agent_id)
    assert {e['name'] for e in skill_memory.list_memory('alice',session_id=agent.agent_id)} == {'companion'}
    with pytest.raises(ValueError): skill_memory.memory_target('alice','team-skill','project',session_id=agent.agent_id)


def test_custom_path_cannot_select_other_user_or_runtime_controls(setup):
    foreign=workspace.companion_workspace('bob','agent')
    with pytest.raises(ValueError): workspace.normalize_workspace_config({'paths':[str(foreign)]},user_id='alice')
    with pytest.raises(ValueError): workspace.normalize_workspace_config({'cli_root':str(foreign)},user_id='alice')
    from common.runtime_paths import CONFIG_DIR
    CONFIG_DIR.mkdir(parents=True,exist_ok=True)
    with pytest.raises(ValueError): workspace.normalize_workspace_config({'paths':[str(CONFIG_DIR)]},user_id='alice')


def test_landlock_restricts_python_to_multiple_selected_folders(tmp_path, monkeypatch):
    if not command_sandbox.landlock_available(): pytest.skip('Landlock not available on this host')
    monkeypatch.setattr(command_sandbox,'network_fence_available',lambda:False)
    first=tmp_path/'first'; first.mkdir()
    second=tmp_path/'second'; second.mkdir(); (second/'input.txt').write_text('SELECTED')
    outside=tmp_path/'outside.txt';outside.write_text('PRESERVE')
    script=first/'probe.py'
    script.write_text('''from pathlib import Path
import json
second=Path(%r); outside=Path(%r)
assert (second/'input.txt').read_text() == 'SELECTED'
(second/'written.txt').write_text('SUCCESS')
blocked=[]
for operation in ('read','write','delete'):
    try:
        if operation == 'read': outside.read_text()
        elif operation == 'write': outside.write_text('BAD')
        else: outside.unlink()
    except PermissionError: blocked.append(operation)
assert blocked == ['read','write','delete'], blocked
print('MULTI_WORKSPACE_SANDBOX_OK')
''' % (str(second),str(outside)))
    sandbox=command_sandbox.build_landlock_command(root=first,cwd=second,language='python',command='',
        script_path=script,python_executable=sys.executable,workspace_roots=(first,second),strict=True)
    try:
        result=subprocess.run(sandbox.argv,cwd=second,capture_output=True,text=True,timeout=15)
        assert result.returncode == 0, result.stderr
        assert 'MULTI_WORKSPACE_SANDBOX_OK' in result.stdout
        assert (second/'written.txt').read_text() == 'SUCCESS'
        assert outside.read_text() == 'PRESERVE'
        with pytest.raises(command_sandbox.SandboxUnavailable):
            command_sandbox.build_landlock_command(root=first,cwd=first,language='shell',command='true',
                python_executable=sys.executable,workspace_roots=(first,second),strict=True,access='read_path',target=str(outside))
    finally:
        sandbox.settings_path.unlink(missing_ok=True)
        import shutil
        shutil.rmtree(sandbox.temporary_dir,ignore_errors=True)


def test_workspace_api_does_not_persist_cli_origin_and_can_fix_missing_custom_folder(setup,tmp_path,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agents.routes import create_agents_router
    from agents.openai import create_openai_router
    from agents.messages import AgentReply
    store,_=setup
    class Gateway:
        async def ask(self,agent,*args,**kwargs):
            return AgentReply(ok=True,content=str(workspace.resolve_session_workspace(agent.owner,agent.agent_id).cwd))
        async def status(self,agent): return {}
        async def chat(self,agent,request): return {'cwd':str(workspace.resolve_session_workspace(agent.owner,agent.agent_id).cwd)}
    gateway=Gateway()
    app=FastAPI();app.include_router(create_agents_router(internal_token='test-token',verify_password=lambda *_:True,store=store,gateway=gateway))
    app.include_router(create_openai_router(internal_token='test-token',verify_password=lambda *_:True,store=store,gateway=gateway))
    origin=tmp_path/'origin';origin.mkdir()
    custom=tmp_path/'custom';custom.mkdir()
    headers={'Authorization':'Bearer test-token:alice'}
    with TestClient(app) as client:
        result=client.post('/v1/agents',headers=headers,json={'agent_id':'cli-agent','cli_workspace':str(origin),'workspaces':{'paths':[str(custom)]}})
        assert result.status_code==200,result.text
        assert str(origin) not in json.dumps(store.require('alice','cli-agent').config)
        response=client.get('/v1/agents/cli-agent/workspaces',headers=headers)
        assert response.json()['cwd']==str(origin)
        assert client.get('/v1/agents/cli-agent/workspace-origin',headers=headers).json()=={'cli':str(origin)}
        assert client.get('/v1/agents/cli-agent/workspace-origin',headers={'Authorization':'Bearer alice:password'}).status_code==403
        # MCP workers resolve via the live service, with no origin file or DB path.
        def broker(url,**kwargs):
            response=client.get('/v1/agents/cli-agent/workspace-origin',headers=kwargs['headers'])
            return SimpleNamespace(raise_for_status=lambda:None,json=response.json)
        monkeypatch.setenv('CLAWCROSS_WORKSPACE_ORIGIN_SERVICE','http://runtime-origin')
        monkeypatch.setenv('INTERNAL_TOKEN','test-token')
        with patch('requests.get',side_effect=broker):
            # The actual service process has no worker-only broker environment.
            def service_broker(url,**kwargs):
                with patch.dict('os.environ',{'CLAWCROSS_WORKSPACE_ORIGIN_SERVICE':''}):
                    return broker(url,**kwargs)
            with patch('requests.get',side_effect=service_broker):
                assert workspace.cli_workspace('alice','cli-agent')==str(origin)
        monkeypatch.delenv('CLAWCROSS_WORKSPACE_ORIGIN_SERVICE')
        new_origin=tmp_path/'next-origin';new_origin.mkdir()
        chat=client.post('/v1/chat/completions',headers=headers,json={'session_id':'cli-agent','messages':[{'role':'user','content':'test'}],'cli_workspace':str(new_origin)})
        assert chat.status_code==200,chat.text
        assert chat.json()['cwd']==str(new_origin)
        assert str(new_origin) not in json.dumps(store.require('alice','cli-agent').config)
        custom.rmdir()
        editable=client.get('/v1/agents/cli-agent/workspaces',headers=headers)
        assert editable.status_code==200 and editable.json()['error']
        fixed=client.patch('/v1/agents/cli-agent',headers=headers,json={'settings':{'workspaces':{'paths':[]}}})
        assert fixed.status_code==200,fixed.text


def test_subagents_inherit_automatic_scopes_without_copying_directory_paths(setup,tmp_path,monkeypatch):
    from webot import subagents
    from webot.subagents import create_subagent_record,upsert_subagent
    monkeypatch.setattr(subagents,'DEFAULT_DB_PATH',tmp_path/'subagents.db')
    store,teams=setup
    custom=tmp_path/'project';custom.mkdir()
    parent=store.create('alice',driver=WEBOT,agent_id='parent',config={'workspaces':workspace.normalize_workspace_config({'paths':[str(custom)]})})
    teams.create('alice','project');teams.add('alice','project',parent.agent_id)
    child_id='subagent__coder__child'
    record=create_subagent_record(agent_id='child',user_id='alice',session_id=child_id,agent_type='coder',name='Child',description='',parent_session='parent',workspace_mode='isolated')
    upsert_subagent(record)
    child=store.ensure('alice',child_id)
    parent_state=workspace.resolve_session_workspace('alice','parent')
    child_state=workspace.resolve_session_workspace('alice',child_id)
    assert child_state.folders[0]['path']!=parent_state.folders[0]['path']
    assert custom in child_state.roots
    assert workspace.team_workspace('alice','project') in child_state.roots
    assert str(custom) not in json.dumps(child.config)
    teams.remove('alice','project',parent.agent_id)
    assert 'team' not in [f['source'] for f in workspace.resolve_session_workspace('alice',child_id).folders]


def test_srt_policy_contains_every_workspace_without_exposing_settings(setup,tmp_path):
    roots=[tmp_path/'one',tmp_path/'two']
    for root in roots:root.mkdir()
    controls=tmp_path/'controls';controls.mkdir()
    policy=command_sandbox._policy(roots[0],controls/'settings.json',workspace_roots=roots,strict=True)
    for root in roots:
        assert str(root) in policy['filesystem']['allowRead']
        assert str(root) in policy['filesystem']['allowWrite']
    assert str(controls/'settings.json') not in policy['filesystem']['allowWrite']


def test_command_tool_uses_selected_secondary_directory(setup,tmp_path,monkeypatch):
    from webot.mcp import commander
    from webot.runtime_settings import RuntimeSettings
    if not command_sandbox.landlock_available(): pytest.skip('Landlock not available on this host')
    monkeypatch.setattr(command_sandbox,'network_fence_available',lambda:False)
    settings=RuntimeSettings(approval={'mode':'bypass','command_sandbox':'landlock','sandbox_security':'strict'})
    monkeypatch.setattr('webot.runtime_settings.get_runtime_settings',lambda *_:settings)
    store,_=setup
    second=tmp_path/'second';second.mkdir()
    agent=store.create('alice',driver=WEBOT,config={'workspaces':{'paths':[str(second)]}})
    outside=tmp_path/'outside.txt';outside.write_text('PRESERVE')
    code='''from pathlib import Path
assert Path.cwd() == Path(%r)
Path('formal.txt').write_text('FORMAL_PATH_OK')
try:
    Path(%r).unlink()
except PermissionError:
    print('FORMAL_PATH_OK_OUTSIDE_BLOCKED')
else:
    raise RuntimeError('Outside deletion unexpectedly allowed')
''' % (str(second),str(outside))
    result=asyncio.run(commander.run_command('alice',code,language='python',session_id=agent.agent_id,cwd=str(second),timeout_seconds=15))
    assert 'FORMAL_PATH_OK_OUTSIDE_BLOCKED' in result,result
    assert (second/'formal.txt').read_text()=='FORMAL_PATH_OK'
    assert outside.read_text()=='PRESERVE'


def test_default_project_api_rejects_destructive_changes(setup):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from teams.routes import create_teams_router
    store,teams=setup
    agent=store.create('alice',driver=WEBOT)
    app=FastAPI();app.include_router(create_teams_router(internal_token='test-token',verify_password=lambda *_:True,teams=teams))
    headers={'Authorization':'Bearer test-token:alice'}
    with TestClient(app) as client:
        for method,path,body in [('DELETE','/v1/teams/__default__',None),('PATCH','/v1/teams/__default__',{'name':'replaced'}),
                                  ('DELETE',f'/v1/teams/__default__/members/{agent.agent_id}',None),('POST','/v1/teams/__default__/import',None)]:
            result=client.request(method,path,headers=headers,json=body)
            assert result.status_code==400,result.text
    assert store.get('alice',agent.agent_id)


def test_strict_memory_files_stay_within_companion_workspace(setup,monkeypatch):
    from webot.runtime_settings import RuntimeSettings
    monkeypatch.setattr('webot.runtime_settings.get_runtime_settings',lambda *_:RuntimeSettings(approval={'mode':'bypass','sandbox_security':'strict'}))
    store,_=setup
    agent=store.create('alice',driver=WEBOT)
    created=json.loads(asyncio.run(filemanager.write_file('alice','private-skill','Private instructions',storage='memory',session_id=agent.agent_id)))
    assert created['success']
    assert 'Private instructions' in asyncio.run(filemanager.read_file('alice',created['id'],storage='memory',session_id=agent.agent_id))
    assert json.loads(asyncio.run(filemanager.delete_file('alice',created['id'],storage='memory',session_id=agent.agent_id)))['success']
