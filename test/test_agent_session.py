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
    """The caller (the agent layer) says whether a runtime still needs its identity."""

    async def test_send_to_agent_places_the_identity_as_a_system_message(self):
        _HttpClient.posted = None
        request = SendToAgentRequest(
            prompt="hello",
            connect_type="http",
            platform="http",
            session="agent:remote:clawcrosschat",
            options={
                "api_url": "https://example.invalid/v1/chat/completions",
                "identity_prompt": "Stable identity",
                "inject_identity": True,
                "_history_disabled": True,
            },
        )
        with mock.patch("integrations.connectors._generic_http.httpx.AsyncClient", _HttpClient):
            result = await send_to_agent(request)

        self.assertTrue(result.ok)
        self.assertEqual(_HttpClient.posted["json"]["messages"][0], {"role": "system", "content": "Stable identity"})
        self.assertTrue(result.meta["agent_session"]["should_inject_identity"])

    async def test_prepend_user_mode_puts_the_identity_before_the_first_user_message(self):
        prepared, state = await prepare_agent_session(SendToAgentRequest(
            prompt=[{"role": "user", "content": "hello"}],
            connect_type="http",
            platform="openclaw",
            session="agent:reviewer:clawcrosschat",
            options={
                "body": {"messages": [{"role": "user", "content": "hello"}]},
                "identity_prompt": "You are the reviewer.",
                "inject_identity": True,
                "identity_injection_mode": "prepend_user",
            },
        ))
        self.assertTrue(state.should_inject_identity)
        self.assertEqual(prepared.options["body"]["messages"][0]["content"], "You are the reviewer.\n\nhello")
        self.assertNotIn("inject_identity", prepared.options)

    async def test_a_runtime_that_already_knows_its_identity_is_not_told_again(self):
        prepared, state = await prepare_agent_session(SendToAgentRequest(
            prompt="hello",
            connect_type="http",
            platform="custom-http",
            session="agent:remote:clawcrosschat",
            options={"identity_prompt": "Stable identity", "inject_identity": False},
        ))
        self.assertFalse(state.should_inject_identity)
        self.assertNotIn("system_prompt", prepared.options)
        self.assertEqual(prepared.prompt, "hello")

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
