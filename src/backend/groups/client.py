"""A device's group client. Remote credentials never grant access to the agent API."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect

from agents.messages import AgentMessage
from agents.gateway import reply_channel
from groups.config import service_key, service_url
from groups.delivery import render_digest
from groups.service import GroupError

logger = logging.getLogger(__name__)


class ClientError(GroupError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def normalize_url(value: str) -> str:
    raw = value.strip() or service_url()
    if '://' not in raw:
        raw = 'http://' + raw
    parsed = urlsplit(raw)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
        raise ClientError('请使用服务器地址，例如 http://192.168.1.10:51203，不包含凭证或额外路径')
    try:
        port = parsed.port
    except ValueError as exc:
        raise ClientError('服务器端口无效') from exc
    host = parsed.hostname.lower()
    host = '[' + host + ']' if ':' in host else host
    return f'{parsed.scheme}://{host}' + (f':{port}' if port else '')


class ClientStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS group_client_identity(id INTEGER PRIMARY KEY CHECK(id=1),node_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS group_client_connections(
                    owner TEXT NOT NULL, alias TEXT NOT NULL, url TEXT NOT NULL, remote_id TEXT NOT NULL,
                    connection_id TEXT NOT NULL, token TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0,
                    allowed_agents TEXT NOT NULL DEFAULT '[]', metadata TEXT NOT NULL DEFAULT '{}',
                    active INTEGER NOT NULL DEFAULT 1, error TEXT NOT NULL DEFAULT '', PRIMARY KEY(owner,alias)
                );
                CREATE TABLE IF NOT EXISTS group_client_events(
                    owner TEXT NOT NULL,alias TEXT NOT NULL,id INTEGER NOT NULL,body TEXT NOT NULL,
                    PRIMARY KEY(owner,alias,id)
                );
                CREATE TABLE IF NOT EXISTS group_client_deliveries(
                    owner TEXT NOT NULL,alias TEXT NOT NULL,event_id INTEGER NOT NULL,agent_id TEXT NOT NULL,
                    PRIMARY KEY(owner,alias,event_id,agent_id)
                );
            ''')
            db.execute('INSERT OR IGNORE INTO group_client_identity VALUES(1,?)', ('device_' + secrets.token_hex(16),))
            self.node_id = db.execute('SELECT node_id FROM group_client_identity WHERE id=1').fetchone()[0]
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, owner=None):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM group_client_connections' + (' WHERE owner=?' if owner is not None else ''),
                                               (owner,) if owner is not None else ())]

    def get(self, owner, alias):
        with self.db() as db:
            row = db.execute('SELECT * FROM group_client_connections WHERE owner=? AND alias=?', (owner, alias)).fetchone()
            return dict(row) if row else None

    def save(self, owner, url, result):
        gid = result['group']['group_id']
        alias = 'rg_' + hashlib.sha256((url + '/' + gid).encode()).hexdigest()[:24]
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT active FROM group_client_connections WHERE owner=? AND alias=?', (owner, alias)).fetchone()
            if not existing or not existing['active']:
                total = db.execute('SELECT COUNT(*) FROM group_client_connections WHERE active=1').fetchone()[0]
                own = db.execute('SELECT COUNT(*) FROM group_client_connections WHERE owner=? AND active=1', (owner,)).fetchone()[0]
                if total >= 128 or own >= 32:
                    raise ClientError('设备最多连接 128 个群，每个用户最多连接 32 个群', 409)
            db.execute('''INSERT INTO group_client_connections(owner,alias,url,remote_id,connection_id,token,metadata)
                       VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner,alias) DO UPDATE SET
                       connection_id=excluded.connection_id,token=excluded.token,metadata=excluded.metadata,
                       cursor=0,allowed_agents='[]',active=1,error='正在连接' ''',
                       (owner, alias, url, gid, result['connection_id'], result['token'], json.dumps(result['group'], ensure_ascii=False)))
        return alias

    def update(self, owner, alias, **fields):
        allowed = {'cursor', 'metadata', 'active', 'error', 'allowed_agents'}
        if not set(fields) <= allowed:
            raise ValueError('invalid connection fields')
        with self.db() as db:
            db.execute('UPDATE group_client_connections SET ' + ','.join(k + '=?' for k in fields) + ' WHERE owner=? AND alias=?',
                       (*fields.values(), owner, alias))

    def cache(self, row, event):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO group_client_events VALUES(?,?,?,?)',
                       (row['owner'], row['alias'], event['id'], json.dumps(event, ensure_ascii=False)))
            cutoff = db.execute('SELECT id FROM group_client_events WHERE owner=? AND alias=? ORDER BY id DESC LIMIT 1 OFFSET 1999', (row['owner'], row['alias'])).fetchone()
            if cutoff:
                db.execute('DELETE FROM group_client_events WHERE owner=? AND alias=? AND id<?', (row['owner'], row['alias'], cutoff[0]))
                db.execute('DELETE FROM group_client_deliveries WHERE owner=? AND alias=? AND event_id<?', (row['owner'], row['alias'], cutoff[0]))

    def messages(self, owner, alias, after=0):
        with self.db() as db:
            rows = db.execute('SELECT body FROM group_client_events WHERE owner=? AND alias=? AND id>? ORDER BY id DESC LIMIT 100', (owner, alias, after)).fetchall()
            return [json.loads(r['body']) for r in reversed(rows)]

    def delivered(self, row, event_id, agent_id):
        with self.db() as db:
            return bool(db.execute('SELECT 1 FROM group_client_deliveries WHERE owner=? AND alias=? AND event_id=? AND agent_id=?',
                                   (row['owner'], row['alias'], event_id, agent_id)).fetchone())

    def mark_delivered(self, row, event_id, agent_id):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO group_client_deliveries VALUES(?,?,?,?)', (row['owner'], row['alias'], event_id, agent_id))

    def unread_digest(self, row, agent_id, before):
        with self.db() as db:
            after = db.execute('SELECT COALESCE(MAX(event_id),0) FROM group_client_deliveries WHERE owner=? AND alias=? AND agent_id=?',
                               (row['owner'], row['alias'], agent_id)).fetchone()[0]
        messages = [e['message'] for e in self.messages(row['owner'], row['alias'], after)
                    if e['kind'] == 'message' and e['id'] < before]
        return render_digest([{'sender': m['sender_name'], 'content': m['content']} for m in messages[-15:]])


class GroupClient:
    def __init__(self, store: ClientStore, agents, gateway):
        self.store, self.agents, self.gateway = store, agents, gateway
        self.tasks = {}
        self.closed = False

    @staticmethod
    def headers(row):
        return {'Authorization': 'Bearer ' + row['token']}

    def request(self, row, method, path, body=None):
        try:
            with httpx.Client(timeout=10, trust_env=False) as client:
                response = client.request(method, row['url'] + '/relay' + path, headers=self.headers(row), json=body)
            if response.status_code >= 400:
                raise ClientError(str(response.json().get('detail', '群服务器拒绝请求')), response.status_code)
            return response.json()
        except httpx.HTTPError as exc:
            raise ClientError('无法连接群服务器', 503) from exc

    @staticmethod
    def enroll(url, path, headers, body):
        try:
            with httpx.Client(timeout=15, trust_env=False) as client:
                response = client.post(url + '/relay/' + path, headers=headers, json=body)
            if response.status_code >= 400:
                raise ClientError(str(response.json().get('detail', '群服务器拒绝请求')), response.status_code)
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ClientError('无法连接群服务器或服务器响应无效', 503) from exc

    def join(self, owner, *, server_url='', group_id, password='', agents=()):
        url = normalize_url(server_url)
        known = next((r for r in self.store.rows(owner) if r['url'] == url and r['remote_id'] == group_id and r['active']), None)
        if known:
            for aid in agents:
                self.add_agent(owner, known['alias'], aid)
            return self.card(self.require(owner, known['alias']))
        for aid in agents:
            if self.agents.get(owner, aid) is None:
                raise ClientError('只能引入自己拥有的 agent', 403)
        headers = {'X-Group-Service-Key': service_key()} if url == service_url() else {}
        result = self.enroll(url, 'join', headers, {'group_id': group_id, 'password': password,
                             'node_id': self.store.node_id, 'user_id': owner, 'display_name': owner})
        alias = self.store.save(owner, url, result)
        for aid in agents:
            self.add_agent(owner, alias, aid)
        return self.card(self.require(owner, alias))

    def create(self, owner, *, title, kind='group', agents=(), password='', local_join=True):
        if kind == 'direct':
            if len(agents) != 1:
                raise ClientError('私聊需要且只能引入一个 agent')
            for row in self.store.rows(owner):
                if row['active'] and json.loads(row['metadata']).get('kind') == 'direct' and list(agents) == json.loads(row['allowed_agents']):
                    return self.card(row)
        for aid in agents:
            if self.agents.get(owner, aid) is None:
                raise ClientError('只能引入自己拥有的 agent', 403)
        url = service_url()
        result = self.enroll(url, 'create', {'X-Group-Service-Key': service_key()}, {
            'title': title, 'kind': kind, 'password': password, 'local_join': local_join,
            'node_id': self.store.node_id, 'user_id': owner, 'display_name': owner})
        alias = self.store.save(owner, url, result)
        for aid in agents:
            self.add_agent(owner, alias, aid)
        return self.card(self.require(owner, alias))

    def require(self, owner, alias):
        row = self.store.get(owner, alias)
        if not row or not row['active']:
            raise ClientError('未加入此群，或群凭证已撤销', 403)
        return row

    def add_agent(self, owner, alias, aid):
        row = self.require(owner, alias)
        agent = self.agents.get(owner, aid)
        if not agent:
            raise ClientError('只能引入自己拥有的 agent', 403)
        group = self.request(row, 'POST', '/agents', {'agent_id': aid, 'name': agent.name, 'platform': agent.platform})
        allowed = set(json.loads(row['allowed_agents']))
        allowed.add(aid)
        self.store.update(owner, alias, metadata=json.dumps(group, ensure_ascii=False), allowed_agents=json.dumps(sorted(allowed)))
        return self.card(self.require(owner, alias))

    def principal(self, row, principal):
        group = json.loads(row['metadata'])
        for member in group.get('members', []):
            if member['principal'] == principal or (member['connection_id'] == row['connection_id'] and
                    (member['agent_id'] == principal or (not member['agent_id'] and principal == 'u:' + row['owner']))):
                return member['principal']
        raise ClientError('成员不属于此群', 404)

    def manage(self, owner, alias, action, fields):
        row = self.require(owner, alias)
        if fields.get('principal'):
            fields = {**fields, 'principal': self.principal(row, fields['principal'])}
        result = self.request(row, 'POST', '/manage/' + action, fields)
        if action in {'leave', 'delete'}:
            self.store.update(owner, alias, active=0)
        else:
            self.store.update(owner, alias, metadata=json.dumps(result, ensure_ascii=False))
            if action == 'remove_member':
                allowed = set(json.loads(row['allowed_agents']))
                remaining = {m['agent_id'] for m in result['members'] if m['connection_id'] == row['connection_id']}
                self.store.update(owner, alias, allowed_agents=json.dumps(sorted(allowed & remaining)))
        return result if action in {'leave', 'delete'} else self.card(self.require(owner, alias))

    def card(self, row):
        group = json.loads(row['metadata'])
        allowed = set(json.loads(row['allowed_agents']))
        members = []
        for member in group.get('members', []):
            local = member['connection_id'] == row['connection_id']
            if local and member['agent_id'] and member['agent_id'] not in allowed:
                continue
            principal = (member['agent_id'] or 'u:' + row['owner']) if local else member['principal']
            agent = None
            if member['is_agent']:
                agent = {'agent_id': principal, 'name': member['name'], 'platform': member['platform'],
                         'remote': not local, 'settings': {'persona': '', 'tools': [], 'teams': []},
                         'status': {'state': 'remote' if not local else 'idle', 'actions': []}}
            members.append({**member, 'principal': principal, 'agent': agent, 'nickname': '', 'remote': not local})
        primary = None
        for original, visible in zip([m for m in group.get('members', []) if not (m['connection_id'] == row['connection_id'] and m['agent_id'] and m['agent_id'] not in allowed)], members):
            if original['principal'] == group.get('primary_agent'):
                primary = visible['principal']
        messages = self.messages(row['owner'], row['alias'])
        return {**group, 'group_id': row['alias'], 'remote_group_id': row['remote_id'], 'server_url': row['url'],
                'connection_state': 'connected' if not row['error'] else 'reconnecting', 'connection_error': row['error'],
                'owner': row['owner'] if group.get('owner') == row['connection_id'] else 'remote:' + str(group.get('owner')),
                'primary_agent': primary, 'members': members, 'member_count': len(members),
                'member_names': [m['name'] for m in members][:4], 'messages': messages,
                'last_message': messages[-1] if messages else None, 'message_count': len(messages),
                'updated_at': messages[-1]['created_at'] if messages else 0, 'federated': True}

    def messages(self, owner, alias, after=0):
        row = self.require(owner, alias)
        mapping = {m['principal']: (m['agent_id'] or 'u:' + owner) for m in json.loads(row['metadata']).get('members', [])
                   if m['connection_id'] == row['connection_id']}
        result = []
        for event in self.store.messages(owner, alias, after):
            if event['kind'] != 'message':
                continue
            message = event['message']
            result.append({**message, 'id': event['id'], 'sender': mapping.get(message['sender'], message['sender']),
                           'mentions': [mapping.get(m, m) for m in message.get('mentions', [])]})
        return result

    def memberships(self, owner, aid):
        result = []
        for row in self.store.rows(owner):
            if not row['active'] or aid not in json.loads(row['allowed_agents']):
                continue
            card = self.card(row)
            me = next((m for m in card['members'] if m['principal'] == aid and not m['remote']), None)
            if me:
                meta = self.metadata(card, me)
                agent = self.agents.get(owner, aid)
                if agent:
                    meta['reply_channel'] = reply_channel(agent, row['alias'])
                result.append(meta)
        return result

    @staticmethod
    def metadata(card, member):
        return {'group_id': card['group_id'], 'title': card['title'], 'kind': card['kind'],
                'owner': card['owner'], 'identity': member['name'],
                'role': 'primary_agent' if card['primary_agent'] == member['principal'] else 'member',
                'server_url': card['server_url'], 'remote_group_id': card['remote_group_id'],
                'delivery': '群成员可见；按当前群规则唤醒其他 agent。',
                'members': [{k: m[k] for k in ('name', 'muted', 'user_id', 'agent_id', 'node_id') if k in m} | {'kind': 'agent' if m['is_agent'] else 'human'} for m in card['members']],
                'reply_channel': f'send_to_group(group_id="{card["group_id"]}", content="你的回复")'}

    async def post(self, owner, alias, sender, content, **fields):
        row = self.require(owner, alias)
        aid = '' if sender == 'u:' + owner else sender
        if aid and (aid not in json.loads(row['allowed_agents']) or self.agents.get(owner, aid) is None):
            raise ClientError('发送 agent 未经本机用户授权加入此群', 403)
        mentions = [self.principal(row, p) for p in fields.get('mentions') or []]
        body = {'content': content, 'agent_id': aid, 'mentions': mentions, 'attachments': fields.get('attachments') or [],
                'client_msg_id': fields.get('client_msg_id') or secrets.token_hex(16), 'reply_to': fields.get('reply_to'),
                'expected_title': fields.get('expected_title')}
        try:
            async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                response = await client.post(row['url'] + '/relay/messages', headers=self.headers(row), json=body)
        except httpx.HTTPError as exc:
            raise ClientError('无法连接群服务器', 503) from exc
        if response.status_code >= 400:
            raise ClientError(str(response.json().get('detail', '发送失败')), response.status_code)
        result = response.json()
        self.store.cache(row, {'id': result['message']['id'], 'kind': 'message', 'message': result['message']})
        return {**result, 'message': self.messages(owner, alias, result['message']['id'] - 1)[-1]}

    async def consume(self, row, packet):
        if packet.get('connection_id') != row['connection_id']:
            raise ClientError('连接身份不匹配', 403)
        if packet.get('group', {}).get('group_id') != row['remote_id']:
            raise ClientError('群身份不匹配', 403)
        self.store.update(row['owner'], row['alias'], metadata=json.dumps(packet['group'], ensure_ascii=False), error='')
        row = self.require(row['owner'], row['alias'])
        allowed = set(json.loads(row['allowed_agents']))
        card = self.card(row)
        for event in packet.get('events', []):
            self.store.cache(row, event)
            if event.get('kind') == 'message':
                for member in packet['group'].get('members', []):
                    aid = member.get('agent_id')
                    if member.get('connection_id') != row['connection_id'] or aid not in allowed or member.get('muted') or member['principal'] not in event.get('targets', []):
                        continue
                    agent = self.agents.get(row['owner'], aid)
                    if aid not in json.loads(self.require(row['owner'], row['alias'])['allowed_agents']):
                        continue
                    if not agent or self.store.delivered(row, event['id'], aid):
                        continue
                    visible = next((m for m in card['members'] if m['principal'] == aid and not m['remote']), None)
                    if not visible:
                        continue
                    message = event['message']
                    text = self.store.unread_digest(row, aid, event['id']) + f'[群聊「{card["title"]}」 group_id={row["alias"]}] {message["sender_name"]} 说:\n{message["content"]}'
                    receipt = await self.gateway.inbox(agent, AgentMessage(text=text, sender=message['sender'],
                        summary=f'群聊「{card["title"]}」 {message["sender_name"]}: {message["content"][:60]}',
                        attachments=message.get('attachments', [])), context={'conversation_id': row['alias'],
                        'delivery_id': f'relay:{row["alias"]}:{event["id"]}:{aid}', 'groups': [{**self.metadata(card, visible), 'reply_channel': reply_channel(agent, row['alias'])}]})
                    if not receipt.accepted:
                        raise ClientError('本机 agent 暂未接受群消息，稍后重试', 503)
                    self.store.mark_delivered(row, event['id'], aid)
            self.store.update(row['owner'], row['alias'], cursor=event['id'])

    def ensure(self, owner, alias):
        key = (owner, alias)
        if self.closed:
            return
        if key not in self.tasks or self.tasks[key].done():
            task = asyncio.create_task(self.worker(owner, alias))
            self.tasks[key] = task
            def finished(done):
                if self.tasks.get(key) is done:
                    self.tasks.pop(key, None)
            task.add_done_callback(finished)

    async def worker(self, owner, alias):
        delay = 1
        while not self.closed:
            row = self.store.get(owner, alias)
            if not row or not row['active']:
                return
            try:
                uri = row['url'].replace('https://', 'wss://', 1).replace('http://', 'ws://', 1) + '/relay/ws'
                async with connect(uri, open_timeout=10, max_size=2 * 1024 * 1024, proxy=None) as ws:
                    await ws.send(json.dumps({'token': row['token'], 'cursor': row['cursor']}))
                    delay = 1
                    async for raw in ws:
                        packet = json.loads(raw)
                        if packet.get('type') != 'events':
                            continue
                        # A deleted local agent loses permission immediately, including offline deletes.
                        for member in list(packet['group'].get('members', [])):
                            if member['connection_id'] == row['connection_id'] and member.get('agent_id') and self.agents.get(owner, member['agent_id']) is None:
                                packet['group'] = await asyncio.to_thread(self.request, row, 'POST', '/manage/remove_member', {'principal': member['principal']})
                        await self.consume(self.require(owner, alias), packet)
                        cursor = self.require(owner, alias)['cursor']
                        await ws.send(json.dumps({'type': 'ack', 'cursor': cursor}))
                raise ConnectionError("group connection closed")
            except asyncio.CancelledError:
                return
            except Exception as exc:
                from websockets.exceptions import ConnectionClosed
                if isinstance(exc, ConnectionClosed) and exc.rcvd and exc.rcvd.code == 1008:
                    self.store.update(owner, alias, active=0, error='群凭证已失效或成员关系已撤销')
                    return
                self.store.update(owner, alias, error='连接中断，正在重连')
                await asyncio.sleep(delay)
                delay = min(30, delay * 2)

    async def start(self):
        for row in self.store.rows():
            if row['active']:
                self.ensure(row['owner'], row['alias'])

    async def close(self):
        self.closed = True
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
