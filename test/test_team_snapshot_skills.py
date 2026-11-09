import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import services.team_snapshot_skills as snapshot_skills
import webot.skills as webot_skills


class TeamSnapshotSkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmppath = Path(self.tmpdir.name)
        self.orig_user_files_skills = webot_skills.USER_FILES_DIR
        self.orig_user_files_snapshot = snapshot_skills.USER_FILES_DIR
        webot_skills.USER_FILES_DIR = self.tmppath / "user_files"
        snapshot_skills.USER_FILES_DIR = self.tmppath / "user_files"

    def tearDown(self):
        webot_skills.USER_FILES_DIR = self.orig_user_files_skills
        snapshot_skills.USER_FILES_DIR = self.orig_user_files_snapshot
        self.tmpdir.cleanup()

    def _make_skill(self, user_id: str, name: str) -> None:
        content = f"---\nname: {name}\ndescription: {name} desc\n---\n\nUse carefully."
        created = webot_skills.create_skill(user_id, name=name, content=content)
        assert created["success"], created

    def _make_team_skill(self, user_id: str, team: str, name: str) -> None:
        content = f"---\nname: {name}\ndescription: {name} desc\n---\n\nTeam scoped."
        created = webot_skills.create_skill(user_id, name=name, content=content, team=team)
        assert created["success"], created

    def test_export_and_restore_user_and_team_skills_snapshot(self):
        self._make_skill("alice", "deploy-script")
        self._make_team_skill("alice", "ops", "incident-runbook")
        skill_dir = snapshot_skills._user_skills_dir("alice") / "deploy-script"
        (skill_dir / "references").mkdir(parents=True, exist_ok=True)
        (skill_dir / "references" / "notes.md").write_text("hello", encoding="utf-8")
        team_skill_dir = snapshot_skills._team_skills_dir("alice", "ops") / "incident-runbook"
        (team_skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (team_skill_dir / "scripts" / "triage.py").write_text("print('triage')", encoding="utf-8")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            personal_result = snapshot_skills.add_user_skills_to_zip(zf, "alice")
            team_result = snapshot_skills.add_team_skills_to_zip(zf, "alice", "ops")
            exported_names = sorted(zf.namelist())
        self.assertGreaterEqual(personal_result["files"], 2)
        self.assertGreaterEqual(team_result["files"], 2)
        self.assertIn("skills/clawcross_personal/deploy-script/SKILL.md", exported_names)
        self.assertIn("skills/clawcross_team/incident-runbook/SKILL.md", exported_names)

        team_dir = self.tmppath / "team_restore"
        team_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(buf.getvalue()), "r") as zf:
            zf.extractall(team_dir)

        shutil_target = snapshot_skills._user_skills_dir("bob")
        self.assertFalse(shutil_target.exists())
        result = snapshot_skills.restore_skills_from_team_dir(team_dir, "bob", "ops")

        restored_skill = snapshot_skills._user_skills_dir("bob") / "deploy-script" / "SKILL.md"
        restored_index = snapshot_skills._user_skills_dir("bob") / "SKILLS_INDEX.md"
        restored_team_skill = snapshot_skills._team_skills_dir("bob", "ops") / "incident-runbook" / "SKILL.md"
        restored_team_index = snapshot_skills._team_skills_dir("bob", "ops") / "SKILLS_INDEX.md"
        self.assertTrue(restored_skill.is_file())
        self.assertTrue(restored_index.is_file())
        self.assertTrue(restored_team_skill.is_file())
        self.assertTrue(restored_team_index.is_file())
        self.assertEqual(result["restored_user_skill_dirs"], 1)
        self.assertEqual(result["restored_team_skill_dirs"], 1)
        self.assertGreaterEqual(result["restored_user_files"], 2)
        self.assertGreaterEqual(result["restored_team_files"], 2)

    def test_restore_accepts_legacy_skill_roots(self):
        legacy_root = self.tmppath / "legacy_team"
        (legacy_root / "clawcross_user_skills" / "legacy-user").mkdir(parents=True, exist_ok=True)
        (legacy_root / "clawcross_team_skills" / "legacy-team").mkdir(parents=True, exist_ok=True)
        (legacy_root / "clawcross_user_skills" / "legacy-user" / "SKILL.md").write_text(
            "---\nname: legacy-user\ndescription: legacy user\n---\n\nlegacy",
            encoding="utf-8",
        )
        (legacy_root / "clawcross_team_skills" / "legacy-team" / "SKILL.md").write_text(
            "---\nname: legacy-team\ndescription: legacy team\n---\n\nlegacy",
            encoding="utf-8",
        )

        result = snapshot_skills.restore_skills_from_team_dir(legacy_root, "bob", "ops")

        self.assertEqual(result["restored_user_skill_dirs"], 1)
        self.assertEqual(result["restored_team_skill_dirs"], 1)
        self.assertTrue((snapshot_skills._user_skills_dir("bob") / "legacy-user" / "SKILL.md").is_file())
        self.assertTrue((snapshot_skills._team_skills_dir("bob", "ops") / "legacy-team" / "SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
