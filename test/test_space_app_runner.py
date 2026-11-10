"""ClawCross runs a space's app for its owner: apps come from folders, start with a fresh key from the
group server, stop on request, show a crash's log, and start again after ClawCross restarts."""

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

os.environ.setdefault('CLAWCROSS_HOME', tempfile.mkdtemp(prefix='clawcross-test-'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/backend'))

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from groups.config import service_url
from groups import space_apps
from groups.client import ClientError
from groups.facade import GroupFacade, client_router

pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='needs Node.js')

ECHO = """/* 回声 · Echo — writes down how it was started.
 * 简介：测试用的小程序。
 *
 */
require('fs').writeFileSync('started.json', JSON.stringify({server: process.env.CLAWCROSS_SPACE_SERVER, key: process.env.CLAWCROSS_SPACE_KEY}));
setInterval(() => {}, 1000);
"""
BROKEN = "/* 坏掉 · Broken — exits at once.\n */\nconsole.error('连不上群聊服务器'); process.exit(1);\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    runners = []
    original_init = space_apps.SpaceAppRunner.__init__
    def track(runner, client):
        original_init(runner, client)
        runners.append(runner)
    monkeypatch.setattr(space_apps.SpaceAppRunner, '__init__', track)
    shipped, mine = tmp_path / 'shipped', tmp_path / 'state' / 'space-apps'
    for root, name, code in [(shipped, 'echo', ECHO.replace('回声', '自带回声')), (shipped, 'only-shipped', ECHO),
                             (mine, 'echo', ECHO), (mine, 'broken', BROKEN)]:
        (root / name).mkdir(parents=True)
        (root / name / 'server.js').write_text(code)
    (mine / 'kami.key').write_text('not an app')
    for name, value in [('BUILTIN', shipped), ('STATE_DIR', tmp_path / 'state'), ('DATA_DIR', tmp_path / 'data'), ('LOGS_DIR', tmp_path / 'logs')]:
        monkeypatch.setattr(space_apps, name, value)
    yield tmp_path
    for runner in runners:
        runner.close()


class Server:
    """The space's group server, as the runner sees it through the group client."""
    agents = None

    def __init__(self):
        self.keys = 0
        self.removed = []

    def manage(self, owner, gid, action, fields):
        assert action == 'app_remove'
        self.removed.append(gid)
        return {}

    def require(self, owner, gid):
        if owner != 'alice':
            raise ClientError('未加入此群，或群凭证已撤销', 403)
        return {'url': service_url()}

    def request(self, row, method, path, body=None):
        if (method, path) == ('GET', '/group'):
            return {'app': {'name': 'test'}}
        assert (method, path) == ('POST', '/app/key')
        self.keys += 1
        return {'key': f'app_key_{self.keys}'}


def wait(check, seconds=10):
    deadline = time.time() + seconds
    while not check():
        assert time.time() < deadline, 'timed out'
        time.sleep(0.1)


def test_own_apps_are_listed_beside_and_over_the_shipped_ones(home):
    apps = space_apps.catalogue()
    assert set(apps) == {'echo', 'only-shipped', 'broken'}
    assert apps['echo'] | {'entry': ''} == {'id': 'echo', 'name': '回声', 'about': '测试用的小程序。', 'source': 'mine', 'entry': ''}
    assert apps['only-shipped']['source'] == 'builtin'
    assert (apps['broken']['name'], apps['broken']['about']) == ('坏掉', 'exits at once.')


def test_start_runs_the_app_with_a_fresh_key_and_stop_ends_it(home):
    server = Server()
    runner = space_apps.SpaceAppRunner(server)
    assert runner.start('alice', 'rg_1', 'echo')['status'] == 'running'
    started = home / 'state' / 'space-apps' / 'echo' / 'started.json'
    wait(started.exists)
    assert json.loads(started.read_text()) == {'server': service_url(), 'key': 'app_key_1'}
    assert [row['app'] for row in json.loads((home / 'data' / 'space-app-runs.json').read_text())] == ['echo']
    assert runner.status('bob', 'rg_1') == {'app': None, 'status': 'stopped'}   # another local user's view
    proc = runner.runs[('alice', 'rg_1')]['proc']
    # Starting another app for the space replaces the first.
    runner.start('alice', 'rg_1', 'only-shipped')
    assert proc.poll() is not None
    proc = runner.runs[('alice', 'rg_1')]['proc']
    assert runner.stop('alice', 'rg_1') == {'app': None, 'status': 'stopped'}
    assert proc.poll() is not None
    assert server.removed == ['rg_1']   # stopping takes the app off the space: plain chat again
    assert json.loads((home / 'data' / 'space-app-runs.json').read_text()) == []


def test_a_crashed_app_shows_its_log_and_strangers_or_unknown_apps_are_refused(home):
    runner = space_apps.SpaceAppRunner(Server())
    runner.start('alice', 'rg_1', 'broken')
    wait(lambda: runner.status('alice', 'rg_1')['status'] == 'exited')
    assert '连不上群聊服务器' in runner.status('alice', 'rg_1')['error']
    with pytest.raises(ClientError) as refused:
        runner.start('bob', 'rg_1', 'echo')
    assert refused.value.status == 403
    with pytest.raises(ClientError) as missing:
        runner.start('alice', 'rg_1', 'nowhere')
    assert missing.value.status == 404


def test_after_a_restart_runs_start_again_and_a_leftover_process_is_stopped(home):
    before = space_apps.SpaceAppRunner(Server())
    before.start('alice', 'rg_1', 'echo')
    leftover = before.runs[('alice', 'rg_1')]['proc']
    # ClawCross died without stopping its apps; the next one finds the record.
    after = space_apps.SpaceAppRunner(Server())
    after.restore()
    wait(lambda: leftover.poll() is not None)
    wait(lambda: after.status('alice', 'rg_1')['status'] == 'running')
    assert after.runs[('alice', 'rg_1')]['proc'].pid != leftover.pid
    proc = after.runs[('alice', 'rg_1')]['proc']
    after.close()   # ClawCross stopping: the app stops, the record stays for next time
    assert proc.poll() is not None
    assert [row['gid'] for row in json.loads((home / 'data' / 'space-app-runs.json').read_text())] == ['rg_1']


def test_routes_list_start_report_and_stop(home):
    facade = GroupFacade(Server())
    app = FastAPI()
    app.include_router(client_router(facade, internal_token='internal', verify_password=lambda *_: False))
    alice = {'Authorization': 'Bearer internal:alice'}
    with TestClient(app) as api:
        listed = api.get('/groups/space-apps', headers=alice).json()
        assert {app['id'] for app in listed['apps']} == {'echo', 'only-shipped', 'broken'}
        assert 'entry' not in listed['apps'][0] and listed['folder'].endswith('space-apps')
        assert api.post('/groups/rg_1/app-run', json={'app': 'echo'}, headers=alice).json()['status'] == 'running'
        assert api.get('/groups/rg_1/app-run', headers=alice).json()['name'] == '回声'
        assert api.post('/groups/rg_1/app-run', json={'app': 'echo'}, headers={'Authorization': 'Bearer internal:bob'}).status_code == 403
        assert api.delete('/groups/rg_1/app-run', headers=alice).json()['status'] == 'stopped'


def test_a_run_whose_space_is_gone_is_forgotten_at_restart(home):
    record = home / 'data' / 'space-app-runs.json'
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps([{'owner': 'bob', 'gid': 'rg_gone', 'app': 'echo', 'pid': 0}]))
    runner = space_apps.SpaceAppRunner(Server())   # bob is not in that space (any more)
    runner.restore()
    wait(lambda: ('bob', 'rg_gone') not in runner.runs)
    assert json.loads(record.read_text()) == []


