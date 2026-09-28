"""A team's scheduled tasks, named by role so they travel with the team.

A task targets an agent by id; inside a team package it is written with the
role name of that agent, which import maps back to the member now in the role.
"""

from __future__ import annotations

import json
import os
from typing import Any

import requests

from teams.store import TeamStore
from utils.runtime_paths import DATA_DIR

TASKS_FILE = os.path.join(str(DATA_DIR), "timeset", "tasks.json")


def load_alarm_tasks() -> dict[str, dict[str, Any]]:
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def team_alarm_targets(teams: TeamStore, user_id: str, team: str) -> list[dict[str, str]]:
    """The members a team task can target."""
    return [
        {"agent": m.agent.agent_id, "target_name": m.role, "label": f"{m.role} · {m.agent.platform}"}
        for m in teams.members(user_id, team)
    ]


def export_team_alarms(teams: TeamStore, *, user_id: str, team: str) -> list[dict[str, Any]]:
    """The user's tasks that target a member of *team*, with the member's role name."""
    roles = {m.agent.agent_id: m.role for m in teams.members(user_id, team)}
    alarms = []
    for task_id, raw in load_alarm_tasks().items():
        if not isinstance(raw, dict) or raw.get("user_id") != user_id or raw.get("agent") not in roles:
            continue
        alarms.append({
            "task_id": task_id,
            "cron": raw.get("cron", ""),
            "schedule_type": raw.get("schedule_type", "cron"),
            "run_at": raw.get("run_at", ""),
            "text": raw.get("text", ""),
            "target_name": roles[raw["agent"]],
            "agent": raw["agent"],
            "team": team,
            "created_at": raw.get("created_at", ""),
        })
    return alarms


def restore_team_alarms(teams: TeamStore, *, alarms: list[dict[str, Any]], user_id: str, team: str,
                        scheduler_url: str) -> tuple[int, list[str]]:
    """Recreate exported tasks for the members now holding their roles."""
    agents = {m.role: m.agent.agent_id for m in teams.members(user_id, team)}
    restored, errors = 0, []
    for alarm in alarms:
        if not isinstance(alarm, dict):
            continue
        role = str(alarm.get("target_name") or "").strip()
        if role not in agents:
            errors.append(f"{role or '?'}: no member of {team} holds this role")
            continue
        payload = {
            "user_id": user_id,
            "cron": str(alarm.get("cron") or "").strip(),
            "schedule_type": str(alarm.get("schedule_type") or "cron").strip(),
            "run_at": str(alarm.get("run_at") or "").strip(),
            "text": str(alarm.get("text") or ""),
            "agent": agents[role],
            "team": team,
        }
        try:
            resp = requests.post(scheduler_url, json=payload, timeout=10)
        except requests.RequestException as exc:
            errors.append(f"{role}: {exc}")
            continue
        if resp.status_code == 200:
            restored += 1
        else:
            errors.append(f"{role}: HTTP {resp.status_code}: {resp.text[:200]}")
    return restored, errors
