"""MCP tool servers must belong to the ClawCross instance that started them."""

import sys
import unittest
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "backend"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from webot.engine.agent import _mcp_instance_env  # noqa: E402


class TestMcpInstanceEnv(unittest.TestCase):
    def test_instance_variables_are_passed_and_others_are_not(self):
        env = {
            "CLAWCROSS_HOME": "/tmp/dev-home",
            "PORT_AGENT": "52200",
            "PORT_OASIS": "52202",
            "INTERNAL_TOKEN": "tok",
            "OASIS_BASE_URL": "http://127.0.0.1:52202",
            "LLM_API_KEY": "secret",
            "SOMETHING_ELSE": "x",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            passed = _mcp_instance_env()
        self.assertEqual(passed, {k: env[k] for k in (
            "CLAWCROSS_HOME", "PORT_AGENT", "PORT_OASIS", "INTERNAL_TOKEN", "OASIS_BASE_URL",
        )})


if __name__ == "__main__":
    unittest.main()
