"""L1: every agent is one record behind one interface, whatever runtime it lives in."""

import asyncio
import json
import os
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
from pydantic import BaseModel  # noqa: E402

import agents.store as store_module  # noqa: E402
from agents.control import AgentControl, ControlError  # noqa: E402
from agents.gateway import (  # noqa: E402
    NO_TIMEOUT,
    AgentGateway,
    persona_agent,
    reply_channel,
    temp_session_agent,
)
from agents.messages import AgentMessage, AgentReply  # noqa: E402
from agents.routes import create_agents_router  # noqa: E402
from agents.store import (  # noqa: E402
    ACPX,
    HTTP,
    OPENCLAW,
    WEBOT,
    AgentExists,
    AgentNotFound,
    AgentStore,
    driver_for_platform,
)
from integrations.base import SendToAgentResult  # noqa: E402

TOKEN = "tok"


def bearer(user: str) -> str:
    return f"Bearer {TOKEN}:{user}"


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = AgentStore(Path(self.tmp.name) / "clawcross.db")

    def webot(self, owner="alice", name="Coder", session="s1", **config):
        return self.store.create(owner, name=name, driver=WEBOT, config={"session": session, **config})


class TestStore(StoreCase):
    def test_one_record_per_agent_with_a_stable_id_and_address(self):
        coder = self.webot()
        codex = self.store.create("alice", name="Codex", driver=ACPX, config={"platform": "codex", "global_name": "cx"})

        self.assertTrue(coder.agent_id.startswith("ag_") and len(coder.agent_id) == 13)
        self.assertEqual((coder.address, codex.address), ("alice/coder", "alice/codex"))
        self.assertEqual(codex.platform, "codex")
        self.assertEqual(self.store.update(coder.agent_id, name="Builder").agent_id, coder.agent_id)
        self.assertEqual(self.store.get(coder.agent_id).handle, "coder")  # renaming keeps the address

    def test_every_reference_form_finds_the_agent_and_other_users_are_out_of_reach(self):
        coder = self.webot()
        bob = self.webot(owner="bob", session="b1")
        for ref in (coder.agent_id, "alice/coder", "coder", "@coder"):
            with self.subTest(ref=ref):
                self.assertEqual(self.store.resolve("alice", ref).agent_id, coder.agent_id)
        for ref in (bob.agent_id, "bob/coder", "nobody"):
            with self.subTest(ref=ref), self.assertRaises(AgentNotFound):
                self.store.resolve("alice", ref)

    def test_a_runtime_belongs_to_one_agent(self):
        coder = self.webot()
        with self.assertRaises(AgentExists) as ctx:
            self.webot(name="Other")
        self.assertEqual(ctx.exception.agent.agent_id, coder.agent_id)
        self.assertEqual(self.store.find("alice", WEBOT, {"session": "s1"}).agent_id, coder.agent_id)
        self.assertIsNone(self.store.find("bob", WEBOT, {"session": "s1"}))

    def test_same_names_get_distinct_handles(self):
        first, second = self.webot(session="a"), self.webot(session="b")
        self.assertEqual((first.handle, second.handle), ("coder", "coder-2"))
        cjk = self.webot(name="搜索指挥者", session="c")
        self.assertEqual(cjk.handle, "agent")

    def test_driver_follows_the_platform(self):
        self.assertEqual(driver_for_platform("webot"), WEBOT)
        self.assertEqual(driver_for_platform("openclaw"), OPENCLAW)
        self.assertEqual(driver_for_platform("claude-code"), ACPX)
        self.assertEqual(driver_for_platform("some-service"), HTTP)


def _sent(result: str = "ok"):
    """Patch the transport; the mock records every request the gateway builds."""
    return mock.patch("integrations.agent_sender.send_to_agent",
                      mock.AsyncMock(return_value=SendToAgentResult(ok=True, content=result)))


class _FakeResponse:
    status_code = 200
    text = ""


def _http(status: int = 200):
    """Patch httpx.AsyncClient; ``calls`` collects (url, json)."""
    calls = []

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):
            calls.append((url, json))
            response = _FakeResponse()
            response.status_code = status
            return response

    return mock.patch("agents.gateway.httpx.AsyncClient", Client), calls


