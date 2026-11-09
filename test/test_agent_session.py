import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from integrations.agent_session import prepare_agent_session
from integrations.agent_sender import send_to_agent
from integrations.base import SendToAgentRequest


class _Response:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


class _HttpClient:
    posted = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, *, json, headers):
        type(self).posted = {"url": url, "json": json, "headers": headers}
        return _Response()


class AgentSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_to_agent_uses_shared_identity_policy(self):
        _HttpClient.posted = None
        request = SendToAgentRequest(
            prompt="hello",
            connect_type="http",
            platform="http",
            session="agent:remote:clawcrosschat",
            options={
                "api_url": "https://example.invalid/v1/chat/completions",
                "group_db_path": "/tmp/group.db",
                "identity_global_name": "remote",
                "identity_prompt": "Stable identity",
                "_history_disabled": True,
            },
        )
        with (
            mock.patch("api.group_repository.get_http_agent_session", new=mock.AsyncMock(return_value=None)),
            mock.patch("api.group_repository.upsert_http_agent_session", new=mock.AsyncMock(return_value=True)),
            mock.patch("integrations.connectors._generic_http.httpx.AsyncClient", _HttpClient),
        ):
            result = await send_to_agent(request)

        self.assertTrue(result.ok)
        self.assertEqual(_HttpClient.posted["json"]["messages"][0], {
            "role": "system",
            "content": "Stable identity",
        })
        self.assertFalse(result.meta["agent_session"]["initialized"])

    async def test_new_http_session_injects_identity_through_shared_policy(self):
        request = SendToAgentRequest(
            prompt=[{"role": "user", "content": "hello"}],
            connect_type="http",
            platform="openclaw",
            session="agent:reviewer:clawcrosschat",
            options={
                "body": {"messages": [{"role": "user", "content": "hello"}]},
                "group_db_path": "/tmp/group.db",
                "identity_global_name": "reviewer",
                "identity_prompt": "You are the reviewer.",
                "identity_injection_mode": "prepend_user",
            },
        )
        with (
            mock.patch("api.group_repository.get_http_agent_session", new=mock.AsyncMock(return_value=None)),
            mock.patch("api.group_repository.upsert_http_agent_session", new=mock.AsyncMock(return_value=True)) as upsert,
        ):
            prepared, state = await prepare_agent_session(request)

        self.assertFalse(state.initialized)
        self.assertTrue(state.should_inject_identity)
        self.assertEqual(
            prepared.options["body"]["messages"][0]["content"],
            "You are the reviewer.\n\nhello",
        )
        upsert.assert_awaited_once()

    async def test_existing_http_session_does_not_reinject_same_identity(self):
        request = SendToAgentRequest(
            prompt="hello",
            connect_type="http",
            platform="custom-http",
            session="agent:remote:clawcrosschat",
            options={
                "group_db_path": "/tmp/group.db",
                "identity_global_name": "remote",
                "identity_prompt": "Stable identity",
            },
        )
        existing = {"prompt_text": "Stable identity"}
        with (
            mock.patch("api.group_repository.get_http_agent_session", new=mock.AsyncMock(return_value=existing)),
            mock.patch("api.group_repository.upsert_http_agent_session", new=mock.AsyncMock()) as upsert,
        ):
            prepared, state = await prepare_agent_session(request)

        self.assertTrue(state.initialized)
        self.assertFalse(state.should_inject_identity)
        self.assertNotIn("system_prompt", prepared.options)
        upsert.assert_not_awaited()

    async def test_acp_delegates_atomic_first_session_decision_to_acpx(self):
        prepared, state = await prepare_agent_session(SendToAgentRequest(
            prompt="hello",
            connect_type="acp",
            platform="codex",
            session="agent:coder:clawcrosschat",
            options={"identity_prompt": "You are the coder."},
        ))

        self.assertIsNone(state.initialized)
        self.assertIsNone(state.should_inject_identity)
        self.assertEqual(state.source, "acpx_ensure_session")
        self.assertEqual(prepared.options["system_prompt"], "You are the coder.")


if __name__ == "__main__":
    unittest.main()
