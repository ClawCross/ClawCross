import io
import sys
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import services.team_creator_service as svc


class ClawcrossCreatorZipTests(unittest.TestCase):
    def test_build_team_zip_matches_snapshot_layout(self):
        team_config = {
            "oasis_experts": [
                {
                    "name": "QA Lead",
                    "name_en": "QA Lead",
                    "tag": "qa-lead",
                    "persona": "Defines the test strategy.",
                    "temperature": 0.4,
                }
            ],
            "internal_agents": [
                {
                    "name": "QA Lead",
                    "tag": "qa-lead",
                    "persona": "Defines the test strategy.",
                    "temperature": 0.4,
                }
            ],
            "yaml_workflow": "name: ml_code_testing_pipeline\nmode: execute\n",
        }

        archive = svc.build_team_zip(team_config, "ml code testing pipeline")

        with zipfile.ZipFile(io.BytesIO(archive), "r") as zf:
            self.assertEqual(
                zf.namelist(),
                [
                    "internal_agents.json",
                    "oasis_experts.json",
                    "oasis/yaml/ml_code_testing_pipeline.yaml",
                ],
            )
            self.assertNotIn("team_creator_meta.json", zf.namelist())

    def test_content_disposition_is_ascii_safe_for_unicode_filename(self):
        filename = svc.build_team_creator_download_name("SaaS增长团队", "20260328_123000")
        header = svc.build_attachment_content_disposition(filename)

        self.assertIn('filename="team_SaaS_creator_20260328_123000.zip"', header)
        self.assertIn("filename*=UTF-8''team_SaaS%E5%A2%9E%E9%95%BF%E5%9B%A2%E9%98%9F_creator_20260328_123000.zip", header)
        header.encode("latin-1")


if __name__ == "__main__":
    unittest.main()
