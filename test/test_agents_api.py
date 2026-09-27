"""The public face of L1: /v1/agents, and OpenAI-compatible access to any agent."""

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

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agents.gateway import AgentGateway  # noqa: E402
from agents.messages import AgentReply  # noqa: E402
from agents.registry import AgentRegistry  # noqa: E402
from agents.routes import create_agents_router  # noqa: E402
from api.openai_models import ChatCompletionRequest  # noqa: E402
from api.openai_service import OpenAIChatService  # noqa: E402

TOKEN = "tok"


def _registry(root: Path) -> AgentRegistry:
    user_files = root / "user_files"
    (user_files / "alice" / "teams" / "dev").mkdir(parents=True)
    (user_files / "alice" / "teams" / "dev" / "internal_agents.json").write_text(json.dumps([
        {"name": "Coder", "tag": "coder", "session": "s1"},
        {"name": "Code Reviewer", "session": "s2"},
    ]), encoding="utf-8")
    (user_files / "alice" / "teams" / "ops").mkdir(parents=True)
    (user_files / "alice" / "teams" / "ops" / "internal_agents.json").write_text(json.dumps([
        {"name": "Code Reviewer", "session": "s3"},
    ]), encoding="utf-8")
    (user_files / "alice" / "external_agents.json").write_text(json.dumps([
        {"name": "Codex", "tag": "codex", "global_name": "cx", "platform": "codex"},
    ]), encoding="utf-8")
    return AgentRegistry(root / "group_chat.db", user_files)


class TestAgentsRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gateway = AgentGateway(_registry(Path(self.tmp.name)), agent_base_url="http://agent.test", internal_token=TOKEN)
        app = FastAPI()
        app.include_router(create_agents_router(
            internal_token=TOKEN,
            verify_password=lambda user, password: (user, password) == ("alice", "pw"),
            gateway=self.gateway,
        ))
        self.client = TestClient(app)
        self.as_alice = {"Authorization": f"Bearer {TOKEN}:alice"}

    def test_lists_every_runtime_flat(self):
        body = self.client.get("/v1/agents", headers=self.as_alice).json()
        self.assertEqual(
            sorted((a["address"], a["driver"]) for a in body["data"]),
            [("alice/code-reviewer", "webot"), ("alice/code-reviewer-2", "webot"),
             ("alice/coder", "webot"), ("alice/codex", "acpx")],
        )

    def test_password_bearer_works_and_bad_auth_is_rejected(self):
        self.assertEqual(self.client.get("/v1/agents", headers={"Authorization": "Bearer alice:pw"}).status_code, 200)
        for auth in ["Bearer alice:wrong", "Bearer :alice", ""]:
            with self.subTest(auth=auth):
                self.assertEqual(self.client.get("/v1/agents", headers={"Authorization": auth}).status_code, 401)

    def test_card_by_address_404_and_409(self):
        card = self.client.get("/v1/agents/alice/coder", headers=self.as_alice).json()
        self.assertEqual(card["display_name"], "Coder")
        self.assertEqual(self.client.get(f"/v1/agents/{card['agent_id']}", headers=self.as_alice).json()["handle"], "coder")
        self.assertEqual(self.client.get("/v1/agents/nobody", headers=self.as_alice).status_code, 404)
        self.assertEqual(self.client.get("/v1/agents/Code Reviewer", headers=self.as_alice).status_code, 409)
        self.assertEqual(self.client.get("/v1/agents/alice/coder", headers={"Authorization": f"Bearer {TOKEN}:bob"}).status_code, 404)

    def test_ask_and_deliver(self):
        asked = []

        async def fake_ask(owner, record, msg, **kwargs):
            asked.append((owner, record.handle, msg.text, msg.sender, kwargs.get("mode")))
            return AgentReply(ok=True, content="done")

        with mock.patch.object(self.gateway, "ask", fake_ask):
            body = self.client.post(
                "/v1/agents/alice/codex/messages", headers=self.as_alice, json={"text": "go", "mode": "readonly"},
            ).json()
        self.assertEqual((body["ok"], body["content"], body["agent"]["handle"]), (True, "done", "codex"))
        self.assertEqual(asked, [("alice", "codex", "go", "u:alice", "readonly")])

        with mock.patch.object(self.gateway, "deliver", mock.AsyncMock(return_value=mock.Mock(accepted=True, error=""))) as deliver:
            body = self.client.post(
                "/v1/agents/coder/messages", headers=self.as_alice, json={"text": "fyi", "deliver": True},
            ).json()
        self.assertTrue(body["accepted"])
        deliver.assert_awaited_once()

    def test_control_rejects_unknown_actions(self):
        response = self.client.post("/v1/agents/coder/control", headers=self.as_alice, json={"action": "explode"})
        self.assertEqual(response.status_code, 400)


class TestOpenAICompatibility(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = OpenAIChatService(
            internal_token=TOKEN,
            verify_password=lambda user, password: False,
            agent=None,
            extract_text=str,
            build_human_message=lambda *args: None,
        )
        self.service._gateway = AgentGateway(
            _registry(Path(self.tmp.name)), agent_base_url="http://agent.test", internal_token=TOKEN,
        )

    async def test_models_list_the_callers_agents(self):
        anonymous = self.service.list_models(None)
        self.assertEqual([m["id"] for m in anonymous["data"]], ["webot"])

        models = self.service.list_models(f"Bearer {TOKEN}:alice")
        ids = [m["id"] for m in models["data"]]
        self.assertEqual(ids[0], "webot")
        self.assertIn("alice/codex", ids)

    async def test_model_address_routes_to_that_agent(self):
        with mock.patch.object(
            self.service._gateway, "ask", mock.AsyncMock(return_value=AgentReply(ok=True, content="from codex")),
        ) as ask:
            response = await self.service.handle_chat_completions(
                ChatCompletionRequest(model="alice/codex", messages=[{"role": "user", "content": "hi"}]),
                f"Bearer {TOKEN}:alice",
            )
        self.assertEqual(response["model"], "alice/codex")
        self.assertEqual(response["choices"][0]["message"]["content"], "from codex")
        self.assertEqual(ask.await_args.args[2].text, "hi")

    async def test_plain_model_names_keep_the_webot_path(self):
        self.assertIsNone(self.service._model_agent("alice", "webot"))
        self.assertIsNone(self.service._model_agent("alice", "gpt-4o"))
        self.assertEqual(self.service._model_agent("alice", "alice/coder").binding["session"], "s1")


if __name__ == "__main__":
    unittest.main()
