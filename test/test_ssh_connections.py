"""User ownership, Gateway routing and real SSH transport with an isolated CLI fixture."""
import asyncio
import base64
import json
import os
from pathlib import Path
try:import pwd
except ImportError:pwd=None
import shutil
import socket
import subprocess
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/backend'))
from connections.store import ConnectionStore
from connections.ssh import SSHConnections
from connections.acpx import SSHAcpxAdapter
from connections.routes import create_connections_router
from agents.store import AgentStore, ACPX, WEBOT
from agents.gateway import AgentGateway
from agents.routes import create_agents_router
from agents.messages import AgentMessage


def test_connection_owner_validation_and_registration(tmp_path,monkeypatch):
    store=ConnectionStore(tmp_path/'connections.db');manager=SSHConnections(store)
    agents=AgentStore(tmp_path/'agents.db');gateway=AgentGateway(store=agents)
    monkeypatch.setattr('connections.store._store',store);monkeypatch.setattr('connections.ssh._manager',manager)
    app=FastAPI();app.include_router(create_connections_router(internal_token='key',verify_password=lambda *_:False,agents=agents,gateway=gateway,manager=manager))
    app.include_router(create_agents_router(internal_token='key',verify_password=lambda *_:False,store=agents,gateway=gateway))
    with TestClient(app) as api:
        headers={'Authorization':'Bearer key:alice'}
        saved=api.post('/v1/connections',headers=headers,json={'name':'Laptop','host':'remote.example','identity_file':'/private/key'})
        assert saved.status_code==200;cid=saved.json()['id'];assert 'identity_file' not in saved.json()
        assert api.get('/v1/connections',headers={'Authorization':'Bearer key:bob'}).json()['data']==[]
        assert api.post('/v1/connections',headers=headers,json={'host':'-oProxyCommand=bad'}).status_code==400
        assert api.post('/v1/connections',headers=headers,json={'host':'safe','host_key_policy':'no'}).status_code==400
        created=api.post('/v1/agents',headers=headers,json={'platform':'claude','name':'Remote','connection_id':cid,'remote_cwd':'/project'})
        assert created.status_code==200;agent=created.json();assert agent['location']['kind']=='ssh'
        assert agents.require('alice',agent['agent_id']).driver==ACPX
        assert api.post('/v1/agents',headers={'Authorization':'Bearer key:bob'},json={'platform':'claude','connection_id':cid}).status_code==400
        assert api.post('/v1/agents',headers=headers,json={'platform':'webot','connection_id':cid}).status_code==400
        assert api.delete('/v1/connections/'+cid,headers=headers).status_code==409
        assert api.patch('/v1/connections/'+cid,headers=headers,json={'host':'other'}).status_code==409
        paused=api.post('/v1/connections/'+cid+'/disconnect',headers=headers)
        assert paused.status_code==200 and paused.json()['state']=='paused'
        assert not ConnectionStore(store.path).get('alice',cid)['enabled']
        with pytest.raises(RuntimeError,match='暂停'):asyncio.run(manager.connect('alice',cid))


@pytest.fixture
def ssh_endpoint(tmp_path):
    sshd=shutil.which('sshd') or '/usr/sbin/sshd'
    if pwd is None or not Path(sshd).is_file() or not shutil.which('ssh-keygen'):pytest.skip('Unix OpenSSH server/client not installed')
    host=tmp_path/'host';key=tmp_path/'key'
    for path in (host,key):subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(path)],check=True)
    authorized=tmp_path/'authorized';authorized.write_text(key.with_suffix('.pub').read_text())
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    config=tmp_path/'sshd_config';config.write_text(f'''Port {port}
ListenAddress 127.0.0.1
HostKey {host}
PidFile {tmp_path}/sshd.pid
AuthorizedKeysFile {authorized}
StrictModes no
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
AllowTcpForwarding yes
SetEnv CLAWCROSS_HOME={tmp_path}/remote-runtime
''')
    log=(tmp_path/'sshd.log').open('w');server=subprocess.Popen([sshd,'-D','-e','-f',str(config)],stdout=log,stderr=log)
    try:
        for _ in range(40):
            if server.poll() is not None:pytest.fail((tmp_path/'sshd.log').read_text())
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.1):break
            except OSError:time.sleep(.05)
        yield {'host':'127.0.0.1','username':pwd.getpwuid(os.getuid()).pw_name,'port':port,
               'identity_file':str(key),'known_hosts_file':str(tmp_path/'known_hosts'),'host_key_policy':'accept-new'}
    finally:
        server.terminate();server.wait(timeout=5);log.close()


