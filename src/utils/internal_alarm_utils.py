from __future__ import annotations

import json
import os
from typing import Any

import requests


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TASKS_FILE = os.path.join(PROJECT_ROOT, "data", "timeset", "tasks.json")


def load_alarm_tasks() -> dict[str, dict[str, Any]]:
    if not os.path.isfile(TASKS_FILE):
        return {}
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _task_target_type(task: dict[str, Any]) -> str:
    return str(task.get("target_type") or "internal").strip().lower() or "internal"


def _schedule_label(task: dict[str, Any]) -> str:
    target_type = _task_target_type(task)
    if target_type == "external":
        return str(task.get("target_ref") or task.get("session_id") or "")
    return str(task.get("session_id") or "")


def export_team_alarms(
    *,
    user_id: str,
    team: str,
    internal_session_to_name: dict[str, str],
    external_global_to_name: dict[str, str],
) -> list[dict[str, Any]]:
    """Export internal scheduler alarms that point at members of one team.

    Internal agents are exported by stable team display name instead of session_id,
    because team import regenerates internal sessions.
    """
    alarms: list[dict[str, Any]] = []
    tasks = load_alarm_tasks()
    for task_id, raw in tasks.items():
        if not isinstance(raw, dict) or str(raw.get("user_id") or "") != user_id:
            continue
        target_type = _task_target_type(raw)
        item = {
            "task_id": task_id,
            "cron": raw.get("cron", ""),
            "schedule_type": raw.get("schedule_type", "cron"),
            "run_at": raw.get("run_at", ""),
            "text": raw.get("text", ""),
            "target_type": target_type,
            "created_at": raw.get("created_at", ""),
        }
        if target_type == "external":
            target_ref = str(raw.get("target_ref") or raw.get("session_id") or "").strip()
            target_name = str(raw.get("target_name") or external_global_to_name.get(target_ref) or "").strip()
            if str(raw.get("team") or "") != team and target_ref not in external_global_to_name:
                continue
            if not target_name:
                continue
            item.update({
                "target_name": target_name,
                "team": team,
            })
        else:
            session_id = str(raw.get("session_id") or "").strip()
            target_name = internal_session_to_name.get(session_id)
            if not target_name:
                continue
            item.update({
                "target_name": target_name,
                "session_id": session_id,
                "team": team,
            })
        alarms.append(item)
    return alarms


def restore_team_alarms(
    *,
    alarms: list[dict[str, Any]],
    user_id: str,
    team: str,
    internal_name_to_session: dict[str, str],
    external_name_to_global: dict[str, str],
    scheduler_url: str,
) -> tuple[int, list[str]]:
    restored = 0
    errors: list[str] = []
    for alarm in alarms:
        if not isinstance(alarm, dict):
            continue
        target_type = _task_target_type(alarm)
        target_name = str(alarm.get("target_name") or "").strip()
        payload = {
            "user_id": user_id,
            "cron": str(alarm.get("cron") or "").strip(),
            "schedule_type": str(alarm.get("schedule_type") or "cron").strip(),
            "run_at": str(alarm.get("run_at") or "").strip(),
            "text": str(alarm.get("text") or ""),
            "target_type": target_type,
            "target_name": target_name,
            "team": team,
        }
        if payload["schedule_type"] == "once":
            if not payload["run_at"]:
                errors.append(f"{target_name or 'unknown'}: missing run_at")
                continue
        elif not payload["cron"]:
            errors.append(f"{target_name or 'unknown'}: missing cron")
            continue
        if target_type == "external":
            target_ref = external_name_to_global.get(target_name) or str(alarm.get("target_ref") or "").strip()
            if target_name and target_name not in external_name_to_global:
                errors.append(f"{target_name}: target external agent not found")
                continue
            if not target_name and not target_ref:
                errors.append(f"{target_name or 'external'}: target external agent not found")
                continue
            payload["target_ref"] = ""
            payload["session_id"] = f"ext:{target_name or target_ref}"
        else:
            session_id = internal_name_to_session.get(target_name)
            if not session_id:
                errors.append(f"{target_name or 'internal'}: target internal session not found")
                continue
            payload["session_id"] = session_id
            payload["target_type"] = "internal"

        try:
            resp = requests.post(scheduler_url, json=payload, timeout=10)
            if resp.status_code == 200:
                restored += 1
            else:
                errors.append(f"{target_name or _schedule_label(payload)}: HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            errors.append(f"{target_name or _schedule_label(payload)}: {e}")
    return restored, errors
