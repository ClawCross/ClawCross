from flask import Flask
from ops import components
from frontend.proxies.components import register_component_routes


def test_local_component_discovery_and_missing_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(components, 'BIN_DIR', tmp_path)
    monkeypatch.setattr(components.shutil, 'which', lambda _: None)
    monkeypatch.setattr(components.sys, 'platform', 'linux')
    local = tmp_path / 'node/node_modules/.bin/srt'
    local.parent.mkdir(parents=True)
    local.write_text('test')
    state = components.component_status('srt')
    assert state['installed'] and not state['ready']
    assert state['missing'] == ['npm', 'bwrap', 'socat', 'rg']
    assert not state['can_install']


def test_install_api_requires_login_explicit_same_origin_request(monkeypatch):
    app = Flask(__name__)
    app.secret_key = 'test'
    register_component_routes(app)
    calls = []
    monkeypatch.setattr('frontend.proxies.components.start_install', lambda name: calls.append(name) or {'state': 'installing'})
    client = app.test_client()
    assert client.post('/proxy_components/srt').status_code == 401
    with client.session_transaction() as state:
        state['user_id'] = 'test'
    assert client.post('/proxy_components/srt').status_code == 403
    assert client.post('/proxy_components/srt', headers={'X-Requested-With': 'ClawCross', 'Origin': 'https://evil.test'}).status_code == 403
    assert calls == []
    assert client.post('/proxy_components/srt', headers={'X-Requested-With': 'ClawCross'}).status_code == 200
    assert calls == ['srt']
    assert client.get('/proxy_components/unknown').status_code == 400


def test_installer_uses_fixed_command_without_shell(monkeypatch):
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        assert 'shell' not in kwargs
        return type('Result', (), {'returncode': 0})()
    monkeypatch.setattr(components.subprocess, 'run', run)
    components._install('acpx')
    assert commands[0][-2:] == ['install', 'acpx']
    assert components.component_status('acpx')['state'] == 'complete'
    components._jobs.clear()


def test_system_dependencies_use_explicit_fixed_package_list(monkeypatch):
    commands = []
    monkeypatch.setattr(components.sys, 'platform', 'linux')
    monkeypatch.setattr(components.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(components.shutil, 'which', lambda name: '/usr/bin/' + name)
    def run(command, **kwargs):
        commands.append(command)
        assert 'shell' not in kwargs
        return type('Result', (), {'returncode': 0})()
    monkeypatch.setattr(components.subprocess, 'run', run)
    components._install('srt-system')
    assert commands == [['/usr/bin/apt-get', 'install', '--yes', '--no-upgrade', 'bubblewrap', 'socat', 'ripgrep']]
    components._jobs.clear()
