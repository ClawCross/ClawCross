"""Saved SSH endpoints. Credentials stay outside every Agent workspace."""
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from contextlib import contextmanager


class ConnectionStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS device_connections(owner TEXT, id TEXT, config TEXT NOT NULL, PRIMARY KEY(owner,id))')
        if os.name != 'nt':self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path, timeout=10)
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def validate(value):
        allowed = {'name','host','username','port','identity_file','known_hosts_file','host_key_policy','python','acpx','node','enabled'}
        if not isinstance(value,dict) or set(value)-allowed:raise ValueError('Unknown SSH connection setting')
        config = {'name':'','username':'','port':22,'identity_file':'','known_hosts_file':'','enabled':True,
                  'host_key_policy':'yes','python':'python3','acpx':'acpx','node':'node',**value}
        if type(config['enabled']) is not bool:raise ValueError('Invalid enabled setting')
        for field in allowed-{'port','enabled'}:
            if not isinstance(config.get(field),str) or len(config[field])>1024 or any(ord(c)<32 for c in config[field]):
                raise ValueError('Invalid '+field)
            config[field]=config[field].strip()
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.:\-]{0,253}',config.get('host','')):raise ValueError('请输入 IP、域名或 SSH 配置别名')
        if config['username'] and not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',config['username']):raise ValueError('Invalid SSH username')
        if type(config['port']) is not int or not 1<=config['port']<=65535:raise ValueError('Invalid SSH port')
        if config['host_key_policy'] not in {'yes','accept-new'}:raise ValueError('Host key verification cannot be disabled')
        for field in ('python','acpx','node'):
            if not re.fullmatch(r'[A-Za-z0-9_./~:\\-]{1,1024}',config[field]):raise ValueError('Invalid remote executable: '+field)
        config['name']=config['name'] or config['host']
        return config

    def get(self, owner, connection_id):
        with self.db() as db:
            row=db.execute('SELECT config FROM device_connections WHERE owner=? AND id=?',(owner,connection_id)).fetchone()
        if not row:raise LookupError('SSH connection does not belong to this user or no longer exists')
        return {'id':connection_id,**json.loads(row[0])}

    def list(self, owner):
        with self.db() as db:
            return [{'id':row[0],**json.loads(row[1])} for row in db.execute('SELECT id,config FROM device_connections WHERE owner=? ORDER BY id',(owner,))]

    def save(self, owner, value, connection_id=''):
        if connection_id:self.get(owner,connection_id)
        config=self.validate(value);connection_id=connection_id or 'ssh_'+secrets.token_hex(8)
        with self.db() as db:
            db.execute('INSERT INTO device_connections VALUES(?,?,?) ON CONFLICT(owner,id) DO UPDATE SET config=excluded.config',
                       (owner,connection_id,json.dumps(config)))
        return self.get(owner,connection_id)

    def delete(self, owner, connection_id):
        self.get(owner,connection_id)
        with self.db() as db:db.execute('DELETE FROM device_connections WHERE owner=? AND id=?',(owner,connection_id))


_store=None
def get_connection_store():
    global _store
    if _store is None:
        from common.runtime_paths import DATA_DIR
        _store=ConnectionStore(DATA_DIR/'device_connections.sqlite3')
    return _store


def public_connection(connection):
    return {key:connection[key] for key in ('id','name','host','username','port','host_key_policy','python','acpx','node','enabled')} | {
        'kind':'ssh','has_identity_file':bool(connection['identity_file']),
        'has_known_hosts_file':bool(connection['known_hosts_file'])}
