#!/usr/bin/env python3
"""
Repo-level Markdown skill self-evolution helper.

Runs a command, captures failure output, and updates a target Markdown skill
document with a managed self-evolution block plus a report under docs/.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from webot.skill_evolution import update_markdown_skill_document  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update a Markdown skill file from execution failures.")
    parser.add_argument("--skill", required=True, help="Path to the Markdown skill file to update.")
    parser.add_argument("--command", default="", help="Shell command to run and analyze.")
    parser.add_argument("--cwd", default=str(PROJECT_ROOT), help="Working directory for the command.")
    parser.add_argument("--stdout-file", default="", help="Optional file to read stdout from instead of running a command.")
    parser.add_argument("--stderr-file", default="", help="Optional file to read stderr from instead of running a command.")
    parser.add_argument("--exit-code", type=int, default=None, help="Explicit exit code when using --stdout-file/--stderr-file.")
    parser.add_argument("--strategy", default="auto", help="Evolution strategy preset: auto, balanced, innovate, harden, repair-only.")
    parser.add_argument("--force", action="store_true", help="Write/update the self-evolution block even if the command succeeds.")
    return parser.parse_args()


def _read_optional(path_value: str) -> str:
    if not path_value:
        return ""
    return Path(path_value).expanduser().read_text(encoding="utf-8", errors="replace")


def main() -> int:
    args = _parse_args()
    stdout = _read_optional(args.stdout_file)
    stderr = _read_optional(args.stderr_file)
    exit_code = args.exit_code
    duration_ms = None

    if args.command:
        started = time.perf_counter()
        completed = subprocess.run(
            args.command,
            shell=True,
            cwd=Path(args.cwd).expanduser(),
            capture_output=True,
            text=True,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        stdout = completed.stdout or stdout
        stderr = completed.stderr or stderr
        exit_code = completed.returncode
    elif exit_code is None:
        exit_code = 1

    result = update_markdown_skill_document(
        skill_path=args.skill,
        command=args.command or "(external failure context)",
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        force=args.force,
        strategy=args.strategy,
        duration_ms=duration_ms,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(exit_code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
