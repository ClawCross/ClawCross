"""send_to_session stores messages in the inbox and wakes the target session."""

import importlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import httpx

webot = importlib.import_module("webot.tools.webot")
from webot import runtime_store


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


class _Client:
    """Records posts; answers like /system_trigger does."""

    def __init__(self, calls, *, reply="pong", timeout_error=False):
        self.calls = calls
        self.reply = reply
        self.timeout_error = timeout_error

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append((url, json))
        if self.timeout_error:
            raise httpx.ReadTimeout("slow")
        if json.get("wait_reply"):
            return _Response({"status": "completed", "reply": self.reply})
        return _Response({"status": "received"})


class SendToSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls = []
        for target, value in [
            ("_ensure_internal_token", lambda: "token"),
            ("_source_label", lambda username, session: ("", session)),
            ("_resolve_target_sessions", lambda username, ref, source: (
                [{"target_session": "a"}, {"target_session": "b"}] if ref == "*" else [{"target_session": ref}]
            )),
        ]:
            patcher = patch.object(webot, target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _client(self, **kwargs):
        patcher = patch.object(webot.httpx, "AsyncClient", _Client(self.calls, **kwargs))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_without_wait_it_queues_through_system_trigger_and_returns(self):
        self._client()
        result = await webot.send_to_session("alice", "worker", "please check X", source_session="main", summary="Review X")
        self.assertIn("已入", result)
        url, body = self.calls[0]
        self.assertTrue(url.endswith("/system_trigger"))
        self.assertEqual((body["user_id"], body["session_id"], body["wait_reply"]), ("alice", "worker", False))
        self.assertEqual(body["inbox_source_session"], "main")
        self.assertEqual(body["inbox_source_user"], "alice")
        self.assertEqual(body["inbox_summary"], "Review X")
        # The receiver can tell who wrote: the message is never mistaken for its user.
        self.assertTrue(body["text"].startswith("[来自 alice#main 的消息]"))

    async def test_with_wait_it_returns_the_reply(self):
        self._client(reply="X is fine")
        result = await webot.send_to_session("alice", "worker", "is X fine?", wait=True, source_session="main")
        self.assertIn("X is fine", result)
        self.assertTrue(self.calls[0][1]["wait_reply"])
        self.assertEqual(self.calls[0][1]["inbox_source_session"], "main")

    async def test_waiting_on_its_own_session_is_refused(self):
        self._client()
        result = await webot.send_to_session("alice", "main", "hi", wait=True, source_session="main")
        self.assertIn("不能等待自己", result)
        self.assertEqual(self.calls, [])

    async def test_broadcast_cannot_wait(self):
        self._client()
        self.assertIn("只能发给一个会话", await webot.send_to_session("alice", "*", "hi", wait=True, source_session="main"))
        await webot.send_to_session("alice", "*", "hi", source_session="main")
        self.assertEqual([body["session_id"] for _url, body in self.calls], ["a", "b"])

    async def test_another_user_needs_an_explicit_session(self):
        self._client()
        self.assertIn("具体的会话 id", await webot.send_to_session("alice", "*", "hi", target_user="bob"))
        await webot.send_to_session("alice", "s9", "hi", target_user="bob", source_session="main")
        self.assertEqual((self.calls[0][1]["user_id"], self.calls[0][1]["session_id"]), ("bob", "s9"))

    async def test_a_reply_that_takes_too_long_reports_the_message_still_delivered(self):
        self._client(timeout_error=True)
        result = await webot.send_to_session("alice", "worker", "slow question", wait=True, source_session="main", timeout=1)
        self.assertIn("超时", result)
        self.assertIn("可能已经入箱", result)

    async def test_read_and_mark_tools_are_scoped_to_the_current_session(self):
        with TemporaryDirectory() as tmpdir, patch.object(runtime_store, "DEFAULT_DB_PATH", Path(tmpdir) / "runtime.db"):
            own = runtime_store.create_inbox_message("alice", source_session="main", target_session="worker", content="full private text", title="private summary")
            other_session = runtime_store.create_inbox_message("alice", source_session="main", target_session="other", content="other session")
            other_user = runtime_store.create_inbox_message("bob", source_session="main", target_session="worker", content="other user")
            listed = await webot.read_session_inbox("alice", source_session="worker")
            self.assertIn(own.message_id, listed)
            self.assertIn("full private text", listed)
            self.assertNotIn(other_session.message_id, listed)
            self.assertNotIn(other_user.message_id, listed)
            self.assertEqual(runtime_store.count_inbox_messages("alice", "worker", status="unread"), 1)
            missing = await webot.read_session_inbox("alice", [other_session.message_id], source_session="worker")
            self.assertIn("未找到", missing)
            marked = await webot.mark_session_inbox_read("alice", [own.message_id, other_session.message_id], source_session="worker")
            self.assertIn("已标记 1 条", marked)
            self.assertEqual(runtime_store.count_inbox_messages("alice", "worker", status="unread"), 0)
            self.assertEqual(runtime_store.count_inbox_messages("alice", "other", status="unread"), 1)
            self.assertTrue(runtime_store.get_inbox_message("alice", "worker", own.message_id).read_at)
            self.assertFalse(runtime_store.get_inbox_message("alice", "other", other_session.message_id).read_at)

    async def test_read_all_and_mark_all_have_separate_effects(self):
        with TemporaryDirectory() as tmpdir, patch.object(runtime_store, "DEFAULT_DB_PATH", Path(tmpdir) / "runtime.db"):
            for body in ("first", "second"):
                runtime_store.create_inbox_message("alice", source_session="main", target_session="worker", content=body)
            result = await webot.read_session_inbox("alice", source_session="worker")
            self.assertIn("first", result)
            self.assertIn("second", result)
            self.assertEqual(runtime_store.count_inbox_messages("alice", "worker", status="unread"), 2)
            marked = await webot.mark_session_inbox_read("alice", source_session="worker")
            self.assertIn("已标记 2 条", marked)
            self.assertEqual(runtime_store.count_inbox_messages("alice", "worker", status="unread"), 0)


if __name__ == "__main__":
    unittest.main()
