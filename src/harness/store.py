"""Durable control-plane state for cross-computer agent harnesses.

The store is intentionally small and dependency-free. It gives ClawCross a
machine-readable state source for external workers without tying the first
version to Supabase/Postgres.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import threading
import uuid
from typing import Any

from utils.runtime_paths import DATA_DIR


STORE_SCHEMA_VERSION = "clawcross_harness_store.v1"
STATE_SCHEMA_VERSION = "clawcross_harness.v1"
VALID_AGENT_STATUSES = frozenset({"idle", "running", "blocked", "needs_user", "review", "done", "error", "offline"})
VALID_TASK_STATUSES = frozenset({"todo", "active", "blocked", "needs_user", "review", "done"})
VALID_RUN_STATUSES = frozenset({"not_run", "started", "running", "failed", "passed", "verified"})
VERIFIER_STATUSES = frozenset({"not_run", "passed", "failed"})
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]*$")
MAX_EVENTS_PER_USER = 500
STALE_AFTER_SECONDS = 15 * 60

_lock = threading.RLock()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _state_path() -> Path:
    explicit = os.getenv("CLAWCROSS_HARNESS_STATE_PATH", "").strip()
    return Path(explicit).expanduser() if explicit else DATA_DIR / "harness_state.json"


def _empty_store() -> dict[str, Any]:
    return {"schema_version": STORE_SCHEMA_VERSION, "users": {}, "updated_at": _now_iso()}


def _empty_user_state(user_id: str) -> dict[str, Any]:
    now = _now_iso()
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "user_id": user_id,
        "projects": {},
        "tasks": {},
        "agents": {},
        "runs": {},
        "events": [],
        "updated_at": now,
        "created_at": now,
    }


def _read_store() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    if not isinstance(data.get("users"), dict):
        data["users"] = {}
    data.setdefault("schema_version", STORE_SCHEMA_VERSION)
    return data


def _write_store(data: dict[str, Any]) -> None:
    data["updated_at"] = _now_iso()
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _require_id(value: str, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{label} is required")
    if not SAFE_ID.fullmatch(clean):
        raise ValueError(f"{label} contains unsafe characters: {clean!r}")
    return clean


def _optional_id(value: str | None, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    return _require_id(clean, label)


def _string(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_status(value: Any, allowed: frozenset[str], fallback: str) -> str:
    clean = _string(value).lower().replace("-", "_")
    if not clean:
        return fallback
    if clean not in allowed:
        raise ValueError(f"invalid status: {value!r}")
    return clean


def _get_user_state(store: dict[str, Any], user_id: str) -> dict[str, Any]:
    clean_user = _require_id(user_id, "user_id")
    users = store.setdefault("users", {})
    state = users.get(clean_user)
    if not isinstance(state, dict):
        state = _empty_user_state(clean_user)
        users[clean_user] = state
    for key in ("projects", "tasks", "agents", "runs"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    if not isinstance(state.get("events"), list):
        state["events"] = []
    state.setdefault("schema_version", STATE_SCHEMA_VERSION)
    state.setdefault("user_id", clean_user)
    return state


def _ensure_project(state: dict[str, Any], project_id: str, event: dict[str, Any]) -> dict[str, Any]:
    project_id = _require_id(project_id or "default", "project_id")
    now = _now_iso()
    projects = state.setdefault("projects", {})
    project = projects.get(project_id)
    if not isinstance(project, dict):
        project = {
            "project_id": project_id,
            "title": _string(event.get("project_title")) or _string(event.get("title")) or project_id,
            "status": "active",
            "summary": "",
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }
        projects[project_id] = project
    if _string(event.get("project_title")):
        project["title"] = _string(event.get("project_title"))
    if _string(event.get("project_summary")):
        project["summary"] = _string(event.get("project_summary"))
    if isinstance(event.get("metadata"), dict):
        project.setdefault("metadata", {}).update(event["metadata"].get("project", {}) if isinstance(event["metadata"].get("project"), dict) else {})
    project["updated_at"] = now
    return project


def _append_event(state: dict[str, Any], event: dict[str, Any], action: str) -> dict[str, Any]:
    now = _now_iso()
    entry = {
        "event_id": f"event_{uuid.uuid4().hex[:12]}",
        "action": action,
        "agent_id": _string(event.get("agent_id")),
        "project_id": _string(event.get("project_id")),
        "task_id": _string(event.get("task_id")),
        "run_id": _string(event.get("run_id")),
        "summary": _string(event.get("summary") or event.get("message") or event.get("comment")),
        "metadata": _dict(event.get("metadata")),
        "created_at": now,
    }
    events = state.setdefault("events", [])
    events.append(entry)
    if len(events) > MAX_EVENTS_PER_USER:
        del events[:-MAX_EVENTS_PER_USER]
    return entry


def _resolve_project_id(state: dict[str, Any], event: dict[str, Any]) -> str:
    explicit = _optional_id(event.get("project_id"), "project_id")
    if explicit:
        return explicit
    task_id = _optional_id(event.get("task_id"), "task_id")
    if task_id:
        task = state.get("tasks", {}).get(task_id)
        if isinstance(task, dict) and task.get("project_id"):
            return str(task["project_id"])
    return "default"


def _upsert_task(state: dict[str, Any], event: dict[str, Any], *, status_override: str | None = None) -> dict[str, Any]:
    task_id = _require_id(event.get("task_id"), "task_id")
    project_id = _resolve_project_id(state, event)
    _ensure_project(state, project_id, event)
    now = _now_iso()
    task = state.setdefault("tasks", {}).get(task_id)
    if not isinstance(task, dict):
        task = {
            "task_id": task_id,
            "project_id": project_id,
            "title": _string(event.get("title")) or task_id,
            "description": _string(event.get("description")),
            "status": "todo",
            "priority": _string(event.get("priority")) or "normal",
            "assignee": _string(event.get("assignee")),
            "comments": [],
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }
        state["tasks"][task_id] = task
    if _string(event.get("title")):
        task["title"] = _string(event.get("title"))
    if _string(event.get("description")):
        task["description"] = _string(event.get("description"))
    if _string(event.get("priority")):
        task["priority"] = _string(event.get("priority"))
    if "assignee" in event:
        task["assignee"] = _string(event.get("assignee"))
    if "due_at" in event:
        task["due_at"] = _string(event.get("due_at"))
    if status_override is not None or _string(event.get("status")):
        task["status"] = _clean_status(status_override or event.get("status"), VALID_TASK_STATUSES, task.get("status") or "todo")
    if isinstance(event.get("metadata"), dict):
        task.setdefault("metadata", {}).update(event["metadata"])
    task["project_id"] = project_id
    task["updated_at"] = now
    return task


def _append_task_comment(state: dict[str, Any], event: dict[str, Any], *, kind: str = "comment") -> dict[str, Any]:
    task = _upsert_task(state, event)
    comment = {
        "comment_id": f"comment_{uuid.uuid4().hex[:12]}",
        "author": _string(event.get("agent_id")) or _string(event.get("author")) or "agent",
        "kind": _string(event.get("kind")) or kind,
        "body": _string(event.get("comment") or event.get("message") or event.get("summary")),
        "created_at": _now_iso(),
    }
    if not comment["body"]:
        raise ValueError("comment/message is required")
    task.setdefault("comments", []).append(comment)
    task["updated_at"] = _now_iso()
    return comment


def _update_agent(state: dict[str, Any], event: dict[str, Any], *, status_override: str | None = None, needs_user_override: bool | None = None) -> dict[str, Any]:
    agent_id = _require_id(event.get("agent_id"), "agent_id")
    project_id = _resolve_project_id(state, event)
    _ensure_project(state, project_id, event)
    now = _now_iso()
    agents = state.setdefault("agents", {})
    agent = agents.get(agent_id)
    if not isinstance(agent, dict):
        agent = {
            "agent_id": agent_id,
            "agent_type": _string(event.get("agent_type")) or "external-worker",
            "project_id": project_id,
            "status": "idle",
            "current_task_id": "",
            "needs_user": False,
            "message": "",
            "capabilities": [],
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }
        agents[agent_id] = agent
    status = status_override or event.get("status") or agent.get("status") or "idle"
    agent["status"] = _clean_status(status, VALID_AGENT_STATUSES, "idle")
    if needs_user_override is not None:
        agent["needs_user"] = needs_user_override
    elif event.get("needs_user") is not None:
        agent["needs_user"] = bool(event.get("needs_user"))
    else:
        agent["needs_user"] = agent["status"] == "needs_user"
    agent["project_id"] = project_id
    agent["agent_type"] = _string(event.get("agent_type")) or agent.get("agent_type") or "external-worker"
    if "current_task_id" in event and not _string(event.get("current_task_id")):
        agent["current_task_id"] = ""
    else:
        agent["current_task_id"] = _optional_id(event.get("current_task_id") or event.get("task_id"), "task_id") or agent.get("current_task_id", "")
    agent["message"] = _string(event.get("summary") or event.get("message")) or agent.get("message", "")
    if isinstance(event.get("capabilities"), list):
        agent["capabilities"] = [str(item) for item in event["capabilities"] if str(item).strip()]
    for field in ("session_ref", "remote_host", "worktree", "branch", "git_sha", "last_run_id"):
        if field in event and event.get(field) is not None:
            agent[field] = _string(event.get(field))
    if isinstance(event.get("metadata"), dict):
        agent.setdefault("metadata", {}).update(event["metadata"])
    agent["last_heartbeat_at"] = now
    agent["updated_at"] = now
    return agent


def _record_run(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    run_id = _require_id(event.get("run_id"), "run_id")
    project_id = _resolve_project_id(state, event)
    _ensure_project(state, project_id, event)
    agent_id = _optional_id(event.get("agent_id"), "agent_id")
    task_id = _optional_id(event.get("task_id"), "task_id")
    status = _clean_status(event.get("status"), VALID_RUN_STATUSES, "not_run")
    verifier = _dict(event.get("verifier"))
    verifier_status = _clean_status(verifier.get("status"), VERIFIER_STATUSES, "not_run")
    verifier["status"] = verifier_status
    exit_code = event.get("exit_code")
    if exit_code is not None:
        try:
            exit_code = int(exit_code)
        except Exception as exc:
            raise ValueError("exit_code must be an integer") from exc
    if status in {"passed", "verified"}:
        if verifier_status != "passed":
            raise ValueError("passed/verified runs require verifier.status='passed'")
        if exit_code != 0:
            raise ValueError("passed/verified runs require exit_code=0")
        if not _string(event.get("git_sha")):
            raise ValueError("passed/verified runs require git_sha")
        if not _string(event.get("command")):
            raise ValueError("passed/verified runs require command")
    now = _now_iso()
    run = {
        "run_id": run_id,
        "project_id": project_id,
        "task_id": task_id,
        "agent_id": agent_id,
        "status": status,
        "git_sha": _string(event.get("git_sha")),
        "command": _string(event.get("command")),
        "exit_code": exit_code,
        "log_path": _string(event.get("log_path")),
        "metrics_path": _string(event.get("metrics_path")),
        "metrics_sha256": _string(event.get("metrics_sha256")),
        "started_at": _string(event.get("started_at")),
        "ended_at": _string(event.get("ended_at")) or now,
        "verifier": verifier,
        "summary": _string(event.get("summary") or event.get("message")),
        "metadata": _dict(event.get("metadata")),
        "updated_at": now,
        "created_at": state.setdefault("runs", {}).get(run_id, {}).get("created_at", now),
    }
    state["runs"][run_id] = run
    if agent_id:
        agent = _update_agent(
            state,
            {**event, "last_run_id": run_id, "status": "done" if verifier_status == "passed" else "error"},
            needs_user_override=False,
        )
        agent["last_run_id"] = run_id
    if task_id and status in {"passed", "verified"}:
        task = _upsert_task(state, {**event, "status": "review"})
        task["run_id"] = run_id
    return run


def apply_harness_event(user_id: str, event: dict[str, Any]) -> dict[str, Any]:
    action = _string(event.get("action")) or "heartbeat"
    action = action.lower().replace("-", "_")
    with _lock:
        store = _read_store()
        state = _get_user_state(store, user_id)
        changed: dict[str, Any] | None = None
        if action == "heartbeat":
            changed = _update_agent(state, event)
        elif action == "needs_user":
            changed = _update_agent(state, {**event, "status": "needs_user"}, needs_user_override=True)
            if event.get("task_id") and _string(event.get("message") or event.get("summary")):
                _append_task_comment(state, event, kind="needs_user")
        elif action == "blocked":
            changed = _update_agent(state, {**event, "status": "blocked"}, needs_user_override=True)
            if event.get("task_id") and _string(event.get("message") or event.get("summary")):
                _append_task_comment(state, event, kind="blocker")
        elif action == "task_upsert":
            changed = _upsert_task(state, event)
        elif action == "task_status":
            changed = _upsert_task(state, event)
            if _string(event.get("message") or event.get("summary")):
                _append_task_comment(state, event, kind="status_change")
        elif action == "task_comment":
            changed = _append_task_comment(state, event)
        elif action == "run":
            changed = _record_run(state, event)
        elif action in {"agent_delete", "delete_agent"}:
            agent_id = _require_id(event.get("agent_id"), "agent_id")
            changed = state.setdefault("agents", {}).pop(agent_id, {"agent_id": agent_id, "deleted": False})
            changed = {**changed, "deleted": agent_id not in state.setdefault("agents", {})}
        else:
            raise ValueError(f"unknown harness action: {action}")
        event_record = _append_event(state, event, action)
        state["updated_at"] = _now_iso()
        _write_store(store)
    return {
        "status": "success",
        "ok": True,
        "action": action,
        "event": event_record,
        "record": changed,
        "state": get_harness_state(user_id),
    }


def _heartbeat_age_seconds(value: Any) -> int | None:
    text = _string(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return max(0, int((datetime.now().astimezone() - parsed).total_seconds()))


def _annotate_agent(agent: dict[str, Any]) -> dict[str, Any]:
    item = dict(agent)
    age = _heartbeat_age_seconds(item.get("last_heartbeat_at"))
    item["heartbeat_age_seconds"] = age
    item["stale"] = age is None or age > STALE_AFTER_SECONDS
    if item["stale"] and item.get("status") not in {"done", "blocked", "needs_user", "error"}:
        item["effective_status"] = "offline"
    else:
        item["effective_status"] = item.get("status") or "idle"
    return item


def get_harness_state(user_id: str) -> dict[str, Any]:
    with _lock:
        store = _read_store()
        state = deepcopy(_get_user_state(store, user_id))
    projects = sorted(state.get("projects", {}).values(), key=lambda item: str(item.get("updated_at", "")), reverse=True)
    tasks = sorted(state.get("tasks", {}).values(), key=lambda item: str(item.get("updated_at", "")), reverse=True)
    agents = sorted((_annotate_agent(item) for item in state.get("agents", {}).values()), key=lambda item: str(item.get("updated_at", "")), reverse=True)
    runs = sorted(state.get("runs", {}).values(), key=lambda item: str(item.get("updated_at", "")), reverse=True)
    return {
        "status": "success",
        "ok": True,
        "schema_version": STATE_SCHEMA_VERSION,
        "user_id": state.get("user_id", user_id),
        "projects": projects,
        "tasks": tasks,
        "agents": agents,
        "runs": runs,
        "events": list(reversed(state.get("events", [])[-100:])),
        "counts": {
            "projects": len(projects),
            "tasks": len(tasks),
            "agents": len(agents),
            "runs": len(runs),
            "needs_user": sum(1 for item in agents if item.get("needs_user")),
            "stale_agents": sum(1 for item in agents if item.get("stale")),
        },
        "updated_at": state.get("updated_at", ""),
    }
