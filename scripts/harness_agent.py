#!/usr/bin/env python3
"""Post worker/task/run events into the ClawCross harness control plane."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

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


def post_event(args: argparse.Namespace, payload: dict) -> dict:
    load_env_file()
    token = args.internal_token or os.getenv("INTERNAL_TOKEN", "")
    if not token:
        raise SystemExit("INTERNAL_TOKEN is required. Start ClawCross once or pass --internal-token.")
    base_url = (args.base_url or os.getenv("CLAWCROSS_AGENT_BASE_URL") or f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}").rstrip("/")
    user_id = args.user_id or os.getenv("CLAWCROSS_HARNESS_USER") or os.getenv("CLAWCROSS_USER_ID") or os.getenv("USER") or "default"
    payload = {key: value for key, value in payload.items() if value not in (None, "")}
    payload["user_id"] = user_id
    request = urllib.request.Request(
        f"{base_url}/harness/event",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Internal-Token": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"harness event failed: HTTP {exc.code} {text}") from exc
    data = json.loads(text)
    if data.get("ok") is False or data.get("status") == "error":
        raise SystemExit(f"harness event failed: {text}")
    return data


def request_json(args: argparse.Namespace, path: str, *, payload: dict | None = None) -> dict:
    load_env_file()
    token = args.internal_token or os.getenv("INTERNAL_TOKEN", "")
    if not token:
        raise SystemExit("INTERNAL_TOKEN is required. Start ClawCross once or pass --internal-token.")
    base_url = (args.base_url or os.getenv("CLAWCROSS_AGENT_BASE_URL") or f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}").rstrip("/")
    user_id = args.user_id or os.getenv("CLAWCROSS_HARNESS_USER") or os.getenv("CLAWCROSS_USER_ID") or os.getenv("USER") or "default"
    url = f"{base_url}{path}"
    headers = {"Accept": "application/json", "X-Internal-Token": token}
    data = None
    method = "GET"
    if payload is not None:
        payload = {key: value for key, value in payload.items() if value not in (None, "")}
        payload["user_id"] = user_id
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    elif "?" in url:
        url = f"{url}&user_id={urllib.parse.quote(user_id)}"
    else:
        url = f"{url}?user_id={urllib.parse.quote(user_id)}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"harness request failed: HTTP {exc.code} {text}") from exc
    data = json.loads(text)
    if data.get("ok") is False or data.get("status") == "error":
        raise SystemExit(f"harness request failed: {text}")
    return data


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent-id", default="")
    parser.add_argument("--agent-type", default="")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--project-title", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--session-ref", default="")
    parser.add_argument("--remote-host", default="")
    parser.add_argument("--worktree", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--git-sha", default="")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Update ClawCross harness state from an agent hook.")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--internal-token", default="")
    parser.add_argument("--timeout", type=float, default=20.0)
    sub = parser.add_subparsers(dest="subcommand", required=True)

    heartbeat = sub.add_parser("heartbeat")
    add_common(heartbeat)
    heartbeat.add_argument("--status", default="running")

    needs_user = sub.add_parser("needs_user")
    add_common(needs_user)

    blocked = sub.add_parser("blocked")
    add_common(blocked)

    task = sub.add_parser("task")
    task.add_argument("--project-id", default="default")
    task.add_argument("--task-id", required=True)
    task.add_argument("--title", required=True)
    task.add_argument("--description", default="")
    task.add_argument("--status", default="todo")
    task.add_argument("--priority", default="normal")
    task.add_argument("--assignee", default="")
    task.add_argument("--due-at", default="")

    task_status = sub.add_parser("task-status")
    task_status.add_argument("--agent-id", default="")
    task_status.add_argument("--project-id", default="default")
    task_status.add_argument("--task-id", required=True)
    task_status.add_argument("--status", required=True)
    task_status.add_argument("--message", default="")

    comment = sub.add_parser("comment")
    comment.add_argument("--agent-id", default="")
    comment.add_argument("--project-id", default="default")
    comment.add_argument("--task-id", required=True)
    comment.add_argument("--message", required=True)
    comment.add_argument("--kind", default="comment")

    run = sub.add_parser("run")
    add_common(run)
    run.add_argument("--run-id", required=True)
    run.add_argument("--status", default="verified")
    run.add_argument("--command", required=True)
    run.add_argument("--exit-code", type=int, required=True)
    run.add_argument("--log-path", default="")
    run.add_argument("--metrics-path", default="")
    run.add_argument("--metrics-sha256", default="")
    run.add_argument("--started-at", default="")
    run.add_argument("--ended-at", default="")
    run.add_argument("--verifier-command", default="")
    run.add_argument("--verifier-status", default="passed")
    run.add_argument("--verifier-exit-code", type=int, default=0)

    opencli_status = sub.add_parser("opencli-status", help="Show OpenCLI availability and ClawCross harness capabilities.")
    opencli_status.add_argument("--query", default="")

    opencli_run = sub.add_parser("opencli-run", help="Run OpenCLI through the private ClawCross host.")
    opencli_run.add_argument("--profile", default="")
    opencli_run.add_argument("--allow-mutating", action="store_true")
    opencli_run.add_argument("--max-output-chars", type=int, default=20000)
    opencli_run.add_argument("args", nargs=argparse.REMAINDER)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    command = args.subcommand
    if command in {"heartbeat", "needs_user", "blocked"}:
        payload = {
            "action": command,
            "agent_id": args.agent_id,
            "agent_type": args.agent_type,
            "project_id": args.project_id,
            "project_title": args.project_title,
            "task_id": args.task_id,
            "status": getattr(args, "status", ""),
            "message": args.message,
            "session_ref": args.session_ref,
            "remote_host": args.remote_host,
            "worktree": args.worktree,
            "branch": args.branch,
            "git_sha": args.git_sha,
        }
    elif command == "task":
        payload = {
            "action": "task_upsert",
            "project_id": args.project_id,
            "task_id": args.task_id,
            "title": args.title,
            "description": args.description,
            "status": args.status,
            "priority": args.priority,
            "assignee": args.assignee,
            "due_at": args.due_at,
        }
    elif command == "task-status":
        status_aliases = {
            "doing": "active",
            "active": "active",
            "todo": "todo",
            "undone": "todo",
            "blocked": "blocked",
            "needs-user": "needs_user",
            "needs_user": "needs_user",
            "review": "review",
            "done": "done",
        }
        status = status_aliases.get(args.status.strip().lower().replace(" ", "_"), args.status)
        payload = {
            "action": "task_status",
            "agent_id": args.agent_id,
            "project_id": args.project_id,
            "task_id": args.task_id,
            "status": status,
            "message": args.message,
        }
    elif command == "comment":
        payload = {
            "action": "task_comment",
            "agent_id": args.agent_id,
            "project_id": args.project_id,
            "task_id": args.task_id,
            "message": args.message,
            "kind": args.kind,
        }
    elif command == "run":
        payload = {
            "action": "run",
            "agent_id": args.agent_id,
            "agent_type": args.agent_type,
            "project_id": args.project_id,
            "task_id": args.task_id,
            "run_id": args.run_id,
            "status": args.status,
            "message": args.message,
            "session_ref": args.session_ref,
            "remote_host": args.remote_host,
            "worktree": args.worktree,
            "branch": args.branch,
            "git_sha": args.git_sha,
            "command": args.command,
            "exit_code": args.exit_code,
            "log_path": args.log_path,
            "metrics_path": args.metrics_path,
            "metrics_sha256": args.metrics_sha256,
            "started_at": args.started_at,
            "ended_at": args.ended_at,
            "verifier": {
                "command": args.verifier_command,
                "status": args.verifier_status,
                "exit_code": args.verifier_exit_code,
            },
        }
    elif command == "opencli-status":
        query = f"?query={urllib.parse.quote(args.query)}" if args.query else ""
        result = request_json(args, f"/harness/opencli/status{query}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    else:
        opencli_args = args.args
        if opencli_args and opencli_args[0] == "--":
            opencli_args = opencli_args[1:]
        result = request_json(
            args,
            "/harness/opencli/run",
            payload={
                "args": opencli_args,
                "profile": args.profile,
                "allow_mutating": args.allow_mutating,
                "max_output_chars": args.max_output_chars,
                "timeout_seconds": args.timeout,
            },
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    result = post_event(args, payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