class TestGateway(StoreCase):
    def setUp(self):
        super().setUp()
        self.gateway = AgentGateway(agent_base_url="http://agent.test", internal_token=TOKEN,
                                    runtime_db_path=self.store.db_path)
        patcher = mock.patch("integrations.external_persona.build_external_persona_prompt", return_value="PERSONA")
        patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, agent, **kwargs):
        with _sent() as send:
            reply = asyncio.run(self.gateway.ask(agent, AgentMessage(text="hi", instructions="rules"), **kwargs))
        self.assertTrue(reply.ok, reply.error)
        return send.await_args.args[0]

    def test_webot_is_asked_in_its_session_with_mode_tools_and_schema(self):
        class Reply(BaseModel):
            content: str

        request = self.ask(self.webot(), mode="readonly", tools=["read_file"], response_format=Reply,
                           timeout=NO_TIMEOUT)
        body = request.options["body"]
        self.assertEqual(request.session, "s1")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "rules"})
        self.assertEqual((body["session_mode"], body["enabled_tools"]), ("readonly", ["read_file"]))
        self.assertEqual(body["response_format"]["json_schema"]["name"], "Reply")
        self.assertIsNone(request.options["timeout"])
        self.assertEqual(request.options["headers"]["Authorization"], bearer("alice"))

    def test_acpx_agent_runs_in_its_named_session_without_a_reply_schema(self):
        codex = self.store.create("alice", name="Codex", driver=ACPX,
                                  config={"platform": "codex", "global_name": "cx", "persona": "coder"})
        request = self.ask(codex, mode="bypass", response_format={"type": "json_schema"})
        self.assertEqual((request.connect_type, request.platform, request.session), ("acp", "codex", "agent:cx:clawcrosschat"))
        self.assertIn("PERSONA", request.options["identity_prompt"])
        self.assertIn("【群聊与私聊规则】", request.options["identity_prompt"])  # the shared chat rules
        self.assertEqual(request.options["runtime_db_path"], self.store.db_path)

    def test_openclaw_uses_the_runtime_endpoint_and_its_session_key(self):
        claw = self.store.create("alice", name="Claw", driver=OPENCLAW,
                                 config={"platform": "openclaw", "global_name": "main", "api_url": "http://saved"})
        with mock.patch.dict(os.environ, {"OPENCLAW_API_URL": "http://device:18789", "OPENCLAW_GATEWAY_TOKEN": "gw"}):
            request = self.ask(claw)
        self.assertEqual(request.options["api_url"], "http://device:18789/v1/chat/completions")
        self.assertEqual(request.options["headers"]["x-openclaw-session-key"], "agent:main:clawcrosschat")
        self.assertEqual(request.options["body"]["model"], "agent:main")

    def test_http_agent_without_endpoint_says_so(self):
        agent = self.store.create("alice", name="Svc", driver=HTTP, config={"platform": "svc", "global_name": "svc"})
        reply = asyncio.run(self.gateway.ask(agent, AgentMessage(text="hi")))
        self.assertFalse(reply.ok)
        self.assertIn("api_url", reply.error)

    def test_persona_call_takes_the_pydantic_model_itself(self):
        class Reply(BaseModel):
            content: str

        request = self.ask(persona_agent("alice", "Critic", llm={"model": "m1"}), response_format=Reply)
        self.assertEqual(request.platform, "temp")
        self.assertIs(request.options["response_schema"], Reply)
        self.assertEqual(request.options["model"], "m1")

    def test_webot_delivery_goes_to_its_inbox(self):
        patcher, calls = _http()
        with patcher:
            receipt = asyncio.run(self.gateway.deliver(self.webot(), AgentMessage(text="hello"), mode="chat",
                                                       coalesce_key="k"))
        self.assertTrue(receipt.accepted)
        url, body = calls[0]
        self.assertEqual(url, "http://agent.test/system_trigger")
        self.assertEqual((body["session_id"], body["text"], body["coalesce_key"]), ("s1", "hello", "k"))
        self.assertEqual(body["enabled_tools"], [])  # chat mode: no tools

    def test_external_delivery_sends_in_the_background_and_reports_back(self):
        codex = self.store.create("alice", name="Codex", driver=ACPX, config={"platform": "codex", "global_name": "cx"})
        replies = []

        async def run():
            with _sent("done"):
                receipt = await self.gateway.deliver(codex, AgentMessage(text="go"), on_complete=replies.append)
                await asyncio.gather(*self.gateway._background)
            return receipt

        self.assertTrue(asyncio.run(run()).accepted)
        self.assertEqual(replies[0].content, "done")

    def test_only_temporary_sessions_are_discarded(self):
        with self.assertRaises(ValueError):
            temp_session_agent("alice", "x", "s1")
        with self.assertRaises(ValueError):
            asyncio.run(self.gateway.discard(self.webot()))
        patcher, calls = _http()
        with patcher:
            self.assertTrue(asyncio.run(self.gateway.discard(temp_session_agent("alice", "x", "tmp__t__x__1"))))
        self.assertEqual(calls[0], ("http://agent.test/delete_session", {"user_id": "alice", "session_id": "tmp__t__x__1"}))

    def test_reply_channel_depends_on_the_runtime(self):
        self.assertIn('send_to_group(group_id="g_1"', reply_channel(self.webot(), "g_1"))
        codex = self.store.create("alice", name="Codex", driver=ACPX, config={"platform": "codex", "global_name": "cx"})
        self.assertIn("groups send --group-id g_1 --agent alice/codex", reply_channel(codex, "g_1"))


