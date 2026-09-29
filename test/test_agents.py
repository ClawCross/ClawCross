"""L1: every agent is one record behind one interface, whatever runtime it lives in."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import agents.store as store_module  # noqa: E402
from agents.client import AgentClient  # noqa: E402
from agents.gateway import AgentGateway, reply_channel  # noqa: E402
from agents.messages import AgentMessage, AgentReply, DeliveryReceipt  # noqa: E402
from agents.routes import create_agents_router  # noqa: E402
from agents.runtime import NO_TIMEOUT, ControlError  # noqa: E402
from agents.store import (  # noqa: E402
    ACPX,
    HTTP,
    LLM,
    OPENCLAW,
    WEBOT,
    AgentExists,
    AgentNotFound,
    AgentStore,
    driver_for_platform,
)
from external.session import runtime_session  # noqa: E402
from webot.driver import WebotRuntime  # noqa: E402

TOKEN = "tok"


def bearer(user: str) -> str:
    return f"Bearer {TOKEN}:{user}"


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = AgentStore(Path(self.tmp.name) / "agents.db")

    def webot(self, owner="alice", name="Coder", session="s1", **config):
        return self.store.create(owner, driver=WEBOT, config=config, name=name, agent_id=session)

    def codex(self, owner="alice", name="Codex"):
        return self.store.create(owner, driver=ACPX, config={"platform": "codex"}, name=name)


class TestStore(StoreCase):
    def test_every_session_is_one_agent_under_its_number(self):
        coder = self.webot()
        codex = self.codex()
        self.assertEqual(coder.agent_id, "s1")  # the session number is the agent id
        self.assertTrue(codex.agent_id.startswith("ag_") and len(codex.agent_id) == 13)  # or one is given
        self.assertEqual(codex.platform, "codex")
        self.assertEqual(self.store.update("alice", "s1", name="Builder").name, "Builder")
        self.assertEqual([a.agent_id for a in self.store.list("alice")], ["s1", codex.agent_id])

    def test_a_number_is_unique_within_its_owners_space(self):
        self.webot()
        with self.assertRaises(AgentExists):
            self.webot(name="Other")
        bobs = self.webot(owner="bob", name="Bob's")  # the same number elsewhere is another agent
        self.assertEqual((self.store.get("bob", "s1").name, self.store.get("alice", "s1").name), ("Bob's", "Coder"))
        self.assertIsNone(self.store.get("carol", bobs.agent_id))

    def test_a_number_not_seen_before_is_a_new_agent(self):
        made = self.store.ensure("alice", "fresh")
        self.assertEqual((made.agent_id, made.driver, made.name), ("fresh", WEBOT, "fresh"))
        self.assertEqual(self.store.ensure("alice", "fresh", driver=ACPX).driver, WEBOT)  # already there
        codex = self.store.ensure("alice", "cx2", driver=ACPX, config={"platform": "codex"})
        self.assertEqual(codex.platform, "codex")

    def test_numbers_are_plain(self):
        for bad in ("a.b", "u:x", "a/b", "x" * 65):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.create("alice", driver=WEBOT, agent_id=bad)
        with self.assertRaises(AgentNotFound):
            self.store.require("alice", "nobody")

    def test_driver_follows_the_platform(self):
        self.assertEqual(driver_for_platform("webot"), WEBOT)
        self.assertEqual(driver_for_platform("openclaw"), OPENCLAW)
        self.assertEqual(driver_for_platform("claude-code"), ACPX)
        self.assertEqual(driver_for_platform("some-service"), HTTP)


class _Acpx:
    """The acpx adapter as the ACP runtime uses it; ``calls`` holds each prompt's arguments."""

    def __init__(self, text: str = "ok"):
        self.calls = []
        self.text = text

    async def prompt_with_trace(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.text, messages=[], tool_uses=[], tool_results=[])


class _Http:
    """httpx.AsyncClient as the HTTP runtime uses it; ``posts`` holds (url, json, headers)."""

    posts: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).posts.append((url, json, headers))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})


class _WebotServices:
    """WeBot's services as its runtime calls them; ``delay`` makes a turn slow."""

    def __init__(self, delay: float = 0):
        self.delay = delay
        self.turns, self.system, self.deleted = [], [], []

    async def answer(self, user_id, session_id, req):
        self.turns.append((user_id, session_id, req))
        await asyncio.sleep(self.delay)
        return "ok"

    async def run(self, req):
        self.system.append(req)
        return {"status": "received"}

    async def delete(self, user_id, session_id):
        self.deleted.append((user_id, session_id))

    async def context_usage(self, user_id, session_id):
        return {"percent": 10}


