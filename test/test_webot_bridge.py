import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import webot.runtime_store as runtime_store
from webot.bridge import WeBotBridgeHub, get_bridge_runtime_payload, issue_bridge_session


class _FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.messages: list[str] = []

    async def accept(self):
        self.accepted = True

    async def send_text(self, text: str):
        self.messages.append(text)


class WeBotBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_issue_connect_publish_disconnect(self):
        with TemporaryDirectory() as tmpdir:
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            try:
                issued = issue_bridge_session(
                    user_id="alice",
                    session_id="default",
                    role="viewer",
                    label="browser",
                )
                self.assertEqual(issued["status"], "detached")
                self.assertEqual(issued["connection_count"], 0)

                hub = WeBotBridgeHub()
                socket = _FakeWebSocket()
                record = runtime_store.get_bridge_session(issued["bridge_id"], "alice")
                self.assertIsNotNone(record)

                await hub.connect(record, socket)
                self.assertTrue(socket.accepted)

                payload = get_bridge_runtime_payload("alice", "default")
                self.assertTrue(payload["attached"])
                self.assertEqual(payload["connection_count"], 1)

                delivered = await hub.publish(
                    issued["bridge_id"],
                    {"type": "runtime_update", "session_id": "default", "reason": "test"},
                )
                self.assertEqual(delivered, 1)
                self.assertEqual(len(socket.messages), 1)
                decoded = json.loads(socket.messages[0])
                self.assertEqual(decoded["type"], "runtime_update")
                self.assertEqual(decoded["session_id"], "default")

                updated = runtime_store.get_bridge_session(issued["bridge_id"], "alice")
                await hub.disconnect(updated, socket)

                payload_after = get_bridge_runtime_payload("alice", "default")
                self.assertFalse(payload_after["attached"])
                self.assertEqual(payload_after["connection_count"], 0)
            finally:
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path


if __name__ == "__main__":
    unittest.main()
