import os
import sys
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from harness import conductor  # noqa: E402
from harness.dashboard_sync import (  # noqa: E402
    import_dashboard_todos,
    publish_dashboard_tasks,
    should_sync_dashboard_comment,
    sync_dashboard_to_supabase,
    sync_harness_to_dashboard,
)
from harness.store import apply_harness_event, get_harness_state  # noqa: E402
from harness.task_markdown import (  # noqa: E402
    parse_task_markdown,
    render_task_markdown,
    sync_task_markdown,
)


def sample_state():
    return {
        "tasks": [
            {
                "task_id": "task_umi_eval",
                "project_id": "project-alpha",
                "title": "Run Project Alpha evaluation",
                "description": "Run verifier and report verified results.",
                "status": "active",
            }
        ],
        "agents": [
            {
                "agent_id": "worker-alpha@192.0.2.1",
                "project_id": "project-alpha",
                "current_task_id": "task_umi_eval",
                "session_ref": "session_abc",
                "status": "needs_user",
                "needs_user": True,
                "updated_at": "2026-05-19T01:00:00+08:00",
            }
        ],
    }


class HarnessConductorDecisionTests(unittest.TestCase):
    def test_decision_replies_to_linked_worker_that_needs_user(self):
        decision = conductor.decide_for_session(
            {
                "display_id": "session_abc",
                "status": "busy",
                "last_message": {"content": "Need user confirmation before continuing."},
            },
            sample_state(),
            {"sent": {}},
            cooldown_seconds=30,
        )

        self.assertIsNotNone(decision)
        self.assertTrue(decision.should_send)
        self.assertIn("ClawCross 本机主控确认", decision.message)
        self.assertIn("task_umi_eval", decision.message)

    def test_decision_does_not_auto_continue_task_waiting_for_user(self):
        state = sample_state()
        state["tasks"][0]["status"] = "needs_user"
        decision = conductor.decide_for_session(
            {
                "display_id": "session_abc",
                "status": "idle",
                "last_message": {"content": "Waiting for your choice."},
            },
            state,
            {"sent": {}},
            cooldown_seconds=30,
        )

        self.assertIsNone(decision)

    def test_decision_blocks_risky_request_for_manual_review(self):
        decision = conductor.decide_for_session(
            {
                "display_id": "session_abc",
                "status": "busy",
                "last_message": {"content": "Please approve sudo rm -rf /tmp/something"},
            },
            sample_state(),
            {"sent": {}},
            cooldown_seconds=30,
        )

        self.assertIsNotNone(decision)
        self.assertFalse(decision.should_send)
        self.assertTrue(decision.manual_review)

    def test_busy_tool_invocation_does_not_prompt_manual_review(self):
        decision = conductor.decide_for_session(
            {
                "display_id": "session_abc",
                "status": "busy",
                "last_message": {
                    "content": "{'command': 'cd ~/workspace/project-beta && .venv/bin/pip install --quiet --no-cache-dir torch==2.9.1 --index-url https://download.pytorch.org/whl/cu128', 'description': 'Install torch 2.9.1 cu128', 'timeout': 600000}",
                },
            },
            sample_state(),
            {"sent": {}},
            cooldown_seconds=30,
        )

        self.assertIsNone(decision)

    def test_busy_risky_tool_invocation_still_requires_manual_review(self):
        decision = conductor.decide_for_session(
            {
                "display_id": "session_abc",
                "status": "busy",
                "last_message": {
                    "content": "{'command': 'sudo rm -rf /tmp/something', 'description': 'Dangerous cleanup', 'timeout': 600000}",
                },
            },
            sample_state(),
            {"sent": {}},
            cooldown_seconds=30,
        )

        self.assertIsNotNone(decision)
        self.assertTrue(decision.manual_review)

    def test_decision_matches_remote_qualified_job_id(self):
        state = sample_state()
        state["agents"][0]["session_ref"] = "remoteuser@192.0.2.29::8a5f7c95"
        state["agents"][0]["agent_id"] = "project-beta-remoteuser@192.0.2.29"
        state["agents"][0]["project_id"] = "project-beta"
        state["tasks"][0]["project_id"] = "project-beta"
        decision = conductor.decide_for_session(
            {
                "remote_key": "remoteuser@192.0.2.29::session_014xgjW9Tj25dnXYDNUYg8NJ",
                "display_id": "session_014xgjW9Tj25dnXYDNUYg8NJ",
                "job_id": "8a5f7c95",
                "status": "busy",
                "last_message": {"content": "Need user confirmation before continuing."},
            },
            state,
            {"sent": {}},
            cooldown_seconds=30,
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.agent_id, "project-beta-remoteuser@192.0.2.29")

    def test_mark_sent_prevents_immediate_duplicate_reply(self):
        session = {
            "display_id": "session_abc",
            "status": "busy",
            "last_message": {"content": "Need user confirmation before continuing."},
        }
        cache = {"sent": {}}
        decision = conductor.decide_for_session(session, sample_state(), cache, cooldown_seconds=120)
        self.assertIsNotNone(decision)
        conductor.mark_decision_sent(cache, session, decision)

        duplicate = conductor.decide_for_session(session, sample_state(), cache, cooldown_seconds=120)
        self.assertIsNone(duplicate)

    def test_dashboard_sync_skips_internal_conductor_comments(self):
        self.assertFalse(
            should_sync_dashboard_comment(
                {
                    "kind": "conductor_reply",
                    "body": "本机主控已向远端 session session_abc 自动回复继续执行。",
                }
            )
        )
        self.assertTrue(
            should_sync_dashboard_comment(
                {
                    "kind": "progress",
                    "body": "VBench wrapper dry-run produced pairs.json.",
                }
            )
        )

    def test_dashboard_publish_only_targets_tasks_json(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            dashboard = repo / "dashboard"
            repo_resolved = repo.resolve()
            (repo / ".git").mkdir()
            (dashboard / "state").mkdir(parents=True)
            (dashboard / "state" / "tasks.json").write_text('{"tasks": []}\n', encoding="utf-8")
            calls: list[list[str]] = []

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                if cmd[3] == "status":
                    return mock.Mock(returncode=0, stdout=" M dashboard/state/tasks.json\n", stderr="")
                if cmd[3:6] == ["diff", "--cached", "--quiet"]:
                    return mock.Mock(returncode=1, stdout="", stderr="")
                if cmd[3:5] == ["rev-parse", "--short"]:
                    return mock.Mock(returncode=0, stdout="abc123\n", stderr="")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("harness.dashboard_sync.subprocess.run", side_effect=fake_run):
                result = publish_dashboard_tasks(dashboard_root=dashboard)

            self.assertTrue(result["ok"])
            self.assertTrue(result["published"])
            self.assertTrue(result["pushed"])
            self.assertIn(["git", "-C", str(repo_resolved), "add", "--", "dashboard/state/tasks.json"], calls)
            self.assertIn(
                [
                    "git",
                    "-C",
                    str(repo_resolved),
                    "commit",
                    "-m",
                    "Update dashboard task status from ClawCross harness",
                    "--",
                    "dashboard/state/tasks.json",
                ],
                calls,
            )

    def test_dashboard_supabase_sync_runs_dashboard_script(self):
        with TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            dashboard = repo / "dashboard"
            script = repo / "scripts" / "sync-dashboard-to-supabase.mjs"
            script.parent.mkdir()
            dashboard.mkdir()
            script.write_text("console.log('ok')\n", encoding="utf-8")
            (repo / ".env").write_text("SUPABASE_URL=https://example.supabase.co\n", encoding="utf-8")
            calls: list[list[str]] = []

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                return mock.Mock(returncode=0, stdout='{"ok":true,"tasks":2}\n', stderr="")

            with mock.patch("harness.dashboard_sync.subprocess.run", side_effect=fake_run):
                result = sync_dashboard_to_supabase(dashboard_root=dashboard, project_id="project-alpha")

            self.assertTrue(result["ok"])
            self.assertTrue(result["synced"])
            self.assertEqual(result["tasks"], 2)
            self.assertEqual(
                calls[0],
                ["npm", "run", "supabase:sync", "--", "--once", "--project-id", "project-alpha"],
            )


class HarnessConductorLoopTests(unittest.TestCase):
    def test_run_once_sends_reply_and_records_comment(self):
        with TemporaryDirectory() as tmpdir:
            old_state = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            old_cache = os.environ.get("CLAWCROSS_HARNESS_CONDUCTOR_CACHE")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            os.environ["CLAWCROSS_HARNESS_CONDUCTOR_CACHE"] = str(Path(tmpdir) / "cache.json")
            try:
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_upsert",
                        "project_id": "project-alpha",
                        "task_id": "task_umi_eval",
                        "title": "Run Project Alpha evaluation",
                        "status": "active",
                    },
                )
                apply_harness_event(
                    "test-user",
                    {
                        "action": "needs_user",
                        "agent_id": "worker-alpha@192.0.2.1",
                        "project_id": "project-alpha",
                        "task_id": "task_umi_eval",
                        "session_ref": "session_abc",
                        "message": "Need confirmation",
                    },
                )
                with mock.patch.object(
                    conductor,
                    "list_remote_claude_sessions",
                    return_value={
                        "ok": True,
                        "sessions": [
                            {
                                "display_id": "session_abc",
                                "status": "busy",
                                "last_message": {"content": "Need user confirmation before continuing."},
                            }
                        ],
                    },
                ), mock.patch.object(conductor, "send_remote_claude_message", return_value={"ok": True}):
                    result = conductor.run_conductor_once("test-user", cooldown_seconds=30, sync_dashboard=False)

                self.assertEqual(len(result["actions"]), 1)
                self.assertTrue(result["actions"][0]["sent"])
                state = get_harness_state("test-user")
                comments = state["tasks"][0].get("comments", [])
                self.assertTrue(any(c.get("kind") == "conductor_reply" for c in comments))
            finally:
                if old_state is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = old_state
                if old_cache is None:
                    os.environ.pop("CLAWCROSS_HARNESS_CONDUCTOR_CACHE", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_CONDUCTOR_CACHE"] = old_cache

    def test_dashboard_pull_verify_assign_and_push_loop(self):
        with TemporaryDirectory() as tmpdir:
            old_state = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            dashboard = Path(tmpdir) / "dashboard"
            (dashboard / "state").mkdir(parents=True)
            (dashboard / "state" / "tasks.json").write_text(
                """{
  "schema_version": "tasks.v1",
  "updated_at": "2026-05-19T00:00:00+08:00",
  "tasks": [
    {
      "task_id": "task_done_decision",
      "project_id": "project-alpha",
      "title": "Decide baseline",
      "description": "Decision task",
      "status": "active",
      "priority": "high",
      "comments": []
    },
    {
      "task_id": "task_next_dashboard",
      "project_id": "project-alpha",
      "title": "Next dashboard TODO",
      "description": "New pulled task",
      "status": "todo",
      "priority": "high",
      "comments": []
    }
  ]
}
""",
                encoding="utf-8",
            )
            try:
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_upsert",
                        "project_id": "project-alpha",
                        "task_id": "task_done_decision",
                        "title": "Decide baseline",
                        "status": "done",
                    },
                )
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_comment",
                        "agent_id": "worker-1",
                        "project_id": "project-alpha",
                        "task_id": "task_done_decision",
                        "kind": "result",
                        "message": "Decision evidence is recorded.",
                    },
                )
                apply_harness_event(
                    "test-user",
                    {
                        "action": "heartbeat",
                        "agent_id": "worker-1",
                        "project_id": "project-alpha",
                        "task_id": "task_done_decision",
                        "current_task_id": "task_done_decision",
                        "session_ref": "session_abc",
                        "status": "done",
                    },
                )

                pull = import_dashboard_todos("test-user", dashboard_root=dashboard, project_id="project-alpha")
                self.assertEqual(pull["created"], 1)
                verify = conductor.verify_finished_tasks("test-user", project_id="project-alpha")
                self.assertEqual(verify["accepted"], 1)
                state = get_harness_state("test-user")
                assigned = conductor.assign_next_dashboard_todos(
                    "test-user",
                    [{"display_id": "session_abc", "status": "idle"}],
                    state,
                    project_id="project-alpha",
                    dry_run=True,
                )
                self.assertEqual(assigned[0]["task_id"], "task_next_dashboard")

                push = sync_harness_to_dashboard("test-user", dashboard_root=dashboard, project_id="project-alpha")
                self.assertTrue(push["changed"])
                doc = (dashboard / "state" / "tasks.json").read_text(encoding="utf-8")
                self.assertIn('"status": "done"', doc)
                self.assertIn("Host verification", doc)
            finally:
                if old_state is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = old_state

    def test_dashboard_push_syncs_project_move_for_existing_task(self):
        with TemporaryDirectory() as tmpdir:
            old_state = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            dashboard = Path(tmpdir) / "dashboard"
            (dashboard / "state").mkdir(parents=True)
            tasks_path = dashboard / "state" / "tasks.json"
            tasks_path.write_text(
                json.dumps(
                    {
                        "schema_version": "tasks.v1",
                        "tasks": [
                            {
                                "task_id": "task_move",
                                "project_id": "project-alpha",
                                "title": "Move this task",
                                "description": "Initial project.",
                                "status": "needs_user",
                                "priority": "medium",
                                "comments": [],
                                "updated_at": "2026-05-19T00:00:00+08:00",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            try:
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_upsert",
                        "project_id": "project-alpha",
                        "task_id": "task_move",
                        "title": "Move this task",
                        "status": "needs_user",
                    },
                )
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_status",
                        "agent_id": "host",
                        "project_id": "project-beta",
                        "task_id": "task_move",
                        "status": "todo",
                        "message": "Move to the survey pool.",
                    },
                )

                summary = sync_harness_to_dashboard("test-user", dashboard_root=dashboard)
                self.assertEqual(summary["project_updates"], 1)
                self.assertEqual(summary["status_updates"], 1)
                task = json.loads(tasks_path.read_text(encoding="utf-8"))["tasks"][0]
                self.assertEqual(task["project_id"], "project-beta")
                self.assertEqual(task["status"], "todo")
            finally:
                if old_state is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = old_state

    def test_all_project_assignment_matches_worker_project(self):
        state = {
            "tasks": [
                {
                    "task_id": "task_umi_next",
                    "project_id": "project-alpha",
                    "title": "Project Alpha next",
                    "status": "todo",
                    "priority": "high",
                },
                {
                    "task_id": "task_robotics_next",
                    "project_id": "project-gamma",
                    "title": "Project Gamma next",
                    "status": "todo",
                    "priority": "high",
                },
            ],
            "agents": [
                {
                    "agent_id": "project-gamma-surveyuser@192.0.2.8",
                    "project_id": "project-gamma",
                    "current_task_id": "",
                    "session_ref": "session_robotics",
                    "status": "idle",
                }
            ],
            "runs": [],
        }

        assigned = conductor.assign_next_dashboard_todos(
            "test-user",
            [{"display_id": "session_robotics", "status": "idle"}],
            state,
            project_id="",
            dry_run=True,
        )

        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0]["task_id"], "task_robotics_next")

    def test_survey_pool_worker_can_take_multiple_survey_projects(self):
        state = {
            "tasks": [
                {
                    "task_id": "task_umi_next",
                    "project_id": "project-alpha",
                    "title": "Project Alpha next",
                    "status": "todo",
                    "priority": "urgent",
                },
                {
                    "task_id": "task_rate_curve_next",
                    "project_id": "project-survey-a",
                    "title": "Rate curve survey next",
                    "status": "todo",
                    "priority": "high",
                },
                {
                    "task_id": "task_hoi_next",
                    "project_id": "project-survey-b",
                    "title": "HOI survey next",
                    "status": "todo",
                    "priority": "medium",
                },
            ],
            "agents": [
                {
                    "agent_id": "survey-pool-surveyuser@192.0.2.8",
                    "project_id": "project-survey-a",
                    "current_task_id": "",
                    "session_ref": "session_survey",
                    "status": "idle",
                    "capabilities": ["survey-pool"],
                    "metadata": {"clawcross": {"survey_pool": True, "project_ids": ["project-survey-a", "project-survey-b"]}},
                }
            ],
            "runs": [],
        }

        assigned = conductor.assign_next_dashboard_todos(
            "test-user",
            [{"display_id": "session_survey", "status": "idle"}],
            state,
            project_id="",
            dry_run=True,
        )

        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0]["task_id"], "task_rate_curve_next")

    def test_unbound_idle_session_is_not_auto_adopted_by_default(self):
        state = {
            "tasks": [
                {
                    "task_id": "task_image_next",
                    "project_id": "project-beta",
                    "title": "Image next",
                    "status": "todo",
                    "priority": "high",
                }
            ],
            "agents": [
                {
                    "agent_id": "project-beta-remoteuser@192.0.2.29",
                    "project_id": "project-beta",
                    "current_task_id": "task_done",
                    "session_ref": "old_session",
                    "remote_host": "remoteuser@192.0.2.29",
                    "status": "done",
                }
            ],
            "runs": [],
        }

        assigned = conductor.assign_next_dashboard_todos(
            "test-user",
            [
                {
                    "remote_key": "remoteuser@192.0.2.29::new_session",
                    "display_id": "new_session",
                    "remote_user": "remoteuser",
                    "remote_host": "192.0.2.29",
                    "status": "idle",
                }
            ],
            state,
            project_id="",
            dry_run=True,
        )

        self.assertEqual(assigned, [])

    def test_unbound_idle_session_can_be_autobound_when_explicitly_enabled(self):
        state = {
            "tasks": [
                {
                    "task_id": "task_image_next",
                    "project_id": "project-beta",
                    "title": "Image next",
                    "status": "todo",
                    "priority": "high",
                }
            ],
            "agents": [
                {
                    "agent_id": "project-beta-remoteuser@192.0.2.29",
                    "project_id": "project-beta",
                    "current_task_id": "task_done",
                    "session_ref": "old_session",
                    "remote_host": "remoteuser@192.0.2.29",
                    "status": "done",
                }
            ],
            "runs": [],
        }

        with mock.patch.dict(os.environ, {"CLAWCROSS_HARNESS_AUTOBIND_UNBOUND_SESSIONS": "1"}):
            assigned = conductor.assign_next_dashboard_todos(
                "test-user",
                [
                    {
                        "remote_key": "remoteuser@192.0.2.29::new_session",
                        "display_id": "new_session",
                        "remote_user": "remoteuser",
                        "remote_host": "192.0.2.29",
                        "status": "idle",
                    }
                ],
                state,
                project_id="",
                dry_run=True,
            )

        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0]["project_id"], "project-beta")
        self.assertEqual(assigned[0]["task_id"], "task_image_next")
        self.assertIn("project-beta", assigned[0]["agent_id"])

    def test_llm_assignment_marks_webot_decision_source(self):
        state = {
            "tasks": [
                {
                    "task_id": "task_a",
                    "project_id": "project-alpha",
                    "title": "Fallback task",
                    "status": "todo",
                    "priority": "low",
                },
                {
                    "task_id": "task_b",
                    "project_id": "project-alpha",
                    "title": "Webot selected task",
                    "status": "todo",
                    "priority": "low",
                },
            ],
            "agents": [
                {
                    "agent_id": "umi-worker",
                    "project_id": "project-alpha",
                    "current_task_id": "",
                    "session_ref": "session_abc",
                    "status": "idle",
                }
            ],
            "runs": [],
        }

        with mock.patch.object(
            conductor,
            "_call_webot_llm_json",
            return_value={"task_id": "task_b", "message": "Webot message", "reason": "better match"},
        ):
            assigned = conductor.assign_next_dashboard_todos(
                "test-user",
                [{"display_id": "session_abc", "status": "idle"}],
                state,
                project_id="project-alpha",
                dry_run=True,
                llm_mode=True,
            )

        self.assertEqual(len(assigned), 1)
        self.assertEqual(assigned[0]["task_id"], "task_b")
        self.assertEqual(assigned[0]["decision_source"], "webot_llm")
        self.assertTrue(assigned[0]["llm_driven"])
        self.assertEqual(assigned[0]["message"], "Webot message")

    def test_rename_bound_remote_sessions_uses_project_and_task(self):
        state = {
            "tasks": [
                {
                    "task_id": "task_pod",
                    "project_id": "project-gamma",
                    "title": "Benchmark photo / text-to-3D engines",
                    "status": "active",
                }
            ],
            "agents": [
                {
                    "agent_id": "project-gamma-surveyuser@192.0.2.8",
                    "project_id": "project-gamma",
                    "current_task_id": "task_pod",
                    "session_ref": "session_robotics",
                    "remote_host": "surveyuser@192.0.2.8",
                    "status": "running",
                }
            ],
        }

        with mock.patch.object(conductor, "rename_remote_claude_session", return_value={"ok": True}) as rename_mock:
            renamed = conductor.rename_bound_remote_sessions(
                [
                    {
                        "display_id": "session_robotics",
                        "title": "old name",
                        "remote_user": "surveyuser",
                        "remote_host": "192.0.2.8",
                    }
                ],
                state,
            )

        self.assertEqual(len(renamed), 1)
        self.assertTrue(renamed[0]["ok"])
        self.assertEqual(renamed[0]["project_id"], "project-gamma")
        rename_mock.assert_called_once_with(
            "session_robotics",
            "ClawCross | Project Gamma | surveyuser | Benchmark photo / text-to-3D...",
        )

    def test_survey_pool_session_rename_uses_pool_label(self):
        state = {
            "tasks": [],
            "agents": [
                {
                    "agent_id": "survey-pool-surveyuser@192.0.2.8",
                    "project_id": "project-survey-a",
                    "current_task_id": "",
                    "session_ref": "session_survey",
                    "remote_host": "surveyuser@192.0.2.8",
                    "status": "idle",
                    "metadata": {"clawcross": {"survey_pool": True}},
                }
            ],
        }

        with mock.patch.object(conductor, "rename_remote_claude_session", return_value={"ok": True}) as rename_mock:
            renamed = conductor.rename_bound_remote_sessions(
                [
                    {
                        "display_id": "session_survey",
                        "title": "old name",
                        "remote_user": "surveyuser",
                        "remote_host": "192.0.2.8",
                    }
                ],
                state,
            )

        self.assertEqual(len(renamed), 1)
        rename_mock.assert_called_once_with(
            "session_survey",
            "ClawCross | Survey Pool | surveyuser",
        )

    def test_cleanup_closes_paused_todo_session_and_deletes_agent(self):
        with TemporaryDirectory() as tmpdir:
            old_state = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_upsert",
                        "project_id": "project-alpha",
                        "task_id": "task_paused",
                        "title": "Paused TODO",
                        "status": "todo",
                        "metadata": {"clawcross": {"paused_by_user": True}},
                    },
                )
                apply_harness_event(
                    "test-user",
                    {
                        "action": "heartbeat",
                        "agent_id": "worker-paused@192.0.2.1",
                        "project_id": "project-alpha",
                        "task_id": "task_paused",
                        "current_task_id": "task_paused",
                        "session_ref": "session_paused",
                        "status": "idle",
                    },
                )
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_upsert",
                        "project_id": "project-alpha",
                        "task_id": "task_active",
                        "title": "Active TODO",
                        "status": "active",
                    },
                )
                apply_harness_event(
                    "test-user",
                    {
                        "action": "heartbeat",
                        "agent_id": "worker-active@192.0.2.1",
                        "project_id": "project-alpha",
                        "task_id": "task_active",
                        "current_task_id": "task_active",
                        "session_ref": "session_active",
                        "status": "running",
                    },
                )

                state = get_harness_state("test-user")
                with mock.patch.object(
                    conductor,
                    "close_remote_claude_session",
                    return_value={"ok": True, "archive_path": "/archive/paused.json"},
                ) as close_mock:
                    cleaned = conductor.cleanup_remote_sessions_without_todos(
                        "test-user",
                        [
                            {"display_id": "session_paused", "status": "idle"},
                            {"display_id": "session_active", "status": "busy"},
                        ],
                        state,
                        project_id="project-alpha",
                    )

                self.assertEqual(len(cleaned), 1)
                self.assertTrue(cleaned[0]["deleted_agent"])
                close_mock.assert_called_once_with("session_paused", force=True)
                self.assertEqual(get_harness_state("test-user")["counts"]["agents"], 1)
            finally:
                if old_state is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = old_state

    def test_cleanup_keeps_last_session_for_active_project_as_standby(self):
        with TemporaryDirectory() as tmpdir:
            old_state = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_upsert",
                        "project_id": "project-alpha",
                        "task_id": "task_done",
                        "title": "Done TODO",
                        "status": "done",
                    },
                )
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_comment",
                        "agent_id": conductor.CONDUCTOR_AGENT_ID,
                        "project_id": "project-alpha",
                        "task_id": "task_done",
                        "kind": "host_verified",
                        "message": "verified",
                    },
                )
                apply_harness_event(
                    "test-user",
                    {
                        "action": "heartbeat",
                        "agent_id": "worker-last@192.0.2.1",
                        "project_id": "project-alpha",
                        "task_id": "task_done",
                        "current_task_id": "task_done",
                        "session_ref": "session_last",
                        "status": "done",
                    },
                )

                state = get_harness_state("test-user")
                with mock.patch.object(conductor, "close_remote_claude_session") as close_mock:
                    cleaned = conductor.cleanup_remote_sessions_without_todos(
                        "test-user",
                        [{"display_id": "session_last", "status": "idle"}],
                        state,
                        project_id="project-alpha",
                    )

                self.assertEqual(len(cleaned), 1)
                self.assertTrue(cleaned[0]["kept"])
                close_mock.assert_not_called()
                agents = get_harness_state("test-user")["agents"]
                self.assertEqual(len(agents), 1)
                self.assertEqual(agents[0]["status"], "idle")
                self.assertEqual(agents[0]["current_task_id"], "")
            finally:
                if old_state is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = old_state

    def test_codex_review_accepts_review_task_and_marks_done(self):
        with TemporaryDirectory() as tmpdir:
            old_state = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_upsert",
                        "project_id": "project-alpha",
                        "task_id": "task_review_me",
                        "title": "Reviewable result",
                        "status": "review",
                    },
                )
                state = get_harness_state("test-user")
                cache = {"sent": {}}
                with mock.patch.object(
                    conductor,
                    "_call_local_codex_review",
                    return_value={
                        "action": "accept",
                        "confidence": 0.91,
                        "summary": "Verifier passed.",
                        "evidence": ["pytest 3/3"],
                        "commands": ["pytest -q"],
                        "reason": "Enough evidence.",
                    },
                ):
                    reviewed = conductor.review_pending_tasks_with_codex(
                        "test-user",
                        state,
                        [],
                        cache,
                        project_id="project-alpha",
                        limit=1,
                    )

                self.assertEqual(len(reviewed), 1)
                self.assertEqual(reviewed[0]["action"], "accept")
                final = get_harness_state("test-user")
                task = final["tasks"][0]
                self.assertEqual(task["status"], "done")
                self.assertTrue(any(c.get("kind") == "host_verified" for c in task.get("comments", [])))
                self.assertIn("task_review_me", cache["codex_review"])
            finally:
                if old_state is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = old_state

    def test_codex_review_reopens_review_task(self):
        with TemporaryDirectory() as tmpdir:
            old_state = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_upsert",
                        "project_id": "project-alpha",
                        "task_id": "task_review_more",
                        "title": "Needs more scale",
                        "status": "review",
                    },
                )
                state = get_harness_state("test-user")
                cache = {"sent": {}}
                with mock.patch.object(
                    conductor,
                    "_call_local_codex_review",
                    return_value={
                        "action": "reopen",
                        "new_status": "active",
                        "summary": "Only 44 clips were verified.",
                        "worker_message": "Scale to 100 clips or report a blocker.",
                    },
                ):
                    reviewed = conductor.review_pending_tasks_with_codex(
                        "test-user",
                        state,
                        [],
                        cache,
                        project_id="project-alpha",
                        limit=1,
                    )

                self.assertEqual(len(reviewed), 1)
                self.assertEqual(reviewed[0]["action"], "reopen")
                task = get_harness_state("test-user")["tasks"][0]
                self.assertEqual(task["status"], "active")
                self.assertTrue(any("Scale to 100" in c.get("body", "") for c in task.get("comments", [])))
            finally:
                if old_state is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = old_state

    def test_codex_review_needs_user_updates_task_status(self):
        with TemporaryDirectory() as tmpdir:
            old_state = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_upsert",
                        "project_id": "project-beta",
                        "task_id": "task_photoshop",
                        "title": "Prototype Photoshop PSD layer pipeline",
                        "status": "review",
                    },
                )
                state = get_harness_state("test-user")
                cache = {"sent": {}}
                with mock.patch.object(
                    conductor,
                    "_call_local_codex_review",
                    return_value={
                        "action": "needs_user",
                        "confidence": 0.94,
                        "summary": "Scope change requires user decision.",
                        "worker_message": "Pick original Photoshop route or approve the Linux substitute.",
                    },
                ):
                    reviewed = conductor.review_pending_tasks_with_codex(
                        "test-user",
                        state,
                        [],
                        cache,
                        project_id="project-beta",
                        limit=1,
                    )

                self.assertEqual(len(reviewed), 1)
                self.assertEqual(reviewed[0]["action"], "needs_user")
                task = get_harness_state("test-user")["tasks"][0]
                self.assertEqual(task["status"], "needs_user")
                self.assertTrue(any(c.get("kind") == "needs_user" for c in task.get("comments", [])))
            finally:
                if old_state is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = old_state

    def test_verify_finished_restores_host_verified_review_task_to_done(self):
        with TemporaryDirectory() as tmpdir:
            old_state = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_upsert",
                        "project_id": "project-beta",
                        "task_id": "task_verified_review",
                        "title": "Already accepted",
                        "status": "review",
                    },
                )
                apply_harness_event(
                    "test-user",
                    {
                        "action": "task_comment",
                        "agent_id": conductor.CONDUCTOR_AGENT_ID,
                        "project_id": "project-beta",
                        "task_id": "task_verified_review",
                        "kind": "host_verified",
                        "message": "Accepted earlier.",
                    },
                )

                result = conductor.verify_finished_tasks("test-user", project_id="project-beta")

                self.assertEqual(result["accepted"], 1)
                task = get_harness_state("test-user")["tasks"][0]
                self.assertEqual(task["status"], "done")
            finally:
                if old_state is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = old_state

    def test_task_md_round_trips_dashboard_and_lifecycle_comment(self):
        with TemporaryDirectory() as tmpdir:
            old_state = os.environ.get("CLAWCROSS_HARNESS_STATE_PATH")
            os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = str(Path(tmpdir) / "harness.json")
            try:
                root = Path(tmpdir) / "dashboard"
                (root / "state").mkdir(parents=True)
                tasks_path = root / "state" / "tasks.json"
                tasks_path.write_text(
                    json.dumps(
                        {
                            "tasks": [
                                {
                                    "task_id": "task_lifecycle",
                                    "project_id": "project-alpha",
                                    "title": "Run lifecycle verifier",
                                    "description": "Exercise TASK.md sync.",
                                    "status": "todo",
                                    "priority": "high",
                                    "comments": [],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                task_md = Path(tmpdir) / "TASK.md"

                exported = sync_task_markdown(
                    "test-user",
                    task_md_path=task_md,
                    dashboard_root=root,
                    project_id="project-alpha",
                    direction="dashboard-to-md",
                )
                self.assertEqual(exported["task_md_export"]["tasks"], 1)
                payload = parse_task_markdown(task_md.read_text(encoding="utf-8"))
                self.assertEqual(payload["tasks"][0]["update"]["plan"], "")

                payload["tasks"][0]["update"].update(
                    {
                        "status": "blocked",
                        "plan": "Run a minimal verifier first.",
                        "execution": "python verify.py --smoke",
                        "modifications": "No code changes.",
                        "experiments": "Exit 2 because input file is missing.",
                        "result": "Blocked on missing fixture.",
                        "next": "Provide fixture.json.",
                    }
                )
                task_md.write_text(render_task_markdown(payload), encoding="utf-8")

                imported = sync_task_markdown(
                    "test-user",
                    task_md_path=task_md,
                    dashboard_root=root,
                    project_id="project-alpha",
                    direction="md-to-dashboard",
                )
                self.assertEqual(imported["task_md_import"]["status_updates"], 1)
                self.assertEqual(imported["task_md_import"]["comments_added"], 1)
                dashboard_doc = json.loads(tasks_path.read_text(encoding="utf-8"))
                task = dashboard_doc["tasks"][0]
                self.assertEqual(task["status"], "blocked")
                self.assertTrue(any("## Plan" in c.get("body", "") for c in task.get("comments", [])))
            finally:
                if old_state is None:
                    os.environ.pop("CLAWCROSS_HARNESS_STATE_PATH", None)
                else:
                    os.environ["CLAWCROSS_HARNESS_STATE_PATH"] = old_state


if __name__ == "__main__":
    unittest.main()
