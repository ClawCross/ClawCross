"""Explicit host session catalog and user-bound registration tickets."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from common.runtime_paths import CONFIG_DIR
from agents.store import ACPX

DB_PATH = CONFIG_DIR / 'native-sessions.sqlite3'


@contextmanager
def database():
    DB_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS tickets (
        ticket TEXT PRIMARY KEY, owner TEXT NOT NULL, platform TEXT NOT NULL,
        native_id TEXT NOT NULL, cwd TEXT NOT NULL, title TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS claims (
        platform TEXT NOT NULL, native_id TEXT NOT NULL, owner TEXT NOT NULL, agent_id TEXT NOT NULL,
        PRIMARY KEY(platform,native_id));''')
    try:
        with db:
            yield db
    finally:
        db.close()


def allowed(user_id: str) -> bool:
    """Authenticated users can import by default; an explicit host list can narrow it."""
    users = [item.strip() for item in os.getenv('CLAWCROSS_NATIVE_SESSION_USERS', '').split(',') if item.strip()]
    return bool(user_id) and (not users or user_id in users)


def _foreign_native_ids(platform: str, owner: str, store=None) -> set[str]:
    # Local acpx metadata already records which ClawCross owner holds a
    # transport. Never read its messages or expose another user's row.
    result = set()
    from agents.store import get_store
    from external.session import runtime_session
    owned_names = {runtime_session(agent) for agent in (store or get_store()).list(owner) if agent.platform == platform}
    package = {'codex':'codex-acp', 'claude':'claude-agent-acp'}[platform]
    for path in (Path.home() / '.acpx' / 'sessions').glob('*.json'):
        try:
            if path.stat().st_size > 16 * 1024 * 1024:
                continue
            row = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        name = row.get('name') or ''
        if (name.startswith('clawcross-') and name not in owned_names
                and package in (row.get('agent_command') or '')):
            result.update(str(row[key]) for key in ('agent_session_id', 'acp_session_id') if row.get(key))
    return result


async def catalog(owner: str, platform: str, *, cursor: str = '', adapter=None, store=None) -> dict:
    if platform not in {'codex', 'claude'}:
        raise ValueError('Only Codex and Claude native sessions are supported')
    if adapter is None:
        from external.acpx import get_acpx_adapter
        adapter = get_acpx_adapter()
    result = await adapter.list_native_sessions(tool=platform, cursor=cursor)
    hidden = _foreign_native_ids(platform, owner, store)
    rows = []
    with database() as db:
        db.execute('DELETE FROM tickets WHERE expires<?', (time.time(),))
        for row in result['sessions']:
            if row['session_id'] in hidden:
                continue
            claim = db.execute('SELECT * FROM claims WHERE platform=? AND native_id=?', (platform, row['session_id'])).fetchone()
            if claim and claim['owner'] != owner:
                continue
            cwd = Path(row['cwd'])
            if not cwd.is_absolute() or not cwd.is_dir():
                continue
            ticket = secrets.token_urlsafe(24)
            db.execute('INSERT INTO tickets VALUES (?,?,?,?,?,?,?)', (ticket, owner, platform, row['session_id'], str(cwd.resolve()), row['title'], time.time()+600))
            rows.append({**row, 'ticket': ticket, 'registered_agent_id': claim['agent_id'] if claim else ''})
    return {'sessions': rows, 'next_cursor': result.get('next_cursor'), 'platform': platform}


def register(owner: str, ticket: str, name: str, store):
    """Register only a catalog result; no prompt, native load or history copy."""
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM tickets WHERE ticket=? AND owner=? AND expires>?', (ticket, owner, time.time())).fetchone()
        if row is None:
            raise ValueError('Session selection expired; refresh the native list')
        claim = db.execute('SELECT * FROM claims WHERE platform=? AND native_id=?', (row['platform'], row['native_id'])).fetchone()
        if claim:
            if claim['owner'] != owner:
                raise ValueError('Session is already registered by another user')
            existing = store.get(owner, claim['agent_id'])
            if existing:
                return existing
            db.execute('DELETE FROM claims WHERE platform=? AND native_id=?', (row['platform'], row['native_id']))
        if not Path(row['cwd']).is_dir():
            raise ValueError('The original session workspace no longer exists')
        agent = store.create(owner, driver=ACPX, name=name.strip()[:160] or row['title'] or row['platform'],
                             config={'platform':row['platform'], 'meta':{'acp':{'clawcross_tools':True}}})
        try:
            store.set_runtime(owner, agent.agent_id, {'native_resume_id':row['native_id'], 'acp_cwd':row['cwd']})
            db.execute('INSERT INTO claims VALUES (?,?,?,?)', (row['platform'], row['native_id'], owner, agent.agent_id))
        except BaseException:
            store.delete(owner, agent.agent_id)
            raise
    return store.require(owner, agent.agent_id)


def release_agent(owner: str, agent_id: str) -> None:
    if not DB_PATH.exists():
        return
    with database() as db:
        db.execute('DELETE FROM claims WHERE owner=? AND agent_id=?', (owner, agent_id))
