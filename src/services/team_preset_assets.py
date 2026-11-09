from __future__ import annotations

import json
import random
import shutil
import time
import utils.scheduler_service
from pathlib import Path
from typing import Any

from utils.runtime_paths import USER_FILES_DIR


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRESET_ROOT = PROJECT_ROOT / "data" / "team_presets"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _to_base36(n: int) -> str:
    if n == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while n > 0:
        out = digits[n % 36] + out
        n //= 36
    return out


def _generate_session_id() -> str:
    timestamp_ms = int(time.time() * 1000)
    random_part = random.randint(0, 36**4 - 1)
    return _to_base36(timestamp_ms) + _to_base36(random_part).zfill(4)


def list_team_presets() -> list[dict[str, Any]]:
    presets: list[dict[str, Any]] = []
    if not PRESET_ROOT.exists():
        return presets
    for child in sorted(PRESET_ROOT.iterdir()):
        manifest_path = child / "manifest.json"
        if not child.is_dir() or not manifest_path.exists():
            continue
        try:
            manifest = _read_json(manifest_path)
        except Exception:
            continue
        manifest["preset_path"] = str(child)
        presets.append(manifest)
    return presets


def get_team_preset_bundle(preset_id: str) -> dict[str, Any] | None:
    key = (preset_id or "").strip()
    if not key:
        return None
    base = PRESET_ROOT / key
    manifest_path = base / "manifest.json"
    internal_agents_path = base / "internal_agents.json"
    experts_path = base / "oasis_experts.json"
    source_map_path = base / "source_map.json"
    if not (manifest_path.exists() and internal_agents_path.exists() and experts_path.exists()):
        return None
    workflows_dir = base / "oasis" / "yaml"
    workflows: dict[str, str] = {}
    if workflows_dir.exists():
        for item in sorted(workflows_dir.iterdir()):
            if item.is_file() and item.suffix in {".yaml", ".yml"}:
                workflows[item.name] = item.read_text(encoding="utf-8")
    python_workflows_dir = base / "oasis" / "python"
    python_workflows: dict[str, str] = {}
    if python_workflows_dir.exists():
        for item in sorted(python_workflows_dir.iterdir()):
            if item.is_file() and item.suffix == ".py":
                python_workflows[item.name] = item.read_text(encoding="utf-8")
    return {
        "manifest": _read_json(manifest_path),
        "internal_agents": _read_json(internal_agents_path),
        "oasis_experts": _read_json(experts_path),
        "source_map": _read_json(source_map_path) if source_map_path.exists() else {},
        "workflows": workflows,
        "python_workflows": python_workflows,
    }


def install_team_preset(
    *,
    user_id: str,
    team_name: str,
    preset_id: str,
    project_root: Path | None = None,
) -> dict[str, Any]:
    bundle = get_team_preset_bundle(preset_id)
    if bundle is None:
        raise FileNotFoundError(f"Unknown team preset: {preset_id}")

    effective_root = project_root or USER_FILES_DIR
    team_dir = (
        effective_root / "data" / "user_files" / user_id / "teams" / team_name
        if project_root is not None
        else effective_root / user_id / "teams" / team_name
    )
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "oasis" / "yaml").mkdir(parents=True, exist_ok=True)
    (team_dir / "oasis" / "python").mkdir(parents=True, exist_ok=True)

    runtime_agents = []
    flat_agents = []
    for entry in bundle["internal_agents"]:
        if not isinstance(entry, dict):
            continue
        meta = {k: v for k, v in entry.items() if k != "session"}
        runtime_agents.append({"session": _generate_session_id(), "meta": meta})
        flat_agents.append(meta)

    internal_agents_path = team_dir / "internal_agents.json"
    internal_agents_path.write_text(
        json.dumps(
            [
                {**item["meta"], "session": item["session"]}
                for item in runtime_agents
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    experts_path = team_dir / "oasis_experts.json"
    experts_path.write_text(
        json.dumps(bundle["oasis_experts"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    workflow_dir = team_dir / "oasis" / "yaml"
    for existing in workflow_dir.glob("*.y*ml"):
        existing.unlink()
    for filename, contents in bundle["workflows"].items():
        (workflow_dir / filename).write_text(contents, encoding="utf-8")

    python_workflow_dir = team_dir / "oasis" / "python"
    for existing in python_workflow_dir.glob("*.py"):
        existing.unlink()
    for filename, contents in bundle.get("python_workflows", {}).items():
        (python_workflow_dir / filename).write_text(contents, encoding="utf-8")

    skills_source = PRESET_ROOT / preset_id / "skills"
    skills_count = 0
    if skills_source.exists() and skills_source.is_dir():
        skills_target = team_dir / "skills"
        skills_target.mkdir(parents=True, exist_ok=True)
        for item in sorted(skills_source.iterdir()):
            target = skills_target / item.name
            if item.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(item, target)
                if (target / "SKILL.md").is_file():
                    skills_count += 1
            elif item.is_file() and item.name != "SKILLS_INDEX.md":
                shutil.copy2(item, target)
        try:
            if effective_root.resolve() != PROJECT_ROOT.resolve():
                raise RuntimeError("skip runtime index rebuild outside project root")
            from webot.skills import _rebuild_index

            _rebuild_index(user_id, team=team_name)
        except Exception:
            index_lines = ["# Skills Index", "", f"Total: {skills_count} skills", ""]
            for skill_dir in sorted(skills_target.iterdir()):
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                index_lines.append(f"- **{skill_dir.name}**: preset team skill")
            (skills_target / "SKILLS_INDEX.md").write_text("\n".join(index_lines), encoding="utf-8")

    (team_dir / "clawcross_preset_manifest.json").write_text(
        json.dumps(bundle["manifest"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (team_dir / "clawcross_preset_source_map.json").write_text(
        json.dumps(bundle["source_map"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "team": team_name,
        "preset": bundle["manifest"],
        "internal_agents": len(flat_agents),
        "experts": len(bundle["oasis_experts"]),
        "workflow_files": sorted(bundle["workflows"].keys()),
        "python_workflow_files": sorted(bundle.get("python_workflows", {}).keys()),
        "skills": skills_count,
    }
