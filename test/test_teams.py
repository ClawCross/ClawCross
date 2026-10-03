"""L3 teams: a team is a set of agents in roles; the team files are only its package format."""

import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src" / "backend"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agents.store import ACPX, WEBOT, AgentStore  # noqa: E402
from teams.manifest import dumps, export_entries, import_entries, import_folder  # noqa: E402
from teams.routes import create_teams_router  # noqa: E402
from teams.store import TeamStore  # noqa: E402

TOKEN = "tok"


class TeamCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.agents = AgentStore(root / "agents.db")
        self.teams = TeamStore(self.agents, root / "user_files")
        self.teams.create("alice", "dev")

    def roles(self, team="dev"):
        return [(m.role, m.agent.driver, m.is_lead) for m in self.teams.members("alice", team)]


class TestMembership(TeamCase):
    def test_agents_join_in_roles_and_one_leads(self):
        a = self.agents.create("alice", driver=WEBOT, name="Coder", agent_id="s1")
        b = self.agents.create("alice", driver=ACPX, config={"platform": "codex"}, name="Codex")
        self.teams.add("alice", "dev", a.agent_id, role="Builder", is_lead=True)
        self.teams.add("alice", "dev", b.agent_id, is_lead=True)

        self.assertEqual(self.roles(), [("Builder", WEBOT, False), ("Codex", ACPX, True)])
        self.assertEqual(self.teams.lead("alice", "dev").agent.agent_id, b.agent_id)
        self.assertEqual(self.teams.member("alice", "dev", "builder").agent.agent_id, a.agent_id)

    def test_deleting_an_agent_takes_it_out_of_its_teams(self):
        a = self.agents.create("alice", driver=WEBOT, name="Coder", agent_id="s1")
        self.teams.add("alice", "dev", a.agent_id)
        self.agents.delete("alice", a.agent_id)
        self.teams.forget_agent("alice", a.agent_id)
        self.assertEqual(self.teams.members("alice", "dev"), [])
        self.assertEqual(self.teams.teams_of("alice", a.agent_id), [])

    def test_membership_lives_in_the_team_folder_and_names_the_agent_in_the_team(self):
        a = self.agents.create("alice", driver=WEBOT, name="Coder", agent_id="s1")
        self.teams.add("alice", "dev", a.agent_id, role="Builder", is_lead=True)
        stored = json.loads((self.teams.folder("alice", "dev") / "members.json").read_text(encoding="utf-8"))
        self.assertEqual(stored, [{"agent": "s1", "name": "Builder", "lead": True}])
        self.assertEqual(self.teams.address("alice", "dev.Builder").agent_id, "s1")
        self.assertIsNone(self.teams.address("alice", "dev.Nobody"))
        self.assertIsNone(self.teams.address("alice", "s1"))


class TestTeamsOfAnAgent(TeamCase):
    """members.json and the agent's ``teams`` always say the same thing."""

    def teams_of(self, agent_id):
        return (self.agents.get("alice", agent_id).teams, self.teams.teams_of("alice", agent_id))

    def test_joining_leaving_renaming_and_deleting_keep_both_in_step(self):
        a = self.agents.create("alice", driver=WEBOT, name="Coder", agent_id="s1")
        self.teams.create("alice", "ops")
        self.teams.add("alice", "dev", a.agent_id, role="Builder")
        self.teams.add("alice", "ops", a.agent_id)  # an agent may be in several teams
        self.assertEqual(self.teams_of("s1"), (["dev", "ops"], ["dev", "ops"]))
        self.teams.rename("alice", "ops", "ops2")
        self.assertEqual(self.teams_of("s1"), (["dev", "ops2"], ["dev", "ops2"]))
        self.teams.remove("alice", "ops2", a.agent_id)
        self.assertEqual(self.teams_of("s1"), (["dev"], ["dev"]))
        self.teams.delete("alice", "dev")
        self.assertEqual(self.teams_of("s1"), ([], []))
        self.assertIsNotNone(self.agents.get("alice", a.agent_id))  # the agent stays