def webot_runtime(services=None, engine=None):
    services = services or _WebotServices()
    return WebotRuntime(engine=engine, chat=services, system=services, sessions=services)


class TestGateway(StoreCase):
    def setUp(self):
        super().setUp()
        self.services = _WebotServices()
        self.gateway = AgentGateway(store=self.store, runtimes={WEBOT: webot_runtime(self.services)})
        self.acpx = _Acpx()
        _Http.posts = []
        for target, value in (("webot.profiles.frame_session_identity", lambda *a: "PERSONA"),
                              ("external.acpx.get_acpx_adapter", lambda *a, **k: self.acpx),
                              ("external.http.httpx.AsyncClient", _Http),
                              ("external.history._STORE", None)):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        from external import history

        history.reset_store_for_test(Path(self.tmp.name) / "history")

    def ask(self, agent, **kwargs):
        reply = asyncio.run(self.gateway.ask(agent, AgentMessage(text="hi", instructions="rules"), **kwargs))
        self.assertTrue(reply.ok, reply.error)
        return reply

    def test_webot_runs_a_turn_in_its_session_with_mode_tools_and_schema(self):
        reply_format = {"type": "json_schema", "json_schema": {"name": "Reply", "schema": {"type": "object"}}}
        reply = asyncio.run(self.gateway.ask(self.webot(llm={"model": "m1"}), AgentMessage(text="hi", instructions="rules"),
                                             mode="readonly", tools=["read_file"], response_format=reply_format,
                                             timeout=NO_TIMEOUT))
        self.assertEqual((reply.ok, reply.content), (True, "ok"))
        user, session, req = self.services.turns[0]
        self.assertEqual((user, session), ("alice", "s1"))
        self.assertEqual([(m.role, m.content) for m in req.messages], [("system", "rules"), ("user", "hi")])
        self.assertEqual((req.session_mode, req.enabled_tools), ("readonly", ["read_file"]))
        self.assertEqual(req.response_format, reply_format)  # WeBot enforces it itself
        self.assertEqual(req.llm_override, {"model": "m1"})

    def test_a_webot_turn_goes_on_when_the_caller_stops_waiting(self):
        services = _WebotServices(delay=0.2)
        gateway = AgentGateway(store=self.store, runtimes={WEBOT: webot_runtime(services)})

        async def run():
            reply = await gateway.ask(self.webot(), AgentMessage(text="hi"), timeout=0.05)
            await asyncio.sleep(0.3)
            return reply

        reply = asyncio.run(run())
        self.assertFalse(reply.ok)
        self.assertIn("no reply within", reply.error)

    def test_acpx_agent_runs_in_the_session_named_after_it(self):
        codex = self.store.create("alice", name="Codex", driver=ACPX, config={"platform": "codex", "persona": "coder"})
        other = self.codex(name="Codex 2")  # the same runtime, another session
        self.ask(codex, mode="bypass", response_format={"type": "json_schema"})
        self.ask(other)
        first, second = self.acpx.calls
        self.assertEqual((first["tool"], first["session_key"], first["prompt_text"]),
                         ("codex", f"clawcross-alice-{codex.agent_id}", "hi"))
        self.assertEqual(second["session_key"], f"clawcross-alice-{other.agent_id}")
        self.assertIn("PERSONA", first["system_prompt"])  # acpx tells a new session who it is
        self.assertIn("【群聊与私聊规则】", first["system_prompt"])  # the shared chat rules
        self.assertIn("rules", first["system_prompt"])  # and the caller's instructions
        self.assertEqual(first["permission_policy"], "approve-all")  # bypass
        self.assertIn("last_used_at", self.store.get("alice", codex.agent_id).runtime)

    def test_openclaw_uses_the_runtime_endpoint_and_its_session_key(self):
        claw = self.store.create("alice", name="Claw", driver=OPENCLAW,
                                 config={"platform": "openclaw", "global_name": "main", "api_url": "http://saved"})
        with mock.patch.dict(os.environ, {"OPENCLAW_API_URL": "http://device:18789", "OPENCLAW_GATEWAY_TOKEN": "gw"}):
            self.ask(claw)
        url, body, headers = _Http.posts[0]
        self.assertEqual(url, "http://device:18789/v1/chat/completions")
        self.assertEqual(headers["x-openclaw-session-key"], f"agent:main:clawcross-alice-{claw.agent_id}")
        self.assertEqual(headers["Authorization"], "Bearer gw")
        self.assertEqual(body["model"], "agent:main")
        self.assertNotIn("session_id", body)  # the session is the header

    def test_a_runtime_is_told_its_identity_once_and_again_when_it_changes(self):
        svc = self.store.create("alice", name="Svc", driver=HTTP, config={"platform": "svc", "api_url": "http://svc"})
        self.ask(svc)
        url, body, _headers = _Http.posts[0]
        self.assertEqual((url, body["session_id"]), ("http://svc/v1/chat/completions", f"clawcross-alice-{svc.agent_id}"))
        told = body["messages"][0]["content"]
        self.assertIn("PERSONA", told)
        self.assertTrue(told.endswith("\n\nhi"))  # the identity comes before the message
        svc = self.store.get("alice", svc.agent_id)
        self.assertIn("PERSONA", svc.runtime["identity_prompt"])
        self.ask(svc)
        self.assertEqual(_Http.posts[1][1]["messages"][0]["content"], "hi")  # already told
        svc = self.store.update("alice", svc.agent_id, config={**svc.config, "persona": "critic"})
        with mock.patch("webot.profiles.frame_session_identity", lambda *a: "CRITIC"):
            self.ask(svc)
        self.assertIn("CRITIC", _Http.posts[2][1]["messages"][0]["content"])  # told again: it changed

    def test_http_agent_without_endpoint_says_so(self):
        agent = self.store.create("alice", name="Svc", driver=HTTP, config={"platform": "svc"})
        reply = asyncio.run(self.gateway.ask(agent, AgentMessage(text="hi")))
        self.assertFalse(reply.ok)
        self.assertIn("api_url", reply.error)

    def test_a_model_call_agent_decodes_within_the_schema(self):
        critic = self.store.create("alice", driver=LLM, config={"llm": {"model": "m1"}}, name="Critic",
                                   agent_id="tmp__t__critic__1")
        reply_format = {"type": "json_schema", "json_schema": {"name": "Reply", "schema": {"type": "object"}}}
        made, schemas = {}, []

        class Model:
            def with_structured_output(self, schema):
                schemas.append(schema)
                return SimpleNamespace(ainvoke=mock.AsyncMock(return_value={"content": "x"}))

        with mock.patch("services.llm_factory.create_chat_model", lambda **kw: made.update(kw) or Model()), \
                mock.patch("webot.engine.tool_schema.forced_tool_choice_supported", return_value=True):
            reply = self.ask(critic, response_format=reply_format)
        self.assertEqual(json.loads(reply.content), {"content": "x"})
        self.assertEqual(schemas, [{"type": "object", "title": "Reply"}])
        self.assertEqual(made["model"], "m1")
        self.assertFalse(critic.remembers)

    def test_webot_is_handed_a_system_message(self):
        receipt = asyncio.run(self.gateway.trigger(self.webot(), AgentMessage(text="hello"), mode="chat",
                                                   coalesce_key="k"))
        self.assertTrue(receipt.accepted)
        req = self.services.system[0]
        self.assertEqual((req.user_id, req.session_id, req.text, req.coalesce_key), ("alice", "s1", "hello", "k"))
        self.assertEqual(req.enabled_tools, [])  # chat mode: no tools
        self.assertEqual(req.inbox_source_session, "")

    def test_inbox_queues_for_webot_and_sends_to_others(self):
        receipt = asyncio.run(self.gateway.inbox(
            self.webot(), AgentMessage(text="later", sender="u:alice", summary="群聊「Dev」 alice: later"),
            mode="bypass"))
        self.assertTrue(receipt.accepted)
        req = self.services.system[0]
        self.assertEqual((req.session_id, req.text, req.inbox_source_session, req.inbox_summary),
                         ("s1", "later", "u:alice", "群聊「Dev」 alice: later"))
        self.assertIsNone(req.session_mode)  # WeBot runs it in the session's own mode

        async def run():
            await self.gateway.inbox(self.codex(), AgentMessage(text="later"), mode="readonly")
            await asyncio.gather(*self.gateway.runtimes[ACPX]._background)

        asyncio.run(run())
        self.assertEqual(self.acpx.calls[0]["prompt_text"], "later")  # handed over at once,
        self.assertEqual(self.acpx.calls[0]["non_interactive_permissions"], "deny")  # in the mode it was sent in

    def test_an_external_trigger_is_sent_in_the_background_and_reports_back(self):
        codex = self.codex()
        replies = []

        self.acpx.text = "done"

        async def run():
            receipt = await self.gateway.trigger(codex, AgentMessage(text="go"), on_complete=replies.append)
            await asyncio.gather(*self.gateway.runtimes[ACPX]._background)
            return receipt

        self.assertTrue(asyncio.run(run()).accepted)
        self.assertEqual(replies[0].content, "done")

    def test_destroying_a_webot_agent_deletes_its_session(self):
        asyncio.run(self.gateway.destroy(self.webot()))
        self.assertEqual(self.services.deleted, [("alice", "s1")])

    def test_reply_channel_depends_on_the_runtime(self):
        self.assertIn('send_to_group(group_id="g_1"', reply_channel(self.webot(), "g_1"))
        codex = self.codex()
        self.assertIn(f"groups send --group-id g_1 --agent {codex.agent_id}", reply_channel(codex, "g_1"))


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
        self.control = AgentGateway(store=self.store, runtimes={WEBOT: webot_runtime(engine=self.webot_runtime)})

    def test_webot_status_cancel_and_reset(self):
        coder = self.webot()
        status = asyncio.run(self.control.status(coder))
        self.assertEqual((status["state"], status["pending"], status["context"]), ("running", 2, {"percent": 10}))
        self.assertEqual(status["actions"], ["status", "cancel", "reset"])
        self.assertTrue(self.control.is_busy(coder))
        self.assertEqual(asyncio.run(self.control.control(coder, "cancel")), {"cancelled": True})
        self.assertEqual(asyncio.run(self.control.control(coder, "reset")), {"reset": True})
        self.assertEqual(self.webot_runtime.cancelled, ["alice#s1", "alice#s1"])

    def test_webot_is_reached_only_where_it_runs(self):
        with self.assertRaises(ControlError):
            asyncio.run(AgentGateway(store=self.store).control(self.webot(), "cancel"))

    def test_history_reads_the_agents_own_conversation(self):
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
        from external import history as external_agent_history

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

        codex = self.codex()
        key = runtime_session(codex)
        patcher = mock.patch.object(external_agent_history, "_STORE", None)
        patcher.start()
        self.addCleanup(patcher.stop)
        history = external_agent_history.reset_store_for_test(Path(self.tmp.name) / "history")

        async def talk():
            rid = await history.record_send(platform="codex", session_key=key, connect_type="acp",
                                            prompt="ping", options={})
            await history.record_recv(platform="codex", session_key=key, connect_type="acp",
                                      request_id=rid, ok=True, content="pong", raw_response=None, error=None, options={})
            return await self.control.history(codex)

        self.assertEqual([(m["role"], m["content"]) for m in asyncio.run(talk())], [("user", "ping"), ("assistant", "pong")])
        acpx = mock.Mock(close_session=mock.AsyncMock(), to_acpx_session_name=mock.Mock(return_value="n"))
        with mock.patch("external.acp.adapter", return_value=acpx):
            asyncio.run(self.control.destroy(codex))  # deleting the agent deletes its history
        acpx.close_session.assert_awaited_once()
        self.assertEqual(asyncio.run(self.control.history(codex)), [])

    def test_http_agents_cannot_be_cancelled_and_reset_makes_them_start_over(self):
        agent = self.store.create("alice", name="Svc", driver=HTTP, config={"platform": "svc"})
        with self.assertRaises(ControlError):
            asyncio.run(self.control.control(agent, "cancel"))
        self.assertEqual(asyncio.run(self.control.status(agent))["state"], "idle")
        self.store.set_runtime("alice", agent.agent_id, {"identity_prompt": "P", "last_used_at": 1.0})
        self.assertEqual(asyncio.run(self.control.status(self.store.get("alice", agent.agent_id)))["state"], "online")
        self.assertEqual(asyncio.run(self.control.control(agent, "reset")), {"reset": True})
        self.assertEqual(self.store.get("alice", agent.agent_id).runtime, {})


