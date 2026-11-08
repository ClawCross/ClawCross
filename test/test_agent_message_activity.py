"""Message ordering uses persisted conversation time, including live WAL writes."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'backend'))
from agents.message_activity import message_times
from agents.store import Agent
from external.history import history_db_name_for
from external.session import runtime_session
from webot.checkpoint_paths import checkpoint_db_name_for_thread


class MessageTimesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def times(self, *agents):
        return message_times(list(agents), checkpoint_dir=self.root, history_dir=self.root)

    def test_existing_webot_history_ignores_settings_tools_and_other_threads(self):
        agent = Agent('partner', 'alice', 'Partner', 'webot', updated_at=99999999)
        path = self.root / checkpoint_db_name_for_thread('alice#partner')
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE context_messages(thread_id, sequence, message_json, created_at, PRIMARY KEY(thread_id,sequence))')
            for thread, seq, kind, stamp in [('alice#partner', 0, 'human', 100), ('alice#partner', 1, 'ai', 200),
                                              ('alice#partner', 2, 'tool', 300), ('bob#partner', 0, 'human', 999)]:
                from datetime import datetime, timezone
                db.execute('INSERT INTO context_messages VALUES (?,?,?,?)',
                           (thread, seq, json.dumps({'type':kind}), datetime.fromtimestamp(stamp, timezone.utc).isoformat()))
        self.assertEqual(self.times(agent), {'partner':200})

    def test_external_messages_ignore_tools_and_invalidate_cache_for_live_wal(self):
        agent = Agent('partner', 'alice', 'Partner', 'acpx', config={'platform':'codex'})
        path = self.root / history_db_name_for('codex', runtime_session(agent))
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE messages(rowid INTEGER PRIMARY KEY, ts REAL, user_id TEXT, role TEXT, direction TEXT)')
        db.execute("INSERT INTO messages VALUES (1,100,'alice','user','send')")
        db.commit()
        self.assertEqual(self.times(agent), {'partner':100})
        db.executemany('INSERT INTO messages VALUES (?,?,?,?,?)', [(2,200,'alice','assistant','recv'),
            (3,300,'alice','tool','tool_result'), (4,400,'bob','assistant','recv')])
        db.commit()
        self.assertEqual(self.times(agent), {'partner':200})
        changed = Agent('partner', 'alice', 'Partner', 'acpx', config={'platform':'codex'}, runtime={'session_generation':'new'})
        self.assertEqual(self.times(changed), {'partner':0})

    def test_missing_histories_do_not_create_files_or_directories(self):
        root = self.root / 'missing'
        agents = [Agent('a','alice','A','webot'), Agent('b','alice','B','acpx',config={'platform':'codex'})]
        self.assertEqual(message_times(agents, checkpoint_dir=root, history_dir=root), {'a':0,'b':0})
        self.assertFalse(root.exists())


if __name__ == '__main__':
    unittest.main()
