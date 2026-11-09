import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import webot.memory as webot_memory
import webot.runtime_store as runtime_store


class WeBotMemoryTests(unittest.TestCase):
    def test_memory_entries_refresh_index_and_recall(self):
        with TemporaryDirectory() as tmpdir:
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            original_project_root = webot_memory.PROJECT_ROOT
            original_user_files_dir = webot_memory.USER_FILES_DIR
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            webot_memory.PROJECT_ROOT = Path(tmpdir)
            webot_memory.USER_FILES_DIR = Path(tmpdir) / "user_files"

            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            workspace_ref = types.SimpleNamespace(root=workspace, cwd=workspace, mode="shared", remote="")
            try:
                with patch.object(webot_memory, "resolve_session_workspace", return_value=workspace_ref):
                    webot_memory.append_memory_entry(
                        "alice",
                        "default",
                        name="OAuth Notes",
                        content="GitHub OAuth requires rate limiting and callback validation.",
                        mem_type="reference",
                        description="Auth implementation notes",
                    )
                    webot_memory.append_memory_entry(
                        "alice",
                        "default",
                        name="Testing Checklist",
                        content="Run browser smoke tests after auth flow changes.",
                        mem_type="feedback",
                        description="Regression reminders",
                    )

                    index_path = webot_memory.refresh_memory_index("alice", "default")
                    state = webot_memory.ensure_memory_state("alice", "default", kairos_enabled=True)
                    entries = webot_memory.list_memory_entries("alice", "default")
                    relevant = webot_memory.recall_relevant_memories(
                        "alice",
                        "default",
                        "Need OAuth callback validation and rate limiting",
                    )
                    persisted = runtime_store.get_memory_state("alice", "default")

                self.assertTrue(index_path.is_file())
                self.assertEqual(len(entries), 2)
                self.assertEqual(state["entry_count"], 2)
                self.assertTrue(state["kairos_enabled"])
                self.assertGreaterEqual(len(relevant), 1)
                self.assertEqual(relevant[0]["name"], "OAuth Notes")
                self.assertTrue(Path(persisted.index_path).is_file())
                self.assertTrue(persisted.kairos_enabled)
                self.assertEqual(persisted.dream_status, "idle")
            finally:
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path
                webot_memory.PROJECT_ROOT = original_project_root
                webot_memory.USER_FILES_DIR = original_user_files_dir

    def test_auto_dream_respects_gates_and_persists_summary(self):
        with TemporaryDirectory() as tmpdir:
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            original_project_root = webot_memory.PROJECT_ROOT
            original_user_files_dir = webot_memory.USER_FILES_DIR
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            webot_memory.PROJECT_ROOT = Path(tmpdir)
            webot_memory.USER_FILES_DIR = Path(tmpdir) / "user_files"

            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            workspace_ref = types.SimpleNamespace(root=workspace, cwd=workspace, mode="shared", remote="")
            try:
                with patch.object(webot_memory, "resolve_session_workspace", return_value=workspace_ref):
                    blocked = webot_memory.run_auto_dream("alice", "default", force=False, reason="too-early")
                    self.assertFalse(blocked["ran"])
                    self.assertEqual(blocked["reason"], "dream gates not satisfied")

                    for index in range(5):
                        webot_memory.append_daily_log(
                            "alice",
                            "default",
                            f"Signal {index}",
                            f"Observed auth regression #{index}",
                        )

                    self.assertTrue(
                        webot_memory.should_run_auto_dream("alice", "default", min_hours=0, min_entries=5)
                    )

                    result = webot_memory.run_auto_dream(
                        "alice",
                        "default",
                        force=False,
                        query="auth regression",
                        plan={"title": "Stabilize auth", "status": "active"},
                        todos=[{"step": "Add regression test", "status": "pending"}],
                        verifications=[{"title": "Smoke", "status": "passed"}],
                        reason="manual dream",
                    )

                    state = webot_memory.get_memory_state("alice", "default", query="auth")
                    persisted = runtime_store.get_memory_state("alice", "default")
                    summary_path = Path(result["summary_path"])

                self.assertTrue(result["ran"])
                self.assertTrue(summary_path.is_file())
                self.assertEqual(state["log_entries_since_dream"], 1)
                self.assertTrue(state["last_dream_at"])
                self.assertEqual(persisted.metadata.get("last_dream_summary"), str(summary_path))
                self.assertEqual(persisted.dream_status, "idle")
                self.assertIn("auto_dream_summary", summary_path.name)
            finally:
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path
                webot_memory.PROJECT_ROOT = original_project_root
                webot_memory.USER_FILES_DIR = original_user_files_dir


if __name__ == "__main__":
    unittest.main()
