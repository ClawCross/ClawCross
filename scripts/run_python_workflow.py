from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from oasis.python_workflow import resolve_python_workflow_path


def _resolve_python_file(user_id: str, python_file: str, team: str = "") -> str:
    if os.path.isabs(python_file) and os.path.isfile(python_file):
        return python_file
    if os.path.isfile(python_file):
        return os.path.abspath(python_file)
    resolved, err = resolve_python_workflow_path(user_id, python_file, team)
    if err:
        raise FileNotFoundError(err)
    return str(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve a saved workflow name and run the target Python workflow script directly.",
    )
    parser.add_argument("python_file", help="Workflow file path or saved workflow name.")
    parser.add_argument("--user-id", default="default", help="User ID for agent/topic scope.")
    parser.add_argument("--team", default="", help="Optional team scope.")
    parser.add_argument("--question", default="", help="Question/task passed into the workflow.")
    parser.add_argument("--result-file", default="", help="Optional JSON output file.")
    parser.add_argument(
        "--no-auto-topic",
        action="store_true",
        help="Disable the default behavior of auto-creating an OASIS topic for this run.",
    )
    args = parser.parse_args()

    try:
        python_file = _resolve_python_file(args.user_id, args.python_file, args.team)
        cmd = [
            sys.executable,
            python_file,
            "--user-id",
            args.user_id,
            "--question",
            args.question,
        ]
        if args.team:
            cmd.extend(["--team", args.team])
        if args.result_file:
            cmd.extend(["--result-file", args.result_file])
        if args.no_auto_topic:
            cmd.append("--no-auto-topic")
        proc = subprocess.run(cmd, cwd=_PROJECT_ROOT)
        return int(proc.returncode)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
