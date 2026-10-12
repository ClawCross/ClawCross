"""Group relay persistence. It knows participants and credentials, never agent runtimes:
agents run on members' devices, a ClawCross or the browser tab of a member who opened the link."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import unicodedata

from groups.delivery import WakeRequest, mentions_everyone, select_wake_targets, resolve_text_mentions


class RelayError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 210000).hex()


# A member is online while its device called within this window; hidden browser tabs poll about once a minute.
ONLINE_SECONDS = 90
# A space's app server is online while it polled within this window.
APP_ONLINE_SECONDS = 60
# What a member's device receives: everything except the app's traffic, and its own agents' app requests.
MEMBER_EVENTS = "(kind NOT IN ('app_request','app_output') OR (kind='app_request' AND json_extract(body,'$.connection')=?))"


class RelayStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS relay_groups (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL,
                    owner_connection TEXT NOT NULL, password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL, local_join INTEGER NOT NULL, dnd INTEGER NOT NULL DEFAULT 0,
                    primary_member TEXT, version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relay_connections (
                    id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES relay_groups(id) ON DELETE CASCADE,
                    token_hash TEXT UNIQUE NOT NULL, node_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
                    ack INTEGER NOT NULL DEFAULT 0, joined_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relay_members (
                    id TEXT PRIMARY KEY, group_id TEXT NOT NULL REFERENCES relay_groups(id) ON DELETE CASCADE,
                    connection_id TEXT NOT NULL REFERENCES relay_connections(id) ON DELETE CASCADE,
                    agent_id TEXT NOT NULL, name TEXT NOT NULL, platform TEXT NOT NULL,
                    muted INTEGER NOT NULL DEFAULT 0, UNIQUE(connection_id, agent_id)
                );
                CREATE TABLE IF NOT EXISTS relay_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL REFERENCES relay_groups(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL, body TEXT NOT NULL, created_at REAL NOT NULL,
                    sender TEXT, client_id TEXT, UNIQUE(group_id, sender, client_id)
                );
                CREATE INDEX IF NOT EXISTS relay_events_group ON relay_events(group_id, id);
                CREATE TABLE IF NOT EXISTS relay_guest_tokens (
                    token_hash TEXT PRIMARY KEY,
                    connection_id TEXT NOT NULL REFERENCES relay_connections(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS relay_guest_invites (
                    group_id TEXT PRIMARY KEY REFERENCES relay_groups(id) ON DELETE CASCADE,
                    token_hash TEXT UNIQUE NOT NULL
                );
                -- A space's app: a server its owner runs, which reaches the group with this key.
                CREATE TABLE IF NOT EXISTS relay_app_keys (
                    group_id TEXT PRIMARY KEY REFERENCES relay_groups(id) ON DELETE CASCADE,
                    key_hash TEXT UNIQUE NOT NULL, name TEXT NOT NULL DEFAULT '', seen_at REAL NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                -- One-shot: uploaded app pages were replaced by app servers.
                DROP TABLE IF EXISTS relay_apps;
            ''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(relay_connections)')}
            # `guest` marks connections made in a browser. guest_salt/guest_password_hash hold the password
            # of any member that joined by link, from a browser or a ClawCross.
            for column, definition in [('guest', 'INTEGER NOT NULL DEFAULT 0'),
                                       ('guest_salt', "TEXT NOT NULL DEFAULT ''"),
                                       ('guest_password_hash', "TEXT NOT NULL DEFAULT ''")]:
                if column not in columns:
                    db.execute(f'ALTER TABLE relay_connections ADD COLUMN {column} {definition}')
            if 'seen_at' not in columns:
                db.execute('ALTER TABLE relay_connections ADD COLUMN seen_at REAL NOT NULL DEFAULT 0')
            if 'host_local' not in columns:
                db.execute('ALTER TABLE relay_connections ADD COLUMN host_local INTEGER NOT NULL DEFAULT 0')
                # A group is created only through the server's machine control API.
                db.execute('UPDATE relay_connections SET host_local=1 WHERE id IN (SELECT owner_connection FROM relay_groups)')
            if 'external_access_enabled' not in {row[1] for row in db.execute('PRAGMA table_info(relay_groups)')}:
                db.execute('ALTER TABLE relay_groups ADD COLUMN external_access_enabled INTEGER NOT NULL DEFAULT 1')
            if 'announcement' not in {row[1] for row in db.execute('PRAGMA table_info(relay_groups)')}:
                db.execute("ALTER TABLE relay_groups ADD COLUMN announcement TEXT NOT NULL DEFAULT ''")
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def auth(db, token: str, *, check_network: bool = True):
        row = db.execute('SELECT * FROM relay_connections WHERE token_hash=? AND revoked=0', (digest(token),)).fetchone()
        if row is None:
            row = db.execute('''SELECT c.* FROM relay_connections c JOIN relay_guest_tokens t ON t.connection_id=c.id
                                WHERE t.token_hash=? AND c.revoked=0''', (digest(token),)).fetchone()
        if row is None:
            raise RelayError('群凭证无效、已退出或被撤销', 401)
        if check_network:
            RelayStore.require_external_access(db, row['group_id'], host_local=bool(row['host_local']))
        if row['seen_at'] < time.time() - 15:
            db.execute('UPDATE relay_connections SET seen_at=? WHERE id=?', (time.time(), row['id']))
        return row

    @staticmethod
    def require_external_access(db, gid: str, *, host_local: bool = False):
        if host_local:
            return
        group = db.execute('SELECT external_access_enabled FROM relay_groups WHERE id=?', (gid,)).fetchone()
        if group is not None and not group['external_access_enabled']:
            raise RelayError('群主已暂停外部联网，请等待恢复；成员与凭证保留', 503)

    @staticmethod
    def _event(db, gid: str, kind: str, body: dict):
        return db.execute('INSERT INTO relay_events(group_id,kind,body,created_at) VALUES(?,?,?,?)',
                          (gid, kind, json.dumps(body, ensure_ascii=False), time.time())).lastrowid

    @staticmethod
    def _card(db, gid: str) -> dict:
        group = db.execute('SELECT * FROM relay_groups WHERE id=?', (gid,)).fetchone()
        if group is None:
            raise RelayError('群不存在', 404)
        members = [dict(row) for row in db.execute('''SELECT m.id AS principal,m.agent_id,m.name,m.platform,m.muted,
                  c.node_id,c.user_id,c.display_name,c.id AS connection_id,c.host_local,c.seen_at,c.guest
                  FROM relay_members m JOIN relay_connections c ON c.id=m.connection_id
                  WHERE m.group_id=? AND c.revoked=0 ORDER BY c.joined_at,m.agent_id''', (gid,))]
        online_since = time.time() - ONLINE_SECONDS
        for member in members:
            member['is_agent'] = bool(member['agent_id'])
            member['muted'] = bool(member['muted'])
            member['host_local'] = bool(member['host_local'])
            member['online'] = member.pop('seen_at') > online_since
            # What the device is, for views that draw the group by device.
            member['device_kind'] = 'browser' if member.pop('guest') else 'client'
        app = db.execute('SELECT name,seen_at FROM relay_app_keys WHERE group_id=?', (gid,)).fetchone()
        return {'group_id': gid, 'title': group['title'], 'kind': group['kind'],
                'announcement': group['announcement'],
                'app': {'name': app['name'], 'online': app['seen_at'] > time.time() - APP_ONLINE_SECONDS} if app else None,
                'owner': group['owner_connection'], 'primary_agent': group['primary_member'],
                'dnd': bool(group['dnd']), 'version': group['version'], 'members': members,
                'external_access_enabled': bool(group['external_access_enabled']),
                'member_count': len(members)}

    def _connect(self, db, gid: str, *, node_id: str, user_id: str, display_name: str, host_local: bool = False):
        self.require_external_access(db, gid, host_local=host_local)
        self._unique_name(db, gid, display_name, guests_only=True)
        if len(self._card(db, gid)['members']) >= 256:
            raise RelayError('群最多容纳 256 个成员', 409)
        if db.execute('SELECT COUNT(*) FROM relay_connections WHERE group_id=? AND revoked=0', (gid,)).fetchone()[0] >= 128:
            raise RelayError('群成员连接已达到上限', 409)
        cid, token = 'c_' + secrets.token_hex(12), secrets.token_urlsafe(32)
        db.execute('INSERT INTO relay_connections(id,group_id,token_hash,node_id,user_id,display_name,joined_at,host_local) VALUES(?,?,?,?,?,?,?,?)',
                   (cid, gid, digest(token), node_id, user_id, display_name, time.time(), int(host_local)))
        db.execute('INSERT INTO relay_members(id,group_id,connection_id,agent_id,name,platform) VALUES(?,?,?,?,?,?)',
                   ('p_' + secrets.token_hex(12), gid, cid, '', display_name, 'human'))
        return cid, token

    def create(self, *, title: str, node_id: str, user_id: str, display_name: str, kind: str = 'group') -> dict:
        if kind not in {'group', 'direct'} or not title.strip():
            raise RelayError('群类型或名称无效')
        gid = 'g_' + secrets.token_hex(12)
        with self.db() as db:
            # password_hash/salt/local_join are columns of the password joins that invitation links replaced.
            db.execute('INSERT INTO relay_groups(id,title,kind,owner_connection,password_hash,salt,local_join,created_at) VALUES(?,?,?,?,?,?,?,?)',
                       (gid, title.strip(), kind, '', '', '', 0, time.time()))
            cid, token = self._connect(db, gid, node_id=node_id, user_id=user_id, display_name=display_name, host_local=True)
            db.execute('UPDATE relay_groups SET owner_connection=? WHERE id=?', (cid, gid))
            self._event(db, gid, 'metadata', {})
            return {'token': token, 'connection_id': cid, 'group': self._card(db, gid)}

    def join(self, invite: str, *, node_id: str, user_id: str, display_name: str, password: str, host_local: bool = False) -> dict:
        """A ClawCross joins by the group's invitation link, as the member that name and password identify."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT group_id FROM relay_guest_invites WHERE token_hash=?', (digest(invite),)).fetchone()
            if not row:
                raise RelayError('邀请链接已失效', 403)
            gid = row['group_id']
            cid, token = self._sign_in(db, gid, display_name, password, lambda: self._connect(
                db, gid, node_id=node_id, user_id=user_id, display_name=display_name, host_local=host_local))
            return {'token': token, 'connection_id': cid, 'group': self._card(db, gid)}

    @staticmethod
    def _check_password(password):
        if not isinstance(password, str) or not 6 <= len(password) <= 128:
            raise RelayError('密码需要 6 至 128 个字符')

    def _sign_in(self, db, gid, name, password, create):
        """A member is its name and password in the group: the same pair on any device, a browser or a
        ClawCross, returns to that member with a new credential. A new name makes a member through
        `create()`, which returns ``(connection id, credential)``."""
        self._check_password(password)
        normalized = unicodedata.normalize('NFKC', name.strip()).casefold()
        existing = next((m for m in db.execute('''SELECT m.name,c.* FROM relay_members m
            JOIN relay_connections c ON c.id=m.connection_id WHERE m.group_id=? AND c.revoked=0 AND m.agent_id=?''', (gid, ''))
            if unicodedata.normalize('NFKC', m['name'].strip()).casefold() == normalized), None)
        if existing:
            if not existing['guest_password_hash']:
                raise RelayError('这个名字已有人使用；如果是你，请在原来的设备上设置密码后再登录', 409)
            if not hmac.compare_digest(existing['guest_password_hash'], password_hash(password, existing['guest_salt'])):
                raise RelayError('名字或密码不正确', 403)
            token = secrets.token_urlsafe(32)
            db.execute('INSERT INTO relay_guest_tokens VALUES(?,?,?)', (digest(token), existing['id'], time.time()))
            # Bound stored credentials while retaining concurrent devices.
            db.execute('''DELETE FROM relay_guest_tokens WHERE connection_id=? AND token_hash NOT IN
                          (SELECT token_hash FROM relay_guest_tokens WHERE connection_id=? ORDER BY created_at DESC LIMIT 16)''',
                       (existing['id'], existing['id']))
            return existing['id'], token
        self._unique_name(db, gid, name)
        cid, token = create()
        salt = secrets.token_hex(16)
        db.execute('UPDATE relay_connections SET guest_salt=?,guest_password_hash=? WHERE id=?', (salt, password_hash(password, salt), cid))
        db.execute('UPDATE relay_groups SET version=version+1 WHERE id=?', (gid,))
        self._event(db, gid, 'joined', {'connection_id': cid, 'display_name': name.strip()})
        return cid, token

    def confirm_host_local(self, token: str) -> dict:
        """Called only after the relay API verifies a local machine credential."""
        with self.db() as db:
            conn = self.auth(db, token, check_network=False)
            if conn['guest']:
                raise RelayError('访客不能登记为主机连接', 403)
            db.execute('UPDATE relay_connections SET host_local=1 WHERE id=?', (conn['id'],))
            return {'host_local': True}

    @staticmethod
    def _unique_name(db, gid, name, *, exclude=None, guests_only=False):
        normalized = unicodedata.normalize('NFKC', name.strip()).casefold()
        if not guests_only and (not normalized or len(name.strip()) > 40 or any(unicodedata.category(c).startswith('C') for c in name)):
            raise RelayError('名字需要 1 至 40 个字符，不能包含控制字符')
        rows = db.execute('''SELECT m.id,m.name,c.guest FROM relay_members m
            JOIN relay_connections c ON c.id=m.connection_id WHERE m.group_id=? AND c.revoked=0''', (gid,))
        for row in rows:
            if row['id'] != exclude and (not guests_only or row['guest']) and unicodedata.normalize('NFKC', row['name'].strip()).casefold() == normalized:
                raise RelayError('这个名字已有人使用，请换一个', 409)

    def guest_invite(self, token, *, disable=False):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            conn = self.auth(db, token)
            group = self._card(db, conn['group_id'])
            if conn['guest'] or group['owner'] != conn['id'] or group['kind'] != 'group':
                raise RelayError('只有群主可以创建访客邀请', 403)
            db.execute('DELETE FROM relay_guest_invites WHERE group_id=?', (conn['group_id'],))
            if disable:
                return {'disabled': True}
            invite = secrets.token_urlsafe(32)
            db.execute('INSERT INTO relay_guest_invites VALUES(?,?)', (conn['group_id'], digest(invite)))
            return {'invite': invite, 'title': group['title']}

    def guest_info(self, invite, *, host_local: bool = False):
        with self.db() as db:
            row = db.execute('SELECT group_id FROM relay_guest_invites WHERE token_hash=?', (digest(invite),)).fetchone()
            if not row:
                raise RelayError('分享链接已失效', 403)
            self.require_external_access(db, row['group_id'], host_local=host_local)
            return {'title': self._card(db, row['group_id'])['title'], 'group_id': row['group_id']}

    def guest_join(self, invite, name, password):
        """A person opens the link in a browser and signs in with a name and password; the browser may bring an agent."""
        name = name.strip()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT group_id FROM relay_guest_invites WHERE token_hash=?', (digest(invite),)).fetchone()
            if not row:
                raise RelayError('分享链接已失效', 403)
            gid = row['group_id']
            self.require_external_access(db, gid)
            def browser():
                cid, token = self._connect(db, gid, node_id='guest', user_id='guest_' + secrets.token_hex(12), display_name=name)
                db.execute('UPDATE relay_connections SET guest=1 WHERE id=?', (cid,))
                return cid, token
            cid, token = self._sign_in(db, gid, name, password, browser)
            member = db.execute("SELECT name FROM relay_members WHERE connection_id=? AND agent_id=''", (cid,)).fetchone()
            return {'token': token, 'name': member['name'], 'password_set': True}

    def set_password(self, token, password):
        """Sets or changes the password of the caller's member, e.g. one that joined before passwords."""
        self._check_password(password)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            conn = self.auth(db, token)
            salt = secrets.token_hex(16)
            db.execute('UPDATE relay_connections SET guest_salt=?,guest_password_hash=? WHERE id=?',
                       (salt, password_hash(password, salt), conn['id']))
            return {'password_set': True}

    def guest_identity(self, token):
        with self.db() as db:
            conn = self.auth(db, token)
            member = db.execute("SELECT id,name FROM relay_members WHERE connection_id=? AND agent_id=''", (conn['id'],)).fetchone()
            return {'principal': member['id'], 'name': member['name']}

    def guest_state(self, token, after=-1):
        with self.db() as db:
            db.execute('BEGIN')
            conn = self.auth(db, token)
            group = self._card(db, conn['group_id'])
            member = next(m for m in group['members'] if m['connection_id'] == conn['id'] and not m['is_agent'])
            if after < 0:
                cursor = db.execute('SELECT COALESCE(MAX(id),0) FROM relay_events WHERE group_id=?', (conn['group_id'],)).fetchone()[0]
                rows = list(reversed(db.execute("SELECT * FROM relay_events WHERE group_id=? AND kind='message' ORDER BY id DESC LIMIT 100", (conn['group_id'],)).fetchall()))
                more = False
            else:
                rows = db.execute(f'SELECT * FROM relay_events WHERE group_id=? AND id>? AND {MEMBER_EVENTS} ORDER BY id LIMIT 50',
                                  (conn['group_id'], after, conn['id'])).fetchall()
                cursor = rows[-1]['id'] if rows else after
                more = len(rows) >= 50
            messages = []
            for row in rows:
                if row['kind'] == 'message':
                    body = json.loads(row['body'])
                    message = body['message']
                    messages.append({k: message[k] for k in ('sender', 'sender_name', 'content', 'created_at')} |
                                    {'id': row['id'], 'reply_to':message.get('reply_to'), 'reply':message.get('reply'),
                                     'targets': body.get('targets', [])})
            # `device` numbers the connections, so a browser can group members by device without learning ids.
            devices = list(dict.fromkeys(m['connection_id'] for m in group['members']))
            members = [{k: m[k] for k in ('principal', 'name', 'is_agent', 'online', 'device_kind', 'platform')} |
                       {'own': m['connection_id'] == conn['id'], 'device': devices.index(m['connection_id'])}
                       for m in group['members']]
            return {'group_id': conn['group_id'], 'password_set': bool(conn['guest_password_hash']), 'title': group['title'], 'members': members,
                    'announcement': group['announcement'], 'app': group['app'],
                    # What the space's app asks of the agent this browser runs.
                    'requests': [{'id': row['id'], **{k: v for k, v in json.loads(row['body']).items() if k in ('agent', 'content')}}
                                 for row in rows if row['kind'] == 'app_request'],
                    'messages': messages, 'cursor': cursor, 'has_more': more, 'principal': member['principal'], 'name': member['name']}

    def guest_rename(self, token, name):
        name = name.strip()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            conn = self.auth(db, token)
            member = db.execute("SELECT id FROM relay_members WHERE connection_id=? AND agent_id=''", (conn['id'],)).fetchone()
            self._unique_name(db, conn['group_id'], name, exclude=member['id'])
            db.execute('UPDATE relay_members SET name=? WHERE id=?', (name, member['id']))
            db.execute('UPDATE relay_connections SET display_name=? WHERE id=?', (name, conn['id']))
            db.execute('UPDATE relay_groups SET version=version+1 WHERE id=?', (conn['group_id'],))
            self._event(db, conn['group_id'], 'metadata', {})
            return {'name': name}

    def detail(self, token: str) -> dict:
        with self.db() as db:
            conn = self.auth(db, token)
            return self._card(db, conn['group_id'])

    def add_agent(self, token: str, *, agent_id: str, name: str, platform: str) -> dict:
        if not agent_id:
            raise RelayError('agent 编号不能为空')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            conn = self.auth(db, token)
            existing = db.execute('SELECT id FROM relay_members WHERE connection_id=? AND agent_id=?', (conn['id'], agent_id)).fetchone()
            self._unique_name(db, conn['group_id'], name, exclude=existing and existing['id'], guests_only=True)
            if not existing and db.execute('SELECT COUNT(*) FROM relay_members WHERE connection_id=?', (conn['id'],)).fetchone()[0] >= 33:
                raise RelayError('此连接最多引入 32 个 agent', 409)
            group = self._card(db, conn['group_id'])
            if not existing and len(group['members']) >= 256:
                raise RelayError('群最多容纳 256 个成员', 409)
            if not existing and group['kind'] == 'direct' and any(m['is_agent'] for m in group['members']):
                raise RelayError('私聊只能有一个 agent', 409)
            db.execute('INSERT INTO relay_members(id,group_id,connection_id,agent_id,name,platform) VALUES(?,?,?,?,?,?) '
                       'ON CONFLICT(connection_id,agent_id) DO UPDATE SET name=excluded.name,platform=excluded.platform',
                       ('p_' + secrets.token_hex(12), conn['group_id'], conn['id'], agent_id, name, platform))
            db.execute('UPDATE relay_groups SET version=version+1 WHERE id=?', (conn['group_id'],))
            self._event(db, conn['group_id'], 'metadata', {})
            return self._card(db, conn['group_id'])

    def manage(self, token: str, action: str, fields: dict, *, admin_group: str | None = None) -> dict:
        for key in ('principal', 'name'):
            value = fields.get(key)
            if value is not None and (not isinstance(value, str) or not value or len(value) > 160):
                raise RelayError('成员字段无效')
        if fields.get('muted') is not None and not isinstance(fields['muted'], bool):
            raise RelayError('禁言设置必须为布尔值')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if admin_group:
                owner_id = self._card(db, admin_group)['owner']
                conn = db.execute('SELECT * FROM relay_connections WHERE id=?', (owner_id,)).fetchone()
            else:
                conn = self.auth(db, token)
            if action == 'member_patch' and fields.get('name'):
                self._unique_name(db, conn['group_id'], fields['name'],
                                  exclude=fields.get('principal'), guests_only=True)
            group = self._card(db, conn['group_id'])
            owner = group['owner'] == conn['id']
            if action == 'leave':
                if owner:
                    raise RelayError('群主应删除群或先转让管理权限', 409)
                db.execute('UPDATE relay_connections SET revoked=1 WHERE id=?', (conn['id'],))
            elif action == 'remove_member':
                member = next((m for m in group['members'] if m['principal'] == fields.get('principal')), None)
                if member is None:
                    raise RelayError('成员不存在', 404)
                if not owner and member['connection_id'] != conn['id']:
                    raise RelayError('只能移除自己引入的 agent', 403)
                if not member['is_agent']:
                    if not owner or member['connection_id'] == group['owner']:
                        raise RelayError('不能移除群主；退出请使用 leave', 403)
                    db.execute('UPDATE relay_connections SET revoked=1 WHERE id=?', (member['connection_id'],))
                else:
                    db.execute('DELETE FROM relay_members WHERE id=?', (member['principal'],))
                    db.execute('UPDATE relay_groups SET primary_member=NULL WHERE id=? AND primary_member=?', (conn['group_id'], member['principal']))
            elif action == 'delete':
                if not owner:
                    raise RelayError('只有群主可以删除群', 403)
                db.execute('DELETE FROM relay_groups WHERE id=?', (conn['group_id'],))
                return {'deleted': conn['group_id']}
            elif action in {'disconnect_external', 'external_access'}:
                if not owner:
                    raise RelayError('只有群主可以设置外部联网', 403)
                enabled = False if action == 'disconnect_external' else fields.get('enabled')
                if not isinstance(enabled, bool):
                    raise RelayError('联网开关必须为布尔值')
                db.execute('UPDATE relay_groups SET external_access_enabled=? WHERE id=?', (int(enabled), conn['group_id']))
            elif action == 'patch':
                if not owner:
                    raise RelayError('只有群主可以管理群', 403)
                for key in ('title', 'dnd', 'announcement'):
                    if key in fields and fields[key] is not None:
                        if key == 'title' and (not isinstance(fields[key], str) or not fields[key].strip() or len(fields[key]) > 160):
                            raise RelayError('群名称需要 1 至 160 个字符')
                        if key == 'announcement' and (not isinstance(fields[key], str) or len(fields[key]) > 4000):
                            raise RelayError('群公告最多 4000 个字符')
                        if key == 'dnd' and not isinstance(fields[key], bool):
                            raise RelayError('群设置必须为布尔值')
                        value = fields[key].strip() if isinstance(fields[key], str) else fields[key]
                        db.execute(f'UPDATE relay_groups SET {key}=? WHERE id=?', (value, conn['group_id']))
            elif action == 'app_remove':
                if not owner:
                    raise RelayError('只有群主可以断开小程序', 403)
                db.execute('DELETE FROM relay_app_keys WHERE group_id=?', (conn['group_id'],))
            elif action == 'primary':
                if not owner:
                    raise RelayError('只有群主可以设置主 agent', 403)
                principal = fields.get('principal')
                if principal and not any(m['principal'] == principal and m['is_agent'] for m in group['members']):
                    raise RelayError('主 agent 必须是群成员')
                db.execute('UPDATE relay_groups SET primary_member=? WHERE id=?', (principal, conn['group_id']))
            elif action == 'member_patch':
                if not owner:
                    raise RelayError('只有群主可以管理成员', 403)
                if not any(m['principal'] == fields.get('principal') for m in group['members']):
                    raise RelayError('成员不存在', 404)
                for key in ('muted', 'name'):
                    if fields.get(key) is not None:
                        db.execute(f'UPDATE relay_members SET {key}=? WHERE group_id=? AND id=?', (fields[key], conn['group_id'], fields.get('principal')))
            else:
                raise RelayError('不支持的群管理操作')
            db.execute('''UPDATE relay_groups SET primary_member=NULL WHERE id=? AND primary_member IS NOT NULL
                       AND NOT EXISTS(SELECT 1 FROM relay_members m JOIN relay_connections c ON c.id=m.connection_id
                                      WHERE m.id=relay_groups.primary_member AND c.revoked=0)''', (conn['group_id'],))
            db.execute('UPDATE relay_groups SET version=version+1 WHERE id=?', (conn['group_id'],))
            self._event(db, conn['group_id'], 'metadata', {})
            result = {'left': True} if action == 'leave' else self._card(db, conn['group_id'])
            return result

    def post(self, token: str, *, content: str, agent_id: str = '', mentions: list[str] = (),
             attachments: list[dict] = (), client_msg_id: str = '', expected_title: str | None = None,
             reply_to: int | None = None) -> dict:
        if len(json.dumps({'content': content, 'attachments': list(attachments)}, ensure_ascii=False).encode()) > 512 * 1024:
            raise RelayError('群消息及附件最多 512 KiB', 413)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            conn = self.auth(db, token)
            group = self._card(db, conn['group_id'])
            sender = next((m for m in group['members'] if m['connection_id'] == conn['id'] and m['agent_id'] == agent_id), None)
            if sender is None or sender['muted']:
                raise RelayError('发送者未加入群或已被禁言', 403)
            if expected_title is not None and expected_title != group['title']:
                raise RelayError('群名称与预期不符，请确认目标群')
            if db.execute('SELECT 1 FROM relay_app_keys WHERE group_id=?', (conn['group_id'],)).fetchone():
                return self._app_output(db, conn['group_id'], sender, content, list(attachments), client_msg_id)
            quote = None
            if reply_to is not None:
                if not isinstance(reply_to, int) or isinstance(reply_to, bool) or reply_to <= 0:
                    raise RelayError('无效的回复引用')
                original = db.execute('SELECT body FROM relay_events WHERE id=? AND group_id=? AND kind=?',
                                      (reply_to, conn['group_id'], 'message')).fetchone()
                if not original:
                    raise RelayError('回复引用不属于本群')
                source = json.loads(original['body'])['message']
                quote = {'id':reply_to, 'sender_name':source['sender_name'], 'content':source['content'][:500]}
            if client_msg_id:
                previous = db.execute('SELECT * FROM relay_events WHERE group_id=? AND sender=? AND client_id=?',
                                      (conn['group_id'], sender['principal'], client_msg_id)).fetchone()
                if previous:
                    return {'message': {**json.loads(previous['body'])['message'], 'id': previous['id']}, 'created': False}
            member_ids = {m['principal'] for m in group['members']}
            if any(m not in member_ids for m in mentions):
                raise RelayError('@ 的成员不属于本群')
            mentioned = list(mentions)
            for principal in resolve_text_mentions(content, [(m['name'], m['principal']) for m in group['members']]):
                if principal not in mentioned:
                    mentioned.append(principal)
            targets = [] if group['dnd'] else select_wake_targets(WakeRequest(
                agent_ids=[m['principal'] for m in group['members'] if m['is_agent'] and not m['muted']],
                sender_id=sender['principal'] if agent_id else '', mentions=mentioned,
                mention_all=mentions_everyone(content), primary_id=group['primary_agent'], direct=group['kind'] == 'direct'))
            # A persisted budget prevents agents from amplifying traffic across process restarts.
            if agent_id and targets:
                recent = db.execute("SELECT body FROM relay_events WHERE group_id=? AND kind='message' AND created_at>? ORDER BY id DESC LIMIT 128",
                                    (conn['group_id'], time.time() - 600)).fetchall()
                spent = 0
                for event in recent:
                    value = json.loads(event['body'])
                    if not value['message'].get('sender_agent_id'):
                        break
                    spent += len(value.get('targets', []))
                if spent + len(targets) > 24:
                    targets = []
            message = {'sender': sender['principal'], 'sender_name': sender['name'], 'sender_agent_id': agent_id,
                       'sender_connection': conn['id'], 'content': content, 'mentions': mentioned,
                       'reply_to': reply_to, 'reply':quote, 'attachments': list(attachments), 'created_at': time.time()}
            cursor = db.execute('INSERT INTO relay_events(group_id,kind,body,created_at,sender,client_id) VALUES(?,?,?,?,?,?)',
                                (conn['group_id'], 'message', json.dumps({'message': message, 'targets': targets}, ensure_ascii=False),
                                 message['created_at'], sender['principal'], client_msg_id or None))
            return {'message': {**message, 'id': cursor.lastrowid}, 'created': True}

    def events(self, token: str, since: int, *, limit: int = 50) -> dict:
        with self.db() as db:
            conn = self.auth(db, token)
            group = self._card(db, conn['group_id'])
            # Join grants access to the group history, while credentials constrain write identity.
            rows = db.execute(f'SELECT * FROM relay_events WHERE group_id=? AND id>? AND {MEMBER_EVENTS} ORDER BY id LIMIT ?',
                              (conn['group_id'], since, conn['id'], min(100, max(1, limit)))).fetchall()
            events, size = [], 0
            for row in rows:
                event = {'id': row['id'], 'kind': row['kind'], **json.loads(row['body'])}
                length = len(json.dumps(event, ensure_ascii=False).encode())
                if events and size + length > 768 * 1024:
                    break
                events.append(event)
                size += length
            return {'group': group, 'connection_id': conn['id'], 'events': events}

    def messages(self, token: str, after: int = 0) -> list[dict]:
        with self.db() as db:
            conn = self.auth(db, token)
            rows = db.execute("SELECT * FROM relay_events WHERE group_id=? AND kind='message' AND id>? ORDER BY id DESC LIMIT 100",
                              (conn['group_id'], after)).fetchall()
            return [{**json.loads(r['body'])['message'], 'id': r['id']} for r in reversed(rows)]

    def search_messages(self, token: str, query: str, before_id: int = 0, limit: int = 50, *, sender='', from_ts=0, to_ts=0) -> dict:
        query = query.strip()
        if (not query and not (sender or from_ts or to_ts)) or len(query) > 120:
            raise RelayError('搜索词需要 1–120 个字符')
        if from_ts < 0 or to_ts < 0 or (from_ts and to_ts and from_ts > to_ts):
            raise RelayError('日期范围无效')
        limit = min(50, max(1, limit))
        with self.db() as db:
            conn = self.auth(db, token)
            pattern = '%' + query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
            rows = db.execute("""SELECT id,body FROM relay_events WHERE group_id=? AND kind='message'
                AND (?=0 OR id<?) AND (?='' OR json_extract(body,'$.message.content') LIKE ? ESCAPE '\\'
                OR json_extract(body,'$.message.sender_name') LIKE ? ESCAPE '\\')
                AND (?='' OR json_extract(body,'$.message.sender')=?)
                AND (?=0 OR json_extract(body,'$.message.created_at')>=?)
                AND (?=0 OR json_extract(body,'$.message.created_at')<=?) ORDER BY id DESC LIMIT ?""",
                (conn['group_id'],before_id,before_id,query,pattern,pattern,sender,sender,from_ts,from_ts,to_ts,to_ts,limit+1)).fetchall()
            found=rows[:limit]
            return {'messages':[{**json.loads(row['body'])['message'],'id':row['id']} for row in found],
                    'next_before_id':found[-1]['id'] if len(rows)>limit else 0}

    def message_context(self, token, message_id):
        with self.db() as db:
            conn=self.auth(db,token)
            args=(conn['group_id'],message_id)
            focus=db.execute("SELECT id FROM relay_events WHERE group_id=? AND id=? AND kind='message'",args).fetchone()
            if not focus: raise RelayError('此群没有这条消息',404)
            before=db.execute("SELECT id,body FROM relay_events WHERE group_id=? AND id<? AND kind='message' ORDER BY id DESC LIMIT 15",args).fetchall()
            after=db.execute("SELECT id,body FROM relay_events WHERE group_id=? AND id>=? AND kind='message' ORDER BY id LIMIT 16",args).fetchall()
            return {'focus_id':message_id,'messages':[{**json.loads(row['body'])['message'],'id':row['id']} for row in [*reversed(before),*after]]}

    def acknowledge(self, token: str, cursor: int):
        with self.db() as db:
            conn = self.auth(db, token)
            maximum = db.execute('SELECT COALESCE(MAX(id),0) FROM relay_events WHERE group_id=?', (conn['group_id'],)).fetchone()[0]
            if cursor > maximum:
                raise RelayError('确认游标超出群消息范围')
            db.execute('UPDATE relay_connections SET ack=MAX(ack,?) WHERE id=?', (cursor, conn['id']))

    # ── Space apps: a server the owner runs reaches the members' agents through the group. ──

    def app_key(self, token: str) -> dict:
        """The owner makes (or replaces) the key its app server connects with."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            conn = self.auth(db, token)
            if self._card(db, conn['group_id'])['owner'] != conn['id'] or conn['guest']:
                raise RelayError('只有群主可以设置小程序', 403)
            key = 'app_' + secrets.token_urlsafe(32)
            db.execute('INSERT INTO relay_app_keys(group_id,key_hash,created_at) VALUES(?,?,?) ON CONFLICT(group_id) DO UPDATE '
                       'SET key_hash=excluded.key_hash,seen_at=0', (conn['group_id'], digest(key), time.time()))
            db.execute('UPDATE relay_groups SET version=version+1 WHERE id=?', (conn['group_id'],))
            self._event(db, conn['group_id'], 'metadata', {})
            return {'key': key}

    def app_auth(self, key: str, name: str | None = None) -> str:
        """The space an app key opens; a poll also marks the app online under the name it gives."""
        with self.db() as db:
            row = db.execute('SELECT * FROM relay_app_keys WHERE key_hash=?', (digest(key),)).fetchone()
            if row is None:
                raise RelayError('小程序密钥无效或已被群主重置', 401)
            if name is not None:
                name = name.strip()[:80] or '小程序'
                if row['seen_at'] < time.time() - APP_ONLINE_SECONDS or row['name'] != name:
                    # Going online or renamed changes the card every member sees.
                    db.execute('UPDATE relay_groups SET version=version+1 WHERE id=?', (row['group_id'],))
                    self._event(db, row['group_id'], 'metadata', {})
                db.execute('UPDATE relay_app_keys SET seen_at=?,name=? WHERE group_id=?', (time.time(), name, row['group_id']))
            return row['group_id']

    def app_members(self, gid: str) -> dict:
        with self.db() as db:
            group = self._card(db, gid)
            devices = list(dict.fromkeys(m['connection_id'] for m in group['members']))
            return {'title': group['title'], 'members': [
                {'id': m['principal'], 'name': m['name'], 'agent': m['is_agent'], 'platform': m['platform'], 'online': m['online'],
                 'device': devices.index(m['connection_id']), 'owner': not m['is_agent'] and m['connection_id'] == group['owner']}
                for m in group['members']]}

    def app_ask(self, gid: str, agent: str, content: str) -> dict:
        """Delivers a request to one agent's device only; its replies come back as app output."""
        with self.db() as db:
            member = next((m for m in self._card(db, gid)['members'] if m['principal'] == agent and m['is_agent']), None)
            if member is None:
                raise RelayError('这个空间没有这个 agent', 404)
            if member['muted']:
                raise RelayError('这个 agent 已被禁言', 403)
            event = self._event(db, gid, 'app_request', {'agent': agent, 'agent_id': member['agent_id'],
                                                         'connection': member['connection_id'], 'content': content})
            return {'request': event}

    def app_events(self, gid: str, cursor: int | None) -> dict:
        """What the app receives after `cursor`: agent output and membership changes. No cursor starts now."""
        with self.db() as db:
            if cursor is None:
                return {'items': [], 'cursor': db.execute('SELECT COALESCE(MAX(id),0) FROM relay_events WHERE group_id=?', (gid,)).fetchone()[0]}
            rows = db.execute("SELECT * FROM relay_events WHERE group_id=? AND id>? AND kind IN ('app_output','metadata','joined') "
                              'ORDER BY id LIMIT 100', (gid, cursor)).fetchall()
            items, members = [], False
            for row in rows:
                if row['kind'] == 'app_output':
                    items.append({'type': 'agent', 'id': row['id'], **json.loads(row['body'])})
                elif not members:
                    members = True
                    items.append({'type': 'members', 'id': row['id']})
            return {'items': items, 'cursor': rows[-1]['id'] if rows else cursor}

    def app_viewer(self, token: str) -> dict:
        """The member a device or browser opens the space's app as."""
        with self.db() as db:
            conn = self.auth(db, token)
            member = db.execute("SELECT id,name FROM relay_members WHERE connection_id=? AND agent_id=''", (conn['id'],)).fetchone()
            if not db.execute('SELECT 1 FROM relay_app_keys WHERE group_id=?', (conn['group_id'],)).fetchone():
                raise RelayError('这个空间没有小程序', 404)
            return {'group_id': conn['group_id'], 'member': member['id']}

    def app_member(self, gid: str, principal: str) -> dict:
        with self.db() as db:
            member = next((m for m in self._card(db, gid)['members'] if m['principal'] == principal and not m['is_agent']), None)
            if member is None:
                raise RelayError('你已不在这个空间', 403)
            return {'id': member['principal'], 'name': member['name']}

    @staticmethod
    def _app_output(db, gid, sender, content, attachments, client_msg_id):
        """In a space with an app, what an agent posts goes to the app instead of the chat."""
        if not sender['is_agent']:
            raise RelayError('这个空间由小程序接管，请在小程序里操作', 409)
        if client_msg_id:
            previous = db.execute('SELECT * FROM relay_events WHERE group_id=? AND sender=? AND client_id=?',
                                  (gid, sender['principal'], client_msg_id)).fetchone()
            if previous:
                return {'message': {**json.loads(previous['body']), 'id': previous['id']}, 'created': False, 'app': True}
        request = db.execute("SELECT id FROM relay_events WHERE group_id=? AND kind='app_request' AND json_extract(body,'$.agent')=? "
                             'ORDER BY id DESC LIMIT 1', (gid, sender['principal'])).fetchone()
        output = {'agent': sender['principal'], 'name': sender['name'], 'content': content, 'attachments': attachments,
                  'request': request['id'] if request else None, 'created_at': time.time()}
        event = db.execute('INSERT INTO relay_events(group_id,kind,body,created_at,sender,client_id) VALUES(?,?,?,?,?,?)',
                           (gid, 'app_output', json.dumps(output, ensure_ascii=False), output['created_at'],
                            sender['principal'], client_msg_id or None)).lastrowid
        return {'message': {**output, 'id': event}, 'created': True, 'app': True}

    def admin_list(self):
        with self.db() as db:
            return [self._card(db, row['id']) for row in db.execute('SELECT id FROM relay_groups ORDER BY created_at DESC')]
