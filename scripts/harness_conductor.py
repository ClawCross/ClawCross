#!/usr/bin/env python3
"""Run the ClawCross local conductor loop for remote harness workers."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from harness.conductor import run_conductor_once  # noqa: E402
from utils.runtime_paths import ENV_FILE  # noqa: E402


def load_env_file() -> None:
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Keep remote Claude harness workers moving from the local ClawCross host.")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--interval", type=float, default=float(os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_INTERVAL", "25")))
    parser.add_argument("--cooldown", type=int, default=int(os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_COOLDOWN", "180")))
    parser.add_argument("--remote-limit", type=int, default=int(os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_REMOTE_LIMIT", "12")))
    parser.add_argument(
        "--project-id",
        default=os.getenv("CLAWCROSS_HARNESS_PROJECT_ID", ""),
        help="Dashboard project to sync. Empty means all projects.",
    )
    parser.add_argument("--dashboard-root", default=os.getenv("CLAWCROSS_HARNESS_DASHBOARD_ROOT", ""))
    parser.add_argument("--no-dashboard-sync", action="store_true")
    parser.add_argument(
        "--dashboard-publish",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("CLAWCROSS_HARNESS_DASHBOARD_PUBLISH", True),
        help="Commit and push dashboard/state/tasks.json when the conductor changes dashboard task state.",
    )
    parser.add_argument(
        "--dashboard-supabase-sync",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("CLAWCROSS_HARNESS_DASHBOARD_SUPABASE_SYNC", True),
        help="Sync dashboard/state/*.json into Supabase when the conductor changes dashboard task state.",
    )
    parser.add_argument(
        "--llm-mode",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("CLAWCROSS_HARNESS_CONDUCTOR_LLM", True),
        help="Use the configured Webot/ClawCross LLM to choose assignments and draft replies.",
    )
    parser.add_argument(
        "--codex-review",
        action=argparse.BooleanOptionalAction,
        default=_env_bool("CLAWCROSS_HARNESS_CONDUCTOR_CODEX_REVIEW", True),
        help="Use local Codex exec to inspect and resolve review/待审查 TODOs before surfacing them to the user.",
    )
    parser.add_argument(
        "--codex-review-limit",
        type=int,
        default=int(os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_CODEX_REVIEW_LIMIT", "1")),
        help="Maximum review TODOs local Codex may inspect per conductor tick.",
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    load_env_file()
    if not _env_bool("CLAWCROSS_HARNESS_CONDUCTOR", True):
        print("[harness-conductor] disabled by CLAWCROSS_HARNESS_CONDUCTOR=0", flush=True)
        return
    args = build_parser().parse_args()
    user_id = args.user_id or os.getenv("CLAWCROSS_HARNESS_USER") or os.getenv("CLAWCROSS_USER_ID") or os.getenv("USER") or "default"
    driver = "webot_llm" if args.llm_mode else "rules"
    print(
        f"[harness-conductor] started user={user_id} interval={args.interval}s cooldown={args.cooldown}s driver={driver}",
        flush=True,
    )

    while True:
        try:
            result = run_conductor_once(
                user_id,
                remote_limit=max(1, args.remote_limit),
                cooldown_seconds=max(30, args.cooldown),
                dry_run=args.dry_run,
                sync_dashboard=not args.no_dashboard_sync,
                dashboard_root=Path(args.dashboard_root).expanduser() if args.dashboard_root else None,
                project_id=args.project_id,
                llm_mode=bool(args.llm_mode),
                codex_review=bool(args.codex_review),
                codex_review_limit=max(0, args.codex_review_limit),
                publish_dashboard=bool(args.dashboard_publish) and not args.no_dashboard_sync,
                sync_supabase=bool(args.dashboard_supabase_sync) and not args.no_dashboard_sync,
            )
            actions = result.get("actions") or []
            if actions or not result.get("remote_ok"):
                print("[harness-conductor] " + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
        except Exception as exc:
            print(f"[harness-conductor] error: {exc}", file=sys.stderr, flush=True)
        if args.once:
            break
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    main()
