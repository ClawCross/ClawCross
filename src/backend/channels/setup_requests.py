"""Server-owned setup forms. Credentials are written directly, never in a tool result."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from channels.channel_catalog import get_channel, get_channels
from common.runtime_paths import CONFIG_DIR, ENV_FILE, PID_DIR
from common.env_settings import read_env_all, write_env_settings

DB_PATH = CONFIG_DIR / 'channel-setup.sqlite3'


def human_only(field: dict) -> bool:
    name = str(field.get('name', '')).upper()
    return field.get('type') == 'password' or any(word in name for word in
        ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'URL', 'API_ROOT', '_BIN', '_CONFIG'))


def describe(channel_id: str = '') -> list[dict]:
    channels = [get_channel(channel_id)] if channel_id else get_channels()
    if any(ch is None for ch in channels):
        raise ValueError('Unknown channel')
    return [{**ch, 'fields': [{**field, 'human_only': human_only(field)}
                              for field in ch.get('fields', [])]} for ch in channels]


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS requests (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
        channel TEXT NOT NULL, draft TEXT NOT NULL, status TEXT NOT NULL,
        created REAL NOT NULL)''')
    try:
        with db:
            yield db
    finally:
        db.close()


def _values(channel: dict, values: dict, *, from_agent: bool = False) -> dict:
    if not isinstance(values, dict):
        raise ValueError('Settings must be an object')
    fields = {field['name']: field for field in channel.get('fields', [])}
    result = {}
    for key, value in values.items():
        field = fields.get(key)
        if field is None or (from_agent and human_only(field)):
            raise ValueError('Unknown field or field requires private user input: ' + str(key))
        if isinstance(value, bool):
            value = 'true' if value else 'false'
        if not isinstance(value, str) or len(value) > 8192 or (field.get('target') == 'env' and ('\r' in value or '\n' in value)):
            raise ValueError('Invalid value for field: ' + key)
        if field.get('type') == 'boolean' and value not in {'true', 'false'}:
            raise ValueError('Expected true or false for field: ' + key)
        if field.get('type') == 'number' and value and (not value.isdigit() or not 1 <= int(value) <= 65535):
            raise ValueError('Expected a valid port for field: ' + key)
        if value and (key.endswith('_WS_URLS') or key.endswith('_API_ROOTS')):
            try:
                parsed = json.loads(value)
                if not isinstance(parsed, list if key.endswith('_WS_URLS') else dict):
                    raise ValueError()
            except ValueError:
                raise ValueError('Invalid JSON for field: ' + key) from None
        if value and field.get('pattern') and not re.fullmatch(field['pattern'], value):
            raise ValueError('Invalid format for field: ' + key)
        result[key] = value
    return result


def create(user_id: str, session_id: str, channel_id: str, values: dict | None = None) -> dict:
    if not user_id or not session_id:
        raise ValueError('An authenticated user and Agent are required')
    channel = describe(channel_id)[0]
    draft = _values(channel, values or {}, from_agent=True)
    request_id = 'setup-' + uuid.uuid4().hex
    with _connect() as db:
        # Repeated requests from the same Agent replace its unsubmitted form.
        db.execute("UPDATE requests SET status='cancelled' WHERE user_id=? AND session_id=? AND status='pending'",
                   (user_id, session_id))
        db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?)',
                   (request_id, user_id, session_id, channel['id'], json.dumps(draft), 'pending', time.time()))
    return {'kind': 'clawcross_channel_setup_v1', 'id': request_id, 'channel': channel['id'], 'status': 'pending',
            'message': '请在设置气泡中填写并保存。密钥不进入对话；无网页时请打开渠道设置页面。'}


def list_requests(user_id: str, session_id: str = '', *, include_finished: bool = False) -> list[dict]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM requests WHERE user_id=? AND (? OR status='pending') AND created>? "
                          "AND (?='' OR session_id=?) ORDER BY created DESC LIMIT 20",
                          (user_id, include_finished, time.time() - 86400, session_id, session_id)).fetchall()
    return [{**dict(row), 'draft': json.loads(row['draft']), 'schema': describe(row['channel'])[0]} for row in rows]


def status(user_id: str, request_id: str) -> dict:
    with _connect() as db:
        row = db.execute('SELECT status,channel,created FROM requests WHERE id=? AND user_id=?',
                         (request_id, user_id)).fetchone()
    if row is None:
        raise ValueError('Setup request not found')
    return {'id': request_id, 'channel': row['channel'], 'status': 'expired' if row['status'] == 'pending' and row['created'] < time.time()-86400 else row['status']}


def close_pending(user_id: str, request_id: str, state: str) -> dict:
    """Close a waiting tool's form without overwriting a simultaneous submission."""
    if state not in {'cancelled', 'expired'}:
        raise ValueError('Invalid final form state')
    with _connect() as db:
        db.execute("UPDATE requests SET status=?,draft='{}' WHERE id=? AND user_id=? AND status='pending'",
                   (state, request_id, user_id))
    return status(user_id, request_id)


