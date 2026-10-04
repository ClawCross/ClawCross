"""Private, authenticated configuration forms; only completion enters Agent memory."""
from __future__ import annotations

import copy
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from common.runtime_paths import CONFIG_DIR, ENV_FILE
from common.env_settings import read_env_all, write_env_settings
from ops.configuration_catalog import CATALOG

DB_PATH = CONFIG_DIR / 'configuration-setup.sqlite3'


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15); db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS requests (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
        topic TEXT NOT NULL, draft TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL)''')
    try:
        with db: yield db
    finally: db.close()


def describe(user_id: str, session_id: str, topic: str = '', *, env_path=None) -> list[dict]:
    from channels import setup_requests as channels
    if topic.startswith('channel:'):
        return [{**channels.describe(topic[8:])[0], 'id': topic, 'scope': 'host'}]
    if not topic:
        return [dict(id=key, label=value['label'], scope=value['scope'], help=value['help']) for key, value in CATALOG.items()] + [
            dict(id='channel:'+ch['id'], label=ch['label'], scope='host', help=ch.get('help','')) for ch in channels.describe()]
    if topic not in CATALOG: raise ValueError('Unknown configuration topic')
    schema = copy.deepcopy(CATALOG[topic]); schema['id'] = topic
    if schema['scope'] == 'host':
        values = read_env_all(str(env_path or ENV_FILE))
    else:
        from webot.runtime_settings import get_runtime_settings
        values = getattr(get_runtime_settings(user_id, session_id), schema['section']).model_dump()
    for field in schema['fields']:
        value = values.get(field['name'])
        # Private values (even a short key) never appear in tool or API responses.
        field['configured'] = value is not None and value != ''
        if value is not None and field['type'] != 'password' and (not field['human_only'] or schema['scope'] == 'agent'):
            field['current'] = value
    return [schema]


def _values(schema, values, *, from_agent=False):
    if not isinstance(values, dict) or len(values) > len(schema['fields']): raise ValueError('Invalid field list')
    fields = {f['name']: f for f in schema['fields']}; out = {}
    for key, value in values.items():
        field = fields.get(key)
        if field is None or (from_agent and field['human_only']):
            raise ValueError('Unknown field or private user input required: ' + str(key))
        if isinstance(value, bool): value = 'true' if value else 'false'
        if isinstance(value, int) and field['type'] == 'number': value = str(value)
        if not isinstance(value, str) or len(value) > 8192: raise ValueError('Invalid field: ' + key)
        if schema['scope'] == 'host' and ('\n' in value or '\r' in value): raise ValueError('Multiline setting is not allowed: ' + key)
        if field['type'] == 'boolean':
            if value not in {'true', 'false'}: raise ValueError('Expected true or false: ' + key)
            value = value == 'true'
        elif field['type'] == 'number':
            if not value.isdigit() or not field['min'] <= int(value) <= field['max']: raise ValueError('Invalid number: ' + key)
            value = int(value)
        elif field['type'] == 'select' and value not in field['options']: raise ValueError('Invalid selection: ' + key)
        out[key] = value
    return out


def create(user_id: str, session_id: str, topic: str, values=None) -> dict:
    if not user_id or not session_id or session_id == 'settings' and topic in CATALOG and CATALOG[topic]['scope'] == 'agent':
        raise ValueError('Choose an Agent for Agent-specific settings')
    if topic.startswith('channel:'):
        from channels.setup_requests import create as create_channel
        return create_channel(user_id, session_id, topic[8:], values)
    schema = describe(user_id, session_id, topic)[0]
    draft = _values(schema, values or {}, from_agent=True)
    request_id = 'config-' + uuid.uuid4().hex
    with _connect() as db:
        db.execute("UPDATE requests SET status='cancelled',draft='{}' WHERE user_id=? AND session_id=? AND topic=? AND status='pending'", (user_id, session_id, topic))
        db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?)', (request_id,user_id,session_id,topic,json.dumps(draft), 'pending',time.time()))
    return {'kind':'clawcross_configuration_v1','id':request_id,'topic':topic,'status':'pending',
            'message':'请在对话中的设置表单填写并保存。密钥只交给后端；无网页时请到设置页面填写，不要在聊天中发送密钥。'}


def list_requests(user_id: str, session_id: str = '', *, env_path=None) -> list[dict]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM requests WHERE user_id=? AND status='pending' AND created>? AND (?='' OR session_id=?) ORDER BY created DESC LIMIT 20",
            (user_id,time.time()-86400,session_id,session_id)).fetchall()
    from channels.setup_requests import list_requests as channel_requests
    return [{**dict(row), 'draft':json.loads(row['draft']), 'schema':describe(user_id,row['session_id'],row['topic'],env_path=env_path)[0]} for row in rows] + [
        {**row, 'topic':'channel:'+row['channel']} for row in channel_requests(user_id,session_id)]


def status(user_id, request_id):
    if request_id.startswith('setup-'):
        from channels.setup_requests import status as channel_status
        return channel_status(user_id,request_id)
    with _connect() as db:
        row = db.execute('SELECT topic,status,created FROM requests WHERE id=? AND user_id=?',(request_id,user_id)).fetchone()
    if row is None: raise ValueError('Configuration request not found')
    return {'id':request_id,'topic':row['topic'],'status':'expired' if row['status']=='pending' and row['created'] < time.time()-86400 else row['status']}


def submit(user_id, request_id, values, *, cancel=False, env_path=None):
    if request_id.startswith('setup-'):
        from channels.setup_requests import submit as channel_submit
        return channel_submit(user_id,request_id,values,cancel=cancel,env_path=env_path)
    with _connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM requests WHERE id=? AND user_id=?',(request_id,user_id)).fetchone()
        if row is None or row['status']!='pending' or row['created']<time.time()-86400:
            raise ValueError('Configuration request not found, expired or completed')
        schema = describe(user_id,row['session_id'],row['topic'],env_path=env_path)[0]
        if not cancel:
            merged = {**json.loads(row['draft']), **values}
            # Defaults in a form are already typed. Validate them through the
            # same field path as user submissions; no credentials are stored.
            validated = _values(schema,merged)
            if schema['scope']=='host': write_env_settings(str(env_path or ENV_FILE),validated)
            else:
                from webot.runtime_settings import save_runtime_settings
                save_runtime_settings(user_id,session_id=row['session_id'],settings={schema['section']:validated})
                if 'mode' in validated:
                    from webot.runtime_store import save_session_mode
                    save_session_mode(user_id,row['session_id'],mode=validated['mode'],reason='用户通过配置表单选择')
        state = 'cancelled' if cancel else 'completed'
        db.execute('UPDATE requests SET status=?,draft=? WHERE id=?',(state,'{}',request_id))
    if row['session_id']!='settings':
        from webot.runtime_store import create_inbox_message
        create_inbox_message(user_id=user_id,target_session=row['session_id'],source_label='配置填写',message_id=request_id,
            content=f"[配置填写] {row['topic']}: {state}。用户填写值不提供给模型。")
    return {'id':request_id,'status':state,'message': '已保存。主机设置需要重新加载服务后生效。' if schema['scope']=='host' else '已保存，下次调用生效。'}
