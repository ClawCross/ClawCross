"""One SSH request, with JSON on stdin and native acpx events on stdout. No installation."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def main():
    payload=json.loads(sys.stdin.readline(8*1024*1024))
    env={**os.environ,'PATH':os.pathsep.join([str(Path.home()/'.local/bin'),str(Path.home()/'.clawcross/bin/node/node_modules/.bin'),os.environ.get('PATH','')])}
    runtime_root=Path(os.environ.get('CLAWCROSS_HOME') or Path.home()/'.clawcross')
    if payload.get('offline'):env['npm_config_offline']='true'
    if 'cwd' in payload:
        cwd=Path(os.path.expanduser(payload['cwd']))
        default_root=Path.home()/'.clawcross/workspaces'
        try:relative=cwd.relative_to(default_root)
        except ValueError:relative=None
        if relative is not None:
            cwd=runtime_root/'workspaces'/relative
            cwd.mkdir(parents=True,exist_ok=True)
        cwd=cwd.resolve()
        if not cwd.is_dir():raise ValueError('Remote workspace does not exist: '+str(cwd))
        payload['cwd']=str(cwd)
    if payload['action']=='workspace':
        print(json.dumps({'cwd':payload['cwd']}),flush=True);return 0
    if payload['action']=='probe':
        executable=os.path.expanduser(payload['acpx'])
        binary=shutil.which(executable,path=env['PATH'])
        result={'python':sys.version.split()[0],'acpx_available':bool(binary),'platforms':[]}
        if binary:
            help_text=subprocess.run([binary,'--help'],capture_output=True,text=True,env=env,timeout=15).stdout
            result['platforms']=sorted(set(re.findall(r'^\s*([a-z][a-z0-9_-]+)\s+\[options\].*Use\s+\1\s+agent\s*$',help_text,re.M|re.I) or
                re.findall(r'^\s{2}([a-z][a-z0-9_-]+)\s{2,}',help_text,re.M))-
                {'sessions','config','permissions','help','cancel','status','exec','set-mode','set-config','version'})
        print(json.dumps(result),flush=True);return 0
    if payload['action']=='reconnect':
        cwd=str(Path(os.path.expanduser(payload['cwd'])).resolve());record_id=None
        for file in (Path.home()/'.acpx/sessions').glob('*.json'):
            if file.stat().st_size>16*1024*1024:continue
            try:record=json.loads(file.read_text())
            except (OSError,ValueError):continue
            if record.get('name')==payload['name'] and record.get('cwd')==cwd and not record.get('closed'):
                record_id=record.get('acpx_record_id');break
        binary=shutil.which(os.path.expanduser(payload['acpx']),path=env['PATH'])
        modules=list(Path(binary).resolve().parent.glob('ipc-*.js')) if binary else []
        if not record_id:return 0
        if len(modules)!=1:raise ValueError('Remote acpx cannot reconnect MCP safely; existing conversation preserved')
        script='const ipc=await import(process.argv[1]);const fn=Object.values(ipc).find(v=>typeof v==="function"&&v.name==="terminateQueueOwnerForSession");if(!fn)throw Error("Reconnect unavailable");await fn(process.argv[2]);'
        return subprocess.run([payload['node'],'--input-type=module','-e',script,modules[0].as_uri(),str(record_id)],env=env).returncode
    if payload['action']!='run':raise ValueError('Unknown remote operation')
    cwd=os.path.expanduser(payload['cwd'])
    if cwd.startswith(str(Path.home()/'.clawcross/workspaces')+os.sep):Path(cwd).mkdir(parents=True,exist_ok=True)
    if not Path(cwd).is_dir():raise ValueError('Remote workspace does not exist: '+cwd)
    command=[str(value) for value in payload['command']]
    command[0]=os.path.expanduser(command[0])
    namespace=payload['namespace']
    if not re.fullmatch(r'[a-f0-9]{32}',namespace):raise ValueError('Invalid connection namespace')
    bridge_dir=runtime_root/'connections'/namespace
    bridge_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='request-',dir=bridge_dir) as temporary:
        for index,item in enumerate(payload.get('files',[])):
            content=base64.b64decode(item['content']);file=Path(temporary)/str(index)
            if item.get('connector'):
                connector=json.loads(content)
                for server in connector.get('mcpServers',[]):
                    if server.get('name')!='ClawCross':raise ValueError('Remote MCP connector must use the scoped ClawCross bridge')
                    config_path=bridge_dir/'bridge.json'
                    env_fields=server['env']
                    if isinstance(env_fields,list):env_fields={item['name']:item['value'] for item in env_fields}
                    config_path.write_text(json.dumps({'url':payload['bridge_url'],'token':env_fields['CLAWCROSS_BRIDGE_TOKEN']}))
                    if os.name!='nt':config_path.chmod(0o600)
                    server['command']=sys.executable
                    server['args']=['-u','-c',"import base64;exec(base64.b64decode('"+payload['bridge_script']+"'))"]
                    server['env']=[{'name':'CLAWCROSS_BRIDGE_CONFIG','value':str(config_path)}]
                file=bridge_dir/'mcp.json'
                content=json.dumps(connector).encode()
            file.write_bytes(content)
            if os.name!='nt':file.chmod(0o600)
            command=[str(file) if value==item['path'] else value for value in command]
        for index,value in enumerate(command):
            if index and command[index-1]=='--cwd':command[index]=cwd
        child=subprocess.Popen(command,stdin=subprocess.DEVNULL,env=env,cwd=cwd)
        return child.wait()


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:print('SSH Agent: '+str(exc),file=sys.stderr,flush=True);sys.exit(1)