class TestManifest(TeamCase):
    INTERNAL = [
        {"name": "Planner", "tag": "plan", "is_primary": True},
        {"name": "Coder", "tag": "coder", "session": "s1", "note": "kept"},
    ]
    EXTERNAL = [
        {"name": "Claw", "tag": "openclaw", "platform": "openclaw", "global_name": "claw",
         "meta": {"model": "m1"}, "config": {"agents": {}}, "workspace_files": {"a": "1"}},
    ]

    def setUp(self):
        super().setUp()
        library = [{"tag": "plan", "name": "Planner", "persona": "你负责规划。"}]
        patcher = mock.patch("oasis.experts.get_all_experts", lambda owner, team="": library)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_import_makes_agents_and_members(self):
        existing = self.agents.create("alice", driver=WEBOT, name="Coder", agent_id="s1")
        import_entries(self.teams, "alice", "dev", self.INTERNAL, self.EXTERNAL)

        self.assertEqual(self.roles(), [("Planner", WEBOT, True), ("Coder", WEBOT, False), ("Claw", ACPX, False)])
        coder = self.teams.member("alice", "dev", "Coder")
        self.assertEqual(coder.agent.agent_id, existing.agent_id)  # "session" is the id of an agent already there
        planner = self.teams.member("alice", "dev", "Planner").agent
        # A new agent gets its own copy of the team persona its tag names.
        self.assertEqual((planner.config["persona"], planner.teams), ("你负责规划。", ["dev"]))
        claw = self.teams.member("alice", "dev", "Claw").agent
        self.assertEqual((claw.platform, claw.config["model"]), ("openclaw", "m1"))

    def test_reimport_follows_the_entries_and_keeps_agents(self):
        import_entries(self.teams, "alice", "dev", self.INTERNAL, [])
        planner = self.teams.member("alice", "dev", "Planner").agent
        coder = self.teams.member("alice", "dev", "Coder").agent
        import_entries(self.teams, "alice", "dev", [dict(self.INTERNAL[1])], [])

        self.assertEqual(self.roles(), [("Coder", WEBOT, False)])
        self.assertEqual(self.teams.member("alice", "dev", "Coder").agent.agent_id, coder.agent_id)
        self.assertIsNotNone(self.agents.get("alice", planner.agent_id))

    def test_export_is_the_same_format(self):
        import_entries(self.teams, "alice", "dev", self.INTERNAL, self.EXTERNAL)
        internal, external = export_entries(self.teams, "alice", "dev", portable=False)
        self.assertEqual(internal[1], {"name": "Coder", "tag": "coder", "note": "kept", "session": "s1"})
        self.assertEqual((internal[0]["tag"], internal[0]["persona"]), ("plan", "你负责规划。"))
        self.assertTrue(internal[0]["is_primary"])
        self.assertEqual(external[0]["global_name"], self.teams.member("alice", "dev", "Claw").agent.agent_id)
        self.assertEqual(external[0]["workspace_files"], {"a": "1"})  # unknown keys travel along

        internal, external = export_entries(self.teams, "alice", "dev", portable=True)
        self.assertNotIn("session", internal[0])
        self.assertNotIn("global_name", external[0])
        # Importing a portable package elsewhere makes new agents.
        self.teams.create("bob", "copy")
        external[0]["global_name"] = "bob_1"
        import_entries(self.teams, "bob", "copy", internal, external)
        self.assertEqual(len(self.teams.members("bob", "copy")), 3)
        self.assertTrue(self.teams.member("bob", "copy", "Coder").agent.agent_id.startswith("ag_"))
        self.assertEqual(self.teams.member("bob", "copy", "Planner").agent.config["persona"], "你负责规划。")

    def test_an_entry_that_names_an_agent_is_that_agent(self):
        codex = self.agents.create("alice", driver=ACPX, config={"platform": "codex"}, name="Codex", agent_id="cx-1")
        import_entries(self.teams, "alice", "dev", [{"name": "New", "tag": "x", "session": "fresh"}],
                       [{"name": "Rev", "tag": "codex", "platform": "codex", "global_name": "cx-1"}])
        self.assertEqual(self.teams.member("alice", "dev", "Rev").agent.agent_id, codex.agent_id)
        self.assertEqual(self.teams.member("alice", "dev", "New").agent.agent_id, "fresh")  # a new agent with that id

    def test_folder_import_removes_the_files(self):
        folder = self.teams.folder("alice", "dev")
        (folder / "internal_agents.json").write_text(dumps(self.INTERNAL), encoding="utf-8")
        import_folder(self.teams, "alice", "dev")
        self.assertEqual(len(self.teams.members("alice", "dev")), 2)
        self.assertFalse((folder / "internal_agents.json").exists())


