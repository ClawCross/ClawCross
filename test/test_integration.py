import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import front
from chatbot.adapters.base import ChannelAdapter, MagicLink
from utils.env_settings import mask_all_sensitive, read_env_all, write_env_settings


class _MockJsonResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class EnvSettingsTests(unittest.TestCase):
    def test_bot_json_values_are_shell_safe_and_read_back_unquoted(self):
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as f:
            path = f.name

        try:
            write_env_settings(path, {"ONEBOTV11_BOTS": '[{"token":"abc"}]'})
            raw_text = Path(path).read_text(encoding="utf-8")

            self.assertIn("ONEBOTV11_BOTS='[{\"token\":\"abc\"}]'", raw_text)
            self.assertEqual(read_env_all(path)["ONEBOTV11_BOTS"], '[{"token":"abc"}]')
            self.assertIn("****", mask_all_sensitive(read_env_all(path))["ONEBOTV11_BOTS"])
        finally:
            Path(path).unlink(missing_ok=True)


class ChatbotCommandTests(unittest.TestCase):
    def test_cross_command_matches_exact_command_or_arguments_only(self):
        self.assertTrue(ChannelAdapter.is_cross_command("/cross"))
        self.assertTrue(ChannelAdapter.is_cross_command("  /Cross   "))
        self.assertTrue(ChannelAdapter.is_cross_command("/cross login"))
        self.assertFalse(ChannelAdapter.is_cross_command("/crossword"))
        self.assertFalse(ChannelAdapter.is_cross_command("please /cross"))

    def test_cross_reply_includes_expiry_when_available(self):
        reply = ChannelAdapter.format_cross_reply(
            MagicLink(
                link="https://example.test/login-link/token?user=default",
                expires_at=1778119853,
                valid_hours=24,
            )
        )

        self.assertIn("已生成新的有效链接", reply)
        self.assertIn("https://example.test/login-link/token?user=default", reply)
        self.assertIn("有效至", reply)


class FrontendIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        front.app.config.update(TESTING=True)

    def setUp(self):
        self.client = front.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "integration-user"

    def test_studio_page_renders_shell_and_settings_modal(self):
        response = self.client.get("/studio", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="page-tab active" id="tab-chat"', html)
        self.assertIn('id="page-chat" class="chat-page" style="display:flex;"', html)
        self.assertIn('id="tab-orchestrate"', html)
        self.assertIn('id="settings-modal"', html)
        self.assertIn('id="oasis-chat-workspace-switcher"', html)
        self.assertIn('id="oasis-chat-graph-host"', html)
        self.assertIn('id="webot-subagent-panel"', html)
        self.assertIn('id="webot-subagent-list"', html)
        self.assertIn('id="webot-policy-panel"', html)
        self.assertIn('id="webot-policy-editor"', html)
        self.assertIn("/static/js/orchestration.js", html)
        self.assertIn("/static/js/tinyfish-live-shared.js", html)

    def test_proxy_settings_full_get_forwards_user_context(self):
        with mock.patch.object(
            front.requests,
            "get",
            return_value=_MockJsonResponse({"settings": {"LLM_MODEL": "gpt-5.4"}}, 200),
        ) as mock_get:
            response = self.client.get("/proxy_settings_full")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["settings"]["LLM_MODEL"], "gpt-5.4")
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"], {"user_id": "integration-user"})
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})
        self.assertEqual(kwargs["timeout"], 10)

    def test_proxy_settings_full_post_merges_session_user_id(self):
        with mock.patch.object(
            front.requests,
            "post",
            return_value=_MockJsonResponse({"status": "success", "updated": ["LLM_MODEL"]}, 200),
        ) as mock_post:
            response = self.client.post(
                "/proxy_settings_full",
                json={"settings": {"LLM_MODEL": "gpt-5.4"}},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "success")
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "settings": {"LLM_MODEL": "gpt-5.4"},
                "user_id": "integration-user",
            },
        )
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_proxy_chatbot_whitelist_get_forwards_user_context(self):
        payload = {"status": "success", "whitelist": {"telegram": {"entries": {}, "name_map": {}}}}
        with mock.patch.object(
            front.requests,
            "get",
            return_value=_MockJsonResponse(payload, 200),
        ) as mock_get:
            response = self.client.get("/proxy_chatbot_whitelist")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), payload)
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"], {"user_id": "integration-user"})
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})
        self.assertEqual(kwargs["timeout"], 10)

    def test_proxy_chatbot_whitelist_post_merges_session_user_id(self):
        whitelist = {"telegram": {"entries": {"123": {"username": "alice"}}, "name_map": {}}}
        with mock.patch.object(
            front.requests,
            "post",
            return_value=_MockJsonResponse({"status": "success", "whitelist": whitelist}, 200),
        ) as mock_post:
            response = self.client.post("/proxy_chatbot_whitelist", json={"whitelist": whitelist})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "success")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"], {"whitelist": whitelist, "user_id": "integration-user"})
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_generate_login_link_returns_expiry_metadata(self):
        response = self.client.post(
            "/generate_login_link",
            json={"user_id": "integration-user"},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertIn("/login-link/", payload["link"])
        self.assertEqual(payload["valid_hours"], 24)
        self.assertGreater(payload["expires_at"], payload["generated_at"])

    def test_proxy_weclaw_qr_returns_pending_when_missing(self):
        with mock.patch.object(front.os.path, "exists", return_value=False):
            response = self.client.get("/proxy_weclaw_qr")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "pending")
        self.assertEqual(payload["qr"], "")
        self.assertIn("weclaw", payload["message"].lower())

    def test_proxy_openclaw_sessions_forwards_filter_and_preserves_shape(self):
        with mock.patch.object(
            front.requests,
            "get",
            return_value=_MockJsonResponse({"available": True, "agents": []}, 200),
        ) as mock_get:
            response = self.client.get("/proxy_openclaw_sessions?filter=main")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"available": True, "agents": []})
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"], {"filter": "main"})
        self.assertEqual(kwargs["timeout"], 10)

    def test_proxy_webot_subagents_forwards_user_context(self):
        with mock.patch.object(
            front.requests,
            "get",
            return_value=_MockJsonResponse({"status": "success", "subagents": []}, 200),
        ) as mock_get:
            response = self.client.get("/proxy_webot_subagents")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "success")
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"], {"user_id": "integration-user"})
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_proxy_webot_subagent_history_forwards_agent_ref(self):
        with mock.patch.object(
            front.requests,
            "post",
            return_value=_MockJsonResponse({"status": "success", "messages": []}, 200),
        ) as mock_post:
            response = self.client.post(
                "/proxy_webot_subagent_history",
                json={"agent_ref": "worker-1", "limit": 8},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "success")
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "user_id": "integration-user",
                "agent_ref": "worker-1",
                "limit": 8,
            },
        )
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_proxy_webot_subagent_cancel_forwards_agent_ref(self):
        with mock.patch.object(
            front.requests,
            "post",
            return_value=_MockJsonResponse({"status": "success", "cancelled": True}, 200),
        ) as mock_post:
            response = self.client.post(
                "/proxy_webot_subagent_cancel",
                json={"agent_ref": "worker-1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["cancelled"])
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "user_id": "integration-user",
                "agent_ref": "worker-1",
            },
        )

    def test_proxy_webot_tool_policy_forwards_user_context(self):
        with mock.patch.object(
            front.requests,
            "get",
            return_value=_MockJsonResponse({"status": "success", "policy": {"default_approval": "allow"}}, 200),
        ) as mock_get:
            response = self.client.get("/proxy_webot_tool_policy")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["policy"]["default_approval"], "allow")
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["params"], {"user_id": "integration-user"})
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_proxy_webot_tool_policy_update_forwards_payload(self):
        with mock.patch.object(
            front.requests,
            "post",
            return_value=_MockJsonResponse({"status": "success", "policy": {"default_approval": "manual"}}, 200),
        ) as mock_post:
            response = self.client.post(
                "/proxy_webot_tool_policy",
                json={"policy": {"default_approval": "manual", "tools": {"run_command": {"approval": "manual"}}}},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["policy"]["default_approval"], "manual")
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "user_id": "integration-user",
                "policy": {"default_approval": "manual", "tools": {"run_command": {"approval": "manual"}}},
            },
        )
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_proxy_webot_session_runtime_forwards_session_context(self):
        with mock.patch.object(
            front.requests,
            "get",
            return_value=_MockJsonResponse(
                {
                    "status": "success",
                    "session_id": "subagent__coder__worker-1",
                    "workspace": "/tmp/clawcross/workers/worker-1",
                    "plan": {"title": "Plan", "status": "active", "items": []},
                    "todos": {"items": []},
                    "verifications": [],
                    "approvals": [],
                },
                200,
            ),
        ) as mock_get:
            response = self.client.get(
                "/proxy_webot_session_runtime?session_id=subagent__coder__worker-1"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "success")
        _, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["params"],
            {
                "user_id": "integration-user",
                "session_id": "subagent__coder__worker-1",
            },
        )
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_proxy_webot_workflow_routes_forward_payloads(self):
        with self.subTest("list workflow presets"):
            with mock.patch.object(
                front.requests,
                "get",
                return_value=_MockJsonResponse({"status": "success", "presets": [{"preset_id": "review_gate"}]}, 200),
            ) as mock_get:
                response = self.client.get("/proxy_webot_workflow_presets")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["presets"][0]["preset_id"], "review_gate")
            _, kwargs = mock_get.call_args
            self.assertEqual(kwargs["params"], {"user_id": "integration-user"})

        with self.subTest("apply workflow preset"):
            with mock.patch.object(
                front.requests,
                "post",
                return_value=_MockJsonResponse({"status": "success", "preset": {"preset_id": "review_gate"}}, 200),
            ) as mock_post:
                response = self.client.post(
                    "/proxy_webot_workflow_apply",
                    json={"session_id": "default", "preset_id": "review_gate"},
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["preset"]["preset_id"], "review_gate")
            _, kwargs = mock_post.call_args
            self.assertEqual(
                kwargs["json"],
                {
                    "user_id": "integration-user",
                    "session_id": "default",
                    "preset_id": "review_gate",
                },
            )

    def test_proxy_webot_session_inbox_forwards_query_params(self):
        with mock.patch.object(
            front.requests,
            "get",
            return_value=_MockJsonResponse({"status": "success", "items": []}, 200),
        ) as mock_get:
            response = self.client.get(
                "/proxy_webot_session_inbox?session_id=subagent__coder__worker-1&target_ref=worker-1&status=queued&limit=9"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "success")
        _, kwargs = mock_get.call_args
        self.assertEqual(
            kwargs["params"],
            {
                "user_id": "integration-user",
                "session_id": "subagent__coder__worker-1",
                "target_ref": "worker-1",
                "status": "queued",
                "limit": "9",
            },
        )
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_proxy_webot_session_inbox_send_forwards_payload(self):
        with mock.patch.object(
            front.requests,
            "post",
            return_value=_MockJsonResponse({"status": "success", "created": 1}, 200),
        ) as mock_post:
            response = self.client.post(
                "/proxy_webot_session_inbox_send",
                json={
                    "session_id": "default",
                    "target_ref": "worker-1",
                    "body": "Need a review pass",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["created"], 1)
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "user_id": "integration-user",
                "session_id": "default",
                "target_ref": "worker-1",
                "body": "Need a review pass",
            },
        )
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_proxy_webot_session_inbox_deliver_forwards_payload(self):
        with mock.patch.object(
            front.requests,
            "post",
            return_value=_MockJsonResponse({"status": "success", "delivered_total": 1}, 200),
        ) as mock_post:
            response = self.client.post(
                "/proxy_webot_session_inbox_deliver",
                json={"session_id": "default", "target_ref": "worker-1", "limit": 5, "force": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["delivered_total"], 1)
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "user_id": "integration-user",
                "session_id": "default",
                "target_ref": "worker-1",
                "limit": 5,
                "force": True,
            },
        )
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_proxy_webot_runtime_controls_forward_payloads(self):
        with self.subTest("session mode"):
            with mock.patch.object(
                front.requests,
                "post",
                return_value=_MockJsonResponse({"status": "success"}, 200),
            ) as mock_post:
                response = self.client.post(
                    "/proxy_webot_session_mode",
                    json={"session_id": "default", "mode": "review", "reason": "triage"},
                )
            self.assertEqual(response.status_code, 200)
            _, kwargs = mock_post.call_args
            self.assertEqual(
                kwargs["json"],
                {
                    "user_id": "integration-user",
                    "session_id": "default",
                    "mode": "review",
                    "reason": "triage",
                },
            )

        with self.subTest("interrupt"):
            with mock.patch.object(
                front.requests,
                "post",
                return_value=_MockJsonResponse({"status": "success"}, 200),
            ) as mock_post:
                response = self.client.post(
                    "/proxy_webot_run_interrupt",
                    json={"session_id": "default", "run_id": "run-1", "agent_ref": "worker-1"},
                )
            self.assertEqual(response.status_code, 200)
            _, kwargs = mock_post.call_args
            self.assertEqual(
                kwargs["json"],
                {
                    "user_id": "integration-user",
                    "session_id": "default",
                    "run_id": "run-1",
                    "agent_ref": "worker-1",
                },
            )

        with self.subTest("voice"):
            with mock.patch.object(
                front.requests,
                "post",
                return_value=_MockJsonResponse({"status": "success"}, 200),
            ) as mock_post:
                response = self.client.post(
                    "/proxy_webot_voice",
                    json={
                        "session_id": "default",
                        "enabled": True,
                        "auto_read_aloud": True,
                        "last_transcript": "ship it",
                        "tts_model": "gpt-4o-mini-tts",
                        "tts_voice": "alloy",
                        "stt_model": "gpt-4o-mini-transcribe",
                    },
                )
            self.assertEqual(response.status_code, 200)
            _, kwargs = mock_post.call_args
            self.assertEqual(
                kwargs["json"],
                {
                    "user_id": "integration-user",
                    "session_id": "default",
                    "enabled": True,
                    "auto_read_aloud": True,
                    "last_transcript": "ship it",
                    "tts_model": "gpt-4o-mini-tts",
                    "tts_voice": "alloy",
                    "stt_model": "gpt-4o-mini-transcribe",
                },
            )
            self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_builtin_team_preset_api_uses_asset_loader(self):
        with self.subTest("list"):
            with mock.patch.object(
                front,
                "list_team_presets",
                return_value=[{"preset_id": "modern-ceo", "name": "现代企业制"}],
            ) as mock_list:
                response = self.client.get("/api/team-presets")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["presets"][0]["preset_id"], "modern-ceo")
            mock_list.assert_called_once()

        with self.subTest("install"):
            with mock.patch.object(
                front,
                "install_team_preset",
                return_value={
                    "team": "现代企业制",
                    "preset": {"preset_id": "modern-ceo", "name": "现代企业制"},
                    "internal_agents": 14,
                    "experts": 14,
                    "workflow_files": ["modern_ceo_baseline.yaml"],
                },
            ) as mock_install:
                response = self.client.post(
                    "/api/team-presets/install",
                    json={"preset_id": "modern-ceo", "team": "现代企业制"},
                )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["ok"])
            _, kwargs = mock_install.call_args
            self.assertEqual(kwargs["user_id"], "integration-user")
            self.assertEqual(kwargs["team_name"], "现代企业制")
            self.assertEqual(kwargs["preset_id"], "modern-ceo")

    def test_proxy_webot_bridge_memory_and_buddy_controls_forward_payloads(self):
        with self.subTest("bridge attach"):
            with mock.patch.object(
                front.requests,
                "post",
                return_value=_MockJsonResponse({"status": "success"}, 200),
            ) as mock_post:
                response = self.client.post(
                    "/proxy_webot_bridge_attach",
                    json={"session_id": "default", "role": "viewer", "label": "browser"},
                )
            self.assertEqual(response.status_code, 200)
            _, kwargs = mock_post.call_args
            self.assertEqual(
                kwargs["json"],
                {
                    "user_id": "integration-user",
                    "session_id": "default",
                    "role": "viewer",
                    "label": "browser",
                },
            )

        with self.subTest("bridge detach"):
            with mock.patch.object(
                front.requests,
                "post",
                return_value=_MockJsonResponse({"status": "success"}, 200),
            ) as mock_post:
                response = self.client.post(
                    "/proxy_webot_bridge_detach",
                    json={"bridge_id": "bridge-123"},
                )
            self.assertEqual(response.status_code, 200)
            _, kwargs = mock_post.call_args
            self.assertEqual(
                kwargs["json"],
                {
                    "user_id": "integration-user",
                    "bridge_id": "bridge-123",
                },
            )

        with self.subTest("kairos"):
            with mock.patch.object(
                front.requests,
                "post",
                return_value=_MockJsonResponse({"status": "success"}, 200),
            ) as mock_post:
                response = self.client.post(
                    "/proxy_webot_kairos",
                    json={"session_id": "default", "enabled": True, "reason": "ui-toggle"},
                )
            self.assertEqual(response.status_code, 200)
            _, kwargs = mock_post.call_args
            self.assertEqual(
                kwargs["json"],
                {
                    "user_id": "integration-user",
                    "session_id": "default",
                    "enabled": True,
                    "reason": "ui-toggle",
                },
            )

        with self.subTest("dream"):
            with mock.patch.object(
                front.requests,
                "post",
                return_value=_MockJsonResponse({"status": "success"}, 200),
            ) as mock_post:
                response = self.client.post(
                    "/proxy_webot_dream",
                    json={"session_id": "default", "reason": "manual"},
                )
            self.assertEqual(response.status_code, 200)
            _, kwargs = mock_post.call_args
            self.assertEqual(
                kwargs["json"],
                {
                    "user_id": "integration-user",
                    "session_id": "default",
                    "reason": "manual",
                },
            )

        with self.subTest("buddy"):
            with mock.patch.object(
                front.requests,
                "post",
                return_value=_MockJsonResponse({"status": "success"}, 200),
            ) as mock_post:
                response = self.client.post(
                    "/proxy_webot_buddy",
                    json={"session_id": "default", "action": "pet"},
                )
            self.assertEqual(response.status_code, 200)
            _, kwargs = mock_post.call_args
            self.assertEqual(
                kwargs["json"],
                {
                    "user_id": "integration-user",
                    "session_id": "default",
                    "action": "pet",
                },
            )
            self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_proxy_webot_tool_approval_resolve_forwards_resolution_payload(self):
        with mock.patch.object(
            front.requests,
            "post",
            return_value=_MockJsonResponse(
                {
                    "status": "success",
                    "approval": {
                        "approval_id": "approval-1",
                        "tool_name": "run_command",
                        "status": "approved",
                        "remember": True,
                    },
                },
                200,
            ),
        ) as mock_post:
            response = self.client.post(
                "/proxy_webot_tool_approval_resolve",
                json={
                    "approval_id": "approval-1",
                    "action": "approve",
                    "reason": "allowed for current task",
                    "remember": True,
                    "session_id": "subagent__coder__worker-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["approval"]["status"], "approved")
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "user_id": "integration-user",
                "approval_id": "approval-1",
                "action": "approve",
                "reason": "allowed for current task",
                "remember": True,
                "session_id": "subagent__coder__worker-1",
            },
        )
        self.assertEqual(kwargs["headers"], {"X-Internal-Token": front.INTERNAL_TOKEN})

    def test_tinyfish_status_sync_polls_before_returning_overview(self):
        overview = {
            "config": {"api_key_configured": True, "targets_path_exists": True},
            "pending_runs": 0,
            "recent_runs": [],
            "sites": [],
            "recent_changes": [],
        }
        with mock.patch.object(front, "poll_pending_runs_once") as mock_poll, mock.patch.object(
            front, "get_monitor_overview", return_value=overview
        ) as mock_overview:
            response = self.client.get("/api/tinyfish/status?sync=1&runs=5&changes=7&sites=3&snapshots=2")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        mock_poll.assert_called_once_with()
        mock_overview.assert_called_once_with(
            recent_change_limit=7,
            recent_run_limit=5,
            latest_site_limit=3,
            snapshots_per_site=2,
        )

    def test_export_openclaw_config_falls_back_to_saved_masked_values(self):
        stub_module = types.SimpleNamespace(
            export_llm_config_to_openclaw=mock.Mock(
                return_value={"ok": True, "model_ref": "openai/gpt-5.4"}
            )
        )
        payload = {
            "api_key": "****masked****",
            "base_url": "",
            "model": "",
            "provider": "",
        }
        saved = {
            "api_key": "saved-key",
            "base_url": "https://api.openai.com",
            "model": "gpt-5.4",
            "provider": "openai",
        }

        with mock.patch("shutil.which", return_value="/usr/local/bin/openclaw"), mock.patch.object(
            front, "_read_saved_clawcross_llm_config", return_value=saved
        ), mock.patch.dict(sys.modules, {"configure_openclaw": stub_module}):
            response = self.client.post("/api/export_openclaw_config", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        stub_module.export_llm_config_to_openclaw.assert_called_once_with(
            api_key="saved-key",
            base_url="https://api.openai.com",
            model="gpt-5.4",
            provider="openai",
        )

    def test_export_openclaw_config_allows_keyless_ollama(self):
        stub_module = types.SimpleNamespace(
            export_llm_config_to_openclaw=mock.Mock(
                return_value={"ok": True, "model_ref": "ollama/llama3.2:latest"}
            )
        )
        payload = {
            "api_key": "",
            "base_url": "http://127.0.0.1:11434",
            "model": "llama3.2:latest",
            "provider": "ollama",
        }

        with mock.patch("shutil.which", return_value="/usr/local/bin/openclaw"), mock.patch.dict(
            sys.modules, {"configure_openclaw": stub_module}
        ):
            response = self.client.post("/api/export_openclaw_config", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        stub_module.export_llm_config_to_openclaw.assert_called_once_with(
            api_key="",
            base_url="http://127.0.0.1:11434",
            model="llama3.2:latest",
            provider="ollama",
        )

    def test_save_current_user_password_persists_hashed_credential(self):
        captured = {}

        def _capture_write(users):
            captured["users"] = dict(users)

        with mock.patch.object(front, "_load_users_json", return_value={}), mock.patch.object(
            front, "_write_users_json", side_effect=_capture_write
        ) as mock_write:
            response = self.client.post(
                "/api/current_user/password",
                json={"password": "temporary-secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "ok": True,
                "user_id": "integration-user",
                "status": "created",
                "has_password": True,
            },
        )
        self.assertEqual(
            captured["users"],
            {"integration-user": front._hash_password("temporary-secret")},
        )
        mock_write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
