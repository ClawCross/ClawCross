"""TASK.md sync helpers for remote ClawCross workers.

The dashboard remains the public TODO board and the harness remains the private
control plane. TASK.md is a per-worktree working copy for remote Claude workers:
dashboard -> harness -> TASK.md, and worker edits in TASK.md -> harness ->
dashboard.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from harness.dashboard_sync import (
    clean_status,
    import_dashboard_todos,
    now_iso,
    sync_harness_to_dashboard,
)
from harness.store import apply_harness_event, get_harness_state


TASK_MD_SCHEMA_VERSION = "clawcross.task_md.v1"
TASK_MD_START = "<!-- CLAWCROSS_TASK_MD_START -->"
TASK_MD_END = "<!-- CLAWCROSS_TASK_MD_END -->"
TASK_MD_JSON_RE = re.compile(
    rf"{re.escape(TASK_MD_START)}\s*```json\s*(?P<payload>.*?)\s*```\s*{re.escape(TASK_MD_END)}",
    re.DOTALL,
)
OPEN_TASK_STATUSES = {"todo", "active", "blocked", "needs_user", "review"}


def _coerce_task_list(state: dict[str, Any], project_id: str, *, include_done: bool = False) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task in state.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        if project_id and task.get("project_id") != project_id:
            continue
        status = clean_status(task.get("status")) or "todo"
        if not include_done and status == "done":
            continue
        tasks.append(task)
    tasks.sort(
        key=lambda item: (
            {"active": 0, "blocked": 1, "needs_user": 2, "review": 3, "todo": 4, "done": 5}.get(
                clean_status(item.get("status")) or "todo",
                9,
            ),
            str(item.get("due_at") or ""),
            str(item.get("priority") or ""),
            str(item.get("task_id") or ""),
        )
    )
    return tasks


def task_markdown_payload(
    user_id: str,
    *,
    project_id: str,
    include_done: bool = False,
) -> dict[str, Any]:
    """Build the machine-editable TASK.md payload from harness state."""

    state = get_harness_state(user_id)
    projects = {
        str(project.get("project_id") or ""): project
        for project in state.get("projects", []) or []
        if isinstance(project, dict)
    }
    project = projects.get(project_id) or {}
    tasks = []
    for task in _coerce_task_list(state, project_id, include_done=include_done):
        comments = [
            comment
            for comment in task.get("comments", []) or []
            if isinstance(comment, dict) and str(comment.get("body") or "").strip()
        ]
        latest = comments[-1] if comments else {}
        tasks.append(
            {
                "task_id": str(task.get("task_id") or ""),
                "project_id": str(task.get("project_id") or project_id),
                "title": str(task.get("title") or task.get("task_id") or ""),
                "description": str(task.get("description") or ""),
                "status": clean_status(task.get("status")) or "todo",
                "priority": str(task.get("priority") or "medium"),
                "assignee": str(task.get("assignee") or ""),
                "due_at": str(task.get("due_at") or ""),
                "latest_comment": {
                    "kind": str(latest.get("kind") or ""),
                    "author": str(latest.get("author") or ""),
                    "body": str(latest.get("body") or ""),
                    "created_at": str(latest.get("created_at") or ""),
                },
                "update": {
                    "status": "",
                    "plan": "",
                    "execution": "",
                    "modifications": "",
                    "experiments": "",
                    "result": "",
                    "next": "",
                    "comment_kind": "comment",
                    "comment": "",
                },
            }
        )
    return {
        "schema_version": TASK_MD_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "user_id": user_id,
        "project_id": project_id,
        "project_title": str(project.get("title") or project_id),
        "instructions": [
            "Use each tasks[].update object as a plan-execute-modify-experiment log.",
            "Edit update.status, plan, execution, modifications, experiments, result, next, and comment.",
            "Allowed status values: todo, active, blocked, needs_user, review, done.",
            "After editing, run: clawcross-harness-agent task-md import --path TASK.md",
        ],
        "tasks": tasks,
    }


def render_task_markdown(payload: dict[str, Any]) -> str:
    """Render TASK.md with a human summary and a managed JSON block."""

    tasks = payload.get("tasks") if isinstance(payload.get("tasks"), list) else []
    lines = [
        "# ClawCross TASK.md",
        "",
        f"Project: `{payload.get('project_title') or payload.get('project_id')}`",
        f"Generated: `{payload.get('generated_at')}`",
        "",
        "Use `update` fields as the worker log: plan, execution, modifications, experiments, result, next. Then run `clawcross-harness-agent task-md import --path TASK.md`.",
        "",
        "| Status | Priority | Task | Assignee |",
        "| --- | --- | --- | --- |",
    ]
    for task in tasks:
        if not isinstance(task, dict):
            continue
        title = str(task.get("title") or task.get("task_id") or "").replace("|", "\\|")
        lines.append(
            "| {status} | {priority} | `{task_id}` {title} | {assignee} |".format(
                status=str(task.get("status") or ""),
                priority=str(task.get("priority") or ""),
                task_id=str(task.get("task_id") or ""),
                title=title,
                assignee=str(task.get("assignee") or ""),
            )
        )
    lines.extend(["", "## Current Task Context", ""])
    for task in tasks:
        if not isinstance(task, dict):
            continue
        title = " ".join(str(task.get("title") or task.get("task_id") or "").split())
        task_id = str(task.get("task_id") or "")
        lines.extend(
            [
                f"### `{task_id}` {title}",
                "",
                f"- Status: `{task.get('status') or ''}`",
                f"- Priority: `{task.get('priority') or ''}`",
                f"- Assignee: `{task.get('assignee') or ''}`",
            ]
        )
        description = str(task.get("description") or "").strip()
        if description:
            lines.extend(["", "Description:", "", description])
        latest = task.get("latest_comment") if isinstance(task.get("latest_comment"), dict) else {}
        latest_body = str(latest.get("body") or "").strip()
        if latest_body:
            lines.extend(["", "Latest dashboard comment:", "", latest_body])
        lines.append("")
    block = json.dumps(payload, ensure_ascii=False, indent=2)
    lines.extend(["", TASK_MD_START, "```json", block, "```", TASK_MD_END, ""])
    return "\n".join(lines)


def write_task_markdown(
    path: Path,
    payload: dict[str, Any],
    *,
    preserve_outside_block: bool = True,
) -> dict[str, Any]:
    """Write the managed TASK.md block, preserving notes outside it when possible."""

    path = path.expanduser()
    rendered = render_task_markdown(payload)
    if preserve_outside_block and path.exists():
        existing = path.read_text(encoding="utf-8")
        if TASK_MD_JSON_RE.search(existing):
            rendered = TASK_MD_JSON_RE.sub(
                "\n".join(rendered.splitlines()[rendered.splitlines().index(TASK_MD_START) :]),
                existing,
                count=1,
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(rendered, encoding="utf-8")
    return {"path": str(path), "changed": before != rendered, "tasks": len(payload.get("tasks") or [])}


def parse_task_markdown(text: str) -> dict[str, Any]:
    match = TASK_MD_JSON_RE.search(text)
    if not match:
        raise ValueError("TASK.md is missing the ClawCross managed JSON block")
    payload = json.loads(match.group("payload"))
    if not isinstance(payload, dict):
        raise ValueError("TASK.md payload must be a JSON object")
    if payload.get("schema_version") != TASK_MD_SCHEMA_VERSION:
        raise ValueError(f"unsupported TASK.md schema: {payload.get('schema_version')!r}")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("TASK.md payload must contain tasks[]")
    return payload


def import_task_markdown(
    user_id: str,
    *,
    task_md_path: Path,
    project_id: str = "",
    agent_id: str = "task-md-sync",
    write: bool = True,
) -> dict[str, Any]:
    """Apply TASK.md status/comment edits to the local harness."""

    payload = parse_task_markdown(task_md_path.expanduser().read_text(encoding="utf-8"))
    project_id = project_id or str(payload.get("project_id") or "")
    state = get_harness_state(user_id)
    by_id = {
        str(task.get("task_id") or ""): task
        for task in state.get("tasks", []) or []
        if isinstance(task, dict)
    }
    summary = {"status_updates": 0, "comments_added": 0, "tasks_seen": 0, "skipped": 0}
    for task in payload.get("tasks", []) or []:
        if not isinstance(task, dict):
            summary["skipped"] += 1
            continue
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            summary["skipped"] += 1
            continue
        task_project_id = str(task.get("project_id") or project_id or "").strip()
        if project_id and task_project_id != project_id:
            summary["skipped"] += 1
            continue
        summary["tasks_seen"] += 1
        update = task.get("update") if isinstance(task.get("update"), dict) else {}
        status = clean_status(update.get("status"))
        local = by_id.get(task_id) or {}
        if status and status != (clean_status(local.get("status")) or "todo"):
            if write:
                apply_harness_event(
                    user_id,
                    {
                        "action": "task_status",
                        "agent_id": agent_id,
                        "project_id": task_project_id,
                        "task_id": task_id,
                        "status": status,
                        "message": f"TASK.md status -> {status}",
                    },
                )
            summary["status_updates"] += 1
        lifecycle_parts: list[str] = []
        for label, key in [
            ("Plan", "plan"),
            ("Execution", "execution"),
            ("Modifications", "modifications"),
            ("Experiments", "experiments"),
            ("Result", "result"),
            ("Next", "next"),
        ]:
            value = str(update.get(key) or "").strip()
            if value:
                lifecycle_parts.append(f"## {label}\n{value}")
        comment = str(update.get("comment") or "").strip()
        if lifecycle_parts:
            comment = "\n\n".join(lifecycle_parts + ([f"## Comment\n{comment}"] if comment else []))
        if comment:
            existing_bodies = {
                str(comment_item.get("body") or "").strip()
                for comment_item in local.get("comments", []) or []
                if isinstance(comment_item, dict)
            }
            if comment not in existing_bodies:
                if write:
                    apply_harness_event(
                        user_id,
                        {
                            "action": "task_comment",
                            "agent_id": agent_id,
                            "project_id": task_project_id,
                            "task_id": task_id,
                            "kind": str(update.get("comment_kind") or "comment"),
                            "message": comment,
                        },
                    )
                summary["comments_added"] += 1
    summary["source"] = str(task_md_path)
    return summary


def sync_task_markdown(
    user_id: str,
    *,
    task_md_path: Path,
    dashboard_root: Path | None = None,
    project_id: str,
    direction: str = "both",
    include_done: bool = False,
    create_missing: bool = True,
    write: bool = True,
) -> dict[str, Any]:
    """Sync dashboard/harness with TASK.md.

    ``both`` pulls dashboard TODOs first, applies TASK.md edits if the file
    exists, pushes harness updates back to dashboard, and rewrites TASK.md.
    TASK.md edits win over dashboard status for explicit update.status fields.
    """

    if direction not in {"dashboard-to-md", "md-to-dashboard", "both"}:
        raise ValueError("direction must be dashboard-to-md, md-to-dashboard, or both")
    summary: dict[str, Any] = {}
    if direction in {"dashboard-to-md", "both"}:
        summary["dashboard_pull"] = import_dashboard_todos(
            user_id,
            dashboard_root=dashboard_root,
            project_id=project_id,
            write=write,
        )
    if direction in {"md-to-dashboard", "both"} and task_md_path.expanduser().exists():
        summary["task_md_import"] = import_task_markdown(
            user_id,
            task_md_path=task_md_path,
            project_id=project_id,
            write=write,
        )
        summary["dashboard_push"] = sync_harness_to_dashboard(
            user_id,
            dashboard_root=dashboard_root,
            project_id=project_id,
            create_missing=create_missing,
            write=write,
        )
    if direction in {"dashboard-to-md", "both"}:
        payload = task_markdown_payload(user_id, project_id=project_id, include_done=include_done)
        summary["task_md_export"] = write_task_markdown(task_md_path, payload) if write else {
            "path": str(task_md_path),
            "changed": True,
            "tasks": len(payload.get("tasks") or []),
        }
    return summary
