"""A WeBot agent's ``tools`` setting limits what its session may call."""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.store import WEBOT, AgentStore  # noqa: E402
from api.openai_service import _get_agent_tool_whitelist  # noqa: E402


class OpenAIServiceWhitelistScopeTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = AgentStore(Path(tmp.name) / "clawcross.db")
        patcher = mock.patch("agents.store.get_store", lambda *a: self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def agent(self, owner, session, tools):
        self.store.create(owner, driver=WEBOT, config={"tools": tools}, agent_id=session)

    def test_whitelist_is_scoped_to_the_sessions_owner(self):
        self.agent("alice", "shared-session", {"read_file": True, "write_file": False})
        self.agent("bob", "shared-session", {"run_command": True})
        self.assertEqual(_get_agent_tool_whitelist("alice", "shared-session"), {"read_file"})
        self.assertEqual(_get_agent_tool_whitelist("bob", "shared-session"), {"run_command"})
        self.assertIsNone(_get_agent_tool_whitelist("charlie", "shared-session"))

    def test_none_sentinel_disables_every_tool(self):
        self.agent("alice", "quiet", "none")
        self.assertEqual(_get_agent_tool_whitelist("alice", "quiet"), set())

    def test_an_agent_without_the_setting_is_unrestricted(self):
        self.agent("alice", "free", None)
        self.assertIsNone(_get_agent_tool_whitelist("alice", "free"))
        self.assertIsNone(_get_agent_tool_whitelist("alice", "plain-chat"))


if __name__ == "__main__":
    unittest.main()
