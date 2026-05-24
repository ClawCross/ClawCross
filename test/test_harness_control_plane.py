import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.harness_routes import create_harness_router  # noqa: E402
from harness.store import apply_harness_event, get_harness_state  # noqa: E402


class HarnessStoreTests(unittest.TestCase):
    def test_task_agent_and_verified_run_roundtrip(self):
        with TemporaryDirectory() as tmpdir:
            original = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                apply_harness_event(
                    "alice",
                    {
                        "action": "task_upsert",
                        "project_id": "project-alpha",
                        "task_id": "task_umi_eval",
                        "title": "Run Project Alpha verifier",
                        "status": "active",
                    },
                )
                apply_harness_event(
                    "alice",
                    {
                        "action": "heartbeat",
                        "agent_id": "claude-umi-01",
                        "agent_type": "claude-code-worker",
                        "project_id": "project-alpha",
                        "task_id": "task_umi_eval",
                        "status": "running",
                        "message": "running verifier",
                    },
                )
                apply_harness_event(
                    "alice",
                    {
                        "action": "run",
                        "agent_id": "claude-umi-01",
                        "project_id": "project-alpha",
                        "task_id": "task_umi_eval",
                        "run_id": "run_20260518_umi_eval",
                        "status": "verified",
                        "git_sha": "abc123",
                        "command": "python verify.py",
                        "exit_code": 0,
                        "verifier": {"status": "passed", "command": "python verify.py", "exit_code": 0},
                    },
                )

                state = get_harness_state("alice")
                self.assertEqual(state["counts"]["tasks"], 1)
                self.assertEqual(state["counts"]["agents"], 1)
                self.assertEqual(state["counts"]["runs"], 1)
                self.assertEqual(state["agents"][0]["last_run_id"], "run_20260518_umi_eval")
                self.assertEqual(state["runs"][0]["verifier"]["status"], "passed")
            finally:
                if original is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = original

    def test_verified_run_requires_machine_verifier(self):
        with TemporaryDirectory() as tmpdir:
            original = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                with self.assertRaises(ValueError):
                    apply_harness_event(
                        "alice",
                        {
                            "action": "run",
                            "agent_id": "claude-umi-01",
                            "project_id": "project-alpha",
                            "task_id": "task_umi_eval",
                            "run_id": "run_20260518_bad",
                            "status": "verified",
                            "git_sha": "abc123",
                            "command": "python verify.py",
                            "exit_code": 1,
                            "verifier": {"status": "failed"},
                        },
                    )
            finally:
                if original is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = original

    def test_agent_delete_removes_harness_worker(self):
        with TemporaryDirectory() as tmpdir:
            original = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                apply_harness_event(
                    "alice",
                    {
                        "action": "heartbeat",
                        "agent_id": "claude-umi-01",
                        "project_id": "project-alpha",
                        "status": "idle",
                    },
                )
                self.assertEqual(get_harness_state("alice")["counts"]["agents"], 1)

                result = apply_harness_event(
                    "alice",
                    {
                        "action": "agent_delete",
                        "agent_id": "claude-umi-01",
                        "project_id": "project-alpha",
                    },
                )

                self.assertTrue(result["record"]["deleted"])
                self.assertEqual(get_harness_state("alice")["counts"]["agents"], 0)
            finally:
                if original is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = original


class HarnessRouteTests(unittest.TestCase):
    def test_routes_read_and_write_harness_state(self):
        with TemporaryDirectory() as tmpdir:
            original = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                app = FastAPI()
                app.include_router(
                    create_harness_router(
                        verify_auth_or_token=lambda user_id, password, token: None,
                    )
                )
                with TestClient(app) as client:
                    posted = client.post(
                        "/harness/event",
                        json={
                            "user_id": "alice",
                            "action": "needs_user",
                            "agent_id": "remote-claude-01",
                            "project_id": "project-alpha",
                            "task_id": "task_remote_help",
                            "message": "permission prompt",
                        },
                    )
                    self.assertEqual(posted.status_code, 200)
                    self.assertTrue(posted.json()["ok"])

                    state = client.get("/harness/state", params={"user_id": "alice"})
                    self.assertEqual(state.status_code, 200)
                    data = state.json()
                    self.assertEqual(data["counts"]["needs_user"], 1)
                    self.assertEqual(data["agents"][0]["effective_status"], "needs_user")
            finally:
                if original is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = original

    def test_route_heartbeat_omitted_task_id_preserves_existing_binding(self):
        with TemporaryDirectory() as tmpdir:
            original = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                app = FastAPI()
                app.include_router(
                    create_harness_router(
                        verify_auth_or_token=lambda user_id, password, token: None,
                    )
                )
                with TestClient(app) as client:
                    client.post(
                        "/harness/event",
                        json={
                            "user_id": "alice",
                            "action": "heartbeat",
                            "agent_id": "remote-claude-01",
                            "project_id": "project-alpha",
                            "task_id": "task_vbench",
                            "current_task_id": "task_vbench",
                            "status": "running",
                            "session_ref": "session_vbench",
                        },
                    )
                    client.post(
                        "/harness/event",
                        json={
                            "user_id": "alice",
                            "action": "heartbeat",
                            "agent_id": "remote-claude-01",
                            "project_id": "project-alpha",
                            "status": "running",
                            "message": "plain heartbeat",
                        },
                    )
                    state = client.get("/harness/state", params={"user_id": "alice"}).json()
                    self.assertEqual(state["agents"][0]["current_task_id"], "task_vbench")
                    self.assertEqual(state["agents"][0]["session_ref"], "session_vbench")
            finally:
                if original is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = original


if __name__ == "__main__":
    unittest.main()
