import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "src" / "backend" / "ops" / "setup"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import configure_openclaw as co  # noqa: E402
import configure as cfg  # noqa: E402


class ImportOpenClawLlmTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

        root = Path(self.tempdir.name)
        self.env_path = root / "config" / ".env"
        self.env_path.parent.mkdir(parents=True, exist_ok=True)

        self.openclaw_home = root / ".openclaw"
        self.openclaw_home.mkdir(parents=True, exist_ok=True)
        self.config_path = self.openclaw_home / "openclaw.json"

        self.original_env_path = co.ENV_PATH
        self.original_configure_env_path = cfg.ENV_PATH
        self.original_openclaw_home = co.OPENCLAW_HOME
        self.original_config_path = co.OPENCLAW_CONFIG_PATH
        self.original_agent_models_path = co.OPENCLAW_AGENT_MODELS_PATH

        co.ENV_PATH = str(self.env_path)
        cfg.ENV_PATH = str(self.env_path)
        co.OPENCLAW_HOME = str(self.openclaw_home)
        co.OPENCLAW_CONFIG_PATH = str(self.config_path)
        # Must track OPENCLAW_HOME: module-level path otherwise still points at real ~/.openclaw.
        co.OPENCLAW_AGENT_MODELS_PATH = str(
            self.openclaw_home / "agents" / "main" / "agent" / "models.json"
        )

        self.addCleanup(self._restore_paths)

    def _restore_paths(self):
        co.ENV_PATH = self.original_env_path
        cfg.ENV_PATH = self.original_configure_env_path
        co.OPENCLAW_HOME = self.original_openclaw_home
        co.OPENCLAW_CONFIG_PATH = self.original_config_path
        co.OPENCLAW_AGENT_MODELS_PATH = self.original_agent_models_path

    def _write_openclaw_config(self, data):
        self.config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _read_openclaw_config(self):
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def test_detect_llm_config_prefers_effective_openai_env_key(self):
        self._write_openclaw_config(
            {
                "env": {"OPENAI_API_KEY": "env-openai-key"},
                "models": {
                    "providers": {
                        "openai": {
                            "baseUrl": "https://api.openai.com/v1",
                            "apiKey": "provider-openai-key",
                            "api": "openai-completions",
                            "models": [{"id": "gpt-5.4", "name": "gpt-5.4"}],
                        }
                    }
                },
                "agents": {"defaults": {"model": {"primary": "openai/gpt-5.4"}}},
            }
        )

        detected = co.detect_llm_config_from_openclaw()

        self.assertEqual(detected["LLM_API_KEY"], "env-openai-key")
        self.assertEqual(detected["LLM_BASE_URL"], "https://api.openai.com")
        self.assertEqual(detected["LLM_MODEL"], "gpt-5.4")
        self.assertEqual(detected["LLM_PROVIDER"], "openai")

    def test_sync_llm_overwrites_template_deepseek_base_url_from_openclaw(self):
        """--init 模板里的 api.deepseek.com 不得阻止写入 OpenClaw 真实 baseUrl。"""
        self.env_path.write_text(
            "LLM_API_KEY=your_api_key_here\n"
            "LLM_BASE_URL=https://api.deepseek.com\n"
            "LLM_MODEL=\n"
            "LLM_PROVIDER=\n",
            encoding="utf-8",
        )
        agent_models = (
            self.openclaw_home / "agents" / "main" / "agent" / "models.json"
        )
        agent_models.parent.mkdir(parents=True, exist_ok=True)
        agent_models.write_text(
            json.dumps(
                {
                    "providers": {
                        "minimax": {
                            "apiKey": "sk-from-openclaw",
                            "baseUrl": "https://api.minimaxi.com/anthropic/v1",
                            "models": [{"id": "MiniMax-M2.7", "name": "MiniMax-M2.7"}],
                        }
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        self._write_openclaw_config(
            {
                "agents": {"defaults": {"model": {"primary": "minimax/MiniMax-M2.7"}}},
                "models": {"providers": {}},
            }
        )

        n = co.sync_llm_config_from_openclaw()
        self.assertGreaterEqual(n, 1)
        body = self.env_path.read_text(encoding="utf-8")
        self.assertIn("LLM_BASE_URL=https://api.minimaxi.com/anthropic", body)
        self.assertIn("LLM_PROVIDER=minimax", body)
        self.assertLessEqual(body.count("LLM_API_KEY="), 1)


if __name__ == "__main__":
    unittest.main()
