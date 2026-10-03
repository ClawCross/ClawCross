"""Group relay persistence. It knows participants and credentials, never agent runtimes."""
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
            ''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(relay_connections)')}
            for column, definition in [('guest', 'INTEGER NOT NULL DEFAULT 0'),
                                       ('guest_salt', "TEXT NOT NULL DEFAULT ''"),
                                       ('guest_password_hash', "TEXT NOT NULL DEFAULT ''")]:
                if column not in columns:
                    db.execute(f'ALTER TABLE relay_connections ADD COLUMN {column} {definition}')
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
    def auth(db, token: str):
        row = db.execute('SELECT * FROM relay_connections WHERE token_hash=? AND revoked=0', (digest(token),)).fetchone()
        if row is None:
            row = db.execute('''SELECT c.* FROM relay_connections c JOIN relay_guest_tokens t ON t.connection_id=c.id
                                WHERE t.token_hash=? AND c.revoked=0 AND c.guest=1''', (digest(token),)).fetchone()
        if row is None:
            raise RelayError('群凭证无效、已退出或被撤销', 401)
        return row

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
                  c.node_id,c.user_id,c.display_name,c.id AS connection_id
                  FROM relay_members m JOIN relay_connections c ON c.id=m.connection_id
                  WHERE m.group_id=? AND c.revoked=0 ORDER BY c.joined_at,m.agent_id''', (gid,))]
        for member in members:
            member['is_agent'] = bool(member['agent_id'])
            member['muted'] = bool(member['muted'])
        return {'group_id': gid, 'title': group['title'], 'kind': group['kind'],
                'owner': group['owner_connection'], 'primary_agent': group['primary_member'],
                'dnd': bool(group['dnd']), 'version': group['version'], 'members': members,
                'member_count': len(members)}

    def _connect(self, db, gid: str, *, node_id: str, user_id: str, display_name: str):
        self._unique_name(db, gid, display_name, guests_only=True)
        if len(self._card(db, gid)['members']) >= 256:
            raise RelayError('群最多容纳 256 个成员', 409)
        if db.execute('SELECT COUNT(*) FROM relay_connections WHERE group_id=? AND revoked=0', (gid,)).fetchone()[0] >= 128:
            raise RelayError('群成员连接已达到上限', 409)
        cid, token = 'c_' + secrets.token_hex(12), secrets.token_urlsafe(32)
        db.execute('INSERT INTO relay_connections(id,group_id,token_hash,node_id,user_id,display_name,joined_at) VALUES(?,?,?,?,?,?,?)',
                   (cid, gid, digest(token), node_id, user_id, display_name, time.time()))
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
            cid, token = self._connect(db, gid, node_id=node_id, user_id=user_id, display_name=display_name)
            db.execute('UPDATE relay_groups SET owner_connection=? WHERE id=?', (cid, gid))
            self._event(db, gid, 'metadata', {})
            return {'token': token, 'connection_id': cid, 'group': self._card(db, gid)}

    def join(self, invite: str, *, node_id: str, user_id: str, display_name: str) -> dict:
        """A new member connection, by the group's invitation link."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT group_id FROM relay_guest_invites WHERE token_hash=?', (digest(invite),)).fetchone()
            if not row:
                raise RelayError('邀请链接已失效', 403)
            gid = row['group_id']
            cid, token = self._connect(db, gid, node_id=node_id, user_id=user_id, display_name=display_name)
            db.execute('UPDATE relay_groups SET version=version+1 WHERE id=?', (gid,))
            self._event(db, gid, 'joined', {'connection_id': cid, 'display_name': display_name})
            return {'token': token, 'connection_id': cid, 'group': self._card(db, gid)}

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

    def guest_info(self, invite):
        with self.db() as db:
            row = db.execute('SELECT group_id FROM relay_guest_invites WHERE token_hash=?', (digest(invite),)).fetchone()
            if not row:
                raise RelayError('分享链接已失效', 403)
            return {'title': self._card(db, row['group_id'])['title'], 'group_id': row['group_id']}

    @staticmethod
    def _guest_password(password):
        if not isinstance(password, str) or not 6 <= len(password) <= 128:
            raise RelayError('密码需要 6 至 128 个字符')

    def guest_join(self, invite, name, password=''):
        name = name.strip()
        self._guest_password(password)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT group_id FROM relay_guest_invites WHERE token_hash=?', (digest(invite),)).fetchone()
            if not row:
                raise RelayError('分享链接已失效', 403)
            gid = row['group_id']
            normalized = unicodedata.normalize('NFKC', name).casefold()
            existing = next((m for m in db.execute('''SELECT m.name,c.* FROM relay_members m
                JOIN relay_connections c ON c.id=m.connection_id WHERE m.group_id=? AND c.revoked=0''', (gid,))
                if unicodedata.normalize('NFKC', m['name'].strip()).casefold() == normalized), None)
            if existing:
                if not existing['guest'] or not existing['guest_password_hash']:
                    raise RelayError('这个名字已有人使用；旧访客请在原浏览器设置密码，或请群主移除后重新加入', 409)
                if not hmac.compare_digest(existing['guest_password_hash'], password_hash(password, existing['guest_salt'])):
                    raise RelayError('名字或密码不正确', 403)
                token = secrets.token_urlsafe(32)
                db.execute('INSERT INTO relay_guest_tokens VALUES(?,?,?)', (digest(token), existing['id'], time.time()))
                # Bound stored browser credentials while retaining concurrent devices.
                db.execute('''DELETE FROM relay_guest_tokens WHERE connection_id=? AND token_hash NOT IN
                              (SELECT token_hash FROM relay_guest_tokens WHERE connection_id=? ORDER BY created_at DESC LIMIT 16)''',
                           (existing['id'], existing['id']))
                return {'token': token, 'name': existing['name'], 'password_set': True}
            self._unique_name(db, gid, name)
            cid, token = self._connect(db, gid, node_id='guest', user_id='guest_' + secrets.token_hex(12), display_name=name)
            salt = secrets.token_hex(16)
            db.execute('UPDATE relay_connections SET guest=1,guest_salt=?,guest_password_hash=? WHERE id=?',
                       (salt, password_hash(password, salt), cid))
            db.execute('UPDATE relay_groups SET version=version+1 WHERE id=?', (gid,))
            self._event(db, gid, 'joined', {'connection_id': cid, 'display_name': name})
            return {'token': token, 'name': name, 'password_set': True}

    def guest_set_password(self, token, password):
        self._guest_password(password)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            conn = self.auth(db, token)
            if not conn['guest']:
                raise RelayError('需要访客身份', 403)
            salt = secrets.token_hex(16)
            db.execute('UPDATE relay_connections SET guest_salt=?,guest_password_hash=? WHERE id=?',
                       (salt, password_hash(password, salt), conn['id']))
            return {'password_set': True}

    def guest_identity(self, token):
        with self.db() as db:
            conn = self.auth(db, token)
            if not conn['guest']:
                raise RelayError('需要访客身份', 403)
            member = db.execute("SELECT id,name FROM relay_members WHERE connection_id=? AND agent_id=''", (conn['id'],)).fetchone()
            return {'principal': member['id'], 'name': member['name']}

    def guest_state(self, token, after=-1):
        with self.db() as db:
            db.execute('BEGIN')
            conn = self.auth(db, token)
            if not conn['guest']:
                raise RelayError('需要访客身份', 403)
            group = self._card(db, conn['group_id'])
            member = next(m for m in group['members'] if m['connection_id'] == conn['id'] and not m['is_agent'])
            if after < 0:
                cursor = db.execute('SELECT COALESCE(MAX(id),0) FROM relay_events WHERE group_id=?', (conn['group_id'],)).fetchone()[0]
                rows = list(reversed(db.execute("SELECT * FROM relay_events WHERE group_id=? AND kind='message' ORDER BY id DESC LIMIT 100", (conn['group_id'],)).fetchall()))
                more = False
            else:
                rows = db.execute('SELECT * FROM relay_events WHERE group_id=? AND id>? ORDER BY id LIMIT 50', (conn['group_id'], after)).fetchall()
                cursor = rows[-1]['id'] if rows else after
                more = len(rows) >= 50
            messages = []
            for row in rows:
                if row['kind'] == 'message':
                    message = json.loads(row['body'])['message']
                    messages.append({k: message[k] for k in ('sender', 'sender_name', 'content', 'created_at')} | {'id': row['id']})
            return {'group_id': conn['group_id'], 'password_set': bool(conn['guest_password_hash']), 'title': group['title'], 'members': [{k: m[k] for k in ('principal', 'name', 'is_agent')} for m in group['members']],
                    'messages': messages, 'cursor': cursor, 'has_more': more, 'principal': member['principal'], 'name': member['name']}

    def guest_rename(self, token, name):
        name = name.strip()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            conn = self.auth(db, token)
            if not conn['guest']:
                raise RelayError('需要访客身份', 403)
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
            if conn['guest']:
                raise RelayError('访客只能以本人身份聊天', 403)
            self._unique_name(db, conn['group_id'], name, guests_only=True)
            existing = db.execute('SELECT 1 FROM relay_members WHERE connection_id=? AND agent_id=?', (conn['id'], agent_id)).fetchone()
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
            if conn['guest'] and action != 'leave':
                raise RelayError('访客不能管理群或成员', 403)
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
            elif action == 'patch':
                if not owner:
                    raise RelayError('只有群主可以管理群', 403)
                for key in ('title', 'dnd'):
                    if key in fields and fields[key] is not None:
                        if key == 'title' and (not isinstance(fields[key], str) or not fields[key].strip() or len(fields[key]) > 160):
                            raise RelayError('群名称需要 1 至 160 个字符')
                        if key != 'title' and not isinstance(fields[key], bool):
                            raise RelayError('群设置必须为布尔值')
                        db.execute(f'UPDATE relay_groups SET {key}=? WHERE id=?', (fields[key], conn['group_id']))
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
            return {'left': True} if action == 'leave' else self._card(db, conn['group_id'])

    def post(self, token: str, *, content: str, agent_id: str = '', mentions: list[str] = (),
             attachments: list[dict] = (), client_msg_id: str = '', expected_title: str | None = None,
             reply_to: int | None = None) -> dict:
        if len(json.dumps({'content': content, 'attachments': list(attachments)}, ensure_ascii=False).encode()) > 512 * 1024:
            raise RelayError('群消息及附件最多 512 KiB', 413)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            conn = self.auth(db, token)
            if conn['guest'] and (agent_id or attachments):
                raise RelayError('访客只能发送本人文本消息', 403)
            group = self._card(db, conn['group_id'])
            sender = next((m for m in group['members'] if m['connection_id'] == conn['id'] and m['agent_id'] == agent_id), None)
            if sender is None or sender['muted']:
                raise RelayError('发送者未加入群或已被禁言', 403)
            if expected_title is not None and expected_title != group['title']:
                raise RelayError('群名称与预期不符，请确认目标群')
            if reply_to and not db.execute('SELECT 1 FROM relay_events WHERE id=? AND group_id=? AND kind=?',
                                          (reply_to, conn['group_id'], 'message')).fetchone():
                raise RelayError('回复引用不属于本群')
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
                       'reply_to': reply_to, 'attachments': list(attachments), 'created_at': time.time()}
            cursor = db.execute('INSERT INTO relay_events(group_id,kind,body,created_at,sender,client_id) VALUES(?,?,?,?,?,?)',
                                (conn['group_id'], 'message', json.dumps({'message': message, 'targets': targets}, ensure_ascii=False),
                                 message['created_at'], sender['principal'], client_msg_id or None))
            return {'message': {**message, 'id': cursor.lastrowid}, 'created': True}

    def events(self, token: str, since: int, *, limit: int = 50) -> dict:
        with self.db() as db:
            conn = self.auth(db, token)
            group = self._card(db, conn['group_id'])
            # Join grants access to the group history, while credentials constrain write identity.
            rows = db.execute('SELECT * FROM relay_events WHERE group_id=? AND id>? ORDER BY id LIMIT ?',
                              (conn['group_id'], since, min(100, max(1, limit)))).fetchall()
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

    def acknowledge(self, token: str, cursor: int):
        with self.db() as db:
            conn = self.auth(db, token)
            maximum = db.execute('SELECT COALESCE(MAX(id),0) FROM relay_events WHERE group_id=?', (conn['group_id'],)).fetchone()[0]
            if cursor > maximum:
                raise RelayError('确认游标超出群消息范围')
            db.execute('UPDATE relay_connections SET ack=MAX(ack,?) WHERE id=?', (cursor, conn['id']))

    def admin_list(self):
        with self.db() as db:
            return [self._card(db, row['id']) for row in db.execute('SELECT id FROM relay_groups ORDER BY created_at DESC')]
