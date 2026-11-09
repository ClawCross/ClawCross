import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import services.team_creator_service as svc


class ClawcrossCreatorJobStoreTests(unittest.TestCase):
    def test_create_job_persists_pending_record(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "team_creator_jobs.db"
            job = svc.create_job("构建一个增长团队", "增长团队", owner_id="user-a", db_path=db_path)

            self.assertEqual(job.status, "pending")
            self.assertTrue(db_path.exists())

            saved = svc.get_job(job.job_id, owner_id="user-a", db_path=db_path)
            self.assertIsNotNone(saved)
            self.assertEqual(saved.team_name, "增长团队")
            self.assertEqual(saved.task_description, "构建一个增长团队")
            self.assertEqual(saved.owner_id, "user-a")
            self.assertEqual(saved.status, "pending")

    def test_update_job_persists_result_payloads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "team_creator_jobs.db"
            job = svc.create_job("构建产品团队", "产品团队", owner_id="user-a", db_path=db_path)

            roles = [
                {
                    "role_name": "产品经理",
                    "personality_traits": ["结构化"],
                    "primary_responsibilities": ["拆解需求"],
                    "depends_on": [],
                    "tools_used": ["Notion"],
                }
            ]
            team_config = {
                "summary": {"total_roles": 1, "workflow_nodes": 3},
                "oasis_experts": [{"name": "产品经理", "tag": "product_manager"}],
            }

            updated = svc.update_job(
                job.job_id,
                owner_id="user-a",
                status="complete",
                extracted_roles=roles,
                team_config=team_config,
                db_path=db_path,
            )

            self.assertIsNotNone(updated)
            self.assertEqual(updated.status, "complete")

            saved = svc.get_job(job.job_id, owner_id="user-a", db_path=db_path)
            self.assertIsNotNone(saved)
            payload = saved.to_dict(include_payload=True)
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["extracted_roles_count"], 1)
            self.assertEqual(payload["team_config_summary"]["total_roles"], 1)
            self.assertEqual(payload["extracted_roles"][0]["role_name"], "产品经理")
            self.assertEqual(payload["team_config"]["oasis_experts"][0]["name"], "产品经理")

    def test_list_jobs_filters_by_owner_and_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "team_creator_jobs.db"
            first = svc.create_job("任务 A", "团队 A", owner_id="user-a", db_path=db_path)
            second = svc.create_job("任务 B", "团队 B", owner_id="user-b", db_path=db_path)

            svc.update_job(first.job_id, owner_id="user-a", status="failed", error="模型超时", db_path=db_path)
            svc.update_job(second.job_id, owner_id="user-b", status="complete", team_config={"summary": {"total_roles": 2}}, db_path=db_path)

            owner_a_jobs = svc.list_jobs(owner_id="user-a", db_path=db_path)
            owner_b_jobs = svc.list_jobs(owner_id="user-b", db_path=db_path)

            self.assertEqual(len(owner_a_jobs), 1)
            self.assertEqual(owner_a_jobs[0]["job_id"], first.job_id)
            self.assertEqual(owner_a_jobs[0]["status"], "failed")
            self.assertEqual(owner_a_jobs[0]["error"], "模型超时")

            self.assertEqual(len(owner_b_jobs), 1)
            self.assertEqual(owner_b_jobs[0]["job_id"], second.job_id)
            self.assertEqual(owner_b_jobs[0]["team_config_summary"]["total_roles"], 2)

            reloaded = svc.get_job(second.job_id, owner_id="user-b", db_path=db_path)
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.status, "complete")
            self.assertEqual(reloaded.team_config["summary"]["total_roles"], 2)


if __name__ == "__main__":
    unittest.main()
