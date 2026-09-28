import sys
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import webot.runtime_store as runtime_store


class WeBotRuntimeStoreTests(unittest.TestCase):
    def test_existing_inbox_table_gains_read_state_without_losing_messages(self):
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "runtime.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE webot_session_inbox (message_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
                    "source_session TEXT NOT NULL, target_session TEXT NOT NULL, target_agent_id TEXT NOT NULL DEFAULT '', "
                    "title TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '', "
                    "delivery_status TEXT NOT NULL DEFAULT 'queued', wait_for_idle INTEGER NOT NULL DEFAULT 1, "
                    "metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, delivered_at TEXT NOT NULL DEFAULT '')"
                )
                conn.execute(
                    "INSERT INTO webot_session_inbox (message_id, user_id, source_session, target_session, "
                    "content, created_at) VALUES ('inbox-old', 'alice', 'main', 'worker', 'old body', '2026-01-01')"
                )
                conn.execute(
                    "INSERT INTO webot_session_inbox (message_id, user_id, source_session, target_session, "
                    "content, created_at, delivery_status, delivered_at) VALUES "
                    "('inbox-delivered', 'alice', 'main', 'worker', 'seen body', '2026-01-01', "
                    "'delivered', '2026-01-02')"
                )
            old = runtime_store.get_inbox_message("alice", "worker", "inbox-old", db_path=db_path)
            self.assertEqual(old.content, "old body")
            self.assertEqual(old.read_at, "")
            delivered = runtime_store.get_inbox_message("alice", "worker", "inbox-delivered", db_path=db_path)
            self.assertEqual(delivered.read_at, "2026-01-02")
            self.assertEqual(runtime_store.mark_inbox_read("alice", "worker", ["inbox-old"], db_path=db_path), 1)
            self.assertTrue(runtime_store.get_inbox_message("alice", "worker", "inbox-old", db_path=db_path).read_at)

    def test_mark_all_does_not_swallow_a_waiting_reply(self):
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "runtime.db"
            passive = runtime_store.create_inbox_message(
                "alice", source_session="main", target_session="worker", content="FYI", db_path=db_path,
            )
            waiting = runtime_store.create_inbox_message(
                "alice", source_session="main", target_session="worker", content="Reply please",
                metadata={"wait_reply": True}, db_path=db_path,
            )
            self.assertEqual(runtime_store.mark_inbox_read("alice", "worker", db_path=db_path), 1)
            self.assertTrue(runtime_store.get_inbox_message("alice", "worker", passive.message_id, db_path=db_path).read_at)
            self.assertEqual(runtime_store.get_inbox_message("alice", "worker", waiting.message_id, db_path=db_path).status, "queued")
            self.assertEqual(runtime_store.mark_inbox_read("alice", "worker", [waiting.message_id], db_path=db_path), 0)

    def test_run_leases_interrupts_and_events_follow_control_plane_semantics(self):
        with TemporaryDirectory() as tmpdir:
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            try:
                record = runtime_store.create_run_record(
                    run_id="run-1",
                    user_id="alice",
                    agent_id="worker-1",
                    session_id="default",
                    parent_session="default",
                    agent_type="reviewer",
                    title="Review auth flow",
                    input_text="Review the auth runtime",
                    status="queued",
                    timeout_seconds=120,
                    max_turns=12,
                    wait_mode=False,
                )
                runtime_store.upsert_run(record)

                claimed = runtime_store.claim_run_worker(
                    "run-1",
                    "alice",
                    worker_id="worker-a",
                    status="running",
                    lease_seconds=30,
                )
                blocked = runtime_store.claim_run_worker(
                    "run-1",
                    "alice",
                    worker_id="worker-b",
                    status="running",
                    lease_seconds=30,
                )
                heartbeat = runtime_store.heartbeat_run("run-1", "alice", worker_id="worker-a", lease_seconds=45)
                interrupted = runtime_store.request_run_interrupt("run-1", "alice")
                runtime_store.record_run_event(
                    "alice",
                    "run-1",
                    "default",
                    event_type="worker_started",
                    status="running",
                    message="Worker picked up the run",
                    details={"phase": "boot"},
                    attempt=1,
                    worker_id="worker-a",
                    agent_id="worker-1",
                )
                events = runtime_store.list_session_run_events("alice", "default")
                released = runtime_store.release_run_worker(
                    "run-1",
                    "alice",
                    worker_id="worker-a",
                    status="completed",
                    last_result="done",
                    clear_interrupt=True,
                )

                self.assertEqual(claimed.status, "running")
                self.assertEqual(claimed.worker_id, "worker-a")
                self.assertTrue(runtime_store.is_timestamp_active(claimed.lease_expires_at))
                self.assertIsNone(blocked)
                self.assertEqual(heartbeat.worker_id, "worker-a")
                self.assertTrue(runtime_store.is_timestamp_active(heartbeat.lease_expires_at))
                self.assertTrue(interrupted.interrupt_requested)
                self.assertEqual(events[0]["event_type"], "worker_started")
                self.assertEqual(events[0]["details"], {"phase": "boot"})
                self.assertEqual(released.status, "completed")
                self.assertEqual(released.worker_id, "")
                self.assertFalse(released.interrupt_requested)
                self.assertIsNone(runtime_store.get_latest_active_run_for_session("alice", "default"))

                child_run = runtime_store.create_run_record(
                    run_id="run-child-1",
                    user_id="alice",
                    agent_id="planner-1",
                    session_id="subagent__planner__planner-1",
                    parent_session="default",
                    agent_type="planner",
                    title="Plan migration",
                    input_text="Plan a migration",
                    status="queued",
                    timeout_seconds=120,
                    max_turns=12,
                    wait_mode=False,
                    run_kind="ultraplan",
                    mode="plan",
                )
                runtime_store.upsert_run(child_run)

                active = runtime_store.get_latest_active_run_for_session("alice", "default")
                self.assertIsNotNone(active)
                self.assertEqual(active.run_id, "run-child-1")
                parent_runs = runtime_store.list_runs_for_parent_session(
                    "alice",
                    "default",
                    run_kind="ultraplan",
                )
                self.assertEqual([item.run_id for item in parent_runs], ["run-child-1"])
            finally:
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path

    def test_inbox_delivery_counts_and_artifacts_roundtrip(self):
        with TemporaryDirectory() as tmpdir:
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            try:
                message = runtime_store.create_inbox_message(
                    "alice",
                    source_session="default",
                    target_session="subagent__reviewer__worker-1",
                    body="Need another review pass",
                    title="review follow-up",
                    source_agent_id="planner-1",
                    source_label="planner",
                )
                artifact = runtime_store.create_runtime_artifact(
                    user_id="alice",
                    session_id="subagent__reviewer__worker-1",
                    kind="session_inbox_delivery",
                    title="inbox_delivery",
                    summary="Delivered inbox bundle",
                    path=str(Path(tmpdir) / "delivery.md"),
                    metadata={"message_id": message.message_id},
                )
                delivered_total = runtime_store.mark_inbox_delivered("alice", [message.message_id])
                queued_count = runtime_store.count_inbox_messages(
                    "alice",
                    "subagent__reviewer__worker-1",
                    status="queued",
                )
                delivered = runtime_store.list_inbox_messages(
                    "alice",
                    "subagent__reviewer__worker-1",
                    status="delivered",
                )
                artifacts = runtime_store.list_runtime_artifacts(
                    "alice",
                    "subagent__reviewer__worker-1",
                    limit=10,
                )

                self.assertEqual(delivered_total, 1)
                self.assertEqual(queued_count, 0)
                self.assertEqual(delivered[0].source_label, "planner")
                self.assertTrue(delivered[0].delivered_at)
                self.assertEqual(artifacts[0].artifact_id, artifact.artifact_id)
                self.assertEqual(artifacts[0].metadata["message_id"], message.message_id)
            finally:
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path


if __name__ == "__main__":
    unittest.main()
