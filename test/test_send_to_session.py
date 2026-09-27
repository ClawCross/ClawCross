"""send_to_session: one tool for messages between sessions.

Both modes go through /system_trigger, so the receiver's current turn is never
interrupted — a message queues behind it. wait only decides whether the sender
waits for the reply.
"""

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import httpx

webot = importlib.import_module("mcp_servers.webot")


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
        result = await webot.send_to_session("alice", "worker", "please check X", source_session="main")
        self.assertIn("已投递", result)
        url, body = self.calls[0]
        self.assertTrue(url.endswith("/system_trigger"))
        self.assertEqual((body["user_id"], body["session_id"], body["wait_reply"]), ("alice", "worker", False))
        # The receiver can tell who wrote: the message is never mistaken for its user.
        self.assertTrue(body["text"].startswith("[来自 alice#main 的消息]"))

    async def test_with_wait_it_returns_the_reply(self):
        self._client(reply="X is fine")
        result = await webot.send_to_session("alice", "worker", "is X fine?", wait=True, source_session="main")
        self.assertIn("X is fine", result)
        self.assertTrue(self.calls[0][1]["wait_reply"])
        self.assertIn("直接用文字回答", self.calls[0][1]["text"])

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
        self.assertIn("已投递", result)


if __name__ == "__main__":
    unittest.main()
