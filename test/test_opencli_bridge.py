import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.harness_routes import create_harness_router  # noqa: E402
from harness.opencli_bridge import get_opencli_status, run_opencli_command  # noqa: E402


class OpenCliBridgeTests(unittest.TestCase):
    def test_status_exposes_wechat_and_mail_capabilities_without_opencli(self):
        with patch("harness.opencli_bridge.shutil.which", return_value=""):
            status = get_opencli_status(query="wechat")

        self.assertFalse(status["opencli_installed"])
        self.assertEqual(status["install_hint"], "npm install -g @jackwener/opencli")
        names = {item["name"] for item in status["capabilities"]["external_clis"]}
        self.assertIn("wx", names)

        mail = get_opencli_status(query="gmail")
        browser_names = {item["name"] for item in mail["capabilities"]["browser"]}
        self.assertIn("gmail-browser", browser_names)

    def test_run_uses_opencli_binary_without_shell_and_parses_json(self):
        class Completed:
            returncode = 0
            stdout = '{"ok":true,"items":[1]}'
            stderr = ""

        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return Completed()

        with patch("harness.opencli_bridge.shutil.which", return_value="/usr/local/bin/opencli"):
            with patch("harness.opencli_bridge.subprocess.run", side_effect=fake_run):
                result = run_opencli_command(["external", "list", "-f", "json"], timeout_seconds=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["json"]["items"], [1])
        self.assertEqual(calls[0][0], ["/usr/local/bin/opencli", "external", "list", "-f", "json"])
        self.assertFalse(calls[0][1].get("shell", False))

    def test_run_rejects_mutating_opencli_registry_by_default(self):
        with patch("harness.opencli_bridge.shutil.which", return_value="/usr/local/bin/opencli"):
            with self.assertRaises(PermissionError):
                run_opencli_command(["external", "register", "custom"])

    def test_run_returns_tuple_when_output_is_truncated(self):
        class Completed:
            returncode = 0
            stdout = "x" * 1200
            stderr = ""

        with patch("harness.opencli_bridge.shutil.which", return_value="/usr/local/bin/opencli"):
            with patch("harness.opencli_bridge.subprocess.run", return_value=Completed()):
                result = run_opencli_command(["browser", "gmail", "state"], max_output_chars=1000)

        self.assertTrue(result["ok"])
        self.assertTrue(result["truncated"])
        self.assertIn("truncated", result["stdout"])

    def test_harness_opencli_routes(self):
        app = FastAPI()
        app.include_router(
            create_harness_router(
                verify_auth_or_token=lambda user_id, password, token: None,
            )
        )

        with TestClient(app) as client:
            status = client.get("/harness/opencli/status", params={"user_id": "alice", "query": "wx"})
            self.assertEqual(status.status_code, 200)
            self.assertIn("external_clis", status.json()["capabilities"])

            with patch("api.harness_routes.run_opencli_command", return_value={"ok": True, "stdout": "done"}):
                run = client.post(
                    "/harness/opencli/run",
                    json={"user_id": "alice", "args": ["wx", "search", "TODO"]},
                )
            self.assertEqual(run.status_code, 200)
            self.assertTrue(run.json()["ok"])


if __name__ == "__main__":
    unittest.main()