class ApiCase(StoreCase):
    def setUp(self):
        super().setUp()
        self.gateway = AgentGateway(store=self.store, runtimes={WEBOT: webot_runtime(engine=_FakeWebot())})
        self.gateway.ask = mock.AsyncMock(return_value=AgentReply(ok=True, content="pong"))
        self.gateway.inbox = mock.AsyncMock(return_value=DeliveryReceipt(accepted=True))
        self.forgotten = []
        names = {"dev.Critic": "s1"}
        app = FastAPI()
        app.include_router(create_agents_router(
            internal_token=TOKEN, verify_password=lambda u, p: (u, p) == ("alice", "pw"),
            store=self.store, gateway=self.gateway,
            names=lambda owner, ref: self.store.get(owner, names[ref]) if ref in names else None,
            on_delete=[lambda a: self.forgotten.append(a.agent_id)],
        ))
        self.client = TestClient(app)

    def call(self, method, path, user="alice", **kwargs):
        return self.client.request(method, path, headers={"Authorization": bearer(user)}, **kwargs)


class TestAgentsApi(ApiCase):

    def test_create_list_update_and_delete_any_platform(self):
        webot = self.call("POST", "/v1/agents", json={"name": "Coder", "persona": "coder"}).json()
        codex = self.call("POST", "/v1/agents", json={
            "name": "Codex", "platform": "codex", "global_name": "cx", "api_key": "secret",
        }).json()
        self.assertEqual((webot["platform"], codex["platform"]), ("webot", "codex"))
        self.assertNotIn("api_key", codex["settings"])  # secrets never leave
        self.assertTrue(codex["settings"]["has_api_key"])

        listed = self.call("GET", "/v1/agents?status=1").json()["data"]
        self.assertEqual([a["agent_id"] for a in listed], [webot["agent_id"], codex["agent_id"]])
        self.assertEqual(listed[0]["status"]["state"], "idle")

        ref = codex["agent_id"]
        patched = self.call("PATCH", f"/v1/agents/{ref}", json={"name": "Codex 2", "settings": {"model": "o4"}})
        self.assertEqual(patched.json()["settings"]["model"], "o4")
        self.assertTrue(self.store.get("alice", ref).config["api_key"])  # kept
        self.assertEqual(self.call("PATCH", f"/v1/agents/{webot['agent_id']}",
                                   json={"settings": {"api_url": "x"}}).status_code, 400)
        self.assertEqual(self.call("PATCH", f"/v1/agents/{webot['agent_id']}",
                                   json={"settings": {"teams": ["dev"]}}).status_code, 400)  # only teams set it

        self.assertEqual(self.call("DELETE", f"/v1/agents/{ref}").status_code, 200)
        self.assertIsNone(self.store.get("alice", ref))
        self.assertEqual(self.forgotten, [ref])  # what else holds its id lets it go

    def test_a_number_is_made_once(self):
        self.assertEqual(self.call("POST", "/v1/agents", json={"agent_id": "main"}).status_code, 200)
        self.assertEqual(self.call("POST", "/v1/agents", json={"agent_id": "main"}).status_code, 409)
        self.assertEqual(self.call("POST", "/v1/agents", json={"agent_id": "a.b"}).status_code, 400)

    def test_sending_to_a_new_number_makes_that_agent(self):
        reply = self.call("POST", "/v1/agents/brand-new/messages", json={"text": "hi"}).json()
        self.assertEqual((reply["agent"]["agent_id"], reply["agent"]["platform"]), ("brand-new", "webot"))
        queued = self.call("POST", "/v1/agents/cx-7/inbox", json={"text": "later", "platform": "codex"}).json()
        self.assertEqual((queued["agent"]["platform"], queued["accepted"]), ("codex", True))
        self.assertEqual(self.gateway.inbox.await_args.args[0].agent_id, "cx-7")
        self.assertEqual(self.call("POST", "/v1/agents/svc-1/messages",
                                   json={"text": "hi", "platform": "some-service"}).status_code, 400)  # needs an endpoint
        self.assertEqual(self.call("GET", "/v1/agents/never-sent").status_code, 404)  # reading makes nothing

    def test_auth_messages_and_control(self):
        self.webot()
        self.assertEqual(self.client.get("/v1/agents", headers={"Authorization": "Bearer alice:pw"}).status_code, 200)
        self.assertEqual(self.client.get("/v1/agents", headers={"Authorization": "Bearer alice:no"}).status_code, 401)
        self.assertEqual(self.call("GET", "/v1/agents/s1", user="bob").status_code, 404)  # not in bob's space

        reply = self.call("POST", "/v1/agents/dev.Critic/messages", json={"text": "ping"}).json()  # a team name
        self.assertEqual((reply["content"], reply["agent"]["agent_id"]), ("pong", "s1"))
        self.assertEqual(self.gateway.ask.await_args.args[1].text, "ping")
        self.assertEqual(self.call("POST", "/v1/agents/s1/control", json={"action": "explode"}).status_code, 400)
        self.assertEqual(self.call("POST", "/v1/agents/s1/control", json={"action": "cancel"}).json()["cancelled"], True)