def _apply(channel: dict, values: dict, env_path: Path) -> None:
    raw = read_env_all(str(env_path))
    env_key = channel.get('env_key')
    bots = json.loads(raw.get(env_key, '[]') or '[]') if env_key else []
    if not isinstance(bots, list):
        raise ValueError('Existing bot configuration is not an array; edit advanced settings first')
    bot = dict(bots[0]) if bots else {}
    updates = {}
    def location(field):
        parent = bot
        intent = {'bot_intent': 'intent', 'bot_intents': 'intents'}.get(field.get('target'))
        if intent:
            parent = parent.setdefault(intent, {})
        parts = str(field.get('path') or field['name']).split('.')
        for part in parts[:-1]:
            parent = parent.setdefault(part, {})
        return parent, parts[-1]
    for field in channel.get('fields', []):
        key = field['name']
        parent, name = location(field)
        existing = raw.get(field.get('env_key') or key) if field.get('target') == 'env' else parent.get(name)
        value = values.get(key, '' if existing is not None else field.get('default', ''))
        if value == '' and key not in values:
            continue  # Retain stored credentials and omitted optional fields.
        target = field.get('target', 'bot')
        if field.get('required') and not value:
            existing = raw.get(field.get('env_key') or key) if target == 'env' else bot.get(key)
            if not existing:
                raise ValueError('Required field: ' + key)
            continue
        if target == 'env':
            updates[field.get('env_key') or key] = value
        else:
            parent[name] = value == 'true' if field.get('type') == 'boolean' else int(value) if field.get('type') == 'number' and value else value
    # Required fields omitted from a form still require an existing value.
    for field in channel.get('fields', []):
        if field.get('required'):
            parent, name = location(field)
            current = updates.get(field.get('env_key') or field['name'], raw.get(field.get('env_key') or field['name'])) if field.get('target') == 'env' else parent.get(name)
            if not current:
                raise ValueError('Required field: ' + field['name'])
    if env_key:
        bots = [bot, *bots[1:]] if bots else [bot]
        updates[env_key] = json.dumps(bots, ensure_ascii=False)
    if channel.get('kind') == 'nonebot':
        adapters = [item.strip() for item in raw.get('NONEBOT_ADAPTERS', '').split(',') if item.strip()]
        adapter = channel.get('adapter', channel['id'])
        updates['NONEBOT_ADAPTERS'] = ','.join(dict.fromkeys([*adapters, adapter]))
    write_env_settings(str(env_path), updates)


def submit(user_id: str, request_id: str, values: dict, *, cancel=False, env_path: Path | None = None) -> dict:
    with _connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM requests WHERE id=? AND user_id=?', (request_id, user_id)).fetchone()
        if row is None or row['status'] != 'pending' or row['created'] < time.time() - 86400:
            raise ValueError('Setup request not found, expired or already completed')
        if not cancel:
            channel = describe(row['channel'])[0]
            _apply(channel, _values(channel, {**json.loads(row['draft']), **values}), env_path or ENV_FILE)
        state = 'cancelled' if cancel else 'completed'
        db.execute('UPDATE requests SET status=?,draft=? WHERE id=?', (state, '{}', request_id))
    if not cancel:
        PID_DIR.mkdir(parents=True, exist_ok=True)
        (PID_DIR / 'channels_restart_flag').write_text('restart')
    return {'id': request_id, 'status': state, 'message': '已取消。' if cancel else '已保存，渠道将在后台重新连接。'}
