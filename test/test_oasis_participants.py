"""OASIS participants are agents: residents named with ``agent:``, temporary ones with
``persona:``; all of them are asked through the agent gateway."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
for path in (str(PROJECT_ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import agents.gateway as gateway_module  # noqa: E402
from agents.runtime import NO_TIMEOUT  # noqa: E402
from agents.store import LLM  # noqa: E402
from agents.messages import AgentReply  # noqa: E402
from agents.store import ACPX, WEBOT, AgentStore  # noqa: E402
from oasis.engine import DiscussionEngine  # noqa: E402
from oasis.forum import DiscussionForum  # noqa: E402
from oasis.participants import Participant  # noqa: E402
from oasis.schemas import OasisReplyOut  # noqa: E402
from oasis.scheduler import parse_schedule, participant_ref  # noqa: E402
from teams.store import TeamStore  # noqa: E402

REPLY = json.dumps({"clawcross_type": "oasis reply", "reply_to": None, "content": "ok", "votes": []})


class _Gateway:
    def __init__(self, replies=None):
        self.asks = []
        self.discarded = []
        self.replies = list(replies or [])

    async def ask(self, agent, msg, **kwargs):
        self.asks.append({"agent": agent, "msg": msg, **kwargs})
        return AgentReply(ok=True, content=self.replies.pop(0) if self.replies else REPLY)

    async def discard(self, agent):
        self.discarded.append(agent.agent_id)
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
        import mcp_servers.oasis as oasis_mcp
        from visual.main import layout_to_yaml

        layout = oasis_mcp._yaml_to_layout_data(
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
        self.fake = _Gateway()
        for target, value in (("agents.store.get_store", lambda *a: self.store),
                              ("teams.store.get_team_store", lambda *a: self.teams),
                              ("oasis.engine.create_chat_model", mock.MagicMock())):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(gateway_module, "get_gateway", lambda: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        import oasis.agent_center as agent_center
        for module in ("oasis.participants", "oasis.agent_center"):
            patcher = mock.patch(f"{module}.get_gateway", lambda: self.fake)
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
    def test_agents_are_found_by_team_name_or_id_and_a_new_id_is_a_new_agent(self):
        built = self.engine("  - id: a\n    agent: Builder\n  - id: b\n    agent: codex\n  - id: c\n    agent: dev.Nobody\n"
                            "  - id: d\n    agent: fresh\n")
        self.assertEqual([(p.name, p.agent.agent_id, p.temporary) for p in built.experts],
                         [("Builder", self.coder.agent_id, False), ("Codex", self.codex.agent_id, False),
                          ("fresh", "fresh", False)])
        self.assertIsNotNone(self.store.get("alice", "fresh"))

    def test_personas_with_and_without_tools(self):
        built = self.engine("  - id: a\n    persona: critical\n  - id: b\n    persona: critical\n"
                            "    tools: [read_file]\n    instance: 2\n")
        light, tooled = built.experts
        self.assertEqual((light.agent.driver, light.temporary), (LLM, True))
        self.assertTrue(light.persona)  # the library's persona text frames it
        self.assertEqual((tooled.agent.driver, tooled.agent.agent_id), (WEBOT, "tmp__t0pic__critical__2"))
        self.assertEqual(tooled.tools, ["read_file"])
        self.assertNotEqual(light.name, tooled.name)

    def test_temporary_sessions_are_discarded_when_the_topic_ends(self):
        built = self.engine("  - id: a\n    persona: critical\n    tools: all\n  - id: b\n    agent: Builder\n")
        asyncio.run(built.run())
        self.assertEqual(built.forum.status, "concluded")
        self.assertEqual(self.fake.discarded, ["tmp__t0pic__critical__1"])
        self.assertIsNone(self.fake.asks[0]["tools"])  # all tools


class TestParticipant(EngineCase):
    def test_asks_its_agent_with_schema_and_posts_with_its_id(self):
        participant = Participant(self.coder, name="Builder", tools=["read_file"])
        forum = DiscussionForum("t", "问题", "alice")
        asyncio.run(participant.participate(forum, discussion=False))
        ask = self.fake.asks[0]
        self.assertIs(ask["response_format"], OasisReplyOut)
        self.assertEqual((ask["tools"], ask["timeout"]), (["read_file"], NO_TIMEOUT))  # execute mode waits
        self.assertEqual(ask["msg"].instructions, "")  # a resident speaks as itself
        self.assertIn("问题", ask["msg"].text)
        self.assertEqual((forum.posts[0].author, forum.posts[0].author_id), ("Builder", self.coder.agent_id))

    def test_a_reply_without_the_json_is_posted_as_it_is(self):
        self.fake.replies = ["no json here", REPLY]
        forum = DiscussionForum("t", "问题", "alice")
        asyncio.run(Participant(self.codex, name="Codex").participate(forum))
        self.assertEqual(len(self.fake.asks), 1)  # not asked again
        self.assertEqual(forum.posts[0].content, "no json here")

    def test_later_turns_send_only_what_is_new(self):
        participant = Participant(self.codex, name="Codex")
        forum = DiscussionForum("t", "问题", "alice")
        asyncio.run(participant.participate(forum))
        asyncio.run(forum.publish(author="Other", content="新观点"))
        asyncio.run(participant.participate(forum))
        self.assertIn("第", self.fake.asks[1]["msg"].text)
        self.assertIn("新观点", self.fake.asks[1]["msg"].text)
        self.assertNotIn("讨论主题", self.fake.asks[1]["msg"].text)


class TestAgentCenter(EngineCase):
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
        self.assertEqual(persona_ask["agent"].driver, LLM)
        self.assertIn("评一下", persona_ask["msg"].text)


if __name__ == "__main__":
    unittest.main()
