import sys
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from integrations.agent_sender import SendToAgentResult
import front


class ProxyLoginI18nTests(unittest.TestCase):

    def setUp(self):
        self.client = front.app.test_client()

    def test_local_login_without_username_returns_bilingual_error(self):
        response = self.client.post(
            "/proxy_login",
            json={"user_id": "", "password": ""},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {
                "error": "请输入用户名 / Username required",
                "error_code": "user_id_required",
            },
        )

    def test_remote_login_without_password_returns_bilingual_error(self):
        response = self.client.post(
            "/proxy_login",
            json={"user_id": "alice", "password": ""},
            environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {
                "error": "请输入密码 / Password required",
                "error_code": "password_required",
            },
        )

    def test_remote_login_without_username_prefers_username_error(self):
        response = self.client.post(
            "/proxy_login",
            json={"user_id": "", "password": ""},
            environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {
                "error": "请输入用户名 / Username required",
                "error_code": "user_id_required",
            },
        )

    @mock.patch.object(front, "_user_exists_in_users_json", return_value=False)
    def test_local_no_password_login_returns_mode_and_password_state(self, _mock_exists):
        response = self.client.post(
            "/proxy_login",
            json={"user_id": "alice", "password": ""},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "ok": True,
                "user_id": "alice",
                "mode": "local_no_password",
                "has_password": False,
            },
        )

    @mock.patch.object(front, "_user_exists_in_users_json", return_value=False)
    def test_remote_password_login_for_passwordless_user_returns_localized_code(self, _mock_exists):
        response = self.client.post(
            "/proxy_login",
            json={"user_id": "alice", "password": "secret"},
            environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.get_json(),
            {
                "error": (
                    "用户 'alice' 未设置密码，无法使用密码登录。"
                    "请先使用「本机免密登录」，再到设置页为这个用户名创建密码。"
                    " / User 'alice' does not have a password configured, so password login is unavailable. "
                    "Use Local No-Password Login first, then create a password for this username in Settings."
                ),
                "error_code": "password_login_not_available",
                "user_id": "alice",
            },
        )

    @mock.patch.object(front, "read_env_all")
    def test_setup_status_reads_latest_llm_values_from_env_file(self, mock_read_env_all):
        mock_read_env_all.return_value = {
            "LLM_API_KEY": "sk-live-1234567890",
            "LLM_BASE_URL": "https://api.openai.com",
            "LLM_MODEL": "gpt-5.4",
            "LLM_PROVIDER": "openai",
        }

        with mock.patch.dict(
            front.os.environ,
            {
                "LLM_API_KEY": "",
                "LLM_BASE_URL": "https://api.deepseek.com",
                "LLM_MODEL": "",
                "LLM_PROVIDER": "",
            },
            clear=False,
        ):
            response = self.client.get("/api/setup_status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["llm_configured"])
        self.assertEqual(payload["current_base_url"], "https://api.openai.com")
        self.assertEqual(payload["current_model"], "gpt-5.4")
        self.assertEqual(payload["current_provider"], "openai")

    @mock.patch.object(front, "read_env_all")
    def test_llm_config_status_rejects_placeholder_api_key(self, mock_read_env_all):
        mock_read_env_all.return_value = {
            "LLM_API_KEY": "your_api_key_here",
            "LLM_BASE_URL": "https://api.openai.com",
            "LLM_MODEL": "gpt-5.4",
        }

        response = self.client.get("/api/llm_config_status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"configured": False})

    @mock.patch.object(front, "read_env_all")
    def test_llm_config_status_allows_ollama_without_api_key(self, mock_read_env_all):
        mock_read_env_all.return_value = {
            "LLM_API_KEY": "",
            "LLM_BASE_URL": "http://127.0.0.1:11434",
            "LLM_MODEL": "qwen2.5:1.5b",
            "LLM_PROVIDER": "ollama",
        }

        response = self.client.get("/api/llm_config_status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"configured": True})

    @mock.patch.object(front, "read_env_all")
    def test_setup_status_treats_ollama_without_api_key_as_configured(self, mock_read_env_all):
        mock_read_env_all.return_value = {
            "LLM_API_KEY": "",
            "LLM_BASE_URL": "http://127.0.0.1:11434",
            "LLM_MODEL": "qwen2.5:1.5b",
            "LLM_PROVIDER": "ollama",
        }

        response = self.client.get("/api/setup_status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["llm_configured"])
        self.assertEqual(payload["current_provider"], "ollama")
        self.assertEqual(payload["current_base_url"], "http://127.0.0.1:11434")
        self.assertEqual(payload["current_model"], "qwen2.5:1.5b")

    @mock.patch.object(front, "read_env_all")
    def test_discover_models_ollama_skips_auth_when_key_missing(self, mock_read_env_all):
        mock_read_env_all.return_value = {
            "LLM_API_KEY": "",
            "LLM_BASE_URL": "http://127.0.0.1:11434",
            "LLM_MODEL": "qwen2.5:1.5b",
            "LLM_PROVIDER": "ollama",
        }

        seen_headers = {}

        class _FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"data":[{"id":"qwen2.5:1.5b"}]}'

        def _fake_urlopen(req, timeout=15):
            seen_headers.update(dict(req.header_items()))
            self.assertEqual(req.full_url, "http://127.0.0.1:11434/v1/models")
            self.assertEqual(timeout, 15)
            return _FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            response = self.client.post("/api/discover_models", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"models": ["qwen2.5:1.5b"]})
        self.assertNotIn("Authorization", seen_headers)

    @mock.patch.object(front, "read_env_all")
    @mock.patch.object(front, "send_to_agent")
    def test_proxy_openclaw_chat_reads_latest_runtime_values_from_env_file(self, mock_send_to_agent, mock_read_env_all):
        mock_read_env_all.return_value = {
            "OPENCLAW_API_URL": "http://127.0.0.1:19001/v1/chat/completions",
            "OPENCLAW_GATEWAY_TOKEN": "fresh-gateway-token",
        }
        mock_send_to_agent.return_value = SendToAgentResult(
            ok=True,
            content="",
            raw_response={"ok": True},
        )

        with mock.patch.dict(
            front.os.environ,
            {
                "OPENCLAW_API_URL": "http://127.0.0.1:19999/v1/chat/completions",
                "OPENCLAW_GATEWAY_TOKEN": "stale-token",
            },
            clear=False,
        ):
            response = self.client.post(
                "/proxy_openclaw_chat",
                json={"model": "agent:main", "messages": [{"role": "user", "content": "hi"}], "stream": False},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True).strip(), '{"ok":true}')

        args, kwargs = mock_send_to_agent.call_args
        self.assertEqual(args[0].options["api_url"], "http://127.0.0.1:19001/v1/chat/completions")
        self.assertEqual(args[0].options["api_key"], "fresh-gateway-token")


if __name__ == "__main__":
    unittest.main()
