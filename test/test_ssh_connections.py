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
import shlex
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
    from external.acp_direct import Manager, DirectAdapter
    from external import session, history
    from external.acpx import AcpxError
    server=Path(__file__).parent/'fixtures/acp_agent.py'
    store=ConnectionStore(tmp_path/'connections.db');manager=SSHConnections(store)
    actor=Manager();manager._actor=actor
    connection=store.save('alice',ssh_endpoint)
    agents=AgentStore(tmp_path/'agents.db');cwd=tmp_path/'remote-workspace';cwd.mkdir()
    def create(aid,cwd):
        return agents.create('alice',agent_id=aid,driver=ACPX,config={'platform':'acp',
            'connection_id':connection['id'],'remote_cwd':str(cwd),
            'meta':{'acp':{'agent_command':shlex.join([sys.executable,str(server),'--check-mcp'])}}})
    agent=create('first',cwd)
    from external import tool_bridge
    class BridgeHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            token=self.headers.get('authorization','').removeprefix('Bearer ')
            valid=tool_bridge._tokens.get(token) in {('alice','first'),('alice','second'),('alice','default')}
            self.send_response(200 if valid else 403);self.send_header('Content-Type','application/json');self.end_headers()
            self.wfile.write(json.dumps({'description':'SCOPED_REMOTE'} if valid else {'detail':'Invalid scope'}).encode())
        def log_message(self,*args):pass
    backend=ThreadingHTTPServer(('127.0.0.1',0),BridgeHandler)
    thread=threading.Thread(target=backend.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('PORT_AGENT',str(backend.server_port))
    monkeypatch.setattr('connections.store._store',store);monkeypatch.setattr('connections.ssh._manager',manager)
    monkeypatch.setattr('external.acp_direct.manager',lambda:actor)
    monkeypatch.setattr('external.acp_direct.STATE_DIR',tmp_path)
    monkeypatch.setattr('external.tool_bridge.STATE_DIR',tmp_path)
    monkeypatch.setattr('external.tool_bridge.get_store',lambda:agents)
    monkeypatch.setattr(history,'_STORE',history.ExternalAgentHistoryStore(tmp_path/'history'))
    monkeypatch.setattr(session,'identity_sections',lambda _:{'base_rules':'INITIAL_RULES'})
    monkeypatch.setattr(session,'build_dynamic_context',lambda *a,**k:{'identity_base_rules':k['identity']['base_rules']})
    monkeypatch.setattr('external.acpx.get_acpx_adapter',lambda **_:(_ for _ in ()).throw(AssertionError('acpx forbidden')))
    def wire(directory):
        return [json.loads(line) for line in (directory/'wire.jsonl').read_text().splitlines()]
    try:
        probe=await manager.probe('alice',connection['id'])
        assert probe['state']=='connected' and probe['remote']['requires_acpx'] is False
        # Separate event loops use the same SSH master and actor, with no loop-bound pipe errors.
        await asyncio.gather(*(asyncio.to_thread(lambda:asyncio.run(manager.probe('alice',connection['id']))) for _ in range(3)))
        gateway=AgentGateway(store=agents)
        events=[]
        reply=await gateway.ask(agent,AgentMessage(text='Gateway first',sender='u:alice'))
        assert reply.ok and 'Gateway first' in reply.content and 'INITIAL_RULES' in reply.content
        sid=agents.require('alice',agent.agent_id).runtime['native_resume_id']
        othercwd=tmp_path/'second';othercwd.mkdir();other=create('second',othercwd)
        replies=await asyncio.gather(gateway.ask(agents.require('alice','first'),AgentMessage(text='incremental')),
                                     gateway.ask(other,AgentMessage(text='parallel')))
        assert all(r.ok for r in replies) and 'INITIAL_RULES' not in replies[0].content
        firstrows=wire(cwd);secondrows=wire(othercwd)
        assert sum(r['event']=='initialize' for r in firstrows)==1
        assert any(r['event']=='mcp' for r in firstrows+secondrows)
        direct=DirectAdapter(agents.require('alice','first'),agents,actor)
        def opts():return {'tool':'acp','session_key':session.runtime_session(agents.require('alice','first')),'timeout_sec':30}
        replies=await asyncio.gather(*(direct.prompt_with_trace(prompt_text=str(i),**opts()) for i in range(8)))
        assert [r.text for r in replies]==list(map(str,range(8)))
        rows=wire(cwd)
        assert [r['event'] for r in rows if r['event'] in {'begin','end'}]==['begin','end']*10
        # Cancel a real remote process, then load the same native session on an explicit next request.
        running=asyncio.create_task(direct.prompt_with_trace(prompt_text='slow',**opts()))
        async with asyncio.timeout(10):
            while not any(r.get('text')=='slow' for r in wire(cwd)):await asyncio.sleep(.02)
        remote_pid=wire(cwd)[-1]['pid'];running.cancel()
        with pytest.raises(asyncio.CancelledError):await running
        async with asyncio.timeout(8):
            while Path('/proc/'+str(remote_pid)).exists():await asyncio.sleep(.05)
        assert (await direct.prompt_with_trace(prompt_text='resumed',**opts())).text=='resumed'
        assert agents.require('alice','first').runtime['native_resume_id']==sid
        assert sum(r['event']=='session/new' for r in wire(cwd))==1
        assert sum(r['event']=='session/load' for r in wire(cwd))>=1
        await gateway.control(agents.require('alice','first'),'reset')
        assert not agents.require('alice','first').runtime.get('negotiation_sent')
        assert (await gateway.ask(agents.require('alice','first'),AgentMessage(text='After reset'))).ok
        await gateway.destroy(agents.require('alice','first'));await gateway.destroy(agents.require('alice','second'))
        agent=create('default','')
        reply=await gateway.ask(agent,AgentMessage(text='Default remote workspace'))
        assert reply.ok
        default=agents.require('alice',agent.agent_id)
        assert Path(default.runtime['acp_cwd']).is_relative_to(tmp_path/'remote-runtime/workspaces')
        await gateway.destroy(default)
        await manager.disconnect('alice',connection['id']);assert manager.card('alice',connection['id'])['state']=='disconnected'
    finally:
        await actor.shutdown();backend.shutdown();backend.server_close();thread.join(timeout=3)
