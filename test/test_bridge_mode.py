"""The forwarding daemon needs neither a local web UI nor an LLM setup wizard."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

ROOT=Path(__file__).resolve().parents[1]


def control(monkeypatch,tmp_path):
    monkeypatch.syspath_prepend(str(ROOT/'launch'))
    spec=importlib.util.spec_from_file_location('bridge_runtime_control',ROOT/'launch/runtime_control.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    for key,value in {'RUN_DIR':tmp_path/'run','LOG_DIR':tmp_path/'logs',
                      'WORKSPACE_DIR':tmp_path/'workspace','LAUNCHER_PID':tmp_path/'run/clawcross.pid',
                      'TUNNEL_PID':tmp_path/'run/tunnel.pid'}.items():
        monkeypatch.setattr(module,key,value)
    return module


def test_bridge_start_probes_only_agent_and_group_and_never_creates_magic_login(monkeypatch,tmp_path):
    module=control(monkeypatch,tmp_path)
    for name in ('_migrate_if_needed','ensure_core','_ensure_config','_clear_public_domain'):
        monkeypatch.setattr(module,name,lambda:None)
    monkeypatch.setattr(module,'_stop_pid',lambda path:None)
    for name in ('_magic_links','_check_model','_start_tunnel'):
        monkeypatch.setattr(module,name,lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('web-only startup path')))
    monkeypatch.setattr(module,'_process_env',lambda **kwargs:{'CLAWCROSS_REQUIRE_LLM_MODEL':'1'})
    urls=[];process_env=[]
    monkeypatch.setattr(module,'_probe',lambda url:urls.append(url) or True)
    monkeypatch.setattr(module.subprocess,'Popen',lambda *args,**kwargs:
        process_env.append(kwargs['env']) or SimpleNamespace(pid=123,poll=lambda:None))
    args=SimpleNamespace(bridge=True,foreground=False,no_channel=False,no_tunnel=False,no_openclaw=False)
    assert module.start(args)==0
    assert urls==['http://127.0.0.1:51203/relay/health','http://127.0.0.1:51200/health']
    assert process_env[0]['CLAWCROSS_BRIDGE_MODE']=='1' and process_env[0]['GROUP_SERVER_HOST']=='127.0.0.1'
    assert json.loads((tmp_path/'run/profile.json').read_text())['mode']=='bridge'


def test_boot_supervisor_observes_an_existing_instance_without_install_or_restart(monkeypatch,tmp_path):
    module=control(monkeypatch,tmp_path)
    pids=iter([123,123,None])
    monkeypatch.setattr(module,'_pid',lambda _:next(pids))
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    for name in ('_migrate_if_needed','ensure_core','_ensure_config','_stop_pid'):
        monkeypatch.setattr(module,name,lambda *args:(_ for _ in ()).throw(AssertionError('interrupted current service')))
    args=SimpleNamespace(bridge=True,supervised=True,foreground=True,no_channel=False,no_tunnel=False,no_openclaw=False)
    assert module.start(args)==0


@pytest.mark.skipif(os.environ.get('CLAWCROSS_BRIDGE_INTEGRATION')!='1' or os.name=='nt',
                    reason='Opt in to isolated real daemon startup on POSIX')
def test_boot_entry_starts_only_bridge_and_stops_all_its_children(tmp_path):
    from launch.autostart import MARKER
    home=tmp_path/'runtime'
    (home/'config').mkdir(parents=True)
    sockets=[]
    try:
        for _ in range(5):
            sock=socket.socket();sock.bind(('127.0.0.1',0));sockets.append(sock)
        ports=dict(zip(('PORT_AGENT','PORT_SCHEDULER','PORT_OASIS','PORT_GROUPS','PORT_FRONTEND'),
                       (sock.getsockname()[1] for sock in sockets)))
    finally:
        for sock in sockets:sock.close()
    (home/'config/.env').write_text('INTERNAL_TOKEN=isolated-bridge-fixture\nLLM_MODEL=\n'+
                                  ''.join(f'{key}={value}\n' for key,value in ports.items()))
    env={key:value for key,value in os.environ.items() if not key.startswith((
        'CLAWCROSS_','PORT_','LLM_','TINYFISH_','NONEBOT_')) and key not in {'INTERNAL_TOKEN','PUBLIC_DOMAIN'}}
    env.update(CLAWCROSS_HOME=str(home),CLAWCROSS_VENV_DIR=str(Path(sys.executable).parents[1]),
               CLAWCROSS_CONFIG_DIR=str(home/'config'),CLAWCROSS_RUN_DIR=str(home/'run'),
               CLAWCROSS_LOG_DIR=str(home/'logs'),CLAWCROSS_DATA_DIR=str(home/'data'),
               CLAWCROSS_WORKSPACE_DIR=str(home/'workspace'),CLAWCROSS_BIN_DIR=str(home/'bin'),
               CLAWCROSS_STATE_DIR=str(home))
    env.update({key:str(value) for key,value in ports.items()})
    state=home/'config/autostart.json'
    state.write_text(json.dumps({'managed_by':MARKER,'enabled':True,'profile':'bridge',
                                'code_root':str(ROOT),'python':sys.executable,'environment':env}))
    process=subprocess.Popen([sys.executable,str(ROOT/'launch/autostart.py'),'run','--state',str(state)],
                             env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
    def listening(port):
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.2):return True
        except OSError:return False
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            if process.poll() is not None:
                pytest.fail('Bridge boot entry exited; inspect isolated autostart.log at '+str(home/'logs'))
            try:
                with opener.open(f'http://127.0.0.1:{ports["PORT_AGENT"]}/health',timeout=1) as response:
                    if response.status==200:break
            except (OSError,urllib.error.URLError):pass
            time.sleep(.1)
        else:pytest.fail('Bridge API did not start')
        assert listening(ports['PORT_GROUPS'])
        assert all(not listening(ports[key]) for key in ('PORT_FRONTEND','PORT_OASIS','PORT_SCHEDULER'))
        assert not any((home/'run'/name).exists() for name in ('front.pid','oasis_server.pid','scheduler_service.pid'))
        assert json.loads((home/'run/profile.json').read_text())['mode']=='bridge'
    finally:
        process.terminate()
        try:process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);process.wait()
        process.stderr.close()
    deadline=time.monotonic()+3
    while any(listening(ports[key]) for key in ('PORT_AGENT','PORT_GROUPS')) and time.monotonic()<deadline:
        time.sleep(.1)
    assert not listening(ports['PORT_AGENT']) and not listening(ports['PORT_GROUPS'])