def test_remote_groups_cannot_start_local_apps(home):
    server = Server()
    server.require = lambda owner, gid: {'url':'https://remote.example/groups'}
    runner = space_apps.SpaceAppRunner(server)
    with pytest.raises(ClientError, match='群聊所在主机'):
        runner.start('alice', 'rg_remote', 'echo')
    assert not runner.runs and server.keys == 0


def test_group_delete_stops_the_real_process_and_forgets_restart_record(home):
    server = Server()
    facade = GroupFacade(server)
    facade.apps.start('alice', 'rg_1', 'echo')
    proc = facade.apps.runs[('alice', 'rg_1')]['proc']
    actions = []
    def manage(owner, gid, action, fields):
        actions.append(action)
        if owner != 'alice':
            raise ClientError('只有群主能删除群聊', 403)
        return {'deleted': gid}
    server.manage = manage
    with pytest.raises(ClientError):
        facade.delete('bob', 'rg_1')
    assert proc.poll() is None
    assert facade.delete('alice', 'rg_1') == {'deleted': 'rg_1'}
    assert proc.poll() is not None
    assert json.loads(facade.apps.record.read_text()) == []
    assert actions == ['delete', 'delete']  # no app_remove request to a now-deleted group
    restored = space_apps.SpaceAppRunner(server)
    restored.restore()
    assert restored.runs == {}


def test_host_admin_deletion_is_reaped_but_temporary_outage_is_preserved(home):
    server = Server()
    runner = space_apps.SpaceAppRunner(server)
    runner.start('alice', 'rg_1', 'echo')
    proc = runner.runs[('alice', 'rg_1')]['proc']
    def outage(*args, **kwargs):
        raise ClientError('群服务器不可用', 503)
    server.request = outage
    runner.reconcile()
    assert proc.poll() is None and ('alice', 'rg_1') in runner.runs
    def deleted(*args, **kwargs):
        raise ClientError('群凭证已经删除', 401)
    server.request = deleted
    wait(lambda: proc.poll() is not None, seconds=5)  # real lifecycle monitor, no UI request needed
    wait(lambda: json.loads(runner.record.read_text()) == [])


def test_removing_the_app_also_reaps_its_process(home):
    server = Server()
    runner = space_apps.SpaceAppRunner(server)
    runner.start('alice', 'rg_1', 'echo')
    proc = runner.runs[('alice', 'rg_1')]['proc']
    server.request = lambda *args, **kwargs: {'app': None}
    runner.reconcile()
    assert proc.poll() is not None
    assert json.loads(runner.record.read_text()) == []
