import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import front
import services.team_snapshot_skills as snapshot_skills
import webot.skills as webot_skills


def _skill_content(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\nBody"


class _MockJsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class TeamSnapshotUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        front.app.config.update(TESTING=True)

    def setUp(self):
        self.client = front.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "upload-user"

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

        with TemporaryDirectory() as tmpdir:
            user_files_dir = Path(tmpdir) / "data" / "user_files"
            with mock.patch.object(front, "root_dir", tmpdir), mock.patch.object(
                snapshot_skills, "USER_FILES_DIR", user_files_dir
            ), mock.patch.object(webot_skills, "USER_FILES_DIR", user_files_dir):
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

            personal_skill = user_files_dir / "upload-user" / "skills" / "personal-helper" / "SKILL.md"
            team_skill = user_files_dir / "upload-user" / "teams" / "demo" / "skills" / "team-helper" / "SKILL.md"
            self.assertTrue(personal_skill.is_file())
            self.assertTrue(team_skill.is_file())
            self.assertFalse(
                (user_files_dir / "upload-user" / "teams" / "demo" / "skills" / "clawcross_team").exists()
            )

    def test_upload_restores_new_format_openclaw_agent_and_managed_skills(self):
        snapshot_zip = io.BytesIO()
        with zipfile.ZipFile(snapshot_zip, "w", zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr(
                "external_agents.json",
                json.dumps(
                    [
                        {
                            "name": "architect",
                            "platform": "openclaw",
                            "global_name": "source_architect",
                            "config": {},
                            "workspace_files": {},
                        }
                    ]
                ),
            )
            zip_file.writestr(
                "skills/openclaw_agents/architect/agent-skill/SKILL.md",
                _skill_content("agent-skill", "agent skill"),
            )
            zip_file.writestr(
                "skills/openclaw_managed/managed-skill/SKILL.md",
                _skill_content("managed-skill", "managed skill"),
            )
        snapshot_zip.seek(0)

        with TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "restored_workspace"
            user_files_dir = Path(tmpdir) / "data" / "user_files"
            with mock.patch.object(front, "root_dir", tmpdir), mock.patch.object(
                snapshot_skills, "USER_FILES_DIR", user_files_dir
            ), mock.patch.object(webot_skills, "USER_FILES_DIR", user_files_dir), mock.patch.object(
                front.requests,
                "post",
                return_value=_MockJsonResponse({"ok": True, "workspace": str(workspace)}),
            ):
                response = self.client.post(
                    "/teams/snapshot/upload",
                    data={"team": "demo", "file": (snapshot_zip, "snapshot.zip")},
                    content_type="multipart/form-data",
                )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["success"])
            self.assertIn("1 OpenClaw agents restored", payload["message"])
            self.assertTrue((workspace / "skills" / "agent-skill" / "SKILL.md").is_file())
            self.assertTrue((workspace / "skills" / "managed-skill" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
