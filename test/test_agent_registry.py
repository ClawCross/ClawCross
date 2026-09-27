"""L1 registry: every declared agent gets one stable id, however it is referred to."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.registry import (  # noqa: E402
    AgentNotFound,
    AgentRegistry,
    AmbiguousAgentRef,
    external_driver,
)


class RegistryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.user_files = Path(self.tmp.name) / "user_files"
        self.registry = AgentRegistry(Path(self.tmp.name) / "group_chat.db", self.user_files)

    def write(self, owner: str, team: str, filename: str, entries: list[dict]) -> None:
        base = self.user_files / owner / ("teams/" + team if team else "")
        base.mkdir(parents=True, exist_ok=True)
        path = base / filename
        path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        # Make every rewrite visible to the mtime/size fingerprint.
        os.utime(path, ns=(path.stat().st_mtime_ns + 1_000_000, path.stat().st_mtime_ns + 1_000_000))

    def fresh(self) -> AgentRegistry:
        """A second process looking at the same files and database."""
        return AgentRegistry(self.registry.db_path, self.user_files)


class TestImport(RegistryTestCase):
    def test_every_declared_agent_gets_a_stable_id(self):
        self.write("alice", "dev", "internal_agents.json", [
            {"name": "Coder", "tag": "coder", "session": "s1"},
            {"name": "搜索指挥者", "tag": "search_commander", "session": "s2"},
        ])
        self.write("alice", "", "external_agents.json", [
            {"name": "Codex", "tag": "codex", "global_name": "cx1", "platform": "codex", "config": {}},
        ])

        agents = {a.display_name: a for a in self.registry.list("alice")}

        self.assertEqual(set(agents), {"Coder", "搜索指挥者", "Codex"})
        self.assertTrue(all(a.agent_id.startswith("ag_") and len(a.agent_id) == 13 for a in agents.values()))
        self.assertEqual(agents["Coder"].address, "alice/coder")
        self.assertEqual(agents["搜索指挥者"].handle, "search_commander")  # no ASCII in the name
        self.assertEqual(agents["Codex"].driver, "acpx")

        ids = {a.agent_id for a in agents.values()}
        self.assertEqual({a.agent_id for a in self.fresh().list("alice")}, ids)

    def test_id_survives_rename_and_moving_between_teams(self):
        self.write("alice", "dev", "internal_agents.json", [{"name": "Coder", "tag": "coder", "session": "s1"}])
        before = self.registry.resolve("alice", "coder")

        self.write("alice", "dev", "internal_agents.json", [])
        self.write("alice", "ops", "internal_agents.json", [{"name": "Builder", "tag": "coder", "session": "s1"}])
        after = self.registry.webot_session("alice", "s1")

        self.assertEqual(after.agent_id, before.agent_id)
        self.assertEqual(after.handle, "coder")  # the address stays stable
        self.assertEqual(after.display_name, "Builder")
        self.assertEqual(after.default_context, {"team": "ops"})

    def test_session_in_two_teams_is_one_agent_whose_home_is_the_first_team(self):
        self.write("alice", "zeta", "internal_agents.json", [{"name": "Z", "tag": "z", "session": "s1"}])
        self.write("alice", "alpha", "internal_agents.json", [{"name": "A", "tag": "a", "session": "s1"}])
        self.write("alice", "", "internal_agents.json", [{"name": "Root", "tag": "r", "session": "s1"}])

        records = self.registry.list("alice")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].teams, ["alpha", "zeta", ""])
        # Same answer the runtime got by scanning sorted teams, then the user root.
        self.assertEqual(
            self.registry.internal_session_meta("alice", "s1"),
            {"team": "alpha", "name": "A", "tag": "a"},
        )

    def test_user_root_agent_has_no_team(self):
        self.write("alice", "", "internal_agents.json", [{"name": "Solo", "tag": "t", "session": "s9"}])
        self.assertEqual(self.registry.internal_session_meta("alice", "s9"), {"team": "", "name": "Solo", "tag": "t"})
        self.assertIsNone(self.registry.internal_session_meta("alice", "nope"))

    def test_removed_agent_is_detached_then_restored_with_the_same_id(self):
        self.write("alice", "dev", "internal_agents.json", [{"name": "Coder", "session": "s1"}])
        agent_id = self.registry.resolve("alice", "coder").agent_id

        self.write("alice", "dev", "internal_agents.json", [])
        self.assertEqual(self.registry.list("alice"), [])
        self.assertEqual(self.registry.get(agent_id).status, "detached")

        self.write("alice", "dev", "internal_agents.json", [{"name": "Coder", "session": "s1"}])
        self.assertEqual(self.registry.resolve("alice", "coder").agent_id, agent_id)

    def test_duplicate_names_get_distinct_handles(self):
        self.write("alice", "a", "internal_agents.json", [{"name": "Coder", "session": "s1"}])
        self.write("alice", "b", "internal_agents.json", [{"name": "Coder", "session": "s2"}])
        handles = sorted(a.handle for a in self.registry.list("alice"))
        self.assertEqual(handles, ["coder", "coder-2"])

    def test_external_config_last_file_wins(self):
        self.write("alice", "", "external_agents.json", [
            {"name": "Claw", "tag": "openclaw", "global_name": "g1", "platform": "openclaw",
             "config": {"model": "agent:g1:root"}},
        ])
        self.write("alice", "ops", "external_agents.json", [
            {"name": "Claw", "tag": "openclaw", "global_name": "g1", "platform": "openclaw",
             "config": {"model": "agent:g1:ops"}},
        ])
        record = self.registry.external("alice", "g1")
        self.assertEqual(record.driver, "openclaw")
        self.assertEqual(record.binding["model"], "agent:g1:ops")
        self.assertEqual(record.binding["team"], "ops")

    def test_driver_for_platform(self):
        self.assertEqual(external_driver("openclaw"), "openclaw")
        self.assertEqual(external_driver("claude-code"), "acpx")
        self.assertEqual(external_driver("some-http-service"), "http")


class TestResolve(RegistryTestCase):
    def setUp(self):
        super().setUp()
        self.write("alice", "dev", "internal_agents.json", [
            {"name": "Coder", "tag": "coder", "session": "s1"},
            {"name": "Code Reviewer", "tag": "rev", "session": "s2"},
        ])
        self.write("alice", "ops", "internal_agents.json", [
            {"name": "Code Reviewer", "tag": "rev", "session": "s3"},
        ])
        self.write("alice", "", "external_agents.json", [
            {"name": "Claw", "tag": "openclaw", "global_name": "claw_main", "platform": "openclaw"},
        ])
        self.write("bob", "", "internal_agents.json", [{"name": "Coder", "session": "b1"}])
        self.coder = self.registry.webot_session("alice", "s1")

    def test_every_reference_form_finds_the_same_agent(self):
        for ref in [
            self.coder.agent_id, "alice/coder", "coder", "@coder", "s1",
            "internal:Coder", "dev/Coder", "alice/dev/Coder",
        ]:
            with self.subTest(ref=ref):
                self.assertEqual(self.registry.resolve("alice", ref).agent_id, self.coder.agent_id)
        self.assertEqual(self.registry.resolve("alice", "claw_main").driver, "openclaw")
        self.assertEqual(self.registry.resolve("alice", "external:Claw").driver, "openclaw")

    def test_team_scopes_a_shared_name(self):
        with self.assertRaises(AmbiguousAgentRef) as ctx:
            self.registry.resolve("alice", "Code Reviewer")
        self.assertEqual(len(ctx.exception.candidates), 2)
        self.assertEqual(self.registry.resolve("alice", "ops/Code Reviewer").binding["session"], "s3")
        self.assertEqual(self.registry.resolve("alice", "Code Reviewer", team="dev").binding["session"], "s2")

    def test_team_name_beats_another_teams_handle(self):
        # The dev reviewer holds the handle "code-reviewer"; inside ops the
        # ops reviewer must still win.
        self.assertEqual(self.registry.resolve("alice", "code-reviewer").binding["session"], "s2")
        self.assertEqual(self.registry.resolve("alice", "Code Reviewer", team="ops").binding["session"], "s3")

    def test_other_users_agents_are_out_of_reach(self):
        bob_coder = self.registry.webot_session("bob", "b1")
        for ref in [bob_coder.agent_id, "bob/coder", "bob/team/coder", "nobody"]:
            with self.subTest(ref=ref):
                with self.assertRaises(AgentNotFound):
                    self.registry.resolve("alice", ref)
        self.assertEqual(self.registry.resolve("bob", "coder").agent_id, bob_coder.agent_id)


if __name__ == "__main__":
    unittest.main()
