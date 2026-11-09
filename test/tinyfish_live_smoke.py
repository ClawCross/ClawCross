"""
Opt-in TinyFish live smoke test.

Examples:
  python3 test/tinyfish_live_smoke.py --site notion-pricing
  python3 test/tinyfish_live_smoke.py --site linear-pricing --max-wait 240
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import services.tinyfish_monitor_service as svc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a real TinyFish smoke test against configured targets.")
    parser.add_argument("--site", action="append", required=True, help="site_key from config/tinyfish_targets.json")
    parser.add_argument("--targets", default=str(PROJECT_ROOT / "config" / "tinyfish_targets.json"))
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--max-wait", type=int, default=240)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected_sites = set(args.site or [])

    with tempfile.TemporaryDirectory(prefix="tinyfish-live-smoke-") as tmpdir:
        db_path = Path(tmpdir) / "live_smoke.db"
        result = svc.submit_monitor_run(
            targets_path=args.targets,
            db_path=db_path,
            selected_sites=selected_sites,
            wait=True,
            poll_interval=args.poll_interval,
            max_wait_seconds=args.max_wait,
        )
        completed = result.get("results") or []
        if not completed:
            print("TinyFish live smoke test failed: no completed results.")
            return 1

        print(f"submitted={result.get('submitted', 0)}")
        for item in completed:
            print(
                f"{item['site_key']}: status={item['status']} "
                f"snapshots={item['snapshot_count']} changes={item['change_count']} run_id={item['run_id']}"
            )
            if item["status"] != "COMPLETED" or item["snapshot_count"] <= 0:
                return 1

        overview = svc.get_monitor_overview(db_path=db_path, latest_site_limit=10, snapshots_per_site=20)
        print(f"stored_sites={len(overview['sites'])}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
