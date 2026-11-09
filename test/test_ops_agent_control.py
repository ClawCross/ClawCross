import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from api.ops_models import AgentControlRequest
from api.ops_service import OpsService
from webot.subagents import SubagentRecord


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class _FakeAgent:
    def __init__(self):
        self.cancelled = []
        self.closed = []
        self._db_path = "/tmp/test-agent-control.db"

    def get_all_thread_status(self, prefix):
        return {
            f"{prefix}main": {"busy": True, "source": "user", "pending_system": 1},
            f"{prefix}runtime-only": {"busy": False, "source": "", "pending_system": 0},
        }

    def list_active_task_keys(self, prefix=""):
        return [f"{prefix}main"]

    def get_thread_context_usage(self, thread_id):
        if thread_id.endswith("#main"):
            return {"tokens": 2400, "budget": 12000, "percent": 20, "remaining": 9600}
        return {"tokens": 0, "budget": 0, "percent": 0, "remaining": 0}

    async def cancel_task(self, task_key):
        self.cancelled.append(task_key)
        return task_key.endswith("#main")

    async def close_thread_checkpoint(self, thread_id):
        self.closed.append(thread_id)


class AgentControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.agent = _FakeAgent()
        self.service = OpsService(
            internal_token="token",
            agent=self.agent,
            verify_password=lambda _user, _password: True,
            verify_auth_or_token=lambda _user, _password, _token: None,
        )

    async def test_list_builds_flat_view_and_preserves_team_membership(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_json(
                root / "alice" / "teams" / "alpha" / "internal_agents.json",
                [{"session": "main", "name": "Main", "tag": "lead"}],
            )
            _write_json(
                root / "alice" / "teams" / "beta" / "internal_agents.json",
                [{"session": "main", "name": "Main", "tag": "lead"}],
            )
            _write_json(
                root / "alice" / "teams" / "alpha" / "external_agents.json",
                [{"global_name": "reviewer", "name": "Reviewer", "platform": "codex"}],
            )
            _write_json(
                root / "alice" / "teams" / "beta" / "external_agents.json",
                [{"global_name": "reviewer", "name": "Reviewer", "platform": "codex"}],
            )
            subagent = SubagentRecord(
                agent_id="sub-1",
                user_id="alice",
                session_id="subagent-session",
                agent_type="codex",
                name="Worker",
                description="",
                parent_session="main",
                workspace_mode="isolated",
                workspace_root="",
                cwd="",
                remote="",
                status="idle",
                created_at="now",
                updated_at="now",
            )
            with (
                mock.patch("api.ops_service.USER_FILES_DIR", root),
                mock.patch("webot.subagents.list_subagents_for_user", return_value=[subagent]),
                mock.patch(
                    "api.ops_service.list_thread_ids_by_prefix",
                    new=mock.AsyncMock(return_value=[
                        "alice#main",
                        "alice#persisted-idle",
                        "alice#runtime-only",
                        "alice#subagent-session",
                    ]),
                ),
            ):
                result = await self.service.agent_control(
                    AgentControlRequest(
                        user_id="alice",
                        action="list",
                        refresh_external=False,
                    ),
                    None,
                )

            by_key = {(row["kind"], row["identity"]): row for row in result["agents"]}
            self.assertEqual(by_key[("internal", "main")]["teams"], ["alpha", "beta"])
            self.assertEqual(by_key[("internal", "main")]["status"], "running")
            self.assertEqual(by_key[("internal", "main")]["context"]["percent"], 20)
            self.assertEqual(by_key[("external", "reviewer")]["teams"], ["alpha", "beta"])
            self.assertEqual(by_key[("subagent", "sub-1")]["teams"], ["alpha", "beta"])
            self.assertIn(("internal", "runtime-only"), by_key)
            self.assertIn(("internal", "persisted-idle"), by_key)
            self.assertNotIn(("internal", "subagent-session"), by_key)

    async def test_internal_cancel_reuses_existing_task_registry(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_json(
                root / "alice" / "teams" / "alpha" / "internal_agents.json",
                [{"session": "main", "name": "Main"}],
            )
            with (
                mock.patch("api.ops_service.USER_FILES_DIR", root),
                mock.patch("webot.subagents.list_subagents_for_user", return_value=[]),
            ):
                result = await self.service.agent_control(
                    AgentControlRequest(
                        user_id="alice",
                        action="cancel",
                        kind="internal",
                        identity="main",
                        refresh_external=False,
                    ),
                    None,
                )

            self.assertTrue(result["cancelled"])
            self.assertEqual(self.agent.cancelled, ["alice#main"])

    async def test_http_agent_reports_cancel_as_unsupported(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_json(
                root / "alice" / "external_agents.json",
                [{
                    "global_name": "remote",
                    "name": "Remote",
                    "platform": "custom-http",
                    "config": {"api_url": "https://example.invalid/v1"},
                }],
            )
            with (
                mock.patch("api.ops_service.USER_FILES_DIR", root),
                mock.patch("webot.subagents.list_subagents_for_user", return_value=[]),
            ):
                result = await self.service.agent_control(
                    AgentControlRequest(
                        user_id="alice",
                        action="cancel",
                        kind="external",
                        identity="remote",
                        refresh_external=False,
                    ),
                    None,
                )

            self.assertEqual(result["status"], "unsupported")
            self.assertFalse(result["supported"])

    async def test_internal_reset_removes_runtime_without_editing_agent_config(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "alice" / "teams" / "alpha" / "internal_agents.json"
            config = [{"session": "main", "name": "Main"}]
            _write_json(config_path, config)
            with (
                mock.patch("api.ops_service.USER_FILES_DIR", root),
                mock.patch("webot.subagents.list_subagents_for_user", return_value=[]),
                mock.patch("api.ops_service.delete_thread_records", new=mock.AsyncMock()) as delete_records,
            ):
                result = await self.service.agent_control(
                    AgentControlRequest(
                        user_id="alice",
                        action="reset",
                        kind="internal",
                        identity="main",
                        refresh_external=False,
                    ),
                    None,
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(self.agent.closed, ["alice#main"])
            delete_records.assert_awaited_once_with("/tmp/test-agent-control.db", "alice#main")
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), config)

    async def test_internal_delete_removes_agent_from_all_config_sources(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            public_path = root / "alice" / "internal_agents.json"
            team_path = root / "alice" / "teams" / "alpha" / "internal_agents.json"
            _write_json(public_path, [
                {"session": "main", "name": "Main"},
                {"session": "keep", "name": "Keep"},
            ])
            _write_json(team_path, [{"session": "main", "name": "Main"}])
            with (
                mock.patch("api.ops_service.USER_FILES_DIR", root),
                mock.patch("webot.subagents.list_subagents_for_user", return_value=[]),
                mock.patch("api.ops_service.list_thread_ids_by_prefix", new=mock.AsyncMock(return_value=[])),
                mock.patch("api.ops_service.delete_thread_records", new=mock.AsyncMock()),
            ):
                result = await self.service.agent_control(
                    AgentControlRequest(
                        user_id="alice",
                        action="delete",
                        kind="internal",
                        identity="main",
                        refresh_external=False,
                    ),
                    None,
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["deleted_sources"], ["public", "alpha"])
            self.assertEqual(
                json.loads(public_path.read_text(encoding="utf-8")),
                [{"session": "keep", "name": "Keep"}],
            )
            self.assertEqual(json.loads(team_path.read_text(encoding="utf-8")), [])

    async def test_internal_configure_updates_every_definition_without_team_selection(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            public_path = root / "alice" / "internal_agents.json"
            team_path = root / "alice" / "teams" / "alpha" / "internal_agents.json"
            _write_json(public_path, [{"session": "main", "name": "Old", "tag": "lead"}])
            _write_json(team_path, [{"session": "main", "name": "Old", "tag": "lead"}])
            with (
                mock.patch("api.ops_service.USER_FILES_DIR", root),
                mock.patch("webot.subagents.list_subagents_for_user", return_value=[]),
            ):
                result = await self.service.agent_control(
                    AgentControlRequest(
                        user_id="alice",
                        action="configure",
                        kind="internal",
                        identity="main",
                        refresh_external=False,
                        settings={"name": "Builder", "tag": "coder", "tools": "none"},
                    ),
                    None,
                )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["updated_sources"], ["public", "alpha"])
            for path in (public_path, team_path):
                row = json.loads(path.read_text(encoding="utf-8"))[0]
                self.assertEqual(row["name"], "Builder")
                self.assertEqual(row["tag"], "coder")
                self.assertEqual(row["tools"], "none")


if __name__ == "__main__":
    unittest.main()