@pytest.mark.asyncio
async def test_real_ssh_adapter_prompt_files_streaming_and_disconnect(tmp_path,ssh_endpoint,monkeypatch):
    cli=tmp_path/'acpx-fixture';cli.write_text('''#!/usr/bin/env python3
import json,sys,subprocess,os
from pathlib import Path
args=sys.argv[1:]
if '--help' in args:print('  codex    Codex Agent\\n  claude   Claude Agent');sys.exit(0)
if 'prompt' in args:
 content=json.loads(Path(args[args.index('--file')+1]).read_text());text=content[0]['text']
 if '--mcp-config' in args:
  servers=json.loads(Path(args[args.index('--mcp-config')+1]).read_text())['mcpServers']
  if servers:
   server=servers[0];env={**os.environ,**{item['name']:item['value'] for item in server['env']}}
   init=json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{}})+'\\n'
   query=json.dumps({'jsonrpc':'2.0','id':2,'method':'tools/list'})+'\\n'
   result=subprocess.run([server['command'],*server['args']],input=init+query,capture_output=True,text=True,env=env,timeout=15)
   if 'SCOPED_REMOTE' not in result.stdout:raise RuntimeError(result.stderr+result.stdout)
   text+='|MCP_OK'
 print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'remote:'+text}}}}),flush=True)
 print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'stopReason':'end_turn'}}),flush=True)
else:print(json.dumps({'acpx':{'config_options':[{'id':'model','currentValue':'fixture','options':[{'value':'fixture','name':'Fixture'}]}]}}))
''');cli.chmod(0o700)
    store=ConnectionStore(tmp_path/'connections.db');manager=SSHConnections(store)
    connection=store.save('alice',{**ssh_endpoint,'acpx':str(cli)})
    agents=AgentStore(tmp_path/'agents.db');cwd=tmp_path/'remote-workspace';cwd.mkdir()
    agent=agents.create('alice',driver=ACPX,config={'platform':'claude','connection_id':connection['id'],'remote_cwd':str(cwd)})
    from external import tool_bridge
    class BridgeHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            token=self.headers.get('authorization','').removeprefix('Bearer ')
            valid=tool_bridge._tokens.get(token)==('alice',agent.agent_id)
            self.send_response(200 if valid else 403);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps({'description':'SCOPED_REMOTE'} if valid else {'detail':'Invalid scope'}).encode())
        def log_message(self,*args):pass
    backend=ThreadingHTTPServer(('127.0.0.1',0),BridgeHandler)
    thread=threading.Thread(target=backend.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('PORT_AGENT',str(backend.server_port))
    monkeypatch.setattr('connections.store._store',store);monkeypatch.setattr('connections.ssh._manager',manager)
    monkeypatch.setattr('external.tool_bridge.get_store',lambda:agents)
    monkeypatch.setattr('external.session.identity_sections',lambda _:{'base_rules':'INITIAL_RULES'})
    try:
        probe=await manager.probe('alice',connection['id']);assert probe['state']=='connected';assert probe['remote']['platforms']==['claude','codex']
        adapter=SSHAcpxAdapter(agent,manager);assert adapter._cwd==str(cwd)
        prompt=tmp_path/'prompt.json';prompt.write_text(json.dumps([{'type':'text','text':'hello'}]))
        events=[]
        raw=await adapter._run_json_command([str(cli),'--cwd',str(cwd),'claude','prompt','--file',str(prompt)],timeout_sec=10,allow_nonzero=False,on_event=events.append)
        assert 'remote:hello' in raw and any(event.get('text')=='remote:hello' for event in events)
        assert not list((tmp_path/'remote-runtime').glob('connections/*/request-*'))
        assert await adapter.refresh_options('claude','session')
        await adapter.ops_cancel(tool='claude',session_key='session',timeout_sec=10)
        await manager.disconnect('alice',connection['id']);assert manager.card('alice',connection['id'])['state']=='disconnected'
        await manager.probe('alice',connection['id']);assert manager.card('alice',connection['id'])['state']=='connected'
        gateway=AgentGateway(store=agents)
        reply=await gateway.ask(agent,AgentMessage(text='Gateway first',sender='u:alice'))
        assert reply.ok and 'Gateway first' in reply.content and 'MCP_OK' in reply.content and 'INITIAL_RULES' in reply.content
        current=agents.require('alice',agent.agent_id)
        reply=await gateway.ask(current,AgentMessage(text='Gateway incremental',sender='u:alice'))
        assert reply.ok and 'INITIAL_RULES' not in reply.content
        assert (await gateway.history(current))[ -1]['role']=='assistant'
        await gateway.control(current,'reset')
        reset=agents.require('alice',agent.agent_id);assert not reset.runtime.get('negotiation_sent')
        reply=await gateway.ask(reset,AgentMessage(text='After reset',sender='u:alice'))
        assert reply.ok and 'INITIAL_RULES' in reply.content
        await gateway.destroy(agents.require('alice',agent.agent_id))
        agent=agents.create('alice',driver=ACPX,config={'platform':'claude','connection_id':connection['id']})
        reply=await gateway.ask(agent,AgentMessage(text='Default remote workspace',sender='u:alice'))
        assert reply.ok
        default=agents.require('alice',agent.agent_id)
        assert Path(default.runtime['acp_cwd']).is_relative_to(tmp_path/'remote-runtime/workspaces')
        await gateway.destroy(default)
    finally:
        await manager.close();backend.shutdown();backend.server_close();thread.join(timeout=2)
