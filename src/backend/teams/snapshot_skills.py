from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from webot.skills import USER_FILES_DIR, _rebuild_index


SNAPSHOT_SKILLS_ROOT = "skills"
SNAPSHOT_OPENCLAW_AGENTS_DIR = f"{SNAPSHOT_SKILLS_ROOT}/openclaw_agents"
SNAPSHOT_OPENCLAW_MANAGED_DIR = f"{SNAPSHOT_SKILLS_ROOT}/openclaw_managed"
SNAPSHOT_USER_SKILLS_DIR = f"{SNAPSHOT_SKILLS_ROOT}/clawcross_personal"
SNAPSHOT_TEAM_SKILLS_DIR = f"{SNAPSHOT_SKILLS_ROOT}/clawcross_team"

LEGACY_SNAPSHOT_USER_SKILLS_DIR = "clawcross_user_skills"
LEGACY_SNAPSHOT_TEAM_SKILLS_DIR = "clawcross_team_skills"


def _user_root(user_id: str) -> Path:
    return Path(USER_FILES_DIR) / (user_id or "anonymous")


def _user_skills_dir(user_id: str) -> Path:
    return _user_root(user_id) / "skills"


def _team_skills_dir(user_id: str, team: str) -> Path:
    return _user_root(user_id) / "teams" / team / "skills"


def _add_skills_dir_to_zip(zipf: Any, base: Path, zip_root: str, selected_names: set[str] | None = None) -> tuple[int, int]:
    if not base.is_dir():
        return 0, 0

    selected = {name.strip() for name in (selected_names or set()) if name.strip()}
    skill_count = 0
    file_count = 0

    for item in sorted(base.iterdir()):
        if item.name == "SKILLS_INDEX.md" or not item.is_dir():
            continue
        if selected and item.name not in selected:
            continue
        skill_count += 1
        for path in sorted(item.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(base)
            zipf.write(path, os.path.join(zip_root, str(rel)))
            file_count += 1

    return skill_count, file_count


def add_user_skills_to_zip(zipf: Any, user_id: str, selected_names: set[str] | None = None) -> dict[str, int]:
    skills, files = _add_skills_dir_to_zip(zipf, _user_skills_dir(user_id), SNAPSHOT_USER_SKILLS_DIR, selected_names)
    return {"skills": skills, "files": files}


def add_team_skills_to_zip(zipf: Any, user_id: str, team: str, selected_names: set[str] | None = None) -> dict[str, int]:
    skills, files = _add_skills_dir_to_zip(zipf, _team_skills_dir(user_id, team), SNAPSHOT_TEAM_SKILLS_DIR, selected_names)
    return {"skills": skills, "files": files}


def _restore_scope_skills(extracted: Path, target: Path) -> tuple[int, int]:
    restored_skill_dirs = 0
    restored_files = 0

    if extracted.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for item in sorted(extracted.iterdir()):
            dst = target / item.name
            if item.is_dir():
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(item, dst)
                restored_skill_dirs += 1
                restored_files += sum(1 for p in dst.rglob("*") if p.is_file())
            elif item.is_file():
                shutil.copy2(item, dst)
                restored_files += 1
        shutil.rmtree(extracted, ignore_errors=True)

    return restored_skill_dirs, restored_files


def restore_skills_from_team_dir(team_dir: str | Path, user_id: str, team: str) -> dict[str, Any]:
    team_path = Path(team_dir)

    restored_user_skill_dirs = 0
    restored_user_files = 0
    for source_dir in (
        team_path / SNAPSHOT_USER_SKILLS_DIR,
        team_path / LEGACY_SNAPSHOT_USER_SKILLS_DIR,
    ):
        dirs, files = _restore_scope_skills(source_dir, _user_skills_dir(user_id))
        restored_user_skill_dirs += dirs
        restored_user_files += files

    restored_team_skill_dirs = 0
    restored_team_files = 0
    for source_dir in (
        team_path / SNAPSHOT_TEAM_SKILLS_DIR,
        team_path / LEGACY_SNAPSHOT_TEAM_SKILLS_DIR,
    ):
        dirs, files = _restore_scope_skills(source_dir, _team_skills_dir(user_id, team))
        restored_team_skill_dirs += dirs
        restored_team_files += files

    if restored_user_skill_dirs or restored_user_files:
        _rebuild_index(user_id)
    if restored_team_skill_dirs or restored_team_files:
        _rebuild_index(user_id, team=team)

    return {
        "restored_user_skill_dirs": restored_user_skill_dirs,
        "restored_user_files": restored_user_files,
        "restored_team_skill_dirs": restored_team_skill_dirs,
        "restored_team_files": restored_team_files,
    }
