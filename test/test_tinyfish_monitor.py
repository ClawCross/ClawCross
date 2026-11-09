import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import services.tinyfish_monitor_service as svc


class TinyFishMonitorUnitTests(unittest.TestCase):
    def test_iter_sse_json_events_parses_event_blocks(self):
        lines = [
            "event: STARTED\n",
            'data: {"run_id":"run-live-1","type":"STARTED"}\n',
            "\n",
            "data: {\"type\":\"PROGRESS\",\"message\":\"opening page\"}\n",
            "\n",
            "event: HEARTBEAT\n",
            'data: {"type":"PROGRESS","message":"still crawling"}\n',
            "\n",
            "event: MALFORMED\n",
            "data: not-json\n",
            "\n",
        ]

        events = list(svc.iter_sse_json_events(lines))

        self.assertEqual(len(events), 4)
        self.assertEqual(events[0]["type"], "STARTED")
        self.assertEqual(events[0]["run_id"], "run-live-1")
        self.assertEqual(events[0]["_sse_event"], "STARTED")
        self.assertEqual(events[1]["type"], "PROGRESS")
        self.assertEqual(events[1]["message"], "opening page")
        self.assertEqual(events[2]["type"], "PROGRESS")
        self.assertEqual(events[2]["message"], "still crawling")
        self.assertEqual(events[2]["_sse_event"], "HEARTBEAT")
        self.assertEqual(events[3]["type"], "MALFORMED")
        self.assertEqual(events[3]["raw"], "not-json")
        self.assertEqual(events[3]["_sse_event"], "MALFORMED")

    def test_load_targets_merges_defaults_and_filters_by_site_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            targets_path = Path(tmpdir) / "targets.json"
            targets_path.write_text(
                json.dumps(
                    {
                        "defaults": {
                            "browser_profile": "stealth",
                            "extra_payload": {"proxy_config": {"enabled": True, "country_code": "US"}},
                        },
                        "targets": [
                            {"site_key": "alpha", "name": "Alpha", "url": "https://alpha.example/pricing"},
                            {"site_key": "beta", "name": "Beta", "url": "https://beta.example/pricing"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            targets = svc.load_targets(targets_path, {"beta"})

            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0].site_key, "beta")
            self.assertEqual(targets[0].browser_profile, "stealth")
            self.assertEqual(targets[0].extra_payload["proxy_config"]["country_code"], "US")

    def test_get_db_path_reads_runtime_env(self):
        with patch.dict(os.environ, {"TINYFISH_MONITOR_DB_PATH": "/tmp/tinyfish-env-test.db"}, clear=False):
            self.assertEqual(svc.get_db_path(), Path("/tmp/tinyfish-env-test.db"))

    def test_persist_run_result_detects_updated_new_and_removed_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = svc.MonitorDB(Path(tmpdir) / "tinyfish.db")
            target = svc.Target(
                site_key="competitor-a",
                name="Competitor A",
                url="https://competitor-a.example/pricing",
                goal="Extract pricing",
            )
            try:
                db.record_submissions([target], ["run-1"])
                db.persist_run_result(
                    target,
                    {
                        "run_id": "run-1",
                        "status": "COMPLETED",
                        "created_at": "2026-03-28T00:00:00Z",
                        "started_at": "2026-03-28T00:00:05Z",
                        "finished_at": "2026-03-28T00:00:10Z",
                        "result": {
                            "items": [
                                {
                                    "item_key": "basic",
                                    "name": "Basic",
                                    "price": "$10 / month",
                                    "amount": 10,
                                    "currency": "USD",
                                    "billing_period": "monthly",
                                },
                                {
                                    "item_key": "pro",
                                    "name": "Pro",
                                    "price": "$30 / month",
                                    "amount": 30,
                                    "currency": "USD",
                                    "billing_period": "monthly",
                                },
                            ]
                        },
                    },
                )

                db.record_submissions([target], ["run-2"])
                summary = db.persist_run_result(
                    target,
                    {
                        "run_id": "run-2",
                        "status": "COMPLETED",
                        "created_at": "2026-03-29T00:00:00Z",
                        "started_at": "2026-03-29T00:00:05Z",
                        "finished_at": "2026-03-29T00:00:10Z",
                        "result": {
                            "items": [
                                {
                                    "item_key": "basic",
                                    "name": "Basic",
                                    "price": "$12 / month",
                                    "amount": 12,
                                    "currency": "USD",
                                    "billing_period": "monthly",
                                },
                                {
                                    "item_key": "team",
                                    "name": "Team",
                                    "price": "$50 / month",
                                    "amount": 50,
                                    "currency": "USD",
                                    "billing_period": "monthly",
                                },
                            ]
                        },
                    },
                )

                self.assertEqual(summary["status"], "COMPLETED")
                self.assertEqual(summary["snapshot_count"], 2)
                self.assertEqual(summary["change_count"], 3)

                changes = list(db.list_recent_changes(10))
                change_types = {(row["item_key"], row["change_type"]) for row in changes}
                self.assertIn(("basic", "UPDATED"), change_types)
                self.assertIn(("team", "NEW"), change_types)
                self.assertIn(("pro", "REMOVED"), change_types)

                latest_sites = db.list_latest_site_snapshots(limit_sites=5, per_site_limit=10)
                self.assertEqual(latest_sites[0]["site_key"], "competitor-a")
                self.assertEqual(latest_sites[0]["snapshot_count"], 2)
            finally:
                db.close()

    def test_poll_pending_runs_once_syncs_completed_runs_from_client(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "poll.db"
            db = svc.MonitorDB(db_path)
            target = svc.Target(
                site_key="poll-site",
                name="Polling Site",
                url="https://poll.example/pricing",
                goal="Extract pricing",
            )
            db.record_submissions([target], ["run-poll-1"])
            db.close()

            class FakeClient:
                def get_runs_batch(self, run_ids):
                    return [
                        {
                            "run_id": run_ids[0],
                            "status": "COMPLETED",
                            "created_at": "2026-03-30T00:00:00Z",
                            "started_at": "2026-03-30T00:00:03Z",
                            "finished_at": "2026-03-30T00:00:05Z",
                            "result": {
                                "items": [
                                    {
                                        "item_key": "starter",
                                        "name": "Starter",
                                        "price": "$8 / month",
                                        "amount": 8,
                                        "currency": "USD",
                                        "billing_period": "monthly",
                                    }
                                ]
                            },
                        }
                    ]

            with patch.object(svc, "create_client", return_value=FakeClient()):
                result = svc.poll_pending_runs_once(db_path=db_path)

            self.assertEqual(result["pending"], 1)
            self.assertEqual(len(result["completed"]), 1)
            self.assertEqual(result["completed"][0]["status"], "COMPLETED")
            self.assertEqual(result["still_pending"], [])

            check_db = svc.MonitorDB(db_path)
            try:
                self.assertEqual(check_db.count_pending_runs(), 0)
                rows = check_db.list_snapshots_for_run("run-poll-1")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["item_key"], "starter")
            finally:
                check_db.close()

    def test_stream_live_run_persists_complete_sse_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            targets_path = Path(tmpdir) / "targets.json"
            targets_path.write_text(
                json.dumps(
                    {
                        "targets": [
                            {
                                "site_key": "alpha",
                                "name": "Alpha",
                                "url": "https://alpha.example/pricing",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            db_path = Path(tmpdir) / "live.db"

            class FakeClient:
                def run_sse(self, target):
                    return iter(
                        [
                            {"type": "STARTED", "run_id": "run-live-1", "timestamp": "2026-03-31T00:00:01Z"},
                            {"type": "STREAMING_URL", "run_id": "run-live-1", "streaming_url": "https://watch.example"},
                            {"type": "PROGRESS", "run_id": "run-live-1", "message": "opened pricing page"},
                            {
                                "type": "COMPLETE",
                                "run_id": "run-live-1",
                                "status": "COMPLETED",
                                "timestamp": "2026-03-31T00:00:08Z",
                                "result": {
                                    "items": [
                                        {
                                            "item_key": "starter",
                                            "name": "Starter",
                                            "price": "$9 / month",
                                            "amount": 9,
                                            "currency": "USD",
                                            "billing_period": "monthly",
                                        }
                                    ]
                                },
                            },
                        ]
                    )

            with patch.dict(
                os.environ,
                {
                    "TINYFISH_MONITOR_TARGETS_PATH": str(targets_path),
                    "TINYFISH_MONITOR_DB_PATH": str(db_path),
                },
                clear=False,
            ), patch.object(svc, "create_client", return_value=FakeClient()):
                events = list(svc.stream_live_run(site_key="alpha", db_path=db_path))

            self.assertEqual(events[-1]["type"], "COMPLETE")
            self.assertEqual(events[-1]["clawcross_summary"]["snapshot_count"], 1)

            db = svc.MonitorDB(db_path)
            try:
                runs = list(db.list_recent_runs(5))
                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0]["status"], "COMPLETED")
                snapshots = db.list_snapshots_for_run("run-live-1")
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(snapshots[0]["item_key"], "starter")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
