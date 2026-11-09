import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from integrations import remote_claude_agents as rca  # noqa: E402
from routes.front_group_routes import _merge_review_harness_sessions  # noqa: E402


class RemoteClaudeParserTests(unittest.TestCase):
    def test_parse_claude_transcript_text_and_result(self):
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-05-18T10:00:00Z",
                    "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-05-18T10:00:03Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "hello"},
                            {"type": "tool_use", "name": "Bash"},
                        ],
                    },
                }
            ),
            json.dumps({"type": "result", "subtype": "success", "result": "done"}),
        ]

        messages = rca.parse_claude_transcript_lines(lines, limit=10)

        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "result"])
        self.assertEqual(messages[0]["content"], "hi")
        self.assertIn("hello", messages[1]["content"])
        self.assertIn("[tool_use:Bash]", messages[1]["content"])
        self.assertEqual(messages[2]["content"], "done")

    def test_list_sessions_normalizes_display_id_and_preview(self):
        payload = {
            "sessions": [
                {
                    "id": "local-a",
                    "title": "dataset validation",
                    "status": "idle",
                    "bridge_session_id": "session_abc",
                    "tail_lines": [
                        json.dumps(
                            {
                                "type": "assistant",
                                "message": {"role": "assistant", "content": "latest answer"},
                            }
                        )
                    ],
                }
            ]
        }
        with TemporaryDirectory() as tmpdir:
            cache_path = str(Path(tmpdir) / "remote_cache.json")
            config = rca.RemoteClaudeConfig(host="h", user="u")
            with mock.patch.object(rca, "_cache_path", return_value=cache_path), mock.patch.object(
                rca, "load_remote_claude_configs", return_value=[config]
            ), mock.patch.object(rca, "_run_remote_python", return_value=payload):
                data = rca.list_remote_claude_sessions(limit=3)

        self.assertTrue(data["ok"])
        self.assertEqual(data["sessions"][0]["display_id"], "session_abc")
        self.assertEqual(data["sessions"][0]["remote_key"], "u@h::session_abc")
        self.assertEqual(data["sessions"][0]["last_message"]["content"], "latest answer")
        self.assertEqual(data["remotes"][0]["target"], "u@h")

    def test_tailscale_discovery_uses_registered_users(self):
        status = {
            "Self": {"TailscaleIPs": ["192.0.2.115"]},
            "Peer": {
                "a": {
                    "HostName": "fixture-host",
                    "OS": "linux",
                    "Online": True,
                    "TailscaleIPs": ["192.0.2.29"],
                },
                "b": {
                    "HostName": "BGUO-MC0",
                    "OS": "macOS",
                    "Online": True,
                    "TailscaleIPs": ["192.0.2.115"],
                },
            },
        }
        base = rca.RemoteClaudeConfig(host="fallback", user="fallback")
        with mock.patch.object(rca, "_run_tailscale_status", return_value=status), mock.patch.object(
            rca, "_registered_user_map", return_value={"192.0.2.29": "remoteuser"}
        ):
            configs = rca.discover_tailscale_remote_configs(base)

        self.assertEqual([(item.user, item.host, item.hostname) for item in configs], [("remoteuser", "192.0.2.29", "fixture-host")])

    def test_list_sessions_filters_idle_daemon_spares(self):
        payload = {
            "sessions": [
                {
                    "id": "spare",
                    "title": "be23fef0",
                    "status": "idle",
                    "session_id": "be23fef0-364d-48e8-b1ad-12d4cd28e409",
                    "job_id": "be23fef0",
                    "transcript_path": "",
                    "tail_lines": [],
                },
                {
                    "id": "real",
                    "title": "a49f4986",
                    "status": "busy",
                    "bridge_session_id": "session_real",
                    "job_id": "a49f4986",
                },
            ]
        }
        with TemporaryDirectory() as tmpdir:
            cache_path = str(Path(tmpdir) / "remote_cache.json")
            with mock.patch.object(rca, "_cache_path", return_value=cache_path), mock.patch.object(
                rca, "load_remote_claude_configs", return_value=[rca.RemoteClaudeConfig(host="h", user="u")]
            ), mock.patch.object(rca, "_run_remote_python", return_value=payload):
                data = rca.list_remote_claude_sessions(limit=3)

        self.assertEqual([item["display_id"] for item in data["sessions"]], ["session_real"])
        self.assertEqual(data["remotes"][0]["session_count"], 1)

    def test_list_sessions_falls_back_to_cache_when_remote_is_unreachable(self):
        with TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "remote_cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "sessions": [
                            {
                                "display_id": "session_cached",
                                "title": "cached session",
                                "status": "idle",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(rca, "_cache_path", return_value=str(cache_path)), mock.patch.object(
                rca, "load_remote_claude_configs", return_value=[rca.RemoteClaudeConfig(host="h", user="u")]
            ), mock.patch.object(rca, "_run_remote_python", side_effect=RuntimeError("offline")):
                data = rca.list_remote_claude_sessions(limit=3)

        self.assertFalse(data["ok"])
        self.assertTrue(data["stale"])
        self.assertEqual(data["sessions"][0]["display_id"], "session_cached")

    def test_send_message_rejects_blank_message(self):
        with self.assertRaises(ValueError):
            rca.send_remote_claude_message("session_abc", "   ")

    def test_send_message_returns_daemon_reply_payload(self):
        payload = {
            "found": True,
            "short": "5dd495e1",
            "session": {"bridge_session_id": "session_abc"},
            "response": {"ok": True, "op": "reply"},
        }
        with mock.patch.object(
            rca, "load_remote_claude_configs", return_value=[rca.RemoteClaudeConfig(host="h", user="u")]
        ), mock.patch.object(rca, "_run_remote_python", return_value=payload):
            data = rca.send_remote_claude_message("u@h::session_abc", "hello")

        self.assertTrue(data["ok"])
        self.assertEqual(data["short"], "5dd495e1")
        self.assertEqual(data["response"]["op"], "reply")
        self.assertEqual(data["session"]["remote_key"], "u@h::session_abc")

    def test_send_message_accepts_tmux_interactive_payload(self):
        payload = {
            "found": True,
            "session": {
                "bridge_session_id": "session_abc",
                "session_id": "uuid-abc",
                "pid": 1234,
                "kind": "interactive",
            },
            "response": {"ok": True, "via": "tmux", "pane": "%0"},
        }
        with mock.patch.object(
            rca, "load_remote_claude_configs", return_value=[rca.RemoteClaudeConfig(host="h", user="u")]
        ), mock.patch.object(rca, "_run_remote_python", return_value=payload):
            data = rca.send_remote_claude_message("u@h::session_abc", "hello")

        self.assertTrue(data["ok"])
        self.assertEqual(data["response"]["via"], "tmux")
        self.assertEqual(data["session"]["remote_key"], "u@h::session_abc")

    def test_rename_session_updates_display_name(self):
        payload = {
            "found": True,
            "ok": True,
            "short": "5dd495e1",
            "old_name": "old",
            "name": "Project Alpha | primaryuser",
            "roster_updated": True,
            "session": {"bridge_session_id": "session_abc", "title": "Project Alpha | primaryuser"},
        }
        with mock.patch.object(
            rca, "load_remote_claude_configs", return_value=[rca.RemoteClaudeConfig(host="h", user="u")]
        ), mock.patch.object(rca, "_run_remote_python", return_value=payload):
            data = rca.rename_remote_claude_session("u@h::session_abc", "Project Alpha | primaryuser")

        self.assertTrue(data["ok"])
        self.assertEqual(data["old_name"], "old")
        self.assertEqual(data["name"], "Project Alpha | primaryuser")
        self.assertTrue(data["roster_updated"])
        self.assertEqual(data["session"]["remote_key"], "u@h::session_abc")

    def test_close_session_returns_archive_payload(self):
        payload = {
            "found": True,
            "ok": True,
            "short": "5dd495e1",
            "pid": 1234,
            "session": {"bridge_session_id": "session_abc"},
            "archive_path": "/home/primaryuser/.claude/sessions/.clawcross-archive/1.json",
            "kill": {"attempted": True, "terminated": True},
            "archive": {"attempted": True, "archived": True},
        }
        with mock.patch.object(
            rca, "load_remote_claude_configs", return_value=[rca.RemoteClaudeConfig(host="h", user="u")]
        ), mock.patch.object(rca, "_run_remote_python", return_value=payload):
            data = rca.close_remote_claude_session("session_abc")

        self.assertTrue(data["ok"])
        self.assertEqual(data["short"], "5dd495e1")
        self.assertTrue(data["kill"]["terminated"])
        self.assertIn(".clawcross-archive", data["archive_path"])


class RemoteClaudeRouteTests(unittest.TestCase):
    def setUp(self):
        import front  # noqa: E402

        self.front = front
        front.app.config["TESTING"] = True
        front.app.config["SECRET_KEY"] = "test-secret"

    def _login(self, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "alice"

    def test_sessions_route_requires_login(self):
        client = self.front.app.test_client()
        resp = client.get("/proxy_remote_claude_sessions")
        self.assertEqual(resp.status_code, 401)

    def test_sessions_route_returns_remote_payload(self):
        client = self.front.app.test_client()
        self._login(client)
        payload = {"ok": True, "remote": {"host": "h", "user": "u"}, "sessions": []}
        with mock.patch("routes.front_group_routes.list_remote_claude_sessions", return_value=payload):
            resp = client.get("/proxy_remote_claude_sessions?limit=3")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), payload)

    def test_merge_review_harness_sessions_keeps_settled_review_worker_visible(self):
        data = {
            "ok": True,
            "remote": {"host": "192.0.2.1", "user": "primaryuser"},
            "sessions": [{"display_id": "session_live"}],
        }
        harness_state = {
            "tasks": [
                {
                    "task_id": "task_review",
                    "title": "Review me",
                    "status": "review",
                    "updated_at": "2026-05-19T02:00:00+08:00",
                }
            ],
            "agents": [
                {
                    "agent_id": "worker-review@192.0.2.1",
                    "current_task_id": "task_review",
                    "session_ref": "session_review",
                    "status": "done",
                    "remote_host": "primaryuser@192.0.2.1",
                    "message": "done, awaiting review",
                    "updated_at": "2026-05-19T02:01:00+08:00",
                }
            ],
        }

        merged = _merge_review_harness_sessions(data, harness_state)

        self.assertEqual(len(merged["sessions"]), 2)
        review = merged["sessions"][1]
        self.assertEqual(review["display_id"], "session_review")
        self.assertEqual(review["status"], "review")
        self.assertEqual(review["remote_user"], "primaryuser")
        self.assertEqual(review["remote_host"], "192.0.2.1")
        self.assertTrue(review["harness_review_placeholder"])

    def test_messages_route_returns_remote_payload(self):
        client = self.front.app.test_client()
        self._login(client)
        payload = {"ok": True, "messages": [{"role": "assistant", "content": "ok"}]}
        with mock.patch("routes.front_group_routes.read_remote_claude_messages", return_value=payload):
            resp = client.get("/proxy_remote_claude_sessions/session_abc/messages")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), payload)

    def test_send_message_route_posts_remote_reply(self):
        client = self.front.app.test_client()
        self._login(client)
        payload = {"ok": True, "response": {"ok": True, "op": "reply"}}
        with mock.patch("routes.front_group_routes.send_remote_claude_message", return_value=payload) as send_mock:
            resp = client.post("/proxy_remote_claude_sessions/session_abc/messages", json={"text": "hello"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), payload)
        send_mock.assert_called_once_with("session_abc", "hello")

    def test_send_message_route_rejects_empty_text(self):
        client = self.front.app.test_client()
        self._login(client)
        resp = client.post("/proxy_remote_claude_sessions/session_abc/messages", json={"text": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
