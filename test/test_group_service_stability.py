import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
import sys
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.group_service import GroupService, init_group_db


class TestGroupServiceStability(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = TemporaryDirectory()
        self.group_db_path = str(Path(self.tmpdir.name) / "group_chat.db")
        await init_group_db(self.group_db_path)
        self.service = GroupService(
            internal_token="test-token",
            verify_password=lambda _user, _password: True,
            checkpoint_db_path=str(Path(self.tmpdir.name) / "checkpoints.db"),
            group_db_path=self.group_db_path,
            agent=None,
        )

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    async def test_external_delivery_exception_clears_typing_state(self):
        async def failing_send(*_args, **_kwargs):
            raise RuntimeError("simulated transport crash")

        self.service._send_to_http_agent = failing_send
        with mock.patch("api.group_service.logger.exception") as mock_log_exception:
            await self.service._handle_external_agent_reply(
                "owner::demo",
                {"global_name": "agent-a", "name": "Agent A", "platform": "openclaw"},
                "hello",
                "Agent A",
            )

        mock_log_exception.assert_called_once()
        self.assertEqual(self.service.get_typing_agents("owner::demo"), [])

    async def test_openclaw_http_no_reply_does_not_fallback_to_acp(self):
        async def no_reply(*_args, **_kwargs):
            return None

        self.service._send_to_http_agent = no_reply
        self.service._send_to_acp_agent = mock.AsyncMock()
        await self.service._handle_external_agent_reply(
            "owner::demo",
            {"global_name": "agent-a", "name": "Agent A", "platform": "openclaw"},
            "hello",
            "Agent A",
        )

        self.service._send_to_acp_agent.assert_not_called()
        self.assertEqual(self.service.get_typing_agents("owner::demo"), [])


if __name__ == "__main__":
    unittest.main()
