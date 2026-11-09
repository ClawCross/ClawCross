import tempfile
import unittest
from pathlib import Path

from src.webot.subagents import (
    create_subagent_record,
    delete_subagent_by_session,
    delete_subagents_for_user,
    get_subagent,
    get_subagent_by_name,
    get_subagent_by_session,
    list_subagents_for_user,
    update_subagent_metadata,
    update_subagent_status,
    upsert_subagent,
)


class WeBotSubagentsStoreTests(unittest.TestCase):
    def test_create_store_and_update_subagent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "subagents.db"
            record = create_subagent_record(
                agent_id="agent123",
                user_id="alice",
                session_id="subagent__research__agent123",
                agent_type="research",
                name="researcher-1",
                description="Investigate code paths",
                parent_session="default",
                status="queued",
            )
            upsert_subagent(record, db_path=db_path)

            loaded = get_subagent("agent123", "alice", db_path=db_path)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.agent_type, "research")
            self.assertEqual(loaded.name, "researcher-1")

            renamed = get_subagent_by_name("researcher-1", "alice", db_path=db_path)
            self.assertIsNotNone(renamed)
            self.assertEqual(renamed.agent_id, "agent123")

            by_session = get_subagent_by_session(
                "subagent__research__agent123",
                "alice",
                db_path=db_path,
            )
            self.assertIsNotNone(by_session)
            self.assertEqual(by_session.agent_id, "agent123")

            update_subagent_status(
                "agent123",
                "alice",
                status="completed",
                last_result="done",
                db_path=db_path,
            )
            updated = get_subagent("agent123", "alice", db_path=db_path)
            self.assertEqual(updated.status, "completed")
            self.assertEqual(updated.last_result, "done")

            update_subagent_metadata(
                "agent123",
                "alice",
                description="Updated brief",
                parent_session="parent-2",
                db_path=db_path,
            )
            updated_meta = get_subagent("agent123", "alice", db_path=db_path)
            self.assertEqual(updated_meta.description, "Updated brief")
            self.assertEqual(updated_meta.parent_session, "parent-2")

            all_records = list_subagents_for_user("alice", db_path=db_path)
            self.assertEqual(len(all_records), 1)

            deleted = delete_subagent_by_session("alice", "subagent__research__agent123", db_path=db_path)
            self.assertEqual(deleted, 1)
            self.assertIsNone(get_subagent("agent123", "alice", db_path=db_path))

            record_2 = create_subagent_record(
                agent_id="agent456",
                user_id="alice",
                session_id="subagent__coder__agent456",
                agent_type="coder",
                name="coder-1",
                description="Implement feature",
                parent_session="default",
                status="idle",
            )
            upsert_subagent(record_2, db_path=db_path)
            self.assertEqual(delete_subagents_for_user("alice", db_path=db_path), 1)
            self.assertEqual(list_subagents_for_user("alice", db_path=db_path), [])


if __name__ == "__main__":
    unittest.main()
