"""L1 gateway: one ``ask`` / ``deliver`` / ``control`` for every kind of agent,
mapped onto the right transport."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import agents.gateway as gateway_module  # noqa: E402
import integrations.agent_sender as agent_sender  # noqa: E402
from agents.gateway import AgentGateway  # noqa: E402
from agents.messages import AgentMessage  # noqa: E402
from agents.registry import AgentRegistry  # noqa: E402
from integrations.base import SendToAgentResult  # noqa: E402


class _RecordingHttp:
    """Stands in for httpx.AsyncClient inside agents.gateway."""

    posts: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        _RecordingHttp.posts.append({"url": url, "headers": headers, "json": json})
        return mock.Mock(status_code=200, json=lambda: {"status": "success"}, text="")


class GatewayTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        user_files = Path(self.tmp.name) / "user_files"
        (user_files / "alice" / "teams" / "dev").mkdir(parents=True)
        (user_files / "alice" / "teams" / "dev" / "internal_agents.json").write_text(json.dumps([
            {"name": "Coder", "tag": "coder", "session": "s1"},
        ]), encoding="utf-8")
        (user_files / "alice" / "external_agents.json").write_text(json.dumps([
            {"name": "Codex", "tag": "codex", "global_name": "cx", "platform": "codex", "config": {}},
            {"name": "Claw", "tag": "openclaw", "global_name": "claw", "platform": "openclaw",
             "config": {"api_url": "http://saved:1", "model": "agent:claw:work"}},
            {"name": "Plain", "tag": "plain", "global_name": "plain", "platform": "plainhttp",
             "config": {"api_url": "http://llm.example/v1", "api_key": "k", "model": "m1"}},
            {"name": "Nowhere", "tag": "x", "global_name": "nowhere", "platform": "plainhttp", "config": {}},
        ]), encoding="utf-8")
        registry = AgentRegistry(Path(self.tmp.name) / "group_chat.db", user_files)
        self.gateway = AgentGateway(registry, agent_base_url="http://agent.test", internal_token="tok")

        self.sent = []

        async def fake_send(request):
            self.sent.append(request)
            return SendToAgentResult(ok=True, content="reply")

        for target, attr, value in [
            (agent_sender, "send_to_agent", fake_send),
            (gateway_module.httpx, "AsyncClient", _RecordingHttp),
            (AgentGateway, "_identity_prompt", lambda self, record, context, instructions: f"ID[{instructions}]"),
        ]:
            patcher = mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        _RecordingHttp.posts = []


class TestAsk(GatewayTestCase):
    async def test_webot_goes_to_its_session_with_mode_and_instructions(self):
        reply = await self.gateway.ask(
            "alice", "coder", AgentMessage(text="hi", instructions="rules"), mode="chat",
        )

        self.assertTrue(reply.ok)
        self.assertEqual(reply.content, "reply")
        request = self.sent[0]
        self.assertEqual((request.connect_type, request.platform, request.session), ("http", "internal", "s1"))
        self.assertEqual(request.options["api_url"], "http://agent.test/v1/chat/completions")
        self.assertEqual(request.options["headers"]["Authorization"], "Bearer tok:alice")
        body = request.options["body"]
        self.assertEqual(body["session_mode"], "chat")
        self.assertEqual(body["enabled_tools"], [])  # chat means no tools
        self.assertEqual(body["messages"][0], {"role": "system", "content": "rules"})

    async def test_acpx_agent_uses_its_session_and_mode_overrides(self):
        await self.gateway.ask("alice", "codex", AgentMessage(text="do it", instructions="rules"), mode="readonly")

        request = self.sent[0]
        self.assertEqual((request.connect_type, request.platform), ("acp", "codex"))
        self.assertEqual(request.session, "agent:cx:clawcrosschat")
        self.assertEqual(request.prompt, "do it")
        self.assertEqual(request.options["identity_prompt"], "ID[rules]")
        self.assertEqual(request.options["non_interactive_permissions"], "deny")

    async def test_openclaw_prefers_runtime_endpoint_and_routes_by_session_key(self):
        with mock.patch.dict("os.environ", {"OPENCLAW_API_URL": "http://claw.local:18789", "OPENCLAW_GATEWAY_TOKEN": "gw"}):
            await self.gateway.ask("alice", "claw", AgentMessage(text="hello"))

        request = self.sent[0]
        self.assertEqual((request.connect_type, request.platform), ("http", "openclaw"))
        self.assertEqual(request.session, "agent:claw:work")
        self.assertEqual(request.options["api_url"], "http://claw.local:18789/v1/chat/completions")
        self.assertEqual(request.options["headers"]["x-openclaw-session-key"], "agent:claw:work")
        self.assertEqual(request.options["headers"]["Authorization"], "Bearer gw")
        self.assertEqual(request.options["body"]["model"], "agent:claw:work")

    async def test_plain_http_agent_and_missing_endpoint(self):
        await self.gateway.ask(
            "alice", "plain",
            AgentMessage(text="look", attachments=[{"type": "image", "name": "a.png", "mime_type": "image/png", "data": "AAA"}]),
        )
        request = self.sent[0]
        self.assertEqual(request.options["api_url"], "http://llm.example/v1/chat/completions")
        content = request.options["body"]["messages"][0]["content"]
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,AAA")

        reply = await self.gateway.ask("alice", "nowhere", AgentMessage(text="x"))
        self.assertFalse(reply.ok)
        self.assertIn("api_url", reply.error)


class TestDeliverAndControl(GatewayTestCase):
    async def test_webot_delivery_goes_to_the_inbox(self):
        receipt = await self.gateway.deliver(
            "alice", "coder", AgentMessage(text="new message"), mode="readonly", coalesce_key="group:g:agent:s1",
        )

        self.assertTrue(receipt.accepted)
        post = _RecordingHttp.posts[0]
        self.assertEqual(post["url"], "http://agent.test/system_trigger")
        self.assertEqual(post["headers"], {"X-Internal-Token": "tok"})
        self.assertEqual(post["json"]["session_id"], "s1")
        self.assertEqual(post["json"]["session_mode"], "readonly")
        self.assertEqual(post["json"]["coalesce_key"], "group:g:agent:s1")
        self.assertEqual(self.sent, [])

    async def test_external_delivery_sends_without_waiting(self):
        receipt = await self.gateway.deliver("alice", "codex", AgentMessage(text="fyi"))
        self.assertTrue(receipt.accepted)
        await asyncio.gather(*self.gateway._background)
        self.assertEqual(self.sent[0].platform, "codex")

    async def test_control_maps_to_agent_control(self):
        await self.gateway.control("alice", "coder", "cancel")
        await self.gateway.control("alice", "claw", "reset")

        self.assertEqual(
            [(p["url"], p["json"]["kind"], p["json"]["identity"], p["json"]["action"]) for p in _RecordingHttp.posts],
            [
                ("http://agent.test/agent_control", "internal", "s1", "cancel"),
                ("http://agent.test/agent_control", "external", "claw", "reset"),
            ],
        )

    async def test_cards_describe_capabilities(self):
        cards = {card["handle"]: card for card in self.gateway.list("alice")}
        self.assertEqual(cards["coder"]["address"], "alice/coder")
        self.assertEqual(cards["coder"]["teams"], ["dev"])
        self.assertTrue(cards["coder"]["capabilities"]["structured_output"])
        self.assertEqual(cards["codex"]["platform"], "codex")
        self.assertIn("readonly", cards["codex"]["capabilities"]["modes"])
        self.assertFalse(cards["plain"]["capabilities"]["cancel"])


if __name__ == "__main__":
    unittest.main()