class TestAgentClient(ApiCase):
    """Another process reaches agents over the entrances: ``agents.client`` against the router."""

    def client_for(self, owner="alice"):
        import httpx

        transport, real = httpx.ASGITransport(app=self.client.app), httpx.AsyncClient
        patcher = mock.patch("agents.client.httpx.AsyncClient",
                             lambda timeout=None: real(transport=transport, timeout=timeout))
        patcher.start()
        self.addCleanup(patcher.stop)
        return AgentClient(owner, base_url="http://agent.test", internal_token=TOKEN)

    def test_make_ask_and_delete_a_temporary_agent(self):
        class Reply(BaseModel):
            content: str

        client = self.client_for()

        async def run():
            made = await client.create(agent_id="tmp__t__c__1", name="Critic", platform="llm", llm={"model": "m1"})
            again = await client.create(agent_id="tmp__t__c__1", platform="llm")  # already there: that agent
            reply = await client.ask("tmp__t__c__1", AgentMessage(text="hi", instructions="rules"),
                                     response_format=Reply, timeout=NO_TIMEOUT)
            return made, again, reply, await client.delete("tmp__t__c__1"), await client.delete("tmp__t__c__1")

        made, again, reply, deleted, twice = asyncio.run(run())
        self.assertEqual((made["platform"], again["agent_id"], reply.content), ("llm", "tmp__t__c__1", "pong"))
        agent, msg = self.gateway.ask.await_args.args
        kwargs = self.gateway.ask.await_args.kwargs
        self.assertEqual((agent.driver, agent.config["llm"], msg.instructions), (LLM, {"model": "m1"}, "rules"))
        self.assertEqual((kwargs["timeout"], kwargs["response_format"]["json_schema"]["name"]), (NO_TIMEOUT, "Reply"))
        self.assertEqual((deleted, twice), (True, False))
        self.assertIsNone(self.store.get("alice", "tmp__t__c__1"))


