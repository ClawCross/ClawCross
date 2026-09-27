"""Teams as views over the agent registry: the manifest keeps its format, roles
bind to agents, and a team is addressable through its lead."""

import asyncio
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
for path in (str(PROJECT_ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from agents.registry import AgentRegistry  # noqa: E402
from teams.view import TeamHasNoLead, TeamNotFound, TeamView  # noqa: E402

BUILDER_MANIFEST = [  # what team-builder writes with write_file: no sessions
    {"name": "Requirements Interviewer", "tag": "interviewer"},
    {"name": "Persona Designer", "tag": "persona_designer", "is_primary": True},
]


class TeamViewTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.user_files = Path(self.tmp.name) / "user_files"
        self.registry = AgentRegistry(Path(self.tmp.name) / "group_chat.db", self.user_files)
        self.view = TeamView(self.registry)

    def manifest(self, team: str, filename: str = "internal_agents.json") -> Path:
        return self.user_files / "alice" / "teams" / team / filename

    def write(self, team: str, entries: list, filename: str = "internal_agents.json") -> None:
        path = self.manifest(team, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


class TestManifest(TeamViewTestCase):
    def test_roles_without_sessions_get_one_in_the_same_format(self):
        self.write("builder", BUILDER_MANIFEST)

        entries = self.view.entries("alice", "builder", "internal")

        self.assertTrue(all(e["session"] for e in entries))
        on_disk = json.loads(self.manifest("builder").read_text(encoding="utf-8"))
        self.assertEqual(on_disk, entries)
        for original, stamped in zip(BUILDER_MANIFEST, on_disk):
            # Every original field and its order kept; session appended last, as _ia_save does.
            self.assertEqual(list(stamped)[:-1], list(original))
            self.assertEqual(list(stamped)[-1], "session")
        self.assertEqual(
            self.manifest("builder").read_text(encoding="utf-8"),
            json.dumps(on_disk, ensure_ascii=False, indent=2),
        )

    def test_reconcile_is_idempotent_and_keeps_existing_sessions(self):
        self.write("builder", BUILDER_MANIFEST + [{"name": "Keeper", "session": "keep1"}])
        first = self.view.entries("alice", "builder", "internal")
        mtime = self.manifest("builder").stat().st_mtime_ns
        second = self.view.entries("alice", "builder", "internal")

        self.assertEqual(first, second)
        self.assertEqual(self.manifest("builder").stat().st_mtime_ns, mtime)
        self.assertEqual(second[-1]["session"], "keep1")

    def test_export_zip_is_unchanged_by_reconcile(self):
        import front

        front.app.config.update(TESTING=True)
        client = front.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = "alice"
        self.write("builder", BUILDER_MANIFEST)

        def export() -> bytes:
            with mock.patch.object(front, "USER_FILES_DIR", self.user_files):
                response = client.post("/teams/snapshot/download", json={"team": "builder"})
            self.assertEqual(response.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                return archive.read("internal_agents.json")

        before = export()
        self.view.reconcile("alice", "builder")
        self.assertIn("session", self.manifest("builder").read_text(encoding="utf-8"))
        self.assertEqual(export(), before)
        self.assertEqual(json.loads(before), BUILDER_MANIFEST)


class TestMembership(TeamViewTestCase):
    def test_roles_bind_to_registry_agents_and_the_lead_is_primary(self):
        self.write("dev", [
            {"name": "Coder", "tag": "coder", "session": "s1", "is_primary": True},
            {"name": "Reviewer", "tag": "rev", "session": "s2"},
        ])
        self.write("dev", [{"name": "Codex", "tag": "codex", "global_name": "cx", "platform": "codex"}],
                   filename="external_agents.json")

        members = self.view.members("alice", "dev")

        self.assertEqual([(m.role_name, m.kind) for m in members],
                         [("Coder", "internal"), ("Reviewer", "internal"), ("Codex", "external")])
        self.assertTrue(all(m.agent and m.agent.agent_id.startswith("ag_") for m in members))
        self.assertEqual(self.view.lead("alice", "dev").role_name, "Coder")
        self.assertEqual(self.view.member("alice", "dev", "codex").agent.driver, "acpx")

    def test_one_agent_serves_two_teams_under_different_role_names(self):
        self.write("dev", [{"name": "Coder", "session": "shared"}])
        self.write("ops", [{"name": "On-call engineer", "session": "shared"}])

        dev = self.view.member("alice", "dev", "Coder").agent
        ops = self.view.member("alice", "ops", "On-call engineer").agent

        self.assertEqual(dev.agent_id, ops.agent_id)
        self.assertEqual(self.view.teams_of("alice", dev.agent_id), ["dev", "ops"])

    def test_missing_lead_and_missing_team(self):
        self.write("dev", [{"name": "Coder", "session": "s1"}])
        with self.assertRaises(TeamHasNoLead):
            self.view.lead("alice", "dev")
        with self.assertRaises(TeamNotFound):
            self.view.lead("alice", "nope")


class TestReadersUseTheView(TeamViewTestCase):
    def test_group_from_team_includes_roles_written_without_sessions(self):
        import api.group_service as group_service
        from api.group_models import GroupCreateRequest

        self.write("builder", BUILDER_MANIFEST)

        async def create():
            db = str(Path(self.tmp.name) / "groups.db")
            await group_service.init_group_db(db)
            service = group_service.GroupService(
                internal_token="tok", verify_password=lambda u, p: False,
                checkpoint_db_path=db, group_db_path=db, agent=None,
            )
            with mock.patch.object(group_service, "USER_FILES_DIR", self.user_files):
                return await service.create_group(
                    GroupCreateRequest(name="builders", team_name="builder"), "Bearer tok:alice",
                )

        created = asyncio.run(create())

        self.assertEqual(created["member_count"], 2)  # used to be 0: no sessions, no members
        persona_designer = self.view.member("alice", "builder", "Persona Designer")
        self.assertEqual(created["primary_agent_global_id"], persona_designer.binding_ref)

    def test_oasis_resolves_roles_written_without_sessions(self):
        from oasis import engine as engine_module
        from oasis.engine import DiscussionEngine
        from oasis.forum import DiscussionForum

        self.write("builder", BUILDER_MANIFEST)
        yaml = 'version: 2\nrepeat: false\nplan:\n  - id: n1\n    expert: "#oasis#Persona Designer"\n'
        with mock.patch.object(engine_module, "USER_FILES_DIR", self.user_files), \
                mock.patch.object(engine_module, "create_chat_model", mock.MagicMock()):
            built = DiscussionEngine(
                forum=DiscussionForum("t", "q", "alice"), schedule_yaml=yaml, user_id="alice", team="builder",
            )
        self.assertEqual(len(built.experts), 1)
        self.assertEqual(
            built.experts[0].session_id,
            self.view.member("alice", "builder", "Persona Designer").binding_ref,
        )


class TestTeamAddress(TeamViewTestCase):
    def setUp(self):
        super().setUp()
        self.write("dev", [
            {"name": "Coder", "tag": "coder", "session": "s1"},
        ])
        self.write("dev", [
            {"name": "Codex", "tag": "codex", "global_name": "cx", "platform": "codex", "is_primary": True},
        ], filename="external_agents.json")
        self.write("solo", [{"name": "Alone", "session": "s9"}])

    def gateway(self):
        from agents.gateway import AgentGateway

        return AgentGateway(self.registry, agent_base_url="http://agent.test", internal_token="tok")

    def test_team_api_lists_members_and_messages_the_lead_in_team_context(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from agents.messages import AgentReply
        from teams.routes import create_teams_router

        gateway = self.gateway()
        app = FastAPI()
        app.include_router(create_teams_router(internal_token="tok", verify_password=lambda u, p: False, gateway=gateway))
        client = TestClient(app)
        auth = {"Authorization": "Bearer tok:alice"}

        teams = {t["team"]: t for t in client.get("/v1/teams", headers=auth).json()["data"]}
        self.assertEqual(teams["dev"]["lead"], "alice/codex")
        self.assertIsNone(teams["solo"]["lead"])
        self.assertEqual([m["role_name"] for m in teams["dev"]["members"]], ["Coder", "Codex"])

        with mock.patch.object(gateway, "ask", mock.AsyncMock(return_value=AgentReply(ok=True, content="on it"))) as ask:
            body = client.post("/v1/teams/dev/messages", headers=auth, json={"text": "ship it"}).json()
        self.assertEqual((body["content"], body["agent"]["handle"]), ("on it", "codex"))
        self.assertEqual(ask.await_args.kwargs["context"], {"team": "dev"})

        self.assertEqual(client.post("/v1/teams/solo/messages", headers=auth, json={"text": "x"}).status_code, 409)
        self.assertEqual(client.get("/v1/teams/nope", headers=auth).status_code, 404)

    def test_openai_model_can_be_a_team(self):
        from agents.messages import AgentReply
        from api.openai_models import ChatCompletionRequest
        from api.openai_service import OpenAIChatService

        service = OpenAIChatService(
            internal_token="tok", verify_password=lambda u, p: False, agent=None,
            extract_text=str, build_human_message=lambda *a: None,
        )
        service._gateway = self.gateway()

        models = [m["id"] for m in service.list_models("Bearer tok:alice")["data"]]
        self.assertIn("alice/dev", models)
        self.assertNotIn("alice/solo", models)  # no lead, nobody to answer

        with mock.patch.object(service._gateway, "ask", mock.AsyncMock(return_value=AgentReply(ok=True, content="team reply"))) as ask:
            response = asyncio.run(service.handle_chat_completions(
                ChatCompletionRequest(model="alice/dev", messages=[{"role": "user", "content": "status?"}]),
                "Bearer tok:alice",
            ))
        self.assertEqual(response["model"], "alice/dev")
        self.assertEqual(response["choices"][0]["message"]["content"], "team reply")
        self.assertEqual(ask.await_args.args[1].handle, "codex")
        self.assertEqual(ask.await_args.kwargs["context"], {"team": "dev"})


if __name__ == "__main__":
    unittest.main()
