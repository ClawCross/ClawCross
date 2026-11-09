"""End-to-end tests for /proxy_external_history/* endpoints."""

import asyncio
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import front  # noqa: E402
from utils.external_agent_history import reset_store_for_test  # noqa: E402


def _seed_history(tmp: str) -> None:
    """Populate the store with a few messages spanning two users."""
    store = reset_store_for_test(tmp)

    async def _go():
        rid = await store.record_send(
            platform="claude",
            session_key="sess-alice",
            connect_type="acp",
            prompt="hi from alice",
            options={"_history_user_id": "alice"},
        )
        await store.record_recv(
            platform="claude",
            session_key="sess-alice",
            connect_type="acp",
            request_id=rid,
            ok=True,
            content="reply for alice",
            raw_response={
                "tool_uses": [{"name": "ls"}],
                "tool_results": [{"name": "ls", "output": "a"}],
            },
            error=None,
            options={"_history_user_id": "alice"},
        )

        await store.record_send(
            platform="codex",
            session_key="sess-bob",
            connect_type="acp",
            prompt="hi from bob",
            options={"_history_user_id": "bob"},
        )

    asyncio.run(_go())


class ExternalHistoryRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = TemporaryDirectory()
        _seed_history(self.tmp_ctx.name)
        front.app.config["TESTING"] = True
        front.app.config["SECRET_KEY"] = "test-secret"

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def _login(self, client, user_id: str = "alice") -> None:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id

    def test_sessions_requires_login(self):
        client = front.app.test_client()
        resp = client.get("/proxy_external_history/sessions")
        self.assertEqual(resp.status_code, 401)

    def test_sessions_filters_by_current_user(self):
        client = front.app.test_client()
        self._login(client, user_id="alice")
        resp = client.get("/proxy_external_history/sessions")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        keys = sorted(s["session_key"] for s in data["sessions"])
        self.assertEqual(keys, ["sess-alice"])

    def test_sessions_platform_filter(self):
        client = front.app.test_client()
        self._login(client, user_id="bob")
        resp = client.get("/proxy_external_history/sessions?platform=codex")
        data = resp.get_json()
        self.assertEqual(len(data["sessions"]), 1)
        self.assertEqual(data["sessions"][0]["platform"], "codex")

    def test_messages_returns_send_recv_and_tools(self):
        client = front.app.test_client()
        self._login(client, user_id="alice")
        resp = client.get(
            "/proxy_external_history/messages?platform=claude&session_key=sess-alice"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        directions = [m["direction"] for m in data["messages"]]
        self.assertEqual(
            directions, ["send", "recv", "tool_call", "tool_result"]
        )
        self.assertEqual(data["session_meta"]["user_id"], "alice")

    def test_messages_forbidden_for_other_user(self):
        client = front.app.test_client()
        self._login(client, user_id="alice")
        resp = client.get(
            "/proxy_external_history/messages?platform=codex&session_key=sess-bob"
        )
        self.assertEqual(resp.status_code, 403)

    def test_purge_keeps_recent(self):
        client = front.app.test_client()
        self._login(client, user_id="alice")
        resp = client.post(
            "/proxy_external_history/purge",
            json={
                "platform": "claude",
                "session_key": "sess-alice",
                "keep_last": 1,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(data["deleted_count"], 1)
        # Verify only 1 row remains
        resp2 = client.get(
            "/proxy_external_history/messages?platform=claude&session_key=sess-alice"
        )
        self.assertEqual(len(resp2.get_json()["messages"]), 1)

    def test_openclaw_chat_writes_history_visible_to_endpoint(self):
        """End-to-end: posting to /proxy_openclaw_chat must produce a row that
        /proxy_external_history/messages returns under the same session_key
        the frontend constructs (agent:NAME:clawcrosschat)."""
        from unittest import mock

        agent_name = "demo_agent"
        derived_key = f"agent:{agent_name}:clawcrosschat"

        class _FakeResp:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}

        class _FakeAsyncClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, *a, **kw):
                return _FakeResp()

        client = front.app.test_client()
        self._login(client, user_id="alice")

        with mock.patch(
            "integrations.connectors._generic_http.httpx.AsyncClient", _FakeAsyncClient
        ), mock.patch.object(
            front, "_read_saved_openclaw_runtime_config",
            return_value={"api_url": "http://127.0.0.1:18789", "api_key": ""},
        ):
            chat_resp = client.post(
                "/proxy_openclaw_chat",
                json={
                    "model": f"agent:{agent_name}",
                    "messages": [{"role": "user", "content": "hi from FE"}],
                    "stream": False,
                },
            )
        self.assertEqual(chat_resp.status_code, 200)

        # Now read via the new endpoint with the FE-derived session_key
        from urllib.parse import quote
        read_resp = client.get(
            f"/proxy_external_history/messages?platform=openclaw&session_key={quote(derived_key)}"
        )
        self.assertEqual(read_resp.status_code, 200)
        data = read_resp.get_json()
        self.assertTrue(data["ok"])
        self.assertGreaterEqual(len(data["messages"]), 1)
        send_row = next(
            (m for m in data["messages"] if m["direction"] == "send"), None
        )
        self.assertIsNotNone(send_row)
        # The user's natural-language text should be readable directly
        # (not JSON-encoded as the raw OpenAI messages list).
        self.assertEqual(send_row["content"], "hi from FE")
        self.assertEqual(send_row["user_id"], "alice")
        self.assertEqual(data["session_meta"]["session_key"], derived_key)

    def test_delete_session(self):
        client = front.app.test_client()
        self._login(client, user_id="bob")
        resp = client.delete(
            "/proxy_external_history/session?platform=codex&session_key=sess-bob"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["deleted"])
        # Subsequent listing should not show it
        resp2 = client.get("/proxy_external_history/sessions")
        keys = [s["session_key"] for s in resp2.get_json()["sessions"]]
        self.assertNotIn("sess-bob", keys)


if __name__ == "__main__":
    unittest.main()