class _FakeWebot:
    def __init__(self):
        self.cancelled = []

    def get_all_thread_status(self, prefix):
        return {"alice#s1": {"busy": True, "pending_system": 2}}

    def list_active_task_keys(self, prefix):
        return []

    def get_thread_context_usage(self, thread):
        return {"percent": 10}

    async def cancel_task(self, thread):
        self.cancelled.append(thread)
        return True


class TestControl(StoreCase):
    def setUp(self):
        super().setUp()
        self.webot_runtime = _FakeWebot()
        self.control = AgentControl(self.webot_runtime, runtime_db_path=self.store.db_path)

    def test_webot_status_cancel_and_reset(self):
        coder = self.webot()
        status = asyncio.run(self.control.status(coder))
        self.assertEqual((status["state"], status["pending"], status["context"]), ("running", 2, {"percent": 10}))
        self.assertTrue(self.control.is_busy(coder))
        self.assertEqual(asyncio.run(self.control.run(coder, "cancel")), {"cancelled": True})
        self.assertEqual(asyncio.run(self.control.run(coder, "reset")), {"reset": True})
        self.assertEqual(self.webot_runtime.cancelled, ["alice#s1", "alice#s1"])

    def test_history_reads_the_agents_own_conversation(self):
        from types import SimpleNamespace

        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
        from utils import external_agent_history

        self.webot_runtime.agent_app = mock.Mock()
        self.webot_runtime.agent_app.aget_state = mock.AsyncMock(return_value=SimpleNamespace(values={"messages": [
            HumanMessage("hi"),
            AIMessage("", tool_calls=[{"name": "read_file", "args": {"path": "a"}, "id": "c1"}]),
            ToolMessage("text", tool_call_id="c1", name="read_file"),
            AIMessage("done"),
        ]}))
        self.assertEqual(asyncio.run(self.control.history(self.webot())), [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"name": "read_file", "args": {"path": "a"}}]},
            {"role": "tool", "content": "text", "tool_name": "read_file"},
            {"role": "assistant", "content": "done"},
        ])

        codex = self.store.create("alice", name="Codex", driver=ACPX, config={"platform": "codex", "global_name": "cx"})
        patcher = mock.patch.object(external_agent_history, "_STORE", None)
        patcher.start()
        self.addCleanup(patcher.stop)
        history = external_agent_history.reset_store_for_test(Path(self.tmp.name) / "history")

        async def talk():
            rid = await history.record_send(platform="codex", session_key="agent:cx:clawcrosschat", connect_type="acp",
                                            prompt="ping", options={})
            await history.record_recv(platform="codex", session_key="agent:cx:clawcrosschat", connect_type="acp",
                                      request_id=rid, ok=True, content="pong", raw_response=None, error=None, options={})
            return await self.control.history(codex)

        self.assertEqual([(m["role"], m["content"]) for m in asyncio.run(talk())], [("user", "ping"), ("assistant", "pong")])

    def test_http_agents_cannot_be_cancelled(self):
        agent = self.store.create("alice", name="Svc", driver=HTTP, config={"platform": "svc", "global_name": "svc"})
        with self.assertRaises(ControlError):
            asyncio.run(self.control.run(agent, "cancel"))
        self.assertEqual(asyncio.run(self.control.status(agent))["state"], "idle")


