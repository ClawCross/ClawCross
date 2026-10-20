"""Durable, identity-scoped group sends; retries reuse the server's idempotency key."""
import json
import time


class OutboxStore:
    def init_outbox(self):
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS group_client_outbox(
                owner TEXT NOT NULL, alias TEXT NOT NULL, connection_id TEXT NOT NULL,
                sender TEXT NOT NULL, client_id TEXT NOT NULL, body TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY(owner,alias,sender,client_id))''')

    def enqueue(self, row, body):
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True)
        key = (row['owner'], row['alias'], body['agent_id'], body['client_msg_id'])
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT body,connection_id FROM group_client_outbox WHERE owner=? AND alias=? AND sender=? AND client_id=?', key).fetchone()
            if existing and (existing['body'] != encoded or existing['connection_id'] != row['connection_id']):
                raise ValueError('同一个消息编号不能用于不同内容或成员身份')
            db.execute('''INSERT INTO group_client_outbox(owner,alias,connection_id,sender,client_id,body)
                VALUES(?,?,?,?,?,?) ON CONFLICT(owner,alias,sender,client_id) DO UPDATE
                SET state='pending',next_attempt=0''', (*key[:2], row['connection_id'], *key[2:], encoded))
        return key

    def outbox(self, key):
        with self.db() as db:
            row = db.execute('SELECT * FROM group_client_outbox WHERE owner=? AND alias=? AND sender=? AND client_id=?', key).fetchone()
            return dict(row) if row else None

    def pending_sends(self):
        with self.db() as db:
            return [tuple(r) for r in db.execute('''SELECT owner,alias,sender,client_id FROM group_client_outbox
                WHERE state='pending' AND next_attempt<=? ORDER BY next_attempt LIMIT 8''', (time.time(),))]

    def sent(self, key):
        with self.db() as db:
            db.execute('DELETE FROM group_client_outbox WHERE owner=? AND alias=? AND sender=? AND client_id=?', key)

    def send_failed(self, key, error, retry):
        with self.db() as db:
            db.execute('''UPDATE group_client_outbox SET attempts=attempts+1,error=?,state=?,
                next_attempt=?+MIN(60,2*(attempts+1))
                WHERE owner=? AND alias=? AND sender=? AND client_id=?''',
                (str(error)[:500], 'pending' if retry else 'failed', time.time(), *key))
