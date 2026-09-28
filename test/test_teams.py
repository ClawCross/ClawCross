"""L3 teams: a team is a set of agents in roles; the team files are only its package format."""

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
from agents.store import ACPX, OPENCLAW, WEBOT, AgentStore  # noqa: E402
from teams.manifest import dumps, export_entries, import_entries, import_folder  # noqa: E402
from teams.routes import create_teams_router  # noqa: E402
from teams.store import TeamStore  # noqa: E402

TOKEN = "tok"


class TeamCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.agents = AgentStore(root / "clawcross.db")
        self.teams = TeamStore(self.agents, root / "user_files")
        self.teams.create("alice", "dev")

    def roles(self, team="dev"):
        return [(m.role, m.agent.driver, m.is_lead) for m in self.teams.members("alice", team)]


class TestMembership(TeamCase):
    def test_agents_join_in_roles_and_one_leads(self):
        a = self.agents.create("alice", name="Coder", driver=WEBOT, config={"session": "s1"})
        b = self.agents.create("alice", name="Codex", driver=ACPX, config={"platform": "codex", "global_name": "cx"})
        self.teams.add("alice", "dev", a.agent_id, role="Builder", is_lead=True)
        self.teams.add("alice", "dev", b.agent_id, is_lead=True)

        self.assertEqual(self.roles(), [("Builder", WEBOT, False), ("Codex", ACPX, True)])
        self.assertEqual(self.teams.lead("alice", "dev").agent.agent_id, b.agent_id)
        self.assertEqual(self.teams.member("alice", "dev", "builder").agent.agent_id, a.agent_id)

    def test_an_agent_serves_several_teams_and_outlives_them(self):
        a = self.agents.create("alice", name="Coder", driver=WEBOT, config={"session": "s1"})
        self.teams.create("alice", "ops")
        for team in ("dev", "ops"):
            self.teams.add("alice", team, a.agent_id)
        self.assertEqual(self.teams.teams_of("alice", a.agent_id), ["dev", "ops"])

        self.teams.rename("alice", "ops", "ops2")
        self.assertEqual(self.teams.teams_of("alice", a.agent_id), ["dev", "ops2"])
        self.teams.delete("alice", "dev")
        self.assertFalse(self.teams.exists("alice", "dev"))
        self.assertIsNotNone(self.agents.get(a.agent_id))

    def test_deleting_an_agent_takes_it_out_of_its_teams(self):
        a = self.agents.create("alice", name="Coder", driver=WEBOT, config={"session": "s1"})
        self.teams.add("alice", "dev", a.agent_id)
        self.agents.delete(a.agent_id)
        self.assertEqual(self.teams.members("alice", "dev"), [])


