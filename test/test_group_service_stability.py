import asyncio
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
import sys
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import integrations.agent_sender as agent_sender
from agents.gateway import AgentGateway
from agents.registry import AgentRecord, AgentRegistry
from api.group_service import GroupService, init_group_db
from integrations.base import SendToAgentResult

OPENCLAW = AgentRecord(
    agent_id="ag_openclaw01", owner="owner", handle="agent-a", display_name="Agent A",
    driver="openclaw",
    binding={"global_name": "agent-a", "platform": "openclaw", "api_url": "http://claw.local", "model": ""},
)


class TestGroupServiceStability(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = TemporaryDirectory()
        self.group_db_path = str(Path(self.tmpdir.name) / "group_chat.db")
        await init_group_db(self.group_db_path)
        registry = AgentRegistry(Path(self.tmpdir.name) / "registry.db", Path(self.tmpdir.name) / "user_files")
        self.gateway = AgentGateway(registry, agent_base_url="http://agent.test", internal_token="test-token")
        self.gateway._identity_prompt = lambda record, context, instructions: ""
        self.service = GroupService(
            internal_token="test-token",
            verify_password=lambda _user, _password: True,
            checkpoint_db_path=str(Path(self.tmpdir.name) / "checkpoints.db"),
            group_db_path=self.group_db_path,
            agent=None,
            gateway=self.gateway,
        )

    async def asyncTearDown(self):
        self.tmpdir.cleanup()

    async def test_external_delivery_exception_clears_typing_state(self):
        async def crash(*_args, **_kwargs):
            raise RuntimeError("simulated transport crash")

        with mock.patch.object(self.gateway, "deliver", crash), \
                mock.patch("api.group_service.logger.exception") as mock_log_exception:
            await self.service._deliver_to_agent("owner::demo", "owner", OPENCLAW, "Agent A", "hello")

        mock_log_exception.assert_called_once()
        self.assertEqual(self.service.get_typing_agents("owner::demo"), [])

    async def test_openclaw_http_no_reply_does_not_fallback_to_acp(self):
        sent = []

        async def no_reply(request):
            sent.append(request)
            return SendToAgentResult(ok=True, content="")

        with mock.patch.object(agent_sender, "send_to_agent", no_reply):
            await self.service._deliver_to_agent("owner::demo", "owner", OPENCLAW, "Agent A", "hello")
            await asyncio.gather(*self.gateway._background)

        self.assertEqual([r.connect_type for r in sent], ["http"])
        self.assertEqual(self.service.get_typing_agents("owner::demo"), [])


if __name__ == "__main__":
    unittest.main()
