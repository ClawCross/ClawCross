"""The web frontend relays /v1/agents and /v1/teams to the Agent service as the caller."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "backend"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from flask import Flask  # noqa: E402

from frontend.proxies.agents import register_agent_routes  # noqa: E402


class _Response:
    status_code = 200
    content = b'{"ok": true}'
    headers = {"content-type": "application/json"}


class FrontAgentRoutesTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        register_agent_routes(self.app, port_agent=51200, internal_token="internal")
        self.client = self.app.test_client()

    def relay(self, method, path, **kwargs):
        with mock.patch("frontend.proxies.agents.requests.request", return_value=_Response()) as request:
            response = getattr(self.client, method)(path, **kwargs)
        self.assertEqual(response.status_code, 200)
        return request.call_args

    def test_a_logged_in_user_acts_as_themselves(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = "alice"
        call = self.relay("patch", "/v1/agents/alice/coder", json={"name": "Builder"},
                          headers={"Authorization": "Bearer mallory:pw"})
        self.assertEqual(call.args, ("PATCH", "http://127.0.0.1:51200/v1/agents/alice/coder"))
        self.assertEqual(call.kwargs["headers"], {"Authorization": "Bearer internal:alice"})
        self.assertEqual(call.kwargs["json"], {"name": "Builder"})

    def test_without_a_session_the_callers_own_credentials_are_forwarded(self):
        call = self.relay("get", "/v1/teams?x=1", headers={"Authorization": "Bearer bob:pw"})
        self.assertEqual(call.args, ("GET", "http://127.0.0.1:51200/v1/teams"))
        self.assertEqual(call.kwargs["headers"], {"Authorization": "Bearer bob:pw"})
        self.assertEqual(call.kwargs["params"].to_dict(), {"x": "1"})
        self.assertIsNone(call.kwargs["json"])

    def test_asking_an_agent_may_take_long(self):
        call = self.relay("post", "/v1/agents/coder/messages", json={"text": "hi"})
        self.assertEqual(call.kwargs["timeout"], 900)

    def test_sync_compaction_has_a_long_timeout_but_job_polling_is_short(self):
        call = self.relay('post', '/v1/agents/coder/control', json={'action': 'compact'})
        self.assertEqual(call.kwargs['timeout'], 900)
        call = self.relay('post', '/v1/agents/coder/control', json={'action': 'compact_status'})
        self.assertEqual(call.kwargs['timeout'], 60)


if __name__ == "__main__":
    unittest.main()