class TestOpenAIRouting(StoreCase):
    def setUp(self):
        super().setUp()
        from webot.api.openai_service import OpenAIChatService

        from teams.store import TeamStore

        self.webot()
        self.store.create("alice", driver=ACPX, config={"platform": "codex"}, name="Codex", agent_id="cx")
        teams = TeamStore(self.store, Path(self.tmp.name) / "user_files")
        for target, value in (("agents.store.get_store", lambda *a: self.store),
                              ("teams.store.get_team_store", lambda *a: teams)):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = OpenAIChatService(internal_token=TOKEN, verify_password=lambda u, p: False, agent=mock.Mock(),
                                         extract_text=str, build_human_message=mock.Mock())

    def test_the_session_is_the_agent_and_a_new_one_is_made_with_the_named_runtime(self):
        from fastapi import HTTPException

        self.assertEqual(self.service._target("alice", "s1", "anything").agent_id, "s1")
        self.assertEqual(self.service._target("alice", "cx", None).platform, "codex")
        made = self.service._target("alice", "new-1", "claude")
        self.assertEqual((made.agent_id, made.driver, made.platform), ("new-1", ACPX, "claude"))
        self.assertEqual(self.service._target("alice", "new-2", None).driver, WEBOT)
        self.assertEqual(self.service._target("alice", "new-3", "gpt-4o").driver, WEBOT)  # names no runtime
        with self.assertRaises(HTTPException):
            self.service._target("alice", "a.b", None)  # not a number, not a team name

    def test_models_are_the_runtimes(self):
        ids = [m["id"] for m in self.service.list_models(bearer("alice"))["data"]]
        self.assertEqual(ids[0], "webot")
        self.assertIn("openclaw", ids)

    def test_a_webot_session_is_its_agent_with_its_own_persona_and_tools(self):
        from webot.engine.agent import TeamAgent

        webot = TeamAgent.__new__(TeamAgent)
        self.webot(session="s2", name="Reviewer", persona="你是严谨的审稿人。", tools=["read_file"])
        self.assertEqual(webot._find_internal_session_meta("alice", "s2")["tools"], ["read_file"])
        prompt = webot._get_internal_session_persona_prompt("alice", "s2")
        self.assertIn("你是严谨的审稿人。", prompt)
        self.assertIn("Reviewer", prompt)
        self.assertEqual(webot._get_internal_session_persona_prompt("alice", "s1"), "")  # no persona
        self.assertIsNone(webot._find_internal_session_meta("alice", "s1")["tools"])  # all tools


if __name__ == "__main__":
    unittest.main()
