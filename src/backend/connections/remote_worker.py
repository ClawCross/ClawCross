"""Managed remote ACP byte stream with a JSON bootstrap. No installation."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import hashlib
import shlex
import signal
import threading


def stdio_agent(payload,env,runtime_root):
    spec=payload['launcher']
    if spec.get('alias'):
        value=None
        for source in (runtime_root/'config/acp-launchers.json',Path.home()/'.acpx/config.json'):
            try:
                value=json.loads(source.read_text()).get('agents',{}).get(spec['alias'])
                if isinstance(value,dict):value=value.get('command')
                if value:break
            except (OSError,ValueError):pass
        if not value:raise ValueError('Remote ACP launcher alias is missing')
        command=value if isinstance(value,list) else shlex.split(value)
    elif spec.get('argv'):command=spec['argv']
    else:
        script=runtime_root/'bin/node/node_modules'/spec['package']/'dist/index.js'
        command=[spec['node'],str(script)] if script.is_file() else [spec['binary']]
    if not command or any(not isinstance(value,str) or '\0' in value for value in command):
        raise ValueError('Invalid remote ACP command')
    command[0]=shutil.which(os.path.expanduser(command[0]),path=env['PATH']) or os.path.expanduser(command[0])
    namespace=payload['namespace']
    if not re.fullmatch(r'[a-f0-9]{32}',namespace):raise ValueError('Invalid connection namespace')
    bridge_dir=runtime_root/'connections'/namespace
    bridge_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    servers=payload.get('mcp_servers',[])
    for server in servers:
        if server.get('name')!='ClawCross':raise ValueError('Only scoped ClawCross MCP is supported')
        fields={item['name']:item['value'] for item in server['env']}
        if not fields.get('CLAWCROSS_BRIDGE_TOKEN'):raise ValueError('Missing MCP credential')
        config_path=bridge_dir/'bridge.json'
        with tempfile.NamedTemporaryFile(mode='w',dir=bridge_dir,delete=False) as stream:
            temporary=Path(stream.name)
            json.dump({'url':payload['bridge_url'],'token':fields['CLAWCROSS_BRIDGE_TOKEN']},stream)
        os.replace(temporary,config_path)
        server.update(command=sys.executable,args=['-u','-c',"import base64;exec(base64.b64decode('"+payload['bridge_script']+"'))"],
                      env=[{'name':'CLAWCROSS_BRIDGE_CONFIG','value':str(config_path)}])
    native=payload.get('resume_session_id')
    if not native:
        records=[]
        for path in (Path.home()/'.acpx/sessions').glob('*.json'):
            try:
                if path.stat().st_size>16*1024*1024:continue
                row=json.loads(path.read_text())
                if row.get('name')==payload.get('legacy_name') and Path(row.get('cwd','')).resolve()==Path(payload['cwd']).resolve():
                    records.append((path.stat().st_mtime,row))
            except (OSError,ValueError):pass
        if records:
            row=max(records,key=lambda item:item[0])[1]
            native=row.get('acp_session_id') or row.get('agent_session_id')
            rid=row.get('acpx_record_id')
            if rid:
                lock=Path.home()/'.acpx/queues'/(hashlib.sha256(str(rid).encode()).hexdigest()[:24]+'.lock')
                try:
                    owner=json.loads(lock.read_text());pid=owner.get('pid')
                    if type(pid)is int and pid>1:
                        try:os.kill(pid,0)
                        except ProcessLookupError:pass
                        else:raise ValueError('Old ACP queue still owns this session')
                except FileNotFoundError:pass
        if payload.get('requires_resume') and not native:
            raise ValueError('Previous native session metadata is unavailable; conversation preserved')
    print(json.dumps({'ready':True,'cwd':payload['cwd'],'mcpServers':servers,'native_resume_id':native}),flush=True)
    # Supervise the native process group. SSH EOF must stop its children even
    # if the ACP program ignores EOF; stdout remains a direct bounded SSH pipe.
    child=subprocess.Popen(command,stdin=subprocess.PIPE,env=env,cwd=payload['cwd'],
                           **({'start_new_session':True} if os.name!='nt' else {}))
    stopped=threading.Event()
    def stop():
        if stopped.is_set():return
        stopped.set()
        try:
            if os.name!='nt':os.killpg(child.pid,signal.SIGTERM)
            elif child.poll() is None:child.terminate()
        except ProcessLookupError:pass
        try:child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                if os.name!='nt':os.killpg(child.pid,signal.SIGKILL)
                else:child.kill()
            except ProcessLookupError:pass
            child.wait()
    def pump():
        try:
            while block:=os.read(0,8192):
                child.stdin.write(block);child.stdin.flush()
        except (OSError,BrokenPipeError):pass
        finally:
            try:child.stdin.close()
            except OSError:pass
            stop()
    for sig in (signal.SIGTERM,signal.SIGINT,*([signal.SIGHUP] if hasattr(signal,'SIGHUP') else [])):
        signal.signal(sig,lambda *_:threading.Thread(target=stop,daemon=True).start())
    threading.Thread(target=pump,daemon=True).start()
    try:return child.wait()
    finally:stop()


def main():
    # Do not buffer bytes belonging to the subsequent ACP stream.
    line=bytearray()
    while len(line)<8*1024*1024:
        byte=os.read(0,1)
        if not byte or byte==b'\n':break
        line.extend(byte)
    payload=json.loads(line)
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
        launchers=payload.get('launchers',{})
        packages=payload.get('packages',{})
        installed=[name for name,argv in launchers.items() if shutil.which(argv[0],path=env['PATH'])]
        installed.extend(name for name,package in packages.items() if
            (runtime_root/'bin/node/node_modules'/package[0]/'dist/index.js').is_file() or shutil.which(package[2],path=env['PATH']))
        result={'python':sys.version.split()[0],'acp_available':True,'requires_acpx':False,'platforms':sorted(set(installed))}
        print(json.dumps(result),flush=True);return 0
    if payload['action']=='acp':return stdio_agent(payload,env,runtime_root)
    raise ValueError('Unknown remote operation')


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:print('SSH Agent: '+str(exc),file=sys.stderr,flush=True);sys.exit(1)
