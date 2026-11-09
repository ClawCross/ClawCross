import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

fastapi_module = sys.modules.get("fastapi")
if fastapi_module is not None and not hasattr(fastapi_module, "FastAPI"):
    sys.modules.pop("fastapi", None)
    sys.modules.pop("fastapi.testclient", None)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import webot.memory as memory
import webot.runtime_store as runtime_store
from webot.bridge import issue_bridge_session
from webot.routes import create_webot_router


class _FakeAgent:
    def list_active_task_keys(self, prefix=""):
        return []

    def get_all_thread_status(self, prefix):
        return {}

    def is_thread_busy(self, thread_id):
        return False


class WeBotRoutesTests(unittest.TestCase):
    def test_workflow_preset_routes_return_presets_and_apply_to_runtime(self):
        with TemporaryDirectory() as tmpdir:
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            try:
                app = FastAPI()
                app.include_router(
                    create_webot_router(
                        agent=_FakeAgent(),
                        verify_auth_or_token=lambda user_id, password, token: None,
                        extract_text=lambda content: content if isinstance(content, str) else str(content),
                    )
                )

                with TestClient(app) as client:
                    listed = client.get("/webot/workflow-presets", params={"user_id": "alice"})
                    self.assertEqual(listed.status_code, 200)
                    payload = listed.json()
                    self.assertEqual(payload["status"], "success")
                    self.assertTrue(any(item["preset_id"] == "deep_interview" for item in payload["presets"]))

                    applied = client.post(
                        "/webot/workflow-presets/apply",
                        json={"user_id": "alice", "session_id": "default", "preset_id": "execution_swarm"},
                    )
                    self.assertEqual(applied.status_code, 200)
                    data = applied.json()
                    self.assertEqual(data["preset"]["preset_id"], "execution_swarm")
                    self.assertEqual(data["mode"]["mode"], "execute")

                    runtime = client.get(
                        "/webot/session-runtime",
                        params={"user_id": "alice", "session_id": "default"},
                    )
                    self.assertEqual(runtime.status_code, 200)
                    runtime_payload = runtime.json()
                    self.assertEqual(runtime_payload["active_workflow"]["preset_id"], "execution_swarm")
                    self.assertTrue(any(item["artifact_kind"] == "workflow_preset" for item in runtime_payload["artifacts"]))
            finally:
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path

    def test_bridge_websocket_connects_refreshes_and_cleans_up(self):
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
                issued = issue_bridge_session(
                    user_id="alice",
                    session_id="default",
                    role="viewer",
                    label="browser",
                )

                with TestClient(app) as client:
                    with client.websocket_connect(issued["websocket_path"]) as websocket:
                        connected = websocket.receive_json()
                        self.assertEqual(connected["type"], "connected")
                        self.assertEqual(connected["bridge_id"], issued["bridge_id"])
                        self.assertEqual(connected["session_id"], "default")

                        snapshot = websocket.receive_json()
                        self.assertEqual(snapshot["type"], "runtime_snapshot")
                        self.assertEqual(snapshot["session_id"], "default")
                        self.assertEqual(snapshot["runtime"]["session_id"], "default")
                        self.assertEqual(snapshot["runtime"]["session_role"], "main")
                        self.assertTrue(snapshot["runtime"]["bridge"]["attached"])
                        self.assertEqual(snapshot["runtime"]["bridge"]["connection_count"], 1)

                        websocket.send_json({"type": "ping"})
                        pong = websocket.receive_json()
                        self.assertEqual(pong, {"type": "pong", "bridge_id": issued["bridge_id"]})

                        websocket.send_json({"type": "refresh"})
                        refreshed = websocket.receive_json()
                        self.assertEqual(refreshed["type"], "runtime_snapshot")
                        self.assertEqual(refreshed["changed_session_id"], "default")
                        self.assertEqual(refreshed["runtime"]["bridge"]["primary"]["bridge_id"], issued["bridge_id"])

                    detached = runtime_store.get_bridge_session(issued["bridge_id"], "alice")
                    self.assertIsNotNone(detached)
                    self.assertEqual(detached.status, "detached")
                    self.assertEqual(detached.connection_count, 0)
            finally:
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path
                memory.USER_FILES_DIR = original_user_files_dir

    def test_bridge_websocket_missing_record_closes_with_4404(self):
        app = FastAPI()
        app.include_router(
            create_webot_router(
                agent=_FakeAgent(),
                verify_auth_or_token=lambda user_id, password, token: None,
                extract_text=lambda content: content if isinstance(content, str) else str(content),
            )
        )

        with TestClient(app) as client:
            with self.assertRaises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/webot/ws/alice/bridge-missing"):
                    pass
            self.assertEqual(exc_info.exception.code, 4404)


if __name__ == "__main__":
    unittest.main()
