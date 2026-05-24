#!/usr/bin/env python3
"""Sync ClawCross dashboard/harness TODOs with a worker TASK.md file."""

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

from harness.task_markdown import sync_task_markdown  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync dashboard TODOs with a TASK.md worker log.")
    default_dashboard = os.getenv("CLAWCROSS_DASHBOARD_ROOT") or os.getenv("DASHBOARD_ROOT") or ""
    parser.add_argument("--dashboard-root", type=Path, default=Path(default_dashboard).expanduser() if default_dashboard else None)
    parser.add_argument("--task-md", type=Path, default=Path("TASK.md"))
    parser.add_argument("--user-id", default=os.getenv("CLAWCROSS_HARNESS_USER") or os.getenv("CLAWCROSS_USER_ID") or "default")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--include-done", action="store_true")
    parser.add_argument("--no-create-missing", action="store_false", dest="create_missing")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--direction",
        choices=["dashboard-to-md", "md-to-dashboard", "both"],
        default="both",
        help=(
            "dashboard-to-md pulls dashboard TODOs into TASK.md; "
            "md-to-dashboard imports TASK.md edits and pushes dashboard; "
            "both does dashboard pull, TASK.md import if present, dashboard push, then TASK.md export."
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    summary = sync_task_markdown(
        args.user_id,
        task_md_path=args.task_md,
        dashboard_root=args.dashboard_root,
        project_id=args.project_id,
        direction=args.direction,
        include_done=args.include_done,
        create_missing=args.create_missing,
        write=not args.dry_run,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
