"""The one-shot move of agents, teams, group chat, tasks and workflows into the new structure."""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.store import ACPX, WEBOT, AgentStore  # noqa: E402
from comms.store import ConversationStore  # noqa: E402
from migrations.unify import convert_workflow_yaml, migrate_once  # noqa: E402
from teams.store import TeamStore  # noqa: E402

OLD_GROUP_SCHEMA = """
CREATE TABLE groups (group_id TEXT PRIMARY KEY, name TEXT, owner TEXT, created_at REAL,
                     primary_agent_global_id TEXT, kind TEXT DEFAULT 'group', team TEXT DEFAULT '');
CREATE TABLE group_members (group_id TEXT, user_id TEXT, short_name TEXT, global_id TEXT, is_agent INTEGER,
                            member_type TEXT, tag TEXT, joined_at REAL);
CREATE TABLE group_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT, sender TEXT, sender_display TEXT,
                             content TEXT, attachments TEXT DEFAULT '[]', timestamp REAL, mentions TEXT DEFAULT '[]');
CREATE TABLE group_mute_state (group_id TEXT, target_type TEXT, target_id TEXT, muted INTEGER, updated_at REAL);
CREATE TABLE http_agent_sessions (session_key TEXT PRIMARY KEY, global_name TEXT, prompt_text TEXT, transport TEXT,
                                  created_at REAL, updated_at REAL, last_used_at REAL);
"""


class TestMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        user_files = self.data / "user_files"
        team = user_files / "alice" / "teams" / "dev"
        (team / "oasis" / "yaml").mkdir(parents=True)
        (team / "internal_agents.json").write_text(json.dumps([
            {"name": "Planner", "tag": "plan", "session": "s_plan", "is_primary": True},
            {"name": "Writer", "tag": "writer"},
        ]), encoding="utf-8")
        (team / "external_agents.json").write_text(json.dumps([
            {"name": "Codex", "tag": "codex", "platform": "codex", "global_name": "cx"},
        ]), encoding="utf-8")
        (user_files / "alice" / "internal_agents.json").write_text(json.dumps([
            {"name": "Helper", "tag": "", "session": "s_help"},
        ]), encoding="utf-8")
        (team / ".internal_agents.json.lock").write_text("", encoding="utf-8")  # the old file lock
        (team / "oasis" / "yaml" / "flow.yaml").write_text(
            "version: 2\nplan:\n  - id: a\n    expert: plan#oasis#Planner\n  - id: b\n    expert: critical#temp#1\n",
            encoding="utf-8")

        db = sqlite3.connect(self.data / "group_chat.db")
        db.executescript(OLD_GROUP_SCHEMA)
        db.execute("INSERT INTO groups VALUES ('alice::Dev', 'dev#Dev', 'alice', 1, 's_plan', 'group', 'dev')")
        db.execute("INSERT INTO groups VALUES ('alice::dm', 'Helper#s_help', 'alice', 1, '', 'group', '')")
        for row in [("alice", "alice", "alice", 0, "owner", ""), ("alice", "Helper", "s_help", 1, "oasis", "")]:
            db.execute("INSERT INTO group_members VALUES ('alice::dm', ?, ?, ?, ?, ?, ?, 1)", row)
        for row in [("alice", "alice", "alice", 0, "owner", ""), ("alice", "Planner", "s_plan", 1, "oasis", "plan"),
                    ("alice", "Codex", "cx", 1, "ext", "codex"), ("alice", "Chat", "s_chat", 1, "oasis", "")]:
            db.execute("INSERT INTO group_members VALUES ('alice::Dev', ?, ?, ?, ?, ?, ?, 1)", row)
        db.executemany("INSERT INTO group_messages (group_id, sender, sender_display, content, timestamp, mentions)"
                       " VALUES ('alice::Dev', ?, ?, ?, ?, ?)", [
                           ("alice", "", "@Codex 看下", 2, json.dumps(["cx"])),
                           ("alice#s_plan", "plan#oasis#Planner#s_plan", "好的", 3, "[]"),
                           ("codex#ext#Codex#cx", "codex#ext#Codex#cx", "完成", 4, "[]"),
                       ])
        db.execute("INSERT INTO group_mute_state VALUES ('alice::Dev', 'member', 'cx', 1, 1)")
        db.execute("INSERT INTO http_agent_sessions VALUES ('agent:cx:clawcrosschat', 'cx', 'P', 'http', 1, 1, 1)")
        db.commit()
        db.close()

        (self.data / "timeset").mkdir()
        (self.data / "timeset" / "tasks.json").write_text(json.dumps({
            "t1": {"user_id": "alice", "cron": "0 9 * * *", "text": "morning", "session_id": "default",
                   "target_type": "internal"},
            "t2": {"user_id": "alice", "cron": "0 9 * * *", "text": "ext", "target_type": "external",
                   "target_name": "Codex", "team": "dev"},
        }), encoding="utf-8")

        self.agents = AgentStore(self.data / "clawcross.db")
        self.teams = TeamStore(self.agents, user_files)
        self.conversations = ConversationStore(self.agents)

    def migrate(self):
        migrate_once(data_dir=self.data, teams=self.teams, conversations=self.conversations)

    def test_everything_moves_once(self):
        self.migrate()
        self.migrate()  # a second start changes nothing

        agents = {a.name: a for a in self.agents.list("alice")}
        self.assertEqual(set(agents), {"Planner", "Writer", "Codex", "Helper", "Chat", "主助手"})
        self.assertEqual(agents["Codex"].driver, ACPX)
        self.assertEqual(agents["Planner"].config["team"], "dev")
        self.assertEqual(agents["Helper"].config["team"], "")
        self.assertEqual([(m.role, m.is_lead) for m in self.teams.members("alice", "dev")],
                         [("Planner", True), ("Writer", False), ("Codex", False)])
        team = self.teams.folder("alice", "dev")
        self.assertFalse((team / "internal_agents.json").exists())
        self.assertTrue((team / ".migrated" / "internal_agents.json").exists())
        self.assertFalse((team / ".internal_agents.json.lock").exists())

        group = self.conversations.get("alice::Dev")
        self.assertEqual((group.title, group.kind, group.meta, group.primary_agent),
                         ("Dev", "group", {"team": "dev"}, agents["Planner"].agent_id))
        private = self.conversations.get("alice::dm")
        self.assertEqual((private.title, private.kind), ("Helper", "direct"))
        members = {m.principal: m for m in self.conversations.members("alice::Dev")}
        self.assertEqual(set(members), {"u:alice", agents["Planner"].agent_id, agents["Codex"].agent_id,
                                        agents["Chat"].agent_id})
        self.assertTrue(members[agents["Codex"].agent_id].muted)
        messages = self.conversations.messages("alice::Dev")
        self.assertEqual([m.sender for m in messages],
                         ["u:alice", agents["Planner"].agent_id, agents["Codex"].agent_id])
        self.assertEqual(messages[0].mentions, [agents["Codex"].agent_id])

        tasks = json.loads((self.data / "timeset" / "tasks.json").read_text())
        self.assertEqual(tasks["t1"]["agent"], agents["主助手"].agent_id)
        self.assertEqual(tasks["t2"]["agent"], agents["Codex"].agent_id)
        self.assertNotIn("target_type", tasks["t2"])

        flow = (team / "oasis" / "yaml" / "flow.yaml").read_text()
        self.assertIn("agent: Planner", flow)
        self.assertIn("persona: critical", flow)

        conn = self.agents._connect()
        self.assertEqual(conn.execute("SELECT global_name FROM agent_runtime_sessions").fetchall()[0][0], "cx")
        conn.close()

    def test_workflow_lines_keep_their_layout(self):
        converted = convert_workflow_yaml("  - expert: 'x#temp#2'   # c\n    - expert: a#ext#Bot: v1\n")
        self.assertEqual(converted, "  - persona: x   # c\n    instance: 2\n    - agent: \"Bot: v1\"\n")


if __name__ == "__main__":
    unittest.main()
