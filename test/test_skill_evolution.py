import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import webot.skill_evolution as skill_evolution
import webot.skills as webot_skills
import webot.trajectory as trajectory


class SkillEvolutionTests(unittest.TestCase):
    def test_analyze_and_apply_skill_evolution_from_failed_trajectory(self):
        with TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            original_skills_user_files = webot_skills.USER_FILES_DIR
            original_evolution_user_files = skill_evolution.USER_FILES_DIR
            original_trajectory_dir = trajectory.DATA_DIR
            webot_skills.USER_FILES_DIR = tmp_root / "user_files"
            skill_evolution.USER_FILES_DIR = tmp_root / "user_files"
            trajectory.DATA_DIR = tmp_root / "data" / "trajectories"

            try:
                content = """---
name: ops-playbook
description: Startup and test workflow
---

# Ops Playbook

Always start from the repo root.
"""
                created = webot_skills.create_skill("alice", name="ops-playbook", content=content)
                self.assertTrue(created["success"])

                trajectory.save_trajectory(
                    user_id="alice",
                    session_id="default",
                    messages=[
                        {"role": "user", "content": "Run the regression check."},
                        {"role": "assistant", "content": "pytest timed out after the import step."},
                    ],
                    model="gpt-test",
                    completed=False,
                    metadata={
                        "command": "pytest test/test_skill_evolution.py",
                        "error": "TimeoutError: pytest timed out after import. ModuleNotFoundError: app",
                        "stderr": "TimeoutError: pytest timed out after import. ModuleNotFoundError: app",
                    },
                )

                digest_path = tmp_root / "user_files" / "alice" / "skill_evolution" / "latest_failure_digest.md"
                self.assertTrue(digest_path.is_file())

                report = skill_evolution.analyze_skill_evolution(
                    "alice",
                    name="ops-playbook",
                    command="pytest test/test_skill_evolution.py",
                    strategy="repair-only",
                )
                self.assertTrue(report["success"])
                self.assertGreaterEqual(report["failure_count"], 1)
                self.assertTrue(report["frontier"])
                self.assertIn("summary", report)
                self.assertEqual(report["strategy"]["name"], "repair-only")
                self.assertEqual(report["history_diagnostics"]["history_entries"], 0)
                self.assertEqual(report["validation_report"]["type"], "ValidationReport")
                self.assertEqual(report["local_state"]["feedback_history_entries"], 0)

                applied = skill_evolution.apply_skill_evolution(
                    "alice",
                    name="ops-playbook",
                    command="pytest test/test_skill_evolution.py",
                    error_text="TimeoutError: pytest timed out after import. ModuleNotFoundError: app",
                    source="unit-test",
                    strategy="repair-only",
                )
                self.assertTrue(applied["success"])
                self.assertTrue(Path(applied["path"]).is_file())
                self.assertTrue(Path(applied["latest_report_path"]).is_file())
                self.assertTrue(Path(applied["validation_report_path"]).is_file())
                self.assertTrue(Path(applied["frontier_path"]).is_file())
                self.assertTrue(applied["updated"])
                self.assertFalse(applied["empty_cycle"])
                self.assertEqual(applied["strategy_name"], "repair-only")

                updated_skill = webot_skills.get_skill("alice", name="ops-playbook")
                self.assertIn(skill_evolution.EVOLUTION_BEGIN, updated_skill["content"])
                self.assertIn("Self-Evolution Loop", updated_skill["content"])
                self.assertIn("Latest Trigger Command", updated_skill["content"])
                self.assertIn("Strategy", updated_skill["content"])

                repeated = skill_evolution.apply_skill_evolution(
                    "alice",
                    name="ops-playbook",
                    command="pytest test/test_skill_evolution.py",
                    error_text="TimeoutError: pytest timed out after import. ModuleNotFoundError: app",
                    source="unit-test",
                    strategy="repair-only",
                )
                self.assertTrue(repeated["success"])
                self.assertTrue(repeated["empty_cycle"])
                self.assertFalse(repeated["updated"])
            finally:
                webot_skills.USER_FILES_DIR = original_skills_user_files
                skill_evolution.USER_FILES_DIR = original_evolution_user_files
                trajectory.DATA_DIR = original_trajectory_dir

    def test_auto_strategy_enters_steady_state_after_repeated_empty_cycles(self):
        with TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            original_skills_user_files = webot_skills.USER_FILES_DIR
            original_evolution_user_files = skill_evolution.USER_FILES_DIR
            webot_skills.USER_FILES_DIR = tmp_root / "user_files"
            skill_evolution.USER_FILES_DIR = tmp_root / "user_files"

            try:
                content = """---
name: ops-playbook
description: Startup and test workflow
---

# Ops Playbook

Keep verifier commands explicit.
"""
                created = webot_skills.create_skill("alice", name="ops-playbook", content=content)
                self.assertTrue(created["success"])

                feedback_path = (
                    tmp_root
                    / "user_files"
                    / "alice"
                    / "skill_evolution"
                    / "skills"
                    / "ops-playbook"
                    / "feedback_history.jsonl"
                )
                for idx in range(3):
                    skill_evolution._append_jsonl(
                        feedback_path,
                        {
                            "timestamp": f"2026-04-15T00:00:0{idx}+00:00",
                            "intent": "repair",
                            "signal_ids": ["verification-loop"],
                            "failure_count": 1,
                            "validation_overall_ok": False,
                            "empty_cycle": True,
                            "cycle_signature": f"sig-{idx}",
                        },
                    )

                report = skill_evolution.analyze_skill_evolution(
                    "alice",
                    name="ops-playbook",
                    error_text="still failing with the same timeout",
                    command="pytest test/test_skill_evolution.py",
                )
                self.assertTrue(report["success"])
                self.assertEqual(report["strategy"]["name"], "steady-state")
                self.assertTrue(report["history_diagnostics"]["saturation_detected"])
                self.assertTrue(report["history_diagnostics"]["stagnation_detected"])
            finally:
                webot_skills.USER_FILES_DIR = original_skills_user_files
                skill_evolution.USER_FILES_DIR = original_evolution_user_files

    def test_apply_skill_evolution_for_team_skill(self):
        with TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            original_skills_user_files = webot_skills.USER_FILES_DIR
            original_evolution_user_files = skill_evolution.USER_FILES_DIR
            webot_skills.USER_FILES_DIR = tmp_root / "user_files"
            skill_evolution.USER_FILES_DIR = tmp_root / "user_files"

            try:
                content = """---
name: team-runbook
description: Team incident workflow
---

Start with the team dashboard.
"""
                created = webot_skills.create_skill("alice", name="team-runbook", content=content, team="ops")
                self.assertTrue(created["success"])

                applied = skill_evolution.apply_skill_evolution(
                    "alice",
                    name="team-runbook",
                    team="ops",
                    command="pytest test/test_skill_evolution.py",
                    error_text="same timeout in team workflow",
                    source="unit-test",
                    strategy="repair-only",
                )
                self.assertTrue(applied["success"])
                updated_skill = webot_skills.get_skill("alice", name="team-runbook", team="ops")
                self.assertEqual(updated_skill["scope"], "team")
                self.assertIn(skill_evolution.EVOLUTION_BEGIN, updated_skill["content"])
                self.assertIn("/teams/ops/skill_evolution/", applied["feedback_history_path"])
            finally:
                webot_skills.USER_FILES_DIR = original_skills_user_files
                skill_evolution.USER_FILES_DIR = original_evolution_user_files

    def test_update_markdown_skill_document_writes_managed_block_and_report(self):
        with TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            (repo_root / ".git").mkdir(parents=True, exist_ok=True)
            skill_path = repo_root / "SKILL.md"
            skill_path.write_text(
                "# Demo Skill\n\nThis is a repo-level skill document.\n",
                encoding="utf-8",
            )

            result = skill_evolution.update_markdown_skill_document(
                skill_path=skill_path,
                command="pytest test/test_demo.py",
                stderr="ModuleNotFoundError: demo_app",
                exit_code=1,
                strategy="harden",
            )

            self.assertTrue(result["success"])
            self.assertTrue(result["updated"])
            updated = skill_path.read_text(encoding="utf-8")
            self.assertIn(skill_evolution.EVOLUTION_BEGIN, updated)
            self.assertIn("Self-Evolution Loop", updated)
            self.assertTrue(Path(result["report_path"]).is_file())
            self.assertTrue(Path(result["validation_report_path"]).is_file())
            self.assertEqual(result["strategy_name"], "harden")


if __name__ == "__main__":
    unittest.main()
