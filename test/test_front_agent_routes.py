import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from flask import Flask, session

from routes.front_agent_routes import register_agent_routes


class _Response:
    status_code = 200

    def json(self):
        return {"status": "success", "agents": []}


class FrontAgentRoutesTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        register_agent_routes(self.app, port_agent=51200, internal_token="internal")
        self.client = self.app.test_client()

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = "alice"

    def test_requires_login(self):
        response = self.client.post("/proxy_agent_control", json={"action": "list"})
        self.assertEqual(response.status_code, 401)

    def test_uses_session_user_and_internal_token(self):
        self._login()
        with mock.patch("routes.front_agent_routes.requests.post", return_value=_Response()) as post:
            response = self.client.post(
                "/proxy_agent_control",
                json={"action": "list", "refresh_external": True, "user_id": "mallory"},
            )

        self.assertEqual(response.status_code, 200)
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["json"]["user_id"], "alice")
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": "internal"})
        self.assertTrue(kwargs["json"]["refresh_external"])

    def test_rejects_unknown_action_before_proxying(self):
        self._login()
        with mock.patch("routes.front_agent_routes.requests.post") as post:
            response = self.client.post("/proxy_agent_control", json={"action": "destroy-all"})
        self.assertEqual(response.status_code, 400)
        post.assert_not_called()

    def test_delete_forwards_only_agent_identity(self):
        self._login()
        with mock.patch("routes.front_agent_routes.requests.post", return_value=_Response()) as post:
            response = self.client.post(
                "/proxy_agent_control",
                json={"action": "delete", "kind": "internal", "identity": "worker"},
            )

        self.assertEqual(response.status_code, 200)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["action"], "delete")
        self.assertEqual(payload["kind"], "internal")
        self.assertEqual(payload["identity"], "worker")
        self.assertNotIn("team", payload)

    def test_configure_forwards_agent_settings(self):
        self._login()
        with mock.patch("routes.front_agent_routes.requests.post", return_value=_Response()) as post:
            response = self.client.post(
                "/proxy_agent_control",
                json={
                    "action": "configure",
                    "kind": "internal",
                    "identity": "worker",
                    "settings": {"name": "Worker", "tag": "coder", "tools": "none"},
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["settings"]["tools"], "none")
        self.assertNotIn("team", payload)


if __name__ == "__main__":
    unittest.main()
