import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Other test modules import `front`, which writes OPENCLAW_API_URL into the
# process env at import time. This test asserts the structured error path when
# no api_url is configured, so it must run with the env var cleared regardless
# of execution order.
_ENV_VARS_TO_CLEAR = ("OPENCLAW_API_URL", "OPENCLAW_GATEWAY_TOKEN")

from integrations.acpx_adapter import AcpxAdapter
from integrations.agent_sender import SendToAgentRequest, send_to_agent


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": "pong"}}]}


class _FakeAsyncClient:
    last_post: dict | None = None

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, *, json, headers):
        _FakeAsyncClient.last_post = {
            "url": url,
            "json": json,
            "headers": headers,
        }
        return _FakeResponse()


class TestAgentSenderStability(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_connect_type_returns_structured_error(self):
        result = await send_to_agent(SendToAgentRequest(
            prompt="ping",
            connect_type="missing",
            platform="unknown_platform_xyz",
        ))

        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)

    async def test_http_missing_api_url_returns_structured_error(self):
        scrubbed = {k: os.environ.pop(k) for k in _ENV_VARS_TO_CLEAR if k in os.environ}
        try:
            result = await send_to_agent(SendToAgentRequest(
                prompt="ping",
                connect_type="http",
                platform="openclaw",
                options={},
            ))
        finally:
            os.environ.update(scrubbed)

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "missing api_url")

    async def test_http_openclaw_injects_session_header_and_extracts_content(self):
        _FakeAsyncClient.last_post = None
        with mock.patch("integrations.connectors._generic_http.httpx.AsyncClient", _FakeAsyncClient):
            result = await send_to_agent(SendToAgentRequest(
                prompt="ping",
                connect_type="http",
                platform="openclaw",
                session="agent:demo",
                options={"api_url": "http://127.0.0.1:18789/v1/chat/completions"},
            ))

        self.assertTrue(result.ok)
        self.assertEqual(result.content, "pong")
        self.assertIsNotNone(_FakeAsyncClient.last_post)
        assert _FakeAsyncClient.last_post is not None
        self.assertEqual(
            _FakeAsyncClient.last_post["headers"].get("x-openclaw-session-key"),
            "agent:demo",
        )
        self.assertEqual(_FakeAsyncClient.last_post["json"]["messages"][0]["content"], "ping")

    async def test_acpx_close_session_cancels_before_close(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        adapter._acpx_bin = "/usr/bin/acpx"
        adapter._cwd = str(PROJECT_ROOT)
        calls = []

        async def fake_run_json(args, **kwargs):
            calls.append((args, kwargs))
            return ""

        adapter._run_json = fake_run_json

        await adapter.close_session(
            tool="claude",
            session_key="agent:demo:clawcrosschat",
            acpx_session="agent:demo:clawcrosschat",
            timeout_sec=12,
            ttl_sec=60,
            approve_all=False,
        )

        self.assertEqual(calls[0][0], ["claude", "cancel", "-s", "agent:demo:clawcrosschat"])
        self.assertEqual(calls[1][0], ["claude", "sessions", "close", "agent:demo:clawcrosschat"])
        self.assertTrue(all(call[1]["allow_nonzero"] for call in calls))

    async def test_acpx_close_session_still_closes_when_cancel_fails(self):
        adapter = AcpxAdapter.__new__(AcpxAdapter)
        adapter._acpx_bin = "/usr/bin/acpx"
        adapter._cwd = str(PROJECT_ROOT)
        calls = []

        async def fake_run_json(args, **kwargs):
            calls.append((args, kwargs))
            if args[:2] == ["claude", "cancel"]:
                from integrations.acpx_adapter import AcpxError
                raise AcpxError("cancel timed out")
            return ""

        adapter._run_json = fake_run_json

        await adapter.close_session(
            tool="claude",
            session_key="agent:demo:clawcrosschat",
            acpx_session="agent:demo:clawcrosschat",
            timeout_sec=12,
            ttl_sec=60,
            approve_all=False,
        )

        self.assertEqual(calls[0][0], ["claude", "cancel", "-s", "agent:demo:clawcrosschat"])
        self.assertEqual(calls[1][0], ["claude", "sessions", "close", "agent:demo:clawcrosschat"])


if __name__ == "__main__":
    unittest.main()
