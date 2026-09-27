"""OASIS participants are L1 agents: residents by reference, temporary personas
with or without tools, all reached through the agent gateway."""

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
for path in (str(PROJECT_ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import agents.gateway as gateway_module  # noqa: E402
from agents.gateway import NO_TIMEOUT, AgentGateway  # noqa: E402
from agents.messages import AgentReply  # noqa: E402
from agents.registry import AgentRegistry  # noqa: E402
from oasis import engine as engine_module  # noqa: E402
from oasis.engine import DiscussionEngine  # noqa: E402
from oasis.experts import ExpertAgent, ExternalExpert, SessionExpert  # noqa: E402
from oasis.forum import DiscussionForum  # noqa: E402
from oasis.scheduler import parse_schedule, participant_ref  # noqa: E402
from teams.view import TeamView  # noqa: E402

REPLY = json.dumps({"clawcross_type": "oasis reply", "reply_to": None, "content": "ok", "votes": []})


class _FakeGateway:
    """Records every ask; answers with a valid OASIS reply."""

    def __init__(self, registry):
        self.registry = registry
        self.asks: list[dict] = []
        self.discarded: list[tuple[str, str]] = []

    async def ask(self, owner, record, msg, **kwargs):
        self.asks.append({"owner": owner, "record": record, "msg": msg, **kwargs})
        return AgentReply(ok=True, content=REPLY)

    async def discard_session(self, owner, session):
        self.discarded.append((owner, session))
        return True


class TestYamlForms(unittest.TestCase):
    def test_participant_forms(self):
        self.assertEqual(participant_ref({"agent": "@coder"}), ("@coder", {}))
        self.assertEqual(participant_ref({"persona": "critical"}), ("critical#temp#1", {}))
        self.assertEqual(participant_ref({"persona": "critical", "tools": "none", "instance": 2}), ("critical#temp#2", {}))
        self.assertEqual(participant_ref({"persona": "critical", "tools": "all"}), ("critical#tmp#1", {"tools": "all"}))
        self.assertEqual(
            participant_ref({"persona": "critical", "tools": ["read_file", "web_search"]}),
            ("critical#tmp#1", {"tools": ["read_file", "web_search"]}),
        )
        self.assertEqual(participant_ref({"expert": "creative#temp#1"}), ("creative#temp#1", {}))
        with self.assertRaises(ValueError):
            participant_ref({"persona": "critical", "tools": 3})

    def test_schedule_accepts_new_forms_in_parallel_too(self):
        schedule = parse_schedule("""version: 2
repeat: false
plan:
  - id: a
    agent: coder
    instruction: build it
  - id: b
    parallel:
      - persona: critical
        tools: [read_file]
      - agent: alice/codex
""")
        nodes = {n.node_id: n for n in schedule.nodes}
        self.assertEqual(nodes["a"].expert_names, ["@coder"])
        self.assertEqual(nodes["a"].instructions, {"@coder": "build it"})
        self.assertEqual(nodes["b"].expert_names, ["critical#tmp#1", "@alice/codex"])
        self.assertEqual(nodes["b"].external_configs, {"critical#tmp#1": {"tools": ["read_file"]}})

    def test_visual_layout_draws_new_forms(self):
        import mcp_servers.oasis as oasis_mcp

        layout = oasis_mcp._yaml_to_layout_data("""version: 2
repeat: false
plan:
  - id: a
    agent: coder
  - id: b
    persona: critical
edges:
  - [a, b]
""")
        self.assertEqual(len(layout["nodes"]), 2)


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.user_files = root / "user_files"
        team = self.user_files / "alice" / "teams" / "dev"
        team.mkdir(parents=True)
        (team / "internal_agents.json").write_text(json.dumps([
            {"name": "Coder", "tag": "coder", "session": "s1"},
        ]), encoding="utf-8")
        (team / "external_agents.json").write_text(json.dumps([
            {"name": "Codex", "tag": "codex", "global_name": "cx", "platform": "codex"},
        ]), encoding="utf-8")
        self.registry = AgentRegistry(root / "group_chat.db", self.user_files)
        self.fake = _FakeGateway(self.registry)
        for target, attr, value in [
            (engine_module, "USER_FILES_DIR", self.user_files),
            (engine_module, "create_chat_model", mock.MagicMock()),
            (gateway_module, "get_gateway", lambda: self.fake),
            ("agents.registry.get_registry", None, lambda *a, **k: self.registry),
        ]:
            patcher = mock.patch(target, value) if attr is None else mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def engine(self, plan: str, *, team: str = "dev") -> DiscussionEngine:
        yaml = f"version: 2\nrepeat: false\nplan:\n{plan}"
        return DiscussionEngine(
            forum=DiscussionForum("t0pic", "q", "alice"), schedule_yaml=yaml, user_id="alice",
            team=team, discussion=False,
        )


class TestParticipants(EngineTestCase):
    def test_residents_resolve_through_the_team_and_carry_their_ids(self):
        built = self.engine("""  - id: a
    agent: Coder
  - id: b
    agent: alice/codex
  - id: c
    agent: nobody
""")
        coder = self.registry.webot_session("alice", "s1")
        codex = self.registry.external("alice", "cx")
        self.assertEqual(len(built.experts), 2)
        session, external = built.experts
        self.assertIsInstance(session, SessionExpert)
        self.assertEqual((session.session_id, session.agent_id, session.is_oasis), ("s1", coder.agent_id, False))
        self.assertIsInstance(external, ExternalExpert)
        self.assertEqual((external.agent_id, external.platform), (codex.agent_id, "codex"))

    def test_classic_names_also_get_registry_ids(self):
        built = self.engine("""  - id: a
    expert: "coder#oasis#Coder"
""")
        self.assertEqual(built.experts[0].agent_id, self.registry.webot_session("alice", "s1").agent_id)

    def test_personas_with_and_without_tools(self):
        built = self.engine("""  - id: a
    persona: critical
  - id: b
    persona: critical
    tools: [read_file]
    instance: 2
""")
        light, tooled = built.experts
        self.assertIsInstance(light, ExpertAgent)
        self.assertIsInstance(tooled, SessionExpert)
        self.assertEqual(tooled.session_id, "tmp__t0pic__critical__2")
        self.assertTrue(tooled.ephemeral and tooled.is_oasis)
        self.assertEqual(tooled.enabled_tools, ["read_file"])
        self.assertEqual(tooled.agent_id, "")


class TestThroughTheGateway(EngineTestCase):
    def test_session_expert_asks_its_agent_with_instructions_tools_and_no_timeout(self):
        expert = SessionExpert(
            name="Coder", session_id="s1", user_id="alice", persona="p",
            enabled_tools=["read_file"], inject_identity=True,
        )
        forum = DiscussionForum("t", "问题", "alice")
        asyncio.run(expert.participate(forum, discussion=False))

        ask = self.fake.asks[0]
        self.assertEqual(ask["record"].binding["session"], "s1")
        self.assertEqual(ask["record"].agent_id, self.registry.webot_session("alice", "s1").agent_id)
        self.assertIn("p", ask["msg"].instructions)          # persona framed as system text
        self.assertIn("问题", ask["msg"].text)
        self.assertEqual(ask["tools"], ["read_file"])
        self.assertEqual(ask["timeout"], NO_TIMEOUT)          # execute mode waits
        self.assertIn("json_schema", json.dumps(ask["response_format"]))
        self.assertEqual(forum.posts[0].author_id, ask["record"].agent_id)

    def test_temp_expert_is_an_ephemeral_call_with_a_schema(self):
        expert = ExpertAgent(name="Critic", persona="be critical", tag="critical", temp_id=1, model="m1")
        forum = DiscussionForum("t", "q", "alice")
        asyncio.run(expert.participate(forum, discussion=True))

        ask = self.fake.asks[0]
        self.assertEqual(ask["record"].driver, "ephemeral")
        self.assertEqual(ask["record"].binding["options"]["model"], "m1")
        self.assertIsNotNone(ask["response_format"])
        self.assertEqual(forum.posts[0].author_id, "")

    def test_temporary_sessions_are_discarded_when_the_topic_ends(self):
        built = self.engine("""  - id: a
    persona: critical
    tools: all
""")
        asyncio.run(built.run())

        self.assertEqual(built.forum.status, "concluded")
        self.assertEqual(self.fake.asks[0]["record"].binding["session"], "tmp__t0pic__critical__1")
        self.assertIsNone(self.fake.asks[0]["tools"])  # all tools
        self.assertEqual(self.fake.discarded, [("alice", "tmp__t0pic__critical__1")])


class TestCatalog(EngineTestCase):
    def test_python_workflows_can_name_agents_by_id_or_address(self):
        from oasis import agent_catalog
        from oasis.agent_center import AgentCenter

        with mock.patch.object(agent_catalog, "USER_FILES_DIR", self.user_files):
            center = AgentCenter("alice", "dev")
        coder = self.registry.webot_session("alice", "s1")
        for ref in ("internal:Coder", coder.agent_id, "alice/coder"):
            with self.subTest(ref=ref):
                self.assertEqual(center.get_agent(ref)["session"], "s1")
        self.assertEqual(center.get_agent("alice/codex")["agent_id"], self.registry.external("alice", "cx").agent_id)


class TestDiscardGuard(unittest.TestCase):
    def test_only_temporary_sessions_can_be_discarded(self):
        gateway = AgentGateway(mock.Mock(), agent_base_url="http://agent.test", internal_token="tok")
        for session in ["", "default", "tmp__", "s1"]:
            with self.subTest(session=session):
                with self.assertRaises(ValueError):
                    asyncio.run(gateway.discard_session("alice", session))


if __name__ == "__main__":
    unittest.main()
