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

from api import openai_service


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class OpenAIServiceWhitelistScopeTests(unittest.TestCase):
    def test_whitelist_is_scoped_to_current_user(self):
        with TemporaryDirectory() as tmpdir:
            user_root = Path(tmpdir)
            _write_json(
                user_root / "alice" / "internal_agents.json",
                [
                    {
                        "session": "shared-session",
                        "meta": {"tools": {"read_file": True, "write_file": False}},
                    }
                ],
            )
            _write_json(
                user_root / "bob" / "internal_agents.json",
                [
                    {
                        "session": "shared-session",
                        "meta": {"tools": {"run_command": True, "read_file": False}},
                    }
                ],
            )

            with mock.patch.object(openai_service, "_USER_FILES_DIR", str(user_root)):
                self.assertEqual(
                    openai_service._get_agent_tool_whitelist("alice", "shared-session"),
                    {"read_file"},
                )
                self.assertEqual(
                    openai_service._get_agent_tool_whitelist("bob", "shared-session"),
                    {"run_command"},
                )
                self.assertIsNone(
                    openai_service._get_agent_tool_whitelist("charlie", "shared-session")
                )

    def test_whitelist_reads_current_user_team_agent_config(self):
        with TemporaryDirectory() as tmpdir:
            user_root = Path(tmpdir)
            _write_json(
                user_root / "alice" / "teams" / "ops" / "internal_agents.json",
                [
                    {
                        "session": "team-session",
                        "tools": {"session_send_to": True, "run_command": False},
                    }
                ],
            )
            _write_json(
                user_root / "bob" / "teams" / "ops" / "internal_agents.json",
                [
                    {
                        "session": "team-session",
                        "tools": {"write_file": True},
                    }
                ],
            )

            with mock.patch.object(openai_service, "_USER_FILES_DIR", str(user_root)):
                self.assertEqual(
                    openai_service._get_agent_tool_whitelist("alice", "team-session"),
                    {"session_send_to"},
                )
                self.assertEqual(
                    openai_service._get_agent_tool_whitelist("bob", "team-session"),
                    {"write_file"},
                )

    def test_none_sentinel_disables_every_tool(self):
        with TemporaryDirectory() as tmpdir:
            user_root = Path(tmpdir)
            _write_json(
                user_root / "alice" / "internal_agents.json",
                [{"session": "quiet-agent", "name": "Quiet", "tools": "none"}],
            )
            with mock.patch.object(openai_service, "_USER_FILES_DIR", str(user_root)):
                self.assertEqual(
                    openai_service._get_agent_tool_whitelist("alice", "quiet-agent"),
                    set(),
                )


if __name__ == "__main__":
    unittest.main()
