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

import webot.runtime_store as runtime_store
from webot.api.routes import create_webot_router


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
                    create_webot_router(system=None,
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

if __name__ == "__main__":
    unittest.main()
