"""Memory entries hide paths and keep ordinary supporting-file access separate."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mcp_servers import filemanager
from webot import skills
from webot.skill_memory import memory_target


class MemoryFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patched = patch.object(skills, "USER_FILES_DIR", self.root / "users")
        patched.start()
        self.addCleanup(patched.stop)

    def write(self, name, content="经验正文", **kwargs):
        return asyncio.run(filemanager.write_file("alice", name, content, storage="memory", **kwargs))

    def items(self, **kwargs):
        return json.loads(asyncio.run(filemanager.list_files("alice", storage="memory", **kwargs)))["items"]

    def test_name_create_id_update_and_prompt_never_expose_paths(self):
        created = json.loads(self.write("中文经验", "# 标题\n第一条经验"))
        entry_id = created["id"]
        self.assertEqual(created["name"], "中文经验")
        self.assertNotIn(str(self.root), json.dumps(created))
        self.assertEqual(self.items()[0]["id"], entry_id)
        content = asyncio.run(filemanager.read_file("alice", entry_id, storage="memory"))
        self.assertIn("第一条经验", content)
        self.assertNotIn(str(self.root), content)
        changed = json.loads(self.write(entry_id, mode="str_replace", content="", old_string="第一条", new_string="修正后"))
        self.assertEqual(changed["id"], entry_id)
        self.assertEqual(changed["name"], "中文经验")
        prompt = skills.build_user_skills_listing("alice")
        self.assertIn(entry_id, prompt)
        self.assertNotIn(str(self.root), prompt)
        self.assertNotIn("skill_manage", prompt)
        index = self.root / "users/alice/skills/SKILLS_INDEX.md"
        self.assertIn("中文经验", index.read_text())

    def test_existing_skills_are_available_without_migration(self):
        skills.create_skill("alice", name="existing", category="ops", content="---\nname: existing\ndescription: older entry\n---\nOld body")
        entry = self.items()[0]
        updated = json.loads(self.write(entry["id"], "New body"))
        self.assertEqual(updated["description"], "older entry")
        self.assertEqual(len(self.items()), 1)
        self.assertIn("New body", skills.get_skill("alice", name="existing")["body"])

    def test_memory_never_accesses_supporting_files_or_paths(self):
        entry = json.loads(self.write("safe"))
        path = memory_target("alice", entry["id"])["_path"]
        support = path.parent / "notes.md"
        asyncio.run(filemanager.write_file("alice", str(support), "normal file write"))
        for selector in (str(path), "safe/SKILL.md", "../safe", "safe/notes.md"):
            with self.subTest(selector=selector):
                self.assertTrue(self.write(selector).startswith("❌"))
                read = asyncio.run(filemanager.read_file("alice", selector, storage="memory"))
                self.assertTrue(read.startswith("❌"))
        self.assertEqual(support.read_text(), "normal file write")
        listing = asyncio.run(filemanager.list_files("alice", storage="memory"))
        self.assertNotIn("notes.md", listing)
        deleted = json.loads(asyncio.run(filemanager.delete_file("alice", entry["id"], storage="memory")))
        self.assertTrue(deleted["success"])
        self.assertFalse(path.exists())
        self.assertTrue(support.exists())

    def test_user_and_team_scopes_are_separate_and_names_may_be_ambiguous(self):
        personal = json.loads(self.write("same", "Personal"))
        team = json.loads(self.write("same", "Team", team="ops"))
        self.assertNotEqual(personal["id"], team["id"])
        self.assertEqual(len(self.items(team="ops")), 2)
        self.assertTrue(asyncio.run(filemanager.read_file("alice", "same", storage="memory", team="ops")).startswith("❌"))
        self.assertIn("Personal", asyncio.run(filemanager.read_file("alice", personal["id"], storage="memory", team="ops")))
        self.assertTrue(self.write(personal["id"], "Bad", team="ops").startswith("❌"))
        self.assertTrue(asyncio.run(filemanager.read_file("bob", personal["id"], storage="memory")).startswith("❌"))
        self.assertTrue(self.write("same", team="../bob").startswith("❌"))

    def test_symlink_targets_are_excluded(self):
        created = json.loads(self.write("safe"))
        target = memory_target("alice", created["id"])["_path"]
        outside = self.root / "outside.md"
        outside.write_text(target.read_text())
        target.unlink()
        target.symlink_to(outside)
        self.assertEqual(self.items(), [])
        self.assertTrue(self.write("safe", "Bad").startswith("❌"))
        self.assertTrue(asyncio.run(filemanager.read_file("alice", created["id"], storage="memory")).startswith("❌"))
        self.assertNotEqual(outside.read_text(), "Bad")

    def test_concurrent_sha_guard_allows_only_one_writer(self):
        initial = json.loads(self.write("shared", "Initial"))
        def update(text):
            return self.write(initial["id"], text, expected_sha256=initial["sha256"])
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, ("First", "Second")))
        self.assertEqual(sum(result.startswith("{") for result in results), 1)
        self.assertEqual(sum("sha256 不匹配" in result for result in results), 1)

    def test_unknown_id_and_invalid_body_do_not_create_or_corrupt_entries(self):
        self.assertTrue(self.write("mem-" + "0" * 20).startswith("❌"))
        entry = json.loads(self.write("safe"))
        for invalid in ("---\nname: safe\n---\nmissing description", "x" * 102401):
            with self.subTest(invalid=invalid[:20]):
                self.assertTrue(self.write(entry["id"], invalid).startswith("❌"))
        current = memory_target("alice", entry["id"])["_path"].read_text()
        self.assertIn("经验正文", current)

    def test_evolution_report_keeps_analysis_but_hides_storage_metadata(self):
        from mcp_servers.skills import skill_evolution_report
        entry = json.loads(self.write("report"))
        report = {"success": True, "local_state": {"skill_path": str(self.root), "cwd": str(self.root)},
                  "validation_report": {"env_fingerprint": {"repo_root": str(self.root)}}, "frontier": [{"candidate_id": "candidate"}]}
        with patch("webot.skill_evolution.analyze_skill_evolution", return_value=report):
            result = asyncio.run(skill_evolution_report("alice", entry["id"]))
        self.assertNotIn(str(self.root), result)
        self.assertIn("candidate", result)
        self.assertEqual(json.loads(result)["memory"]["id"], entry["id"])
