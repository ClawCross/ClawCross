"""Group receipts are metadata, never conversation events or Agent wakeups."""
import re


PROTOCOL = {
    'protocol': 1,
    'capabilities': {
        'message_idempotency': True, 'history_search': True,
        'receipts': 'device-v1', 'receipt_notifications': 'metadata',
        'member_mute': True, 'group_wake_settings': True, 'external_access': True,
    },
}


class ReceiptStore:
    def init_receipts(self):
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS relay_receipts(
                connection_id TEXT NOT NULL REFERENCES relay_connections(id) ON DELETE CASCADE,
                device_id TEXT NOT NULL, received_cursor INTEGER NOT NULL DEFAULT 0,
                read_cursor INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(connection_id,device_id))''')

    @staticmethod
    def receipt_snapshot(db, gid):
        return [dict(r) for r in db.execute('''SELECT m.id AS principal,
            COALESCE(MAX(r.received_cursor),0) AS received_cursor,
            COALESCE(MAX(r.read_cursor),0) AS read_cursor
            FROM relay_members m JOIN relay_connections c ON c.id=m.connection_id
            LEFT JOIN relay_receipts r ON r.connection_id=c.id
            WHERE m.group_id=? AND m.agent_id='' AND c.revoked=0 GROUP BY m.id ORDER BY m.id''', (gid,))]

    def receipt(self, token, device_id, received_cursor=0, read_cursor=None):
        from groups.relay_store import RelayError
        if not isinstance(device_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', device_id):
            raise RelayError('设备编号无效')
        if type(received_cursor) is not int:
            raise RelayError('回执游标无效')
        for value in (received_cursor, read_cursor):
            if value is not None and (type(value) is not int or not 0 <= value <= 9007199254740991):
                raise RelayError('回执游标无效')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            conn = self.auth(db, token)
            for value, kind in ((received_cursor, None), (read_cursor, 'message')):
                if value and not db.execute('SELECT 1 FROM relay_events WHERE group_id=? AND id=? AND (? IS NULL OR kind=?)',
                                           (conn['group_id'], value, kind, kind)).fetchone():
                    raise RelayError('回执不属于此群的有效消息')
            old = db.execute('SELECT * FROM relay_receipts WHERE connection_id=? AND device_id=?', (conn['id'], device_id)).fetchone()
            if read_cursor and read_cursor > max(received_cursor, old['received_cursor'] if old else 0):
                raise RelayError('已读位置不能超过本设备已接收的位置')
            if not old and db.execute('SELECT COUNT(*) FROM relay_receipts WHERE connection_id=?', (conn['id'],)).fetchone()[0] >= 64:
                raise RelayError('此成员的回执设备数已达上限', 409)
            db.execute('''INSERT INTO relay_receipts VALUES(?,?,?,?)
                ON CONFLICT(connection_id,device_id) DO UPDATE SET
                received_cursor=MAX(received_cursor,excluded.received_cursor),read_cursor=MAX(read_cursor,excluded.read_cursor)
                WHERE excluded.received_cursor>received_cursor OR excluded.read_cursor>read_cursor''',
                (conn['id'], device_id, received_cursor, read_cursor or 0))
            return {'receipts': self.receipt_snapshot(db, conn['group_id'])}
