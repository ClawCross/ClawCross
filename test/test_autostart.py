"""Startup registration is reversible, private, and never restarts current work."""
import json
from pathlib import Path
import plistlib
import stat
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from launch import autostart


@pytest.fixture
def install(tmp_path, monkeypatch):
    home = tmp_path / 'private home'
    for attribute, suffix in {'CLAWCROSS_HOME':'', 'CONFIG_DIR':'config', 'PID_DIR':'run',
                              'LOGS_DIR':'logs', 'VENV_DIR':'venv'}.items():
        monkeypatch.setattr(autostart.paths, attribute, home / suffix)
    autostart.paths.CONFIG_DIR.mkdir(parents=True)
    monkeypatch.setattr(autostart.paths, 'ENV_FILE', home / 'config/.env')
    autostart.paths.ENV_FILE.write_text('INTERNAL_TOKEN=not-in-output\n')
    python = home / 'venv/python'
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(autostart.paths, 'venv_python', lambda:python)
    # Isolate ALL captured runtime roots, not only the service registration.
    monkeypatch.setattr(autostart.paths, 'set_subprocess_env', lambda env:{**env,
        'CLAWCROSS_HOME':str(home),'CLAWCROSS_LOG_DIR':str(home/'logs')})
    monkeypatch.setattr(autostart, '_registration', lambda kind:(home/('startup.'+kind),'fixture'))
    monkeypatch.setenv('LLM_API_KEY','do-not-capture-an-api-key')
    monkeypatch.setenv('CODEX_THREAD_ID','do-not-capture-a-current-thread')
    return home


def test_linux_enable_status_disable_are_idempotent_and_never_start_a_service(install,monkeypatch):
    calls=[]
    def command(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0,stdout='yes\n' if args[0]=='loginctl' else '',stderr='')
    monkeypatch.setattr(autostart,'_kind',lambda:'systemd')
    monkeypatch.setattr(autostart,'_run',command)
    result=autostart.enable()
    assert result=={'ok':True,'enabled':True,'backend':'systemd','profile':'bridge','trigger':'boot','warnings':[]}
    record=json.loads((install/'config/autostart.json').read_text())
    assert record['profile']=='bridge'
    assert 'LLM_API_KEY' not in record['environment'] and 'CODEX_THREAD_ID' not in record['environment']
    if __import__('os').name!='nt':
        assert stat.S_IMODE((install/'config/autostart.json').stat().st_mode)==0o600
    assert autostart.status()['enabled']
    autostart.enable();autostart.disable()
    count=len(calls);autostart.disable()
    assert len(calls)==count and not autostart.status()['enabled']
    assert not any(any(part in {'start','restart','stop','--now','/Run'} for part in args) for args in calls)


def test_linux_does_not_claim_boot_when_linger_is_denied(install,monkeypatch):
    monkeypatch.setattr(autostart,'_kind',lambda:'systemd')
    monkeypatch.setattr(autostart,'_run',lambda args,**kwargs:
        SimpleNamespace(returncode=1 if args[0]=='loginctl' else 0,stdout='',stderr='denied'))
    result=autostart.enable()
    assert result['enabled'] and result['trigger']=='login' and result['warnings']


def test_failed_registration_rolls_back_its_files(install,monkeypatch):
    monkeypatch.setattr(autostart,'_kind',lambda:'systemd')
    monkeypatch.setattr(autostart,'_run',lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('no user bus')))
    with pytest.raises(RuntimeError,match='no user bus'):
        autostart.enable()
    assert not (install/'config/autostart.json').exists() and not (install/'startup.systemd').exists()


def test_mac_launch_agent_has_literal_arguments_and_no_browser(install,monkeypatch):
    calls=[]
    monkeypatch.setattr(autostart,'_kind',lambda:'launchd')
    monkeypatch.setattr(autostart.os,'getuid',lambda:1000,raising=False)
    monkeypatch.setattr(autostart,'_run',lambda args,**kwargs:calls.append(args))
    assert autostart.enable()['trigger']=='login'
    spec=plistlib.loads((install/'startup.launchd').read_bytes())
    assert spec['RunAtLoad'] and spec['KeepAlive']=={'SuccessfulExit':False}
    assert spec['ProgramArguments'][-2:]==['--state',str(install/'config/autostart.json')]
    assert calls==[['launchctl','enable','gui/1000/fixture']]


def test_windows_task_is_least_privilege_and_reuses_one_task(install,monkeypatch):
    calls=[]
    monkeypatch.setattr(autostart,'_kind',lambda:'windows')
    monkeypatch.setattr(autostart,'_run',lambda args,**kwargs:calls.append(args))
    assert autostart.enable()['trigger']=='login'
    spec=ET.fromstring((install/'startup.windows').read_bytes())
    ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
    assert spec.find('.//t:RunLevel',ns).text=='LeastPrivilege'
    assert spec.find('.//t:LogonType',ns).text=='InteractiveToken'
    assert spec.find('.//t:ExecutionTimeLimit',ns).text=='PT0S'
    assert Path(spec.find('.//t:Command',ns).text).parts[-2:]==('venv','python')
    assert '/Run' not in calls[0] and calls[0][-1]=='/F'


def test_systemd_paths_escape_specifiers_environment_and_quotes():
    spec=autostart._unit(['/usr/bin/python3','/path with spaces/%n/$HOME/"x".py']).decode()
    assert '%%n/$$HOME/\\"x\\".py' in spec
    with pytest.raises(ValueError):autostart._unit(['/path\nExecStart=bad'])


def test_existing_full_runtime_profile_is_preserved(install,monkeypatch):
    (install/'run').mkdir()
    (install/'run/clawcross.pid').write_text('123')
    (install/'run/profile.json').write_text(json.dumps({'pid':123,'mode':'full'}))
    monkeypatch.setattr(autostart,'_kind',lambda:'systemd')
    monkeypatch.setattr(autostart,'_run',lambda args,**kwargs:SimpleNamespace(returncode=0,stdout='yes',stderr=''))
    assert autostart.enable()['profile']=='full'


def test_npx_cache_cannot_be_registered_as_a_persistent_install(install,monkeypatch):
    monkeypatch.setattr(autostart,'ROOT',Path('/home/user/.npm/_npx/temporary/node_modules/clawcross'))
    with pytest.raises(RuntimeError,match='持久安装'):autostart.enable()


def test_status_outputs_no_tokens_or_captured_environment(install,monkeypatch,capsys):
    monkeypatch.setattr(autostart,'_kind',lambda:'systemd')
    monkeypatch.setattr(autostart,'_run',lambda args,**kwargs:SimpleNamespace(returncode=0,stdout='yes',stderr=''))
    autostart.enable()
    assert autostart.main(['status','--json'])==0
    text=capsys.readouterr().out
    assert json.loads(text)['enabled']
    assert all(value not in text for value in ('not-in-output','PATH','do-not-capture'))
