"""Task-only sync between the public dashboard and ClawCross harness.

Dashboard remains a human/agent-readable TODO board. Runtime details such as
Claude settings, session wiring, credentials, and worker process state stay in
ClawCross.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any
import urllib.request

from harness.store import apply_harness_event, get_harness_state


DASHBOARD_STATUSES = {"todo", "active", "blocked", "needs_user", "review", "done"}
OPEN_DASHBOARD_STATUSES = {"todo", "active", "blocked", "needs_user", "review"}
HOST_VERIFIED_COMMENT_KIND = "host_verified"
INTERNAL_DASHBOARD_COMMENT_KINDS = {"conductor_reply", "conductor_note"}
INTERNAL_DASHBOARD_BODY_PREFIXES = (
    "本机主控已向远端 session",
    "本机主控拦截到疑似危险输入请求",
    "ClawCross tried to close remote session",
    "ClawCross closed remote session",
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_dashboard_root() -> Path:
    for key in ("CLAWCROSS_DASHBOARD_ROOT", "DASHBOARD_ROOT"):
        value = os.getenv(key, "").strip()
        if value:
            return Path(value).expanduser()
    raise RuntimeError("dashboard root is not configured; set CLAWCROSS_DASHBOARD_ROOT or pass --dashboard-root")


def default_dashboard_url() -> str:
    for key in ("CLAWCROSS_DASHBOARD_URL", "DASHBOARD_URL"):
        value = os.getenv(key, "").strip().rstrip("/")
        if value:
            return value
    raise RuntimeError("dashboard URL is not configured; set CLAWCROSS_DASHBOARD_URL or DASHBOARD_URL")


def clean_status(value: Any) -> str:
    status = str(value or "").strip().lower().replace("-", "_")
    if status == "doing":
        status = "active"
    if status == "undone":
        status = "todo"
    return status if status in DASHBOARD_STATUSES else ""


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dashboard_tasks_path(dashboard_root: Path | None = None) -> Path:
    return (dashboard_root or default_dashboard_root()) / "state" / "tasks.json"


def load_dashboard_tasks_doc(dashboard_root: Path | None = None) -> tuple[dict[str, Any], str]:
    if dashboard_root is not None:
        path = dashboard_tasks_path(dashboard_root)
        return load_json(path), str(path)
    try:
        path = dashboard_tasks_path(None)
        return load_json(path), str(path)
    except RuntimeError:
        url = f"{default_dashboard_url()}/state/tasks.json"
        with urllib.request.urlopen(url, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
        return json.loads(body), url


def dashboard_repo_root(dashboard_root: Path | None = None) -> Path:
    return (dashboard_root or default_dashboard_root()).resolve().parent


def task_has_host_verification(task: dict[str, Any], runs: list[dict[str, Any]] | None = None) -> bool:
    for comment in task.get("comments", []) or []:
        if isinstance(comment, dict) and str(comment.get("kind") or "") == HOST_VERIFIED_COMMENT_KIND:
            return True
    task_id = str(task.get("task_id") or "")
    run_id = str(task.get("run_id") or "")
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        if str(run.get("task_id") or "") != task_id and (not run_id or str(run.get("run_id") or "") != run_id):
            continue
        status = str(run.get("status") or "").lower()
        verifier = run.get("verifier") if isinstance(run.get("verifier"), dict) else {}
        if status in {"passed", "verified"} and str(verifier.get("status") or "").lower() == "passed":
            return True
    return False


def requires_machine_verifier(task: dict[str, Any]) -> bool:
    text = f"{task.get('task_id') or ''} {task.get('title') or ''} {task.get('description') or ''}".lower()
    decision_markers = ("decision", "decide", "survey", "调研", "决定", "建议", "路线")
    if any(marker in text for marker in decision_markers):
        return False
    keywords = (
        "eval",
        "evaluation",
        "vbench",
        "inference",
        "train",
        "benchmark",
        "评测",
        "推理",
        "训练",
        "安装",
    )
    return any(keyword in text for keyword in keywords)


def has_result_comment(task: dict[str, Any]) -> bool:
    for comment in task.get("comments", []) or []:
        if not isinstance(comment, dict):
            continue
        if str(comment.get("kind") or "") == "result" and str(comment.get("body") or "").strip():
            return True
    return False


def import_dashboard_todos(
    user_id: str,
    *,
    dashboard_root: Path | None = None,
    project_id: str = "",
    write: bool = True,
) -> dict[str, Any]:
    """Import open dashboard TODOs into the local harness task queue."""

    doc, source = load_dashboard_tasks_doc(dashboard_root)
    tasks = doc.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("dashboard/state/tasks.json must contain a tasks list")
    state = get_harness_state(user_id)
    existing = {
        str(task.get("task_id") or ""): task
        for task in state.get("tasks", [])
        if isinstance(task, dict) and task.get("task_id")
    }
    created = 0
    updated = 0
    skipped = 0

    for task in tasks:
        if not isinstance(task, dict):
            skipped += 1
            continue
        if project_id and task.get("project_id") != project_id:
            skipped += 1
            continue
        status = clean_status(task.get("status")) or "todo"
        if status not in OPEN_DASHBOARD_STATUSES:
            skipped += 1
            continue
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            skipped += 1
            continue
        local = existing.get(task_id)
        payload = {
            "action": "task_upsert",
            "project_id": str(task.get("project_id") or project_id),
            "task_id": task_id,
            "title": str(task.get("title") or task_id),
            "description": str(task.get("description") or ""),
            "priority": str(task.get("priority") or "normal"),
            "assignee": str(task.get("assignee") or ""),
            "due_at": str(task.get("due_at") or ""),
            "metadata": {"dashboard": {"imported_at": now_iso(), "source": "dashboard/state/tasks.json"}},
        }
        if not local:
            payload["status"] = status
            created += 1
        else:
            local_status = clean_status(local.get("status")) or "todo"
            if local_status == "todo" and status != "todo":
                payload["status"] = status
            updated += 1
        if write:
            apply_harness_event(user_id, payload)

    return {"created": created, "updated": updated, "skipped": skipped, "source": source}


def dashboard_comment_id(task_id: str, comment: dict[str, Any]) -> str:
    body = str(comment.get("body") or "").strip()
    kind = str(comment.get("kind") or "comment").strip()
    created_at = str(comment.get("created_at") or "").strip()
    digest = hashlib.sha1(f"{task_id}\n{kind}\n{created_at}\n{body}".encode("utf-8")).hexdigest()[:12]
    return f"clawcross_{digest}"


def dashboard_author(kind: str) -> str:
    clean = str(kind or "").strip().lower()
    if clean == "result":
        return "Result"
    if clean == "artifact":
        return "Artifact"
    if clean == "blocker":
        return "Blocker"
    if clean == "needs_user":
        return "Needs user"
    if clean == "host_verified":
        return "Host verification"
    if clean == "review":
        return "Review"
    return "Progress"


def dashboard_comment_kind(kind: str) -> str:
    clean = str(kind or "comment").strip().lower() or "comment"
    aliases = {
        "artifact": "comment",
        "host_progress": "comment",
        "review": "comment",
        "route": "comment",
    }
    return aliases.get(clean, clean)


def should_sync_dashboard_comment(comment: dict[str, Any]) -> bool:
    kind = str(comment.get("kind") or "comment").strip()
    body = str(comment.get("body") or "").strip()
    if kind in INTERNAL_DASHBOARD_COMMENT_KINDS:
        return False
    if any(body.startswith(prefix) for prefix in INTERNAL_DASHBOARD_BODY_PREFIXES):
        return False
    if "session_" in body and ("本机主控" in body or "ClawCross" in body):
        return False
    return True


def sync_harness_to_dashboard(
    user_id: str,
    *,
    dashboard_root: Path | None = None,
    project_id: str = "",
    create_missing: bool = False,
    write: bool = True,
) -> dict[str, Any]:
    """Copy harness task status/comments into dashboard/state/tasks.json."""

    path = dashboard_tasks_path(dashboard_root)
    doc = load_json(path)
    dashboard_tasks = doc.setdefault("tasks", [])
    if not isinstance(dashboard_tasks, list):
        raise ValueError("dashboard/state/tasks.json must contain a tasks list")
    state = get_harness_state(user_id)
    runs = [run for run in state.get("runs", []) if isinstance(run, dict)]
    harness_tasks = {
        str(task.get("task_id")): task
        for task in state.get("tasks", [])
        if isinstance(task, dict) and task.get("task_id")
    }
    existing_ids = {str(task.get("task_id")) for task in dashboard_tasks if isinstance(task, dict)}
    changed = False
    summary = {
        "status_updates": 0,
        "project_updates": 0,
        "comments_added": 0,
        "created": 0,
        "skipped": 0,
        "changed": False,
    }

    if create_missing:
        for task_id, harness_task in sorted(harness_tasks.items()):
            if task_id in existing_ids:
                continue
            if project_id and harness_task.get("project_id") != project_id:
                continue
            status = clean_status(harness_task.get("status")) or "todo"
            if status == "done" and not task_has_host_verification(harness_task, runs):
                status = "review"
            dashboard_tasks.append(
                {
                    "task_id": task_id,
                    "project_id": harness_task.get("project_id") or project_id,
                    "title": harness_task.get("title") or task_id,
                    "description": harness_task.get("description") or "",
                    "status": status,
                    "priority": harness_task.get("priority") or "medium",
                    "assignee": harness_task.get("assignee") or None,
                    "due_at": harness_task.get("due_at") or "",
                    "result": None,
                    "comments": [],
                    "updated_at": harness_task.get("updated_at") or now_iso(),
                }
            )
            existing_ids.add(task_id)
            summary["created"] += 1
            changed = True

    for dashboard_task in dashboard_tasks:
        if not isinstance(dashboard_task, dict):
            continue
        if project_id and dashboard_task.get("project_id") != project_id:
            continue
        task_id = str(dashboard_task.get("task_id") or "")
        harness_task = harness_tasks.get(task_id)
        if not harness_task:
            summary["skipped"] += 1
            continue

        task_changed = False
        status = clean_status(harness_task.get("status"))
        if status == "done" and not task_has_host_verification(harness_task, runs):
            status = "review"
        if status and dashboard_task.get("status") != status:
            dashboard_task["status"] = status
            if status == "done" and not dashboard_task.get("completed_at"):
                dashboard_task["completed_at"] = str(harness_task.get("updated_at") or now_iso()).split("T", 1)[0]
            summary["status_updates"] += 1
            task_changed = True
            changed = True

        for field in ("project_id", "title", "description", "priority", "assignee", "due_at"):
            value = harness_task.get(field)
            if field == "assignee" and not value:
                value = None
            if value is None and field != "assignee":
                continue
            if dashboard_task.get(field) != value:
                dashboard_task[field] = value
                if field == "project_id":
                    summary["project_updates"] += 1
                task_changed = True
                changed = True

        comments = dashboard_task.setdefault("comments", [])
        if not isinstance(comments, list):
            comments = []
            dashboard_task["comments"] = comments
            task_changed = True
            changed = True
        for item in comments:
            if isinstance(item, dict):
                existing_kind = str(item.get("kind") or "comment").strip()
                normalized_kind = dashboard_comment_kind(existing_kind)
                if normalized_kind != existing_kind:
                    item["kind"] = normalized_kind
                    task_changed = True
                    changed = True
        seen_ids = {str(item.get("comment_id") or "").strip() for item in comments if isinstance(item, dict)}
        seen_bodies = {str(item.get("body") or "").strip() for item in comments if isinstance(item, dict)}
        for comment in harness_task.get("comments", []) or []:
            if not isinstance(comment, dict):
                continue
            if not should_sync_dashboard_comment(comment):
                continue
            body = str(comment.get("body") or "").strip()
            kind = str(comment.get("kind") or "comment").strip() or "comment"
            if kind == "status_change":
                continue
            dashboard_kind = dashboard_comment_kind(kind)
            comment_id = dashboard_comment_id(task_id, comment)
            if not body or comment_id in seen_ids or body in seen_bodies:
                continue
            comments.append(
                {
                    "comment_id": comment_id,
                    "author": dashboard_author(kind),
                    "body": body,
                    "created_at": comment.get("created_at") or now_iso(),
                    "kind": dashboard_kind,
                }
            )
            seen_ids.add(comment_id)
            seen_bodies.add(body)
            summary["comments_added"] += 1
            task_changed = True
            changed = True

        if task_changed:
            dashboard_task["updated_at"] = harness_task.get("updated_at") or now_iso()

    if changed:
        doc["updated_at"] = now_iso()
        if write:
            write_json(path, doc)
    summary["changed"] = changed
    summary["target"] = str(path)
    return summary


def _run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def publish_dashboard_tasks(
    *,
    dashboard_root: Path | None = None,
    message: str = "Update dashboard task status from ClawCross harness",
    push: bool = True,
) -> dict[str, Any]:
    """Commit and push task-state changes from the dashboard repo, if any.

    The harness deliberately publishes only dashboard/state/tasks.json so Claude
    configuration, runtime wiring, and any unrelated dashboard edits stay out of
    this automated path.
    """

    path = dashboard_tasks_path(dashboard_root).resolve()
    repo_root = dashboard_repo_root(dashboard_root)
    git_dir = repo_root / ".git"
    summary: dict[str, Any] = {
        "ok": False,
        "published": False,
        "pushed": False,
        "repo": str(repo_root),
        "target": str(path),
    }
    if not git_dir.exists():
        summary["reason"] = "not_git_repo"
        return summary
    if not path.exists():
        summary["reason"] = "missing_tasks_json"
        return summary
    try:
        rel_path = str(path.relative_to(repo_root))
    except ValueError:
        summary["reason"] = "target_outside_repo"
        return summary

    status = _run_git(repo_root, ["status", "--porcelain", "--", rel_path])
    if status.returncode != 0:
        summary["reason"] = "status_failed"
        summary["error"] = (status.stderr or status.stdout or "").strip()
        return summary
    if not (status.stdout or "").strip():
        summary["ok"] = True
        summary["reason"] = "no_changes"
        return summary

    add = _run_git(repo_root, ["add", "--", rel_path])
    if add.returncode != 0:
        summary["reason"] = "add_failed"
        summary["error"] = (add.stderr or add.stdout or "").strip()
        return summary

    diff = _run_git(repo_root, ["diff", "--cached", "--quiet", "--", rel_path])
    if diff.returncode == 0:
        summary["ok"] = True
        summary["reason"] = "no_staged_changes"
        return summary
    if diff.returncode not in {0, 1}:
        summary["reason"] = "diff_failed"
        summary["error"] = (diff.stderr or diff.stdout or "").strip()
        return summary

    commit = _run_git(repo_root, ["commit", "-m", message, "--", rel_path])
    if commit.returncode != 0:
        summary["reason"] = "commit_failed"
        summary["error"] = (commit.stderr or commit.stdout or "").strip()
        return summary
    summary["published"] = True
    rev = _run_git(repo_root, ["rev-parse", "--short", "HEAD"])
    if rev.returncode == 0:
        summary["commit"] = (rev.stdout or "").strip()

    if push:
        pushed = _run_git(repo_root, ["push"])
        if pushed.returncode != 0:
            summary["reason"] = "push_failed"
            summary["error"] = (pushed.stderr or pushed.stdout or "").strip()
            return summary
        summary["pushed"] = True

    summary["ok"] = True
    summary["reason"] = "published"
    return summary


def sync_dashboard_to_supabase(
    *,
    dashboard_root: Path | None = None,
    project_id: str = "",
    timeout_sec: int = 180,
) -> dict[str, Any]:
    """Push dashboard/state/*.json into the Supabase-backed dashboard tables."""

    repo_root = dashboard_repo_root(dashboard_root)
    script = repo_root / "scripts" / "sync-dashboard-to-supabase.mjs"
    env_file = repo_root / ".env"
    summary: dict[str, Any] = {
        "ok": False,
        "synced": False,
        "repo": str(repo_root),
        "script": str(script),
    }
    if not script.exists():
        summary["reason"] = "missing_sync_script"
        return summary
    if not env_file.exists():
        summary["reason"] = "missing_dashboard_env"
        return summary

    command = ["npm", "run", "supabase:sync", "--", "--once"]
    if project_id:
        command.extend(["--project-id", project_id])
    try:
        proc = subprocess.run(
            command,
            cwd=str(repo_root),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(10, int(timeout_sec)),
        )
    except subprocess.TimeoutExpired as exc:
        summary["reason"] = "timeout"
        summary["error"] = str(exc)
        return summary

    output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part and part.strip())
    summary["output"] = output[-4000:] if output else ""
    if proc.returncode != 0:
        summary["reason"] = "sync_failed"
        summary["error"] = summary["output"]
        return summary

    parsed: dict[str, Any] = {}
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    summary.update(parsed)
    summary["ok"] = True
    summary["synced"] = True
    summary.setdefault("reason", "synced")
    return summary
