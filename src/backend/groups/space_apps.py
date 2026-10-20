"""Space apps run by ClawCross for a space's owner.

An app is a folder with a ``server.js``: the examples shipped in ``examples/space-apps`` and the
owner's own in ``<state dir>/space-apps`` (an own folder of the same name wins). Starting one
asks the space's group server for a fresh app key and runs ``node server.js`` with the server and
key in its environment; stopping ends it and takes it off the space. Runs are remembered, so
ClawCross starts them again after a restart.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from common.runtime_paths import DATA_DIR, LOGS_DIR, PROJECT_ROOT, STATE_DIR
from groups.client import ClientError

BUILTIN = PROJECT_ROOT / 'examples' / 'space-apps'
# Each app gets its own process group, so stopping it also stops anything it started.
DETACH = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
# server.js opens with "/* 名字 · English name — what people and agents do here." and a line "简介：…".
HEADER = re.compile(r'/\*\s*(?P<name>[^·\n]+?)\s*·[^—\n]*—\s*(?P<about>.*?)(?:\n\s*\*\s*\n|\*/)', re.S)
SUMMARY = re.compile(r'简介[:：]\s*(.+)')


def own_folder() -> Path:
    return STATE_DIR / 'space-apps'


def catalogue() -> dict:
    apps = {}
    for source, root in (('builtin', BUILTIN), ('mine', own_folder())):
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir()):
            entry = folder / 'server.js'
            if not entry.is_file():
                continue
            head = entry.read_text(errors='replace')[:3000]
            found, summary = HEADER.search(head), SUMMARY.search(head)
            about = summary[1].strip() if summary else ' '.join(line.strip(' *') for line in found['about'].splitlines()).strip() if found else ''
            apps[folder.name] = {'id': folder.name, 'name': found['name'].strip() if found else folder.name,
                                 'about': about[:300], 'source': source, 'entry': str(entry)}
    return apps


def tail(path: Path, lines=8) -> str:
    try:
        return '\n'.join(path.read_text(errors='replace').splitlines()[-lines:])
    except OSError:
        return ''


class SpaceAppRunner:
    def __init__(self, client):
        self.client = client
        self.runs = {}        # (owner, gid) -> {'app', 'name', 'proc', 'started', 'error'}
        self.lock = threading.RLock()
        self.closed = False
        self.wakeup = threading.Event()
        self.monitor = None

    @property
    def record(self) -> Path:
        return DATA_DIR / 'space-app-runs.json'

    @staticmethod
    def log(gid) -> Path:
        return LOGS_DIR / 'space-apps' / (gid + '.log')

    def save(self):
        rows = [{'owner': owner, 'gid': gid, 'app': run['app'], 'pid': run['proc'].pid if run.get('proc') else 0}
                for (owner, gid), run in self.runs.items()]
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text(json.dumps(rows, ensure_ascii=False))

    def status(self, owner, gid) -> dict:
        with self.lock:
            run = self.runs.get((owner, gid))
            if not run:
                return {'app': None, 'status': 'stopped'}
            proc = run.get('proc')
            running = proc is not None and proc.poll() is None
            return {'app': run['app'], 'name': run['name'], 'started': run['started'],
                    'status': 'running' if running else 'starting' if proc is None and not run['error'] else 'exited',
                    'error': '' if running else run['error'] or tail(self.log(gid))}

    def start(self, owner, gid, app_id) -> dict:
        app = catalogue().get(app_id)
        if not app:
            raise ClientError('没有这个小程序', 404)
        node = shutil.which('node')
        if not node:
            raise ClientError('运行小程序需要先安装 Node.js 18 或更新版本', 400)
        row = self.client.require(owner, gid)
        from groups.config import service_url, service_key
        if row['url'].rstrip('/') != service_url().rstrip('/') or row.get('via'):
            raise ClientError('小程序只能在群聊所在主机启动；本地群聊可通过 Hub 公网链接分享', 400)
        # The group server checks that this member owns the space; a new key disconnects the old app.
        key = self.client.request(row, 'POST', '/app/key', {})['key']
        state = DATA_DIR / 'space-app-data' / gid / app_id
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock:
            if self.closed:
                raise ClientError('小程序服务正在停止', 503)
            # A deletion may have completed while the new app key was being requested.
            self.client.require(owner, gid)
            self._stop(owner, gid)
            log = self.log(gid)
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('ab') as out:
                out.write(f'\n--- {time.strftime("%Y-%m-%d %H:%M:%S")} 启动 {app["name"]}\n'.encode())
                proc = subprocess.Popen([node, app['entry']], cwd=str(Path(app['entry']).parent), stdin=subprocess.DEVNULL,
                                        stdout=out, stderr=subprocess.STDOUT, **DETACH,
                                        env={**os.environ, 'CLAWCROSS_SPACE_SERVER': row['url'], 'CLAWCROSS_SPACE_KEY': key, 'CLAWCROSS_SPACE_LOCAL_TOKEN': service_key(), 'CLAWCROSS_SPACE_DATA_DIR': str(state)})
            self.runs[(owner, gid)] = {'app': app_id, 'name': app['name'], 'proc': proc, 'started': time.time(), 'error': ''}
            self.save()
            self._watch()
        return self.status(owner, gid)

    def stop(self, owner, gid) -> dict:
        """Ends the app and takes it off the space, which is plain group chat again."""
        with self.lock:
            self.client.manage(owner, gid, 'app_remove', {})
            self.discard(owner, gid)
        return self.status(owner, gid)

    def discard(self, owner, gid):
        """Forget a deleted/detached group without trying to contact it again."""
        with self.lock:
            self._stop(owner, gid)
            self.save()

    def _watch(self):
        if not self.monitor or not self.monitor.is_alive():
            self.monitor = threading.Thread(target=self._monitor, daemon=True, name='space-apps-lifecycle')
            self.monitor.start()

    def _monitor(self):
        # Also catches deletion from the group host's administration API, outside this facade.
        while not self.wakeup.wait(2):
            with self.lock:
                if self.closed or not self.runs:
                    self.monitor = None
                    return
            self.reconcile()

    def reconcile(self):
        with self.lock:
            runs = list(self.runs.items())
        for (owner, gid), run in runs:
            try:
                row = self.client.require(owner, gid)
                group = self.client.request(row, 'GET', '/group')
                gone = not group.get('app')
            except ClientError as exc:
                # Service outages and paused networking do not mean the group was deleted.
                gone = exc.status in (401, 403, 404)
            if gone:
                with self.lock:
                    if not self.closed and self.runs.get((owner, gid)) is run:
                        self.discard(owner, gid)

    def _stop(self, owner, gid):
        run = self.runs.pop((owner, gid), None)
        if run and run.get('proc'):
            end(run['proc'])

    def restore(self):
        """Starts the runs remembered from before a restart; an orphan left by a crash is stopped first."""
        try:
            rows = json.loads(self.record.read_text())
        except (OSError, ValueError):
            return
        apps = catalogue()
        for row in rows:
            orphan(row.get('pid'), apps.get(row['app'], {}).get('entry'))
            self.runs[(row['owner'], row['gid'])] = {'app': row['app'], 'name': apps.get(row['app'], {}).get('name', row['app']),
                                                     'proc': None, 'started': time.time(), 'error': ''}
        threading.Thread(target=self._restart, args=(rows,), daemon=True, name='space-apps-restore').start()
        with self.lock:
            self._watch()

    def _restart(self, rows):
        # The group server may still be starting: try for a few minutes.
        for row in rows:
            for attempt in range(20):
                if self.closed or (row['owner'], row['gid']) not in self.runs:
                    break
                try:
                    with self.lock:
                        if self.closed or (row['owner'], row['gid']) not in self.runs:
                            break
                        self.start(row['owner'], row['gid'], row['app'])
                    break
                except ClientError as exc:
                    if exc.status in (403, 404):
                        # The space is gone, or this member left it: nothing to run there any more.
                        with self.lock:
                            self.runs.pop((row['owner'], row['gid']), None)
                            self.save()
                        break
                    if exc.status == 400 or attempt == 19:
                        with self.lock:
                            if (row['owner'], row['gid']) in self.runs:
                                self.runs[(row['owner'], row['gid'])]['error'] = '重新启动失败：' + str(exc)
                        break
                    time.sleep(10)

    def close(self):
        """ClawCross is stopping: stop the apps but keep the record, so they start again next time."""
        self.closed = True
        self.wakeup.set()
        with self.lock:
            for run in self.runs.values():
                if run.get('proc'):
                    end(run['proc'])


def end(proc):
    group = hasattr(os, 'killpg')
    if not group:
        if proc.poll() is None:
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL) if group else proc.kill()
        except OSError:
            pass
    finally:
        # The parent can exit first while a descendant ignores SIGTERM.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def orphan(pid, entry):
    """Stops a process from an earlier ClawCross only if it is still that app (Linux /proc)."""
    if not pid or not entry:
        return
    try:
        if entry.encode() in Path(f'/proc/{pid}/cmdline').read_bytes():
            os.killpg(pid, signal.SIGTERM)
    except OSError:
        pass
