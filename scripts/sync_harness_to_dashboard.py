#!/usr/bin/env python3
"""Sync task/TODO facts between ClawCross harness and dashboard."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from harness.dashboard_sync import import_dashboard_todos, sync_harness_to_dashboard  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync ClawCross harness TODOs with dashboard/state/tasks.json.")
    default_dashboard = os.getenv("CLAWCROSS_DASHBOARD_ROOT") or os.getenv("DASHBOARD_ROOT") or ""
    parser.add_argument("--dashboard-root", type=Path, default=Path(default_dashboard).expanduser() if default_dashboard else None)
    parser.add_argument("--user-id", default=os.getenv("CLAWCROSS_HARNESS_USER") or os.getenv("CLAWCROSS_USER_ID") or "default")
    parser.add_argument("--project-id", default=os.getenv("CLAWCROSS_HARNESS_PROJECT_ID", ""))
    parser.add_argument("--create-missing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--direction",
        choices=["push", "pull", "both"],
        default="push",
        help="push copies ClawCross->dashboard, pull imports dashboard TODOs, both does pull then push.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = {}
    if args.direction in {"pull", "both"}:
        summary["pull"] = import_dashboard_todos(
            args.user_id,
            dashboard_root=args.dashboard_root,
            project_id=args.project_id,
            write=not args.dry_run,
        )
    if args.direction in {"push", "both"}:
        summary["push"] = sync_harness_to_dashboard(
            args.user_id,
            dashboard_root=args.dashboard_root,
            project_id=args.project_id,
            create_missing=args.create_missing,
            write=not args.dry_run,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