class TestManifest(TeamCase):
    INTERNAL = [
        {"name": "Planner", "tag": "plan", "is_primary": True},
        {"name": "Coder", "tag": "coder", "session": "s1", "note": "kept"},
    ]
    EXTERNAL = [
        {"name": "Claw", "tag": "openclaw", "platform": "openclaw", "global_name": "main",
         "meta": {"api_url": "http://oc", "model": "agent:main"}, "config": {"agents": {}}, "workspace_files": {"a": "1"}},
    ]

    def test_import_makes_agents_and_members(self):
        existing = self.agents.create("alice", name="Coder", driver=WEBOT, config={"session": "s1"})
        import_entries(self.teams, "alice", "dev", self.INTERNAL, self.EXTERNAL)

        self.assertEqual(self.roles(), [("Planner", WEBOT, True), ("Coder", WEBOT, False), ("Claw", OPENCLAW, False)])
        coder = self.teams.member("alice", "dev", "Coder")
        self.assertEqual(coder.agent.agent_id, existing.agent_id)  # the session names an agent already there
        planner = self.teams.member("alice", "dev", "Planner").agent
        self.assertEqual((planner.config["persona"], planner.config["team"]), ("plan", "dev"))
        claw = self.teams.member("alice", "dev", "Claw").agent
        self.assertEqual((claw.config["api_url"], claw.config["model"]), ("http://oc", "agent:main"))

    def test_reimport_follows_the_entries_and_keeps_agents(self):
        import_entries(self.teams, "alice", "dev", self.INTERNAL, [])
        planner = self.teams.member("alice", "dev", "Planner").agent
        coder = self.teams.member("alice", "dev", "Coder").agent
        import_entries(self.teams, "alice", "dev", [dict(self.INTERNAL[1])], [])

        self.assertEqual(self.roles(), [("Coder", WEBOT, False)])
        self.assertEqual(self.teams.member("alice", "dev", "Coder").agent.agent_id, coder.agent_id)
        self.assertIsNotNone(self.agents.get(planner.agent_id))

    def test_export_is_the_same_format(self):
        import_entries(self.teams, "alice", "dev", self.INTERNAL, self.EXTERNAL)
        internal, external = export_entries(self.teams, "alice", "dev", portable=False)
        self.assertEqual(internal[1], {"name": "Coder", "tag": "coder", "note": "kept", "session": "s1"})
        self.assertTrue(internal[0]["is_primary"])
        self.assertEqual(external[0]["global_name"], "main")
        self.assertEqual(external[0]["workspace_files"], {"a": "1"})  # the OpenClaw snapshot travels along

        internal, external = export_entries(self.teams, "alice", "dev", portable=True)
        self.assertNotIn("session", internal[0])
        self.assertNotIn("global_name", external[0])
        # Importing a portable package elsewhere makes new agents.
        self.teams.create("bob", "copy")
        external[0]["global_name"] = "bob_1"
        import_entries(self.teams, "bob", "copy", internal, external)
        self.assertEqual(len(self.teams.members("bob", "copy")), 3)
        self.assertNotIn(self.teams.member("bob", "copy", "Coder").agent.config["session"], ("s1", ""))

    def test_folder_import_removes_the_files(self):
        folder = self.teams.folder("alice", "dev")
        (folder / "internal_agents.json").write_text(dumps(self.INTERNAL), encoding="utf-8")
        import_folder(self.teams, "alice", "dev")
        self.assertEqual(len(self.teams.members("alice", "dev")), 2)
        self.assertFalse((folder / "internal_agents.json").exists())

    def test_external_entry_needs_a_runtime_name(self):
        with self.assertRaises(ValueError):
            import_entries(self.teams, "alice", "dev", [], [{"name": "X", "platform": "codex"}])


class TestPreset(TeamCase):
    def test_install_makes_every_role_an_agent(self):
        from services.team_preset_assets import get_team_preset_bundle, install_team_preset

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
        self.gateway = mock.Mock(spec=AgentGateway)
        self.gateway.ask = mock.AsyncMock(return_value=AgentReply(ok=True, content="from the lead"))
        app = FastAPI()
        app.include_router(create_teams_router(internal_token=TOKEN, verify_password=lambda u, p: False,
                                               teams=self.teams, gateway=self.gateway))
        self.client = TestClient(app)
        self.coder = self.agents.create("alice", name="Coder", driver=WEBOT, config={"session": "s1"})

    def call(self, method, path, **kwargs):
        return self.client.request(method, path, headers={"Authorization": f"Bearer {TOKEN}:alice"}, **kwargs)

    def test_members_and_lead(self):
        self.assertEqual(self.call("POST", "/v1/teams/dev/messages", json={"text": "hi"}).status_code, 409)
        added = self.call("POST", "/v1/teams/dev/members", json={"agent": "alice/coder", "role": "Builder"}).json()
        self.assertEqual((added["role"], added["agent"]["agent_id"]), ("Builder", self.coder.agent_id))
        self.call("PATCH", "/v1/teams/dev/members/coder", json={"is_lead": True})
        card = self.call("GET", "/v1/teams/dev").json()
        self.assertEqual(card["lead"], self.coder.agent_id)

        reply = self.call("POST", "/v1/teams/dev/messages", json={"text": "hi"}).json()
        self.assertEqual(reply["content"], "from the lead")
        self.assertEqual(self.gateway.ask.await_args.kwargs["context"], {"team": "dev"})

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
