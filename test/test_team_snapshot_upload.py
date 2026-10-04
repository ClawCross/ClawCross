import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src" / "backend"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import frontend.server as front
import teams.snapshot_skills as snapshot_skills
import webot.skills as webot_skills
from agents.store import AgentStore
from teams.store import TeamStore
from teams.manifest import import_entries
from scheduler import internal_alarm


def _skill_content(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\nBody"


class TeamSnapshotUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        front.app.config.update(TESTING=True)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.user_files = self.root / "user_files"
        self.agents = AgentStore(self.root / "agents.db")
        self.teams = TeamStore(self.agents, self.user_files)
        for target, name, value in (
            (front, "USER_FILES_DIR", self.user_files),
            (front, "_teams", lambda: self.teams),
            (snapshot_skills, "USER_FILES_DIR", self.user_files),
            (webot_skills, "USER_FILES_DIR", self.user_files),
            (webot_skills, "WORKSPACE_DIR", self.root / "workspace"),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = front.app.test_client()
        self.login("upload-user")

    def login(self, owner):
        with self.client.session_transaction() as session:
            session["user_id"] = owner

    def upload(self, files, team="demo"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        buf.seek(0)
        return self.client.post("/teams/snapshot/upload", data={
            "team": team, "file": (buf, "snapshot.zip"),
        }, content_type="multipart/form-data")

    def test_upload_restores_new_format_personal_and_team_skills(self):
        snapshot_zip = io.BytesIO()
        with zipfile.ZipFile(snapshot_zip, "w", zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr(
                "skills/clawcross_personal/personal-helper/SKILL.md",
                _skill_content("personal-helper", "personal helper"),
            )
            zip_file.writestr(
                "skills/clawcross_team/team-helper/SKILL.md",
                _skill_content("team-helper", "team helper"),
            )
        snapshot_zip.seek(0)

        response = self.client.post(
            "/teams/snapshot/upload",
            data={"team": "demo", "file": (snapshot_zip, "snapshot.zip")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["skill_restore"]["restored_user_skill_dirs"], 1)
        self.assertEqual(payload["skill_restore"]["restored_team_skill_dirs"], 1)
        personal_skill = snapshot_skills._user_skills_dir("upload-user") / "personal-helper" / "SKILL.md"
        team_skill = snapshot_skills._team_skills_dir("upload-user", "demo") / "team-helper" / "SKILL.md"
        self.assertTrue(personal_skill.is_file())
        self.assertTrue(team_skill.is_file())
        self.assertTrue(personal_skill.is_relative_to(self.root / "workspace"))
        self.assertTrue(team_skill.is_relative_to(self.root / "workspace"))

    def test_upload_imports_an_old_openclaw_entry_as_an_acp_member_without_touching_openclaw(self):
        snapshot_zip = io.BytesIO()
        with zipfile.ZipFile(snapshot_zip, "w", zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr(
                "external_agents.json",
                json.dumps([{"name": "architect", "platform": "openclaw", "global_name": "source_architect",
                             "config": {}, "workspace_files": {}}]),
            )
            # Folders an older ClawCross exported for OpenClaw; nothing reads them now.
            zip_file.writestr("skills/openclaw_agents/architect/agent-skill/SKILL.md",
                              _skill_content("agent-skill", "agent skill"))
        snapshot_zip.seek(0)

        with mock.patch.object(front.requests, "post", side_effect=AssertionError("must not call OpenClaw")):
            response = self.client.post(
                "/teams/snapshot/upload",
                data={"team": "demo", "file": (snapshot_zip, "snapshot.zip")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        member = self.teams.member("upload-user", "demo", "architect")
        self.assertEqual((member.agent.driver, member.agent.platform), ("acpx", "openclaw"))
        self.assertEqual(member.agent.agent_id, "demo_architect")

    def test_assets_only_upload_preserves_members(self):
        import_entries(self.teams, "upload-user", "demo", [{"name": "Writer", "persona": "write"}], [])
        before = self.teams.members("upload-user", "demo")
        response = self.upload({"oasis/yaml/flow.yaml": "version: 2\nplan: []\n"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.teams.members("upload-user", "demo"), before)

    def test_invalid_manifest_does_not_replace_members_or_assets(self):
        import_entries(self.teams, "upload-user", "demo", [{"name": "Writer", "persona": "write"}], [])
        folder = self.teams.folder("upload-user", "demo")
        (folder / "oasis_experts.json").write_text("[]")
        before = self.teams.members("upload-user", "demo")
        for invalid in ("broken", "{}", '[{}]', '[{"name":"X"},{"name":"x"}]'):
            with self.subTest(invalid=invalid):
                response = self.upload({"oasis_experts.json": "overwrite", "internal_agents.json": invalid})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.teams.members("upload-user", "demo"), before)
                self.assertEqual((folder / "oasis_experts.json").read_text(), "[]")

    def test_full_snapshot_roundtrip_without_credentials_or_original_ids(self):
        import_entries(self.teams, "upload-user", "demo", [
            {"name": "Writer", "persona": "write carefully", "tag": "writer", "is_primary": True,
             "tools": ["read_file"], "note": "preserve"},
        ], [
            {"name": "Reviewer", "platform": "codex", "persona": "review carefully",
             "meta": {"model": "test-model", "api_key": "member-secret",
                      "headers": {"Authorization": "Bearer header-secret", "X-API-Key": "key-secret",
                                  "X-Client": "public-header"}, "nested": {"access_token": "nested-secret"}}},
        ])
        folder = self.teams.folder("upload-user", "demo")
        experts = [{"tag": "writer", "name": "Writer", "persona": "write carefully",
                    "api_key": "persona-secret", "model": "test-model", "max_tokens": 4096}]
        (folder / "oasis_experts.json").write_text(json.dumps(experts))
        yaml_content = "version: 2\nrepeat: false\ndiscussion: false\nplan:\n  - id: start\n    manual:\n      author: host\n      content: SNAPSHOT_OASIS_OK\n"
        python_content = "from oasis.workflow import workflow\n@workflow\nasync def main(ctx):\n    ctx.set_result({'status': 'ok'})\n"
        for name, content in (("yaml/flow.yaml", yaml_content), ("python/flow.py", python_content)):
            path = folder / "oasis" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        webot_skills.create_skill("upload-user", name="helper", content=_skill_content("helper", "help"), team="demo")
        skill_dir = snapshot_skills._team_skills_dir("upload-user", "demo") / "helper"
        (skill_dir / "notes.txt").write_text("support file")
        alarm_path = self.root / "tasks.json"
        source_writer = self.teams.member("upload-user", "demo", "Writer").agent.agent_id
        alarm_path.write_text(json.dumps({"daily": {
            "user_id": "upload-user", "agent": source_writer, "cron": "0 8 * * *", "text": "daily work",
        }}))
        with mock.patch.object(internal_alarm, "TASKS_FILE", str(alarm_path)):
            response = self.client.post("/teams/snapshot/download", json={"team": "demo"})
        self.assertEqual(response.status_code, 200)
        data = response.data
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            internal = json.loads(archive.read("internal_agents.json"))
            external = json.loads(archive.read("external_agents.json"))
            persona = json.loads(archive.read("oasis_experts.json"))
            alarms = json.loads(archive.read("cron_jobs.json"))
            self.assertEqual(alarms[0]["target_name"], "Writer")
            self.assertNotIn("session", internal[0])
            self.assertNotIn("global_name", external[0])
            self.assertEqual(external[0]["meta"]["headers"], {"X-Client": "public-header"})
            self.assertEqual(persona[0]["max_tokens"], 4096)
            self.assertNotIn("api_key", persona[0])
            all_json = "\n".join(archive.read(n).decode() for n in archive.namelist() if n.endswith(".json"))
            self.assertNotIn("-secret", all_json)
        self.login("recipient")
        with mock.patch.object(internal_alarm.requests, "post", return_value=mock.Mock(status_code=200)) as restore:
            uploaded = self.client.post("/teams/snapshot/upload", data={
                "team": "copy", "file": (io.BytesIO(data), "snapshot.zip"),
            }, content_type="multipart/form-data")
        self.assertEqual(uploaded.status_code, 200, uploaded.get_json())
        restored_alarm = restore.call_args.kwargs["json"]
        self.assertEqual(restored_alarm["user_id"], "recipient")
        self.assertEqual(restored_alarm["team"], "copy")
        self.assertEqual(restored_alarm["cron"], "0 8 * * *")
        self.assertEqual(restored_alarm["agent"], self.teams.member("recipient", "copy", "Writer").agent.agent_id)
        self.assertNotEqual(restored_alarm["agent"], source_writer)
        writer = self.teams.member("recipient", "copy", "Writer")
        self.assertTrue(writer.is_lead)
        self.assertEqual(writer.extra["note"], "preserve")
        self.assertEqual(writer.agent.config["persona"], "write carefully")
        self.assertEqual(writer.agent.config["tools"], ["read_file"])
        self.assertNotEqual(writer.agent.agent_id, self.teams.member("upload-user", "demo", "Writer").agent.agent_id)
        reviewer = self.teams.member("recipient", "copy", "Reviewer")
        self.assertEqual(reviewer.agent.platform, "codex")
        self.assertEqual(reviewer.agent.config["model"], "test-model")
        restored = self.teams.folder("recipient", "copy")
        self.assertEqual((restored / "oasis/yaml/flow.yaml").read_text(), yaml_content)
        self.assertEqual((restored / "oasis/python/flow.py").read_text(), python_content)
        self.assertEqual((snapshot_skills._team_skills_dir("recipient", "copy") / "helper/notes.txt").read_text(), "support file")
        self.assertFalse((restored / "internal_agents.json").exists())
        self.assertFalse((restored / "cron_jobs.json").exists())


if __name__ == "__main__":
    unittest.main()