class TestPreset(TeamCase):
    def test_install_makes_every_role_an_agent(self):
        from teams.preset_assets import get_team_preset_bundle, install_team_preset

        result = install_team_preset(user_id="alice", team_name="builder", preset_id="team-builder", teams=self.teams)
        roles = [e["name"] for e in get_team_preset_bundle("team-builder")["internal_agents"]]
        self.assertEqual([m.role for m in self.teams.members("alice", "builder")], roles)
        self.assertEqual(result["internal_agents"], len(roles))
        folder = self.teams.folder("alice", "builder")
        self.assertTrue((folder / "oasis_experts.json").is_file())
        self.assertFalse((folder / "internal_agents.json").exists())


class TestTeamsApi(TeamCase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(create_teams_router(internal_token=TOKEN, verify_password=lambda u, p: False,
                                               teams=self.teams))
        self.client = TestClient(app)
        self.coder = self.agents.create("alice", driver=WEBOT, name="Coder", agent_id="coder")

    def call(self, method, path, **kwargs):
        return self.client.request(method, path, headers={"Authorization": f"Bearer {TOKEN}:alice"}, **kwargs)

    def test_members_and_lead(self):
        added = self.call("POST", "/v1/teams/dev/members", json={"agent": "coder", "role": "Builder"}).json()
        self.assertEqual((added["role"], added["agent"]["agent_id"]), ("Builder", self.coder.agent_id))
        self.call("PATCH", "/v1/teams/dev/members/dev.Builder", json={"is_lead": True})
        card = self.call("GET", "/v1/teams/dev").json()
        self.assertEqual(card["lead"], self.coder.agent_id)
        self.assertEqual(self.call("POST", "/v1/teams/dev/messages", json={"text": "hi"}).status_code, 404)  # gone

        self.call("DELETE", "/v1/teams/dev/members/coder")
        self.assertEqual(self.call("GET", "/v1/teams/dev").json()["members"], [])

    def test_create_rename_delete_and_import(self):
        self.assertEqual(self.call("POST", "/v1/teams", json={"team": "../x"}).status_code, 400)
        self.assertEqual(self.call("POST", "/v1/teams", json={"team": "ops"}).status_code, 200)
        (self.teams.folder("alice", "ops") / "internal_agents.json").write_text(
            json.dumps([{"name": "Writer", "tag": "writer"}]), encoding="utf-8")
        imported = self.call("POST", "/v1/teams/ops/import").json()
        self.assertEqual([m["role"] for m in imported["members"]], ["Writer"])
        self.assertEqual(self.call("PATCH", "/v1/teams/ops", json={"name": "ops2"}).json()["team"], "ops2")
        self.assertEqual(self.call("DELETE", "/v1/teams/ops2").status_code, 200)
        self.assertEqual(self.call("GET", "/v1/teams/ops2").status_code, 404)


if __name__ == "__main__":
    unittest.main()
