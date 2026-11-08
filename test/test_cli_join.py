"""The join command preserves identity and never guesses or leaks invitation capabilities."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.cli.commands import join

LINK='https://chat.example/group-guest#private-invitation'


def arguments(**values):
    return SimpleNamespace(invite=LINK,agent='owned',native_session=None,current=False,new=False,
        platform='codex',name=None,member_name=None,user='alice',prepare=False,**values)


@pytest.fixture
def backend(monkeypatch, tmp_path):
    from src.backend.common import local_accounts
    monkeypatch.setattr(local_accounts, 'USER_FILES_DIR', tmp_path/'users')
    monkeypatch.setattr(local_accounts, 'USERS_FILE', tmp_path/'users.json')
    calls=[];agents={'owned':{'agent_id':'owned','platform':'codex','settings':{'persona':'original'}}}
    native=[{'session_id':'original-thread','ticket':'selection-ticket','cwd':'/project','registered_agent_id':''}]
    def request(method,url,headers=None,data=None,timeout=30,**_):
        path=url.removeprefix('http://127.0.0.1:51200');calls.append((method,path,data,headers))
        if path=='/health':return 200,{'ok':True}
        if path.startswith(('/v1/agents/native-sessions?', '/v1/agents/native-sessions/prepare?')):return 200,{'sessions':native,'next_cursor':None}
        if path.endswith('/test-connection'):return 200,{'ok':True}
        if path.startswith('/groups/') and path.endswith('/connection'):
            return 200,{'group_id':'rg_joined','background_running':True,'state':'connected','transport':'http-poll'}
        if path=='/v1/agents/native-sessions':
            assert data['ticket']=='selection-ticket'
            agents['resumed']={'agent_id':'resumed','platform':'codex','native_history':{'status':'loaded'}}
            return 200,agents['resumed']
        if path=='/groups/join':return 200,{'group_id':'rg_joined','title':'未来之室','token':'never-output-this','messages':[{'content':'private history'}]}
        if path=='/v1/agents' and method=='POST':
            agents[data['agent_id']]={**data,'settings':{}}
            return 200,agents[data['agent_id']]
        if path.startswith('/v1/agents/'):
            aid=path.rsplit('/',1)[1]
            return (200,agents[aid]) if aid in agents else (404,{'detail':'unknown Agent'})
        raise AssertionError((method,path))
    monkeypatch.setattr(join,'_configuration',lambda:('http://127.0.0.1:51200','local-secret'))
    monkeypatch.setattr(join.api,'_req',request)
    monkeypatch.setattr(join,'_bridge',lambda *_, **kwargs:None)
    return calls,agents,native


def test_existing_agent_keeps_identity_and_is_not_recreated(backend):
    calls,agents,_=backend
    original=dict(agents['owned'])
    for _ in range(2):
        result=join.connect(arguments(),'alice')
        assert result['agent_id']=='owned'
    assert agents['owned']==original
    assert all(path!='/v1/agents' for _,path,_,_ in calls)
    payloads=[data for _,path,data,_ in calls if path=='/groups/join']
    assert payloads==[{'invite':LINK,'agents':['owned'],'name':''}]*2
    assert all(headers=={'Authorization':'Bearer local-secret:alice'} for _,path,_,headers in calls if path!='/health')


def test_native_session_is_registered_with_a_server_ticket(backend):
    calls,_,_=backend
    args=arguments();args.agent=None;args.native_session='original-thread'
    result=join.connect(args,'alice')
    assert result['agent_id']=='resumed' and result['source']=='native'
    assert result['native_history']=='loaded'
    assert not any(path=='/v1/agents' for _,path,_,_ in calls)
    assert next(body for _,path,body,_ in calls if path=='/groups/join')['agents']==['resumed']


def test_registered_native_session_reuses_its_agent(backend):
    calls,_,native=backend;native[0]['registered_agent_id']='owned'
    args=arguments();args.agent=None;args.native_session='original-thread'
    assert join.connect(args,'alice')['agent_id']=='owned'
    assert not any(method=='POST' and path.startswith('/v1/agents') for method,path,_,_ in calls)


def test_current_session_never_falls_back_to_an_unrelated_or_new_agent(backend,monkeypatch):
    calls,_,_=backend;args=arguments();args.agent=None;args.current=True
    monkeypatch.delenv('CODEX_THREAD_ID',raising=False)
    with pytest.raises(join.JoinError,match='没有新建'):
        join.connect(args,'alice')
    assert not calls
    monkeypatch.setenv('CODEX_THREAD_ID','unlisted-current-thread')
    with pytest.raises(join.JoinError,match='没有新建'):
        join.connect(args,'alice')
    assert not any(method=='POST' for method,_,_,_ in calls)


def test_explicit_new_mode_is_idempotent_and_does_not_inherit_project_access(backend,tmp_path,monkeypatch):
    calls,_,_=backend;args=arguments();args.agent=None;args.new=True
    monkeypatch.chdir(tmp_path)
    first=join.connect(args,'alice');second=join.connect(args,'alice')
    assert first['agent_id']==second['agent_id']
    created=[body for _,path,body,_ in calls if path=='/v1/agents']
    assert len(created)==1 and not created[0].get('cli_workspace')
    assert created[0]['workspaces']=={'companion':True,'user_shared':False,'cli':False,'teams':False,'paths':[]}
    assert created[0]['meta']['acp']['native_tools'] is False
    assert first['owner']=='alice' and first['agent_ready'] is False


@pytest.mark.parametrize('link',['https://chat.example/map','http://remote.example/group-guest#t',
    'https://user:pass@host/group-guest#t','https://host/group-guest?token=t#ticket','https://host:wrong/group-guest#t'])
def test_invalid_links_make_no_requests(backend,link):
    args=arguments();args.invite=link
    with pytest.raises(join.JoinError):join.connect(args,'alice')
    assert backend[0]==[]


def test_service_setup_is_explicit(backend):
    args=arguments()
    with patch.object(join.api,'_req',return_value=(0,{})),patch.object(join,'_lifecycle') as lifecycle:
        with pytest.raises(join.JoinError,match='--prepare'):join.connect(args,'alice')
    lifecycle.assert_not_called()


def test_prepare_does_not_restart_an_existing_runtime(backend):
    args=arguments();args.prepare=True
    with patch.object(join.api,'_req',return_value=(0,{})),patch.object(join,'_launcher_running',return_value=True),patch.object(join,'_lifecycle') as lifecycle:
        with pytest.raises(join.JoinError,match='不会重启'):join.connect(args,'alice')
    lifecycle.assert_not_called()


def test_prepare_starts_bridge_and_initializes_without_a_prompt(backend,monkeypatch):
    calls,_,_=backend;args=arguments();args.prepare=True
    original=join.api._req;health=[]
    def probe(method,url,**kwargs):
        if url.endswith('/health'):
            health.append(True)
            return (0,{}) if len(health)==1 else (200,{'ok':True})
        return original(method,url,**kwargs)
    monkeypatch.setattr(join.api,'_req',probe)
    with patch.object(join,'_launcher_running',return_value=False),patch.object(join,'_lifecycle') as lifecycle:
        assert join.connect(args,'alice')['ok']
    lifecycle.assert_called_once_with('start','--bridge')
    assert any(path.endswith('/test-connection') for _,path,_,_ in calls)
    assert not any(path.endswith(('/ask','/inbox','/messages')) for _,path,_,_ in calls)


def test_native_prepare_is_explicit_and_requires_local_host_proof(backend):
    calls,_,_=backend;args=arguments();args.prepare=True;args.agent=None;args.native_session='original-thread'
    assert join.connect(args,'alice')['ok']
    method,path,_,headers=next(call for call in calls if '/native-sessions/prepare?' in call[1])
    assert method=='POST' and headers['X-ClawCross-Host-Browse']=='local-secret'


def test_autostart_is_opt_in_and_runs_after_join(backend,monkeypatch):
    from launch import autostart
    calls,_,_=backend
    enabled=[]
    def enable():
        assert any(path=='/groups/join' for _,path,_,_ in calls)
        enabled.append(True)
        return {'ok':True,'enabled':True,'trigger':'boot'}
    monkeypatch.setattr(autostart,'enable',enable)
    args=arguments()
    assert 'autostart' not in join.connect(args,'alice') and not enabled
    args.autostart=True
    assert join.connect(args,'alice')['autostart']['enabled'] and enabled==[True]


def test_membership_is_not_reported_as_a_working_background_connection(backend,monkeypatch):
    original=join.api._req
    def request(method,url,**kwargs):
        if url.endswith('/connection'):return 200,{'state':'stopped','background_running':False}
        return original(method,url,**kwargs)
    monkeypatch.setattr(join.api,'_req',request)
    result=join.connect(arguments(),'alice')
    assert result['joined'] and not result['ok']


def test_json_result_excludes_tokens_and_group_history(backend,capsys):
    assert join.main([LINK,'--agent','owned','--json'],default_user='alice')==0
    output=capsys.readouterr().out
    assert json.loads(output)['agent_id']=='owned'
    assert all(secret not in output for secret in ['private-invitation','local-secret','never-output-this','private history'])


def test_installed_cli_joins_with_the_original_agent_and_returns_json(tmp_path):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def respond(self,body):
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps(body).encode())
        def do_GET(self):
            self.respond({'ok':True} if self.path=='/health' else
                         {'state':'connected','background_running':True,'transport':'http-poll'} if self.path.endswith('/connection') else
                         {'agent_id':'original','platform':'codex'})
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path,data,self.headers.get('Authorization')))
            self.respond({'group_id':'rg_fixture','title':'未来之室'})
    node=shutil.which('node')
    if not node:pytest.skip('Node is required for the npm command')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    root=Path(__file__).resolve().parents[1]
    home=tmp_path/'home';config=home/'config';config.mkdir(parents=True)
    (config/'.env').write_text(f'INTERNAL_TOKEN=fixture-secret\nPORT_AGENT={server.server_port}\n')
    env={k:v for k,v in os.environ.items() if not k.startswith(('CLAWCROSS_','PORT_')) and k not in {'INTERNAL_TOKEN','CLAW_USER','CLI_USER'}}
    env.update(CLAWCROSS_HOME=str(home),CLAW_USER='alice')
    entry=os.environ.get('CLAWCROSS_TEST_ENTRY',str(root/'bin/clawcross.js'))
    try:
        result=subprocess.run([node,entry,'join',LINK,'--agent','original','--json'],env=env,cwd=tmp_path,text=True,capture_output=True,timeout=25)
        assert result.returncode==0,result.stderr
        assert json.loads(result.stdout)['agent_id']=='original'
        assert calls==[('/groups/join',{'invite':LINK,'agents':['original'],'name':''},'Bearer fixture-secret:alice')]
        assert not (home/'venv').exists()
    finally:
        server.shutdown();server.server_close();thread.join()