class TestAgentsApi(StoreCase):
    def setUp(self):
        super().setUp()
        self.gateway = mock.Mock(spec=AgentGateway)
        self.gateway.ask = mock.AsyncMock(return_value=AgentReply(ok=True, content="pong"))
        self.control = AgentControl(_FakeWebot(), runtime_db_path=self.store.db_path)
        app = FastAPI()
        app.include_router(create_agents_router(
            internal_token=TOKEN, verify_password=lambda u, p: (u, p) == ("alice", "pw"),
            store=self.store, gateway=self.gateway, control=self.control,
        ))
        self.client = TestClient(app)

    def call(self, method, path, user="alice", **kwargs):
        return self.client.request(method, path, headers={"Authorization": bearer(user)}, **kwargs)

    def test_create_list_update_and_delete_any_platform(self):
        webot = self.call("POST", "/v1/agents", json={"name": "Coder", "persona": "coder"}).json()
        codex = self.call("POST", "/v1/agents", json={
            "name": "Codex", "platform": "codex", "global_name": "cx", "api_key": "secret",
        }).json()
        self.assertEqual((webot["platform"], codex["platform"]), ("webot", "codex"))
        self.assertNotIn("api_key", codex["settings"])  # secrets never leave
        self.assertTrue(codex["settings"]["has_api_key"])

        listed = self.call("GET", "/v1/agents?status=1").json()["data"]
        self.assertEqual([a["address"] for a in listed], ["alice/coder", "alice/codex"])
        self.assertEqual(listed[0]["status"]["state"], "idle")

        patched = self.call("PATCH", "/v1/agents/alice/codex", json={"name": "Codex 2", "settings": {"model": "o4"}})
        self.assertEqual(patched.json()["settings"]["model"], "o4")
        self.assertTrue(self.store.get(codex["agent_id"]).config["api_key"])  # kept
        self.assertEqual(self.call("PATCH", "/v1/agents/coder", json={"settings": {"api_url": "x"}}).status_code, 400)

        self.assertEqual(self.call("DELETE", f"/v1/agents/{codex['agent_id']}").status_code, 200)
        self.assertIsNone(self.store.get(codex["agent_id"]))

    def test_a_runtime_is_registered_once(self):
        self.call("POST", "/v1/agents", json={"name": "Main", "session": "default"})
        again = self.call("POST", "/v1/agents", json={"name": "Again", "session": "default"})
        self.assertEqual(again.status_code, 409)
        found = self.call("GET", "/v1/agents?runtime=webot:default").json()["data"]
        self.assertEqual([a["name"] for a in found], ["Main"])

    def test_auth_messages_and_control(self):
        self.webot()
        self.assertEqual(self.client.get("/v1/agents", headers={"Authorization": "Bearer alice:pw"}).status_code, 200)
        self.assertEqual(self.client.get("/v1/agents", headers={"Authorization": "Bearer alice:no"}).status_code, 401)
        self.assertEqual(self.call("GET", "/v1/agents/coder", user="bob").status_code, 404)

        reply = self.call("POST", "/v1/agents/coder/messages", json={"text": "ping"}).json()
        self.assertEqual(reply["content"], "pong")
        self.assertEqual(self.gateway.ask.await_args.args[1].text, "ping")
        self.assertEqual(self.call("POST", "/v1/agents/coder/control", json={"action": "explode"}).status_code, 400)
        self.assertEqual(self.call("POST", "/v1/agents/coder/control", json={"action": "cancel"}).json()["cancelled"], True)


class TestOpenAIRouting(StoreCase):
    def setUp(self):
        super().setUp()
        from api.openai_service import OpenAIChatService

        self.webot()
        self.store.create("alice", name="Codex", driver=ACPX, config={"platform": "codex", "global_name": "cx"})
        for target, value in (("agents.store.get_store", lambda *a: self.store),):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = OpenAIChatService(internal_token=TOKEN, verify_password=lambda u, p: False, agent=mock.Mock(),
                                         extract_text=str, build_human_message=mock.Mock())

    def test_model_names_an_agent_or_keeps_the_webot_path(self):
        self.assertIsNone(self.service._model_agent("alice", "webot"))
        self.assertIsNone(self.service._model_agent("alice", "gpt-4o"))
        self.assertEqual(self.service._model_agent("alice", "alice/coder").config["session"], "s1")

    def test_models_list_the_callers_agents(self):
        from teams.store import TeamStore

        with mock.patch("teams.store.get_team_store", lambda *a: TeamStore(self.store, Path(self.tmp.name))):
            ids = [m["id"] for m in self.service.list_models(bearer("alice"))["data"]]
        self.assertEqual(ids[0], "webot")
        self.assertIn("alice/codex", ids)

    def test_tool_whitelist_comes_from_the_agent(self):
        from api.openai_service import _get_agent_tool_whitelist

        self.store.create("alice", name="Limited", driver=WEBOT, config={"session": "s2", "tools": {"read_file": True}})
        self.assertEqual(_get_agent_tool_whitelist("alice", "s2"), {"read_file"})
        self.assertIsNone(_get_agent_tool_whitelist("alice", "s1"))


if __name__ == "__main__":
    unittest.main()
