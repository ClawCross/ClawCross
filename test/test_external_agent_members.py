import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import front


class ExternalAgentMemberRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        front.app.config.update(TESTING=True)

    def setUp(self):
        self.client = front.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = "route-user"

    def test_team_external_member_post_preserves_persona_tag_and_platform(self):
        with TemporaryDirectory() as tmpdir:
            team_dir = Path(tmpdir) / "data" / "user_files" / "route-user" / "teams" / "demo"
            team_dir.mkdir(parents=True, exist_ok=True)

            with mock.patch.object(front, "root_dir", tmpdir):
                response = self.client.post(
                    "/teams/demo/members/external",
                    json={
                        "name": "architect",
                        "tag": "paul_graham",
                        "platform": "openclaw",
                        "global_name": "demo_architect",
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["agent"]["tag"], "paul_graham")
            self.assertEqual(payload["agent"]["platform"], "openclaw")

            saved = json.loads((team_dir / "external_agents.json").read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["tag"], "paul_graham")
            self.assertEqual(saved[0]["platform"], "openclaw")
            self.assertEqual(saved[0]["global_name"], "demo_architect")

    def test_team_external_member_put_updates_persona_tag_without_touching_platform(self):
        with TemporaryDirectory() as tmpdir:
            team_dir = Path(tmpdir) / "data" / "user_files" / "route-user" / "teams" / "demo"
            team_dir.mkdir(parents=True, exist_ok=True)
            (team_dir / "external_agents.json").write_text(
                json.dumps(
                    [
                        {
                            "name": "architect",
                            "tag": "",
                            "platform": "openclaw",
                            "global_name": "demo_architect",
                            "meta": {"api_url": "", "api_key": "", "model": "", "headers": {}},
                        }
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with mock.patch.object(front, "root_dir", tmpdir):
                response = self.client.put(
                    "/teams/demo/members/external",
                    json={
                        "global_name": "demo_architect",
                        "new_name": "architect",
                        "new_tag": "paul_graham",
                        "api_url": "",
                        "api_key": "",
                        "model": "",
                        "headers": {},
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["agent"]["tag"], "paul_graham")
            self.assertEqual(payload["agent"]["platform"], "openclaw")

            saved = json.loads((team_dir / "external_agents.json").read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["tag"], "paul_graham")
            self.assertEqual(saved[0]["platform"], "openclaw")
