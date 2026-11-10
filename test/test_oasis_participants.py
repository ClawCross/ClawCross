"""OASIS participants are agents: residents named with ``agent:``, temporary ones with
``persona:``; all of them are asked by id over the agent layer's entrances."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src" / "backend"
for path in (str(PROJECT_ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from agents.runtime import NO_TIMEOUT  # noqa: E402
from agents.messages import AgentReply  # noqa: E402
from agents.store import ACPX, WEBOT, AgentStore  # noqa: E402
from oasis.engine import DiscussionEngine  # noqa: E402
from oasis.forum import DiscussionForum  # noqa: E402
from oasis.participants import Participant  # noqa: E402
from oasis.schemas import OasisReplyOut  # noqa: E402
from oasis.scheduler import parse_schedule, participant_ref  # noqa: E402
from teams.store import TeamStore  # noqa: E402

REPLY = json.dumps({"clawcross_type": "oasis reply", "reply_to": None, "content": "ok", "votes": []})


class _Client:
    """The agent layer as OASIS reaches it: records what is made, asked and deleted."""

    def __init__(self, replies=None):
        self.asks = []
        self.made = []
        self.deleted = []
        self.replies = list(replies or [])

    async def create(self, **fields):
        self.made.append(fields)
        return fields

    async def ask(self, ref, msg, **kwargs):
        self.asks.append({"agent": ref, "msg": msg, **kwargs})
        return AgentReply(ok=True, content=self.replies.pop(0) if self.replies else REPLY)

    async def delete(self, ref):
        self.deleted.append(ref)
        return True


class TestYamlForms(unittest.TestCase):
    def test_participant_forms(self):
        self.assertEqual(participant_ref({"agent": "@coder"}), ("agent:coder", {}))
        self.assertEqual(participant_ref({"persona": "critical"}), ("persona:critical:1", {}))
        self.assertEqual(participant_ref({"persona": "critical", "tools": "none", "instance": 2}), ("persona:critical:2", {}))
        self.assertEqual(participant_ref({"persona": "critical", "tools": "all"}), ("persona:critical:1", {"tools": "all"}))
        self.assertEqual(participant_ref({"persona": "c", "tools": ["read_file"]}), ("persona:c:1", {"tools": ["read_file"]}))
        with self.assertRaises(ValueError):
            participant_ref({"persona": "critical", "tools": 3})
        with self.assertRaisesRegex(ValueError, "no longer supported"):
            participant_ref({"expert": "creative#temp#1"})

    def test_parallel_steps_take_the_same_forms(self):
        schedule = parse_schedule("version: 2\nrepeat: false\nplan:\n  - id: b\n    parallel:\n"
                                  "      - persona: critical\n        tools: [read_file]\n      - agent: alice/codex\n")
        node = schedule.nodes[0]
        self.assertEqual(node.expert_names, ["persona:critical:1", "agent:alice/codex"])
        self.assertEqual(node.participant_configs, {"persona:critical:1": {"tools": ["read_file"]}})

    def test_visual_layout_round_trips_the_forms(self):
        from oasis.layout import yaml_to_layout
        from frontend.visual import layout_to_yaml

        layout = yaml_to_layout(
            "version: 2\nrepeat: false\nplan:\n  - id: a\n    agent: Coder\n  - id: b\n    persona: critical\n"
            "    tools: all\nedges:\n  - [a, b]\n")
        self.assertEqual([n["type"] for n in layout["nodes"]], ["agent", "persona"])
        steps = [s for s in parse_schedule(layout_to_yaml(layout)).nodes]
        self.assertEqual([s.expert_names for s in steps], [["agent:Coder"], ["persona:critical:1"]])


class EngineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = AgentStore(root / "agents.db")
        self.teams = TeamStore(self.store, root / "user_files")
        self.teams.create("alice", "dev")
        self.coder = self.store.create("alice", driver=WEBOT, config={"persona": "coder"}, name="Coder", agent_id="s1")
        self.codex = self.store.create("alice", driver=ACPX, config={"platform": "codex"}, name="Codex", agent_id="codex")
        self.teams.add("alice", "dev", self.coder.agent_id, role="Builder")
        self.fake = _Client()
        for target, value in (("agents.store.get_store", lambda *a: self.store),
                              ("teams.store.get_team_store", lambda *a: self.teams),
                              ("oasis.engine.create_chat_model", mock.MagicMock())):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        import oasis.agent_center as agent_center
        for module in ("oasis.participants", "oasis.agent_center"):
            patcher = mock.patch(f"{module}.AgentClient", lambda owner: self.fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("get_store", lambda *a: self.store), ("get_team_store", lambda *a: self.teams)):
            patcher = mock.patch.object(agent_center, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def engine(self, plan: str) -> DiscussionEngine:
        return DiscussionEngine(forum=DiscussionForum("t0pic", "问题", "alice"),
                                schedule_yaml=f"version: 2\nrepeat: false\nplan:\n{plan}",
                                user_id="alice", team="dev", discussion=False)


class TestParticipants(EngineCase):
    def test_summary_model_is_created_only_when_summarizing(self):
        built = self.engine("  - id: a\n    manual:\n      author: host\n      content: hello\n")
        model = mock.Mock()
        model.ainvoke = mock.AsyncMock(return_value=mock.Mock(content="SUMMARY_OK"))
        with mock.patch("oasis.engine.create_chat_model", return_value=model) as factory:
            self.assertIsNone(built.summarizer)

            async def summarize():
                await built.forum.publish(author="host", content="hello")
                self.assertEqual(await built._summarize(), "SUMMARY_OK")
                self.assertEqual(await built._summarize(), "SUMMARY_OK")

            asyncio.run(summarize())
            factory.assert_called_once()

    def test_agents_are_found_by_team_name_or_id_and_a_new_id_is_a_new_agent(self):
        built = self.engine("  - id: a\n    agent: Builder\n  - id: b\n    agent: codex\n  - id: c\n    agent: dev.Nobody\n"
                            "  - id: d\n    agent: fresh\n")
        self.assertEqual([(p.name, p.agent_id, p.temporary) for p in built.experts],
                         [("Builder", self.coder.agent_id, False), ("Codex", self.codex.agent_id, False),
                          ("fresh", "fresh", False)])
        self.assertIsNotNone(self.store.get("alice", "fresh"))

    def test_personas_with_and_without_tools(self):
        built = self.engine("  - id: a\n    persona: critical\n  - id: b\n    persona: critical\n"
                            "    tools: [read_file]\n    instance: 2\n")
        light, tooled = built.experts
        self.assertEqual((light.agent_id, light.temporary, light._remembers), ("tmp__t0pic__critical__1", True, False))
        self.assertEqual(light._make["platform"], "llm")  # one model call per turn
        self.assertTrue(light.persona)  # the library's persona text frames it
        self.assertEqual((tooled.agent_id, tooled._make["platform"]), ("tmp__t0pic__critical__2", "webot"))
        self.assertEqual(tooled.tools, ["read_file"])
        self.assertNotEqual(light.name, tooled.name)

    def test_temporary_agents_are_made_for_their_first_turn_and_deleted_when_the_topic_ends(self):
        built = self.engine("  - id: a\n    persona: critical\n    tools: all\n  - id: b\n    agent: Builder\n")
        asyncio.run(built.run())
        self.assertEqual(built.forum.status, "concluded")
        self.assertEqual([m["agent_id"] for m in self.fake.made], ["tmp__t0pic__critical__1"])
        self.assertEqual(self.fake.deleted, ["tmp__t0pic__critical__1"])
        self.assertEqual(self.fake.asks[0]["agent"], "tmp__t0pic__critical__1")
        self.assertIsNone(self.fake.asks[0]["enabled_tools"])  # all tools


class TestParticipant(EngineCase):
    def test_asks_its_agent_with_schema_and_posts_with_its_id(self):
        participant = Participant("alice", self.coder.agent_id, name="Builder", tools=["read_file"])
        forum = DiscussionForum("t", "问题", "alice")
        asyncio.run(participant.participate(forum, discussion=False))
        ask = self.fake.asks[0]
        self.assertIs(ask["response_format"], OasisReplyOut)
        self.assertEqual((ask["enabled_tools"], ask["timeout"]), (["read_file"], NO_TIMEOUT))  # execute mode waits
        self.assertEqual(ask["msg"].instructions, "")  # a resident speaks as itself
        self.assertIn("问题", ask["msg"].text)
        self.assertEqual((forum.posts[0].author, forum.posts[0].author_id), ("Builder", self.coder.agent_id))

    def test_a_reply_without_the_json_is_posted_as_it_is(self):
        self.fake.replies = ["no json here", REPLY]
        forum = DiscussionForum("t", "问题", "alice")
        asyncio.run(Participant("alice", self.codex.agent_id, name="Codex").participate(forum))
        self.assertEqual(len(self.fake.asks), 1)  # not asked again
        self.assertEqual(forum.posts[0].content, "no json here")

    def test_later_turns_send_only_what_is_new(self):
        participant = Participant("alice", self.codex.agent_id, name="Codex")
        forum = DiscussionForum("t", "问题", "alice")
        asyncio.run(participant.participate(forum))
        asyncio.run(forum.publish(author="Other", content="新观点"))
        asyncio.run(participant.participate(forum))
        self.assertIn("第", self.fake.asks[1]["msg"].text)
        self.assertIn("新观点", self.fake.asks[1]["msg"].text)
        self.assertNotIn("讨论主题", self.fake.asks[1]["msg"].text)


class TestAgentCenter(EngineCase):
    def test_cancelling_an_active_tool_task_closes_its_agent(self):
        from oasis.agent_center import AgentCenter

        async def work():
            center = AgentCenter("alice", "dev")
            entered = asyncio.Event()

            async def blocked(*args, **kwargs):
                entered.set()
                await asyncio.Event().wait()

            self.fake.ask = blocked
            agent = center.temporary_agent("Active", tools=["read_file"])

            async def task():
                async with agent:
                    await agent.send("read")

            running = asyncio.create_task(task())
            await asyncio.wait_for(entered.wait(), timeout=1)
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
            await center.close_temporary_agents()
            self.assertEqual(self.fake.deleted, [agent.agent_id])

        asyncio.run(work())

    def test_yaml_cleanup_failure_is_visible_and_residents_are_retained(self):
        built = self.engine("  - id: a\n    persona: critical\n    tools: [read_file]\n")
        self.fake.delete = mock.AsyncMock(side_effect=RuntimeError("delete unavailable"))
        asyncio.run(built.run())
        self.assertEqual(built.forum.status, "error")
        self.assertIn("delete unavailable", built.forum.conclusion)
        self.assertIsNotNone(self.store.get("alice", self.coder.agent_id))

    def test_temporary_tool_agent_reuses_memory_and_closes_its_scope(self):
        from oasis.agent_center import AgentCenter

        center = AgentCenter("alice", "dev")

        async def work():
            async with center.temporary_agent("Reader", tools=["read_file"], persona_override="Read carefully") as agent:
                await agent.send("first")
                await agent.send("second")
                agent_id = agent.agent_id
            with self.assertRaisesRegex(RuntimeError, "closed"):
                await agent.send("third")
            await center.close_temporary_agents()
            return agent_id

        agent_id = asyncio.run(work())
        self.assertEqual(len(self.fake.made), 1)
        self.assertEqual(self.fake.made[0]["tools"], ["read_file"])
        self.assertEqual(self.fake.made[0]["persona"], "Read carefully")
        self.assertEqual(self.fake.made[0]["creation_template"], "personal")
        self.assertEqual([ask["agent"] for ask in self.fake.asks], [agent_id, agent_id])
        self.assertEqual(self.fake.deleted, [agent_id])
        self.assertTrue(all(ask["msg"].instructions == "" for ask in self.fake.asks))

    def test_one_task_and_persona_can_use_tools_without_leaving_agents(self):
        from oasis.agent_center import AgentCenter

        center = AgentCenter("alice", "dev")
        asyncio.run(center.send_agent_once("Reader", "read", tools=["read_file"]))
        asyncio.run(center.send_persona("critical", "review", tools="all", creation_template="group"))
        self.assertEqual([made["platform"] for made in self.fake.made], ["webot", "webot"])
        self.assertEqual(self.fake.made[0]["tools"], ["read_file"])
        self.assertIsNone(self.fake.made[1]["tools"])
        self.assertEqual(self.fake.made[1]["creation_template"], "group")
        self.assertEqual(self.fake.deleted, [made["agent_id"] for made in self.fake.made])
        self.assertEqual(center._temporary, set())

    def test_failed_or_cancelled_creation_is_cleaned_up(self):
        from oasis.agent_center import AgentCenter

        for error in (RuntimeError("create failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                center = AgentCenter("alice", "dev")
                self.fake.create = mock.AsyncMock(side_effect=error)
                agent = center.temporary_agent("Partial", tools=["read_file"])

                async def work():
                    with self.assertRaises(type(error)):
                        async with agent:
                            self.fail("Creation should fail")
                    await center.close_temporary_agents()

                asyncio.run(work())
                self.assertEqual(self.fake.deleted[-1], agent.agent_id)
        self.assertNotIn(self.coder.agent_id, self.fake.deleted)

    def test_python_runners_clean_up_agents_even_without_an_explicit_scope(self):
        from oasis.python_workflow import run_python_workflow_standalone
        from oasis.python_workflow_cli import _run

        source = Path(self.tmp.name) / "temporary.py"
        source.write_text("worker = temporary_agent('Reader', tools=['read_file'])\n"
                          "await worker.send(question)\nset_result('DONE')\n")
        result = asyncio.run(run_python_workflow_standalone(python_file=str(source), user_id="alice", question="read"))
        self.assertEqual(result["result"], "DONE")
        self.assertEqual(self.fake.deleted, [made["agent_id"] for made in self.fake.made])

        async def main(ctx):
            worker = ctx.temporary_agent("CLI", tools=["read_file"])
            await worker.send("read")
            raise RuntimeError("workflow failed")

        result_file = Path(self.tmp.name) / "result.json"
        with mock.patch.object(sys, "argv", ["workflow.py", "--user-id", "alice", "--no-auto-topic", "--result-file", str(result_file)]), \
             mock.patch("builtins.print"):
            self.assertEqual(asyncio.run(_run(main)), 1)
        self.assertFalse(json.loads(result_file.read_text())["ok"])
        self.assertEqual(self.fake.deleted, [made["agent_id"] for made in self.fake.made])

    def test_topic_python_workflow_cancellation_cleans_up(self):
        from oasis.python_workflow import PythonWorkflowEngine

        source = Path(self.tmp.name) / "cancel.py"
        source.write_text("worker = ctx.temporary_agent('Reader', tools=['read_file'])\n"
                          "await worker.send(question)\nraise asyncio.CancelledError()\n")
        forum = DiscussionForum("cancel", "read", "alice")
        engine = PythonWorkflowEngine(forum=forum, python_file=str(source), user_id="alice", team="dev")
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(engine.run())
        self.assertEqual(self.fake.deleted, [made["agent_id"] for made in self.fake.made])

    def test_yaml_tool_scope_is_registered_and_run_scope_can_narrow_it(self):
        built = DiscussionEngine(forum=DiscussionForum("limited", "read", "alice"),
                                 schedule_yaml="version: 2\nplan:\n  - id: reader\n    persona: critical\n    tools: all\n",
                                 user_id="alice", bot_enabled_tools=["read_file"])
        participant = built.experts[0]
        self.assertEqual(participant.tools, ["read_file"])
        self.assertEqual(participant._make["tools"], ["read_file"])
        self.assertEqual(participant._make["creation_template"], "personal")

    def test_workflows_reach_team_agents_and_personas(self):
        from oasis.agent_center import AgentCenter

        center = AgentCenter("alice", "dev")
        self.assertEqual([(a["role"], a["agent_id"]) for a in center.list_agents()], [("Builder", self.coder.agent_id)])
        for ref in ("Builder", self.coder.agent_id, "dev.Builder", "codex"):
            with self.subTest(ref=ref):
                self.assertIn(center.get_agent(ref)["agent_id"], (self.coder.agent_id, self.codex.agent_id))
        reply = asyncio.run(center.send_persona("critical", "评一下"))
        self.assertTrue(reply.ok)
        persona_ask = self.fake.asks[-1]
        made = self.fake.made[-1]
        self.assertEqual((made["platform"], persona_ask["agent"]), ("llm", made["agent_id"]))
        self.assertIn("评一下", persona_ask["msg"].text)
        self.assertEqual(self.fake.deleted, [made["agent_id"]])  # made for that one call


class TestAgentBindings(EngineCase):
    def test_runtime_table_overrides_team_only_for_its_run(self):
        from oasis.agent_center import AgentCenter
        center = AgentCenter("alice", "dev", agent_map={"writer": "codex", "builder": "codex"})
        other = AgentCenter("alice", "dev")
        reply = asyncio.run(center.send_agent("WRITER", "draft"))
        self.assertTrue(reply.ok)
        self.assertEqual(self.fake.asks[-1]["agent"], "codex")
        self.assertEqual(center.get_agent("Builder")["agent_id"], "codex")
        self.assertEqual(other.get_agent("Builder")["agent_id"], "s1")
        copy = center.agent_map
        copy.clear()
        self.assertIn("writer", center.agent_map)

    def test_missing_and_cross_user_ids_never_create_agents(self):
        from oasis.agent_center import AgentCenter
        self.store.create("bob", driver=WEBOT, name="Private", agent_id="private")
        for target in ("typo", "private"):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "missing Agent"):
                AgentCenter("alice", "dev", agent_map={"bad": target})
            self.assertIsNone(self.store.get("alice", target))

    def test_same_yaml_can_run_with_different_runtime_tables(self):
        source = "version: 2\nplan:\n  - id: task\n    agent: worker\n"
        for agent_id in ("s1", "codex"):
            built = DiscussionEngine(forum=DiscussionForum("bound_" + agent_id, "test", "alice"),
                                     user_id="alice", team="dev", schedule_yaml=source,
                                     agent_map={"worker": agent_id}, discussion=False)
            asyncio.run(built.run())
            self.assertEqual(built.forum.status, "concluded")
            self.assertEqual(self.fake.asks[-1]["agent"], agent_id)
        self.assertNotIn("agent_map", source)

    def test_runtime_file_input_and_invalid_tables(self):
        from oasis.agent_bindings import load_agent_map, validate_agent_map
        root = Path(self.tmp.name)
        for filename, source in (("agents.json", '{"writer":"s1"}'),
                                 ("agents.yaml", 'agent_map: {writer: s1}')):
            file = root / filename
            file.write_text(source)
            self.assertEqual(load_agent_map(file), {"writer": "s1"})
        for invalid in ([], {"writer": 42}, {"Writer": "s1", "writer": "codex"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_agent_map(invalid)

    def test_start_tool_forwards_map_without_rewriting_workflow(self):
        import inspect
        from webot.mcp import oasis as tools
        self.assertNotIn("agent_map", inspect.signature(tools.save_oasis_workflow).parameters)
        source = "version: 2\nplan:\n  - id: task\n    agent: writer\n"
        response = mock.Mock(status_code=200)
        response.json.return_value = {"topic_id": "test-topic"}
        client = mock.AsyncMock()
        client.post.return_value = response
        client.__aenter__.return_value = client
        with mock.patch.object(tools.httpx, "AsyncClient", return_value=client):
            result = asyncio.run(tools.start_new_oasis(question="write", schedule_yaml=source,
                                                     username="alice", agent_map={"writer": "s1"}))
        self.assertIn("test-topic", result)
        body = client.post.call_args.kwargs["json"]
        self.assertEqual(body["schedule_yaml"], source)
        self.assertEqual(body["agent_map"], {"writer": "s1"})
        with mock.patch.object(tools, "_spawn_standalone_python_workflow", return_value={
            "run_id": "py-test", "pid": 123, "log_file": "test.log", "result_file": "test.json"
        }) as spawn:
            result = asyncio.run(tools.start_new_oasis(question="write", python_file="/tmp/flow.py",
                                                     username="alice", agent_map={"writer": "codex"}))
        self.assertIn("py-test", result)
        self.assertEqual(spawn.call_args.kwargs["agent_map"], {"writer": "codex"})


if __name__ == "__main__":
    unittest.main()
