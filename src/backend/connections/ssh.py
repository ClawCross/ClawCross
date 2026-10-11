"""Managed SSH sessions and a narrow, Agent-authenticated MCP return channel."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import tempfile
from contextlib import contextmanager

import httpx
from fastapi import FastAPI, Request, Response
import uvicorn

from connections.store import get_connection_store, public_connection


class SSHError(RuntimeError):pass


class BridgeServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        yield

    def install_signal_handlers(self):
        pass


class SSHConnections:
    def __init__(self, store=None):
        self.store=store or get_connection_store()
        self._sessions={};self._locks={};self._errors={}
        self._relay=None;self._relay_task=None;self._relay_lock=asyncio.Lock()
        self._socket_root=None

    def card(self, owner, connection_id):
        connection=self.store.get(owner,connection_id);active=self._sessions.get((owner,connection_id))
        return {**public_connection(connection),'state':'paused' if not connection['enabled'] else 'connected' if active and active['process'].returncode is None else 'disconnected',
                'error':self._errors.get((owner,connection_id),'')}

    def command(self, connection, *, multiplex=True):
        binary=shutil.which('ssh')
        if not binary:raise SSHError('OpenSSH client is not installed')
        cmd=[binary,'-T','-o','BatchMode=yes','-o','ConnectTimeout=10',
             '-o','ForwardAgent=no','-o','ForwardX11=no','-o','PermitLocalCommand=no',
             '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
             '-o','StrictHostKeyChecking='+connection['host_key_policy']]
        if connection['port']!=22:cmd+=['-p',str(connection['port'])]
        if connection['username']:cmd+=['-l',connection['username']]
        if connection['identity_file']:cmd+=['-i',os.path.expanduser(connection['identity_file']),'-o','IdentitiesOnly=yes']
        if connection['known_hosts_file']:cmd+=['-o','UserKnownHostsFile='+os.path.expanduser(connection['known_hosts_file'])]
        if multiplex and os.name!='nt':cmd+=['-S',self.socket_path(connection)]
        return cmd

    def socket_path(self, connection):
        if self._socket_root is None:self._socket_root=Path(tempfile.mkdtemp(prefix='clawcross-ssh-'))
        root=self._socket_root
        key=hashlib.sha256((str(self.store.path)+connection['id']+str(os.getpid())).encode()).hexdigest()[:24]
        return str(root/key)

    async def relay(self):
        async with self._relay_lock:
            if self._relay:return self._relay_port
            app=FastAPI()
            @app.post('/external/tool-bridge')
            async def forward(request:Request):
                body=await request.body()
                if len(body)>256000:return Response('Request too large',status_code=413)
                port=os.getenv('PORT_AGENT','51200')
                async with httpx.AsyncClient(timeout=None,trust_env=False) as client:
                    upstream=await client.post(f'http://127.0.0.1:{port}/external/tool-bridge',content=body,
                        headers={'Content-Type':'application/json','Authorization':request.headers.get('authorization','')})
                return Response(upstream.content,status_code=upstream.status_code,media_type='application/json')
            listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(64)
            self._relay_port=listener.getsockname()[1]
            self._relay=BridgeServer(uvicorn.Config(app,log_level='error',access_log=False,lifespan='off'))
            self._relay_task=asyncio.create_task(self._relay.serve(sockets=[listener]))
            for _ in range(100):
                if self._relay.started:return self._relay_port
                if self._relay_task.done():await self._relay_task
                await asyncio.sleep(.01)
            raise SSHError('MCP return channel did not start')

    async def connect(self, owner, connection_id):
        key=(owner,connection_id);lock=self._locks.setdefault(key,asyncio.Lock())
        async with lock:
            active=self._sessions.get(key)
            if active and active['process'].returncode is None:return active
            connection=self.store.get(owner,connection_id)
            if not connection['enabled']:raise SSHError('SSH 连接已暂停，请在设备管理中重新连接')
            port=await self.relay()
            cmd=self.command(connection,multiplex=False)
            if os.name!='nt':cmd+=['-M','-S',self.socket_path(connection)]
            cmd+=['-N','-o','ExitOnForwardFailure=yes','-o','LogLevel=VERBOSE',
                  '-R',f'127.0.0.1:0:127.0.0.1:{port}',connection['host']]
            process=await asyncio.create_subprocess_exec(*cmd,stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
            error=[]
            async def allocated():
                while True:
                    line=await process.stderr.readline()
                    if not line:raise SSHError('\n'.join(error[-3:]) or 'SSH connection closed')
                    text=line.decode(errors='replace').strip();error.append(text)
                    match=re.search(r'Allocated port (\d+)',text)
                    if match:return int(match[1])
            try:
                remote_port=await asyncio.wait_for(allocated(),timeout=15)
            except BaseException as exc:
                if process.returncode is None:process.kill()
                await process.wait();self._errors[key]=str(exc) or 'SSH connection timed out'
                raise
            active={'process':process,'port':remote_port,'connection':connection}
            self._sessions[key]=active;self._errors.pop(key,None)
            async def monitor():
                while await process.stderr.readline():pass
                await process.wait()
                if self._sessions.get(key) is active:
                    self._sessions.pop(key,None);self._errors[key]='SSH connection closed; reconnect to continue'
            active['monitor']=asyncio.create_task(monitor())
            return active

    async def disconnect(self, owner, connection_id):
        key=(owner,connection_id)
        async with self._locks.setdefault(key,asyncio.Lock()):
            active=self._sessions.pop(key,None)
            if active:
                process=active['process']
                if process.returncode is None:process.terminate()
                try:await asyncio.wait_for(process.wait(),timeout=3)
                except asyncio.TimeoutError:process.kill();await process.wait()
                await active['monitor']
            self._errors.pop(key,None)

    async def spawn(self, owner, connection_id, payload, **kwargs):
        active=await self.connect(owner,connection_id);connection=active['connection']
        script=base64.b64encode(Path(__file__).with_name('remote_worker.py').read_bytes()).decode()
        executable=connection['python'] if connection['python'].startswith('~/') else '"'+connection['python']+'"'
        remote=executable+' -u -c "import base64;exec(base64.b64decode(\''+script+'\'))"'
        payload={**payload,'bridge_url':f'http://127.0.0.1:{active["port"]}/external/tool-bridge'}
        command=self.command(connection)+[connection['host'],remote]
        process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,**kwargs)
        try:
            process.stdin.write(json.dumps(payload).encode()+b'\n');await process.stdin.drain();process.stdin.close()
        except BaseException:
            if process.returncode is None:process.kill()
            await process.wait();raise
        return process

    async def probe(self, owner, connection_id):
        connection=self.store.get(owner,connection_id)
        process=await self.spawn(owner,connection_id,{'action':'probe','acpx':connection['acpx']},
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:out,err=await asyncio.wait_for(process.communicate(),timeout=25)
        except BaseException:
            if process.returncode is None:process.kill()
            await process.wait();raise
        if process.returncode:raise SSHError(err.decode(errors='replace').strip()[-1000:] or 'Remote probe failed')
        return {**self.card(owner,connection_id),'remote':json.loads(out)}

    async def resolve_workspace(self, owner, connection_id, cwd):
        process=await self.spawn(owner,connection_id,{'action':'workspace','cwd':cwd},
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:out,err=await asyncio.wait_for(process.communicate(),timeout=15)
        except BaseException:
            if process.returncode is None:process.kill()
            await process.wait();raise
        if process.returncode:raise SSHError(err.decode(errors='replace')[-1000:])
        return json.loads(out)['cwd']

    async def close(self):
        for owner,connection_id in list(self._sessions):await self.disconnect(owner,connection_id)
        if self._relay:
            self._relay.should_exit=True
            await self._relay_task
            self._relay=None;self._relay_task=None
        if self._socket_root:
            shutil.rmtree(self._socket_root);self._socket_root=None


_manager=None
def get_connections():
    global _manager
    if _manager is None:_manager=SSHConnections()
    return _manager
