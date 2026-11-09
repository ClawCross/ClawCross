import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import webot.memory as memory
import webot.runtime_store as runtime_store
from webot.claude_code import parse_reset_time
from webot.routes import create_webot_router


class _FakeAgent:
    def list_active_task_keys(self, prefix=""):
        return []

    def get_all_thread_status(self, prefix):
        return {}

    def is_thread_busy(self, thread_id):
        return False


class WeBotGoalClaudeCodeTests(unittest.TestCase):
    def test_goal_store_roundtrip_and_heartbeat(self):
        with TemporaryDirectory() as tmpdir:
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            try:
                goal = runtime_store.upsert_session_goal(
                    "alice",
                    "default",
                    title="Ship ClawCross control plane",
                    priority="high",
                    budget_tokens=1000,
                    budget_usd=12.5,
                    metrics={"done": False},
                )
                self.assertTrue(goal.goal_id.startswith("goal-"))
                self.assertEqual(goal.status, "active")
                self.assertEqual(goal.priority, "high")

                updated = runtime_store.record_goal_heartbeat(
                    "alice",
                    goal.goal_id,
                    heartbeat_status="active",
                    report="Claude Code probe wired",
                    spent_tokens_delta=125,
                    spent_usd_delta=0.75,
                )
                self.assertIsNotNone(updated)
                self.assertEqual(updated.spent_tokens, 125)
                self.assertAlmostEqual(updated.spent_usd, 0.75)
                self.assertEqual(updated.last_report, "Claude Code probe wired")

                goals = runtime_store.list_session_goals("alice", "default")
                self.assertEqual(len(goals), 1)
                self.assertEqual(goals[0].goal_id, goal.goal_id)
            finally:
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path

    def test_claude_monitor_reset_parser(self):
        now = datetime(2026, 5, 17, 10, 0, tzinfo=ZoneInfo("UTC"))
        absolute = parse_reset_time("Limit resets at: 11:30 AM", timezone_name="UTC", now=now)
        self.assertIsNotNone(absolute)
        self.assertEqual(absolute.hour, 11)
        self.assertEqual(absolute.minute, 30)

        duration = parse_reset_time("Time to Reset: 1h 15m", timezone_name="UTC", now=now)
        self.assertIsNotNone(duration)
        self.assertEqual(duration.hour, 11)
        self.assertEqual(duration.minute, 15)

    def test_routes_expose_goals_and_claude_code_runtime(self):
        with TemporaryDirectory() as tmpdir:
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            original_user_files_dir = memory.USER_FILES_DIR
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            memory.USER_FILES_DIR = Path(tmpdir) / "user_files"
            try:
                app = FastAPI()
                app.include_router(
                    create_webot_router(
                        agent=_FakeAgent(),
                        verify_auth_or_token=lambda user_id, password, token: None,
                        extract_text=lambda content: content if isinstance(content, str) else str(content),
                    )
                )
                fake_status = {
                    "available": True,
                    "status": "available",
                    "claude_path": "/usr/local/bin/claude",
                    "claude_version": "2.1.100 (Claude Code)",
                    "acpx_path": "/usr/local/bin/acpx",
                    "acpx_claude_supported": True,
                    "errors": [],
                    "checked_at": "2026-05-17T00:00:00+00:00",
                }
                fake_probe = {
                    "ok": True,
                    "status": "success",
                    "session_name": "test",
                    "stdout_tail": "CLAUDE_ACP_OK",
                    "stderr_tail": "",
                }
                with patch("webot.service.detect_claude_code_cached", return_value=fake_status), patch(
                    "webot.service.probe_claude_acp", return_value=fake_probe
                ):
                    with TestClient(app) as client:
                        created = client.post(
                            "/webot/session-goals",
                            json={
                                "user_id": "alice",
                                "session_id": "default",
                                "title": "Verify local Claude Code",
                                "priority": "critical",
                                "budget_tokens": 2000,
                            },
                        )
                        self.assertEqual(created.status_code, 200)
                        goal = created.json()["goal"]
                        self.assertEqual(goal["priority"], "critical")

                        heartbeat = client.post(
                            "/webot/session-goals/heartbeat",
                            json={
                                "user_id": "alice",
                                "session_id": "default",
                                "goal_id": goal["goal_id"],
                                "report": "ACP probe passed",
                                "spent_tokens_delta": 50,
                            },
                        )
                        self.assertEqual(heartbeat.status_code, 200)
                        self.assertEqual(heartbeat.json()["goal"]["spent_tokens"], 50)

                        keepalive = client.post(
                            "/webot/claude-code/keepalive",
                            json={
                                "user_id": "alice",
                                "session_id": "default",
                                "enabled": True,
                                "prompt": "ping",
                                "timezone": "Asia/Singapore",
                            },
                        )
                        self.assertEqual(keepalive.status_code, 200)
                        self.assertTrue(keepalive.json()["keepalive"]["enabled"])

                        probe = client.post(
                            "/webot/claude-code/probe",
                            json={"user_id": "alice", "session_id": "default"},
                        )
                        self.assertEqual(probe.status_code, 200)
                        self.assertEqual(probe.json()["status"], "success")

                        runtime = client.get(
                            "/webot/session-runtime",
                            params={"user_id": "alice", "session_id": "default"},
                        )
                        self.assertEqual(runtime.status_code, 200)
                        payload = runtime.json()
                        self.assertEqual(payload["goals"]["active_count"], 1)
                        self.assertEqual(payload["goals"]["active_goal"]["last_report"], "ACP probe passed")
                        self.assertTrue(payload["claude_code"]["status"]["available"])
                        self.assertTrue(payload["claude_code"]["keepalive"]["enabled"])
            finally:
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path
                memory.USER_FILES_DIR = original_user_files_dir


if __name__ == "__main__":
    unittest.main()
