from __future__ import annotations

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
USER_FILES_DIR = PROJECT_ROOT / "data" / "user_files"


def _team_dir(user_id: str, team: str) -> Path | None:
    scoped_user = (user_id or "").strip()
    scoped_team = (team or "").strip()
    if not scoped_user or not scoped_team:
        return None
    team_root = USER_FILES_DIR / scoped_user / "teams" / scoped_team
    return team_root if team_root.is_dir() else None


def _read_json_list(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _team_member_names(team_root: Path) -> list[str]:
    names: list[str] = []
    for filename in ("internal_agents.json", "external_agents.json"):
        for item in _read_json_list(team_root / filename):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _workflow_names(team_root: Path) -> tuple[list[str], list[str]]:
    yaml_dir = team_root / "oasis" / "yaml"
    python_dir = team_root / "oasis" / "python"
    yaml_names = sorted(
        item.name
        for item in yaml_dir.iterdir()
        if item.is_file() and item.suffix.lower() in {".yaml", ".yml"}
    ) if yaml_dir.is_dir() else []
    python_names = sorted(
        item.name
        for item in python_dir.iterdir()
        if item.is_file() and item.suffix.lower() == ".py"
    ) if python_dir.is_dir() else []
    return yaml_names, python_names


def _team_skill_names(user_id: str, team: str) -> list[str]:
    try:
        from webot.skills import list_skills
    except Exception:
        try:
            from src.webot.skills import list_skills
        except Exception:
            return []

    names: list[str] = []
    try:
        skills = list_skills(user_id, team=team)
    except Exception:
        return []
    for item in skills:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def build_team_workflow_prompt(user_id: str, *, team: str = "") -> str:
    """Build a compact current-team context block for system prompt injection.

    If the current session is not bound to a team, return an empty string so
    public/private agents do not receive unrelated team context.
    """
    team_root = _team_dir(user_id, team)
    if team_root is None:
        return ""

    member_names = _team_member_names(team_root)
    yaml_names, python_names = _workflow_names(team_root)
    skill_names = _team_skill_names(user_id, team)

    lines = [
        "\n【当前 Team 信息】",
        f"team name: {team}",
    ]
    if member_names:
        lines.append("成员 name:")
        lines.extend(f"  - {name}" for name in member_names)
    else:
        lines.append("成员 name: 暂无")

    if yaml_names or python_names:
        lines.append("工作流 name:")
        if yaml_names:
            lines.append("  YAML:")
            lines.extend(f"    - {name}" for name in yaml_names)
        if python_names:
            lines.append("  Python:")
            lines.extend(f"    - {name}" for name in python_names)
    else:
        lines.append("工作流 name: 暂无")

    if skill_names:
        lines.append("Skill name:")
        lines.extend(f"  - {name}" for name in skill_names)
    else:
        lines.append("Skill name: 暂无")
    return "\n".join(lines)
