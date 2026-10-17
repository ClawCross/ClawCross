"""The existing ACP runtime over a managed SSH transport."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path

from external.acpx import AcpxAdapter, AcpxError
from connections.ssh import get_connections


class SSHAcpxAdapter(AcpxAdapter):
    def __init__(self, agent, manager=None):
        self.agent=agent;self.manager=manager or get_connections()
        connection=self.manager.store.get(agent.owner,agent.config['connection_id'])
        self._acpx_bin=connection['acpx'];self._node=connection['node']
        from external.session import native_workspace_cwd
        self._cwd=native_workspace_cwd(agent)
        self._pending_initial_prompt={}
        from external.acp_launcher import launch_settings
        launch=launch_settings(agent.config)
        self._agent_command=launch['agent_command'];self._agent_name=launch['agent_name']

    def _local_config_options(self, name):
        return self.agent.runtime.get('remote_config_options') or []

    async def _spawn_process(self, *command, **kwargs):
        connection_id=self.agent.config['connection_id']
        argv=list(command)
        if argv[0]!=self._acpx_bin:argv[0]=self._node
        files=[]
        for index,value in enumerate(argv[1:],1):
            scripts={str(Path(__file__).parents[1]/'external'/name) for name in ('native_history.mjs','native_replay.mjs')}
            if (argv[index-1] in {'--file','--mcp-config'} or value in scripts) and Path(value).is_file():
                content=Path(value).read_bytes()
                files.append({'path':value,'content':base64.b64encode(content).decode(),'connector':argv[index-1]=='--mcp-config'})
        offline=kwargs.pop('env',{}).get('npm_config_offline')=='true'
        kwargs.pop('stdin',None)
        if kwargs.get('stderr') is None:kwargs['stderr']=asyncio.subprocess.STDOUT
        namespace=hashlib.sha256((self.agent.owner+'\0'+self.agent.agent_id+'\0'+connection_id).encode()).hexdigest()[:32]
        try:
            return await self.manager.spawn(self.agent.owner,connection_id,{
                'action':'run','command':argv,'cwd':self._cwd,'namespace':namespace,'offline':offline,
                'files':files,'bridge_script':base64.b64encode(Path(__file__).with_name('bridge_stdio.py').read_bytes()).decode()},**kwargs)
        except Exception as exc:raise AcpxError(str(exc)) from exc

    async def _reconnect_transport(self, name):
        process=await self.manager.spawn(self.agent.owner,self.agent.config['connection_id'],
            {'action':'reconnect','acpx':self._acpx_bin,'node':self._node,'name':name,'cwd':self._cwd},
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:out,err=await asyncio.wait_for(process.communicate(),timeout=20)
        except BaseException:
            if process.returncode is None:process.kill()
            await process.wait();raise
        if process.returncode:raise AcpxError(err.decode(errors='replace')[-1000:])

    async def load_native_history(self, **kwargs):
        return {'status':'unavailable','detail':'Remote native history import is not available; captured ClawCross history is retained.', 'messages':[]}

    async def refresh_options(self, platform, name):
        try:
            output=await self._run_json(self._command_prefix(tool=platform,session_key=name)+['sessions','show',name],timeout_sec=15,allow_nonzero=False,offline=True)
            record=json.loads(output)
            if isinstance(record,list):record=record[-1]
            return (record.get('acpx') or {}).get('config_options') or record.get('configOptions') or []
        except (AcpxError,ValueError,AttributeError):return []
