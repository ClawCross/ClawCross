"""
Self-Evolution Skill System — procedural memory for ClawCross agents.

Ported from Hermes Agent's skill_manager_tool concept:
- Agent can create, edit, patch, and delete reusable skills
- Skills are stored as SKILL.md files with YAML frontmatter
- Skills are indexed for system prompt injection
- Security scanning prevents malicious skill content
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from webot.profiles import slugify

PROJECT_ROOT = Path(__file__).resolve().parents[2]
USER_FILES_DIR = PROJECT_ROOT / "data" / "user_files"

_MAX_SKILL_SIZE = 100 * 1024        # 100KB per SKILL.md
_MAX_SUPPORT_FILE_SIZE = 1 * 1024 * 1024  # 1MB per supporting file
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# ── Security scanning patterns ──────────────────────────────────────

_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)ignore\s+(all\s+)?previous\s+instructions", "prompt injection: ignore previous instructions"),
    (r"(?i)you\s+are\s+now\s+", "role hijacking attempt"),
    (r"(?i)do\s+not\s+tell\s+(the\s+)?user", "deception hiding"),
    (r"(?i)system\s*prompt\s*override", "system prompt override"),
    (r"curl\s+.*\$\{?\w*(TOKEN|KEY|SECRET|PASS)", "exfiltration via curl"),
    (r"wget\s+.*\$\{?\w*(TOKEN|KEY|SECRET|PASS)", "exfiltration via wget"),
    (r"cat\s+~/?\.\w*env", "secret file read attempt"),
    (r"ssh-keygen.*-f\s*/", "SSH key generation in root"),
    (r"rm\s+-rf\s+/(?!\w)", "destructive root deletion"),
    (r"eval\s*\(.*base64", "obfuscated code execution"),
    (r"subprocess\.(?:call|run|Popen)\s*\(", "subprocess execution in skill"),
    (r"os\.system\s*\(", "os.system execution in skill"),
    (r"exec\s*\(.*compile", "dynamic code compilation"),
]


def _skills_dir(user_id: str) -> Path:
    """Per-user skills directory."""
    root = USER_FILES_DIR / (user_id or "anonymous") / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _team_skills_dir(user_id: str, team: str) -> Path:
    """Per-team skills directory."""
    root = USER_FILES_DIR / (user_id or "anonymous") / "teams" / _validate_team(team) / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _scope_skills_dir(user_id: str, team: str = "") -> Path:
    return _team_skills_dir(user_id, team) if team else _skills_dir(user_id)


def _validate_name(name: str) -> str:
    """Validate and normalize a skill name."""
    name = (name or "").strip().lower()
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid skill name '{name}'. "
            "Use lowercase letters, numbers, hyphens, dots, underscores (1-64 chars)."
        )
    return name


def _validate_team(team: str) -> str:
    """Validate a team name for storage under data/user_files/<user>/teams/."""
    team = (team or "").strip()
    if not team:
        raise ValueError("Team name is required")
    if "/" in team or "\\" in team or team.startswith(".") or ".." in team:
        raise ValueError(f"Invalid team name '{team}'")
    return team


def _security_scan(content: str) -> list[str]:
    """Scan skill content for dangerous patterns. Returns list of violations."""
    violations: list[str] = []
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, content):
            violations.append(reason)
    # Check for invisible unicode
    for ch in content:
        if ord(ch) > 127 and ch not in ("\u00a7", "\u2014", "\u2013", "\u2018", "\u2019", "\u201c", "\u201d", "\u2026"):
            cp = ord(ch)
            if (0x200B <= cp <= 0x200F) or (0x2028 <= cp <= 0x202F) or (0xFFF0 <= cp <= 0xFFFF):
                violations.append(f"invisible unicode character U+{cp:04X}")
                break
    return violations


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter from SKILL.md content (simple key: value parser)."""
    if not content.startswith("---"):
        return {}, content
    end_idx = content.find("---", 3)
    if end_idx == -1:
        return {}, content
    fm_text = content[3:end_idx].strip()
    body = content[end_idx + 3:].strip()
    meta: dict[str, str] = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, body


def _build_frontmatter(meta: dict[str, str], body: str) -> str:
    """Build SKILL.md content from frontmatter dict and body."""
    fm_lines = ["---"]
    for key in ("name", "description", "category", "platform"):
        if key in meta:
            fm_lines.append(f"{key}: {meta[key]}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(body)
    return "\n".join(fm_lines)


# ══════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════

def create_skill(
    user_id: str,
    *,
    name: str,
    content: str,
    category: str = "",
    team: str = "",
) -> dict[str, Any]:
    """Create a new skill with SKILL.md content."""
    name = _validate_name(name)
    if len(content.encode("utf-8")) > _MAX_SKILL_SIZE:
        return {"success": False, "error": f"Skill content exceeds {_MAX_SKILL_SIZE // 1024}KB limit"}

    # Validate frontmatter
    meta, body = _parse_frontmatter(content)
    if not meta.get("name") or not meta.get("description"):
        return {"success": False, "error": "SKILL.md must have YAML frontmatter with 'name' and 'description' fields"}

    # Security scan
    violations = _security_scan(content)
    if violations:
        return {"success": False, "error": f"Security scan failed: {'; '.join(violations)}"}

    base = _scope_skills_dir(user_id, team)
    if category:
        category = slugify(category, "general")
        skill_dir = base / category / name
    else:
        skill_dir = base / name

    if skill_dir.exists():
        return {"success": False, "error": f"Skill '{name}' already exists. Use edit or patch to update."}

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"

    # Atomic write
    tmp_path = skill_path.with_suffix(".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(str(tmp_path), str(skill_path))

    _rebuild_index(user_id, team=team)
    return {"success": True, "message": f"Skill '{name}' created", "path": str(skill_path)}


def edit_skill(
    user_id: str,
    *,
    name: str,
    content: str,
    team: str = "",
) -> dict[str, Any]:
    """Full rewrite of a skill's SKILL.md."""
    name = _validate_name(name)
    if len(content.encode("utf-8")) > _MAX_SKILL_SIZE:
        return {"success": False, "error": f"Skill content exceeds {_MAX_SKILL_SIZE // 1024}KB limit"}

    meta, body = _parse_frontmatter(content)
    if not meta.get("name") or not meta.get("description"):
        return {"success": False, "error": "SKILL.md must have YAML frontmatter with 'name' and 'description' fields"}

    violations = _security_scan(content)
    if violations:
        return {"success": False, "error": f"Security scan failed: {'; '.join(violations)}"}

    skill_path = _find_skill_path(user_id, name, team=team)
    if not skill_path:
        return {"success": False, "error": f"Skill '{name}' not found"}

    # Backup old content for rollback
    old_content = skill_path.read_text(encoding="utf-8")
    tmp_path = skill_path.with_suffix(".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(str(tmp_path), str(skill_path))

    _rebuild_index(user_id, team=team)
    return {"success": True, "message": f"Skill '{name}' updated", "path": str(skill_path)}


def patch_skill(
    user_id: str,
    *,
    name: str,
    old_string: str,
    new_string: str,
    file_path: str = "",
    replace_all: bool = False,
    team: str = "",
) -> dict[str, Any]:
    """Targeted find-and-replace within a skill file."""
    name = _validate_name(name)
    skill_dir = _find_skill_dir(user_id, name, team=team)
    if not skill_dir:
        return {"success": False, "error": f"Skill '{name}' not found"}

    if file_path:
        target = skill_dir / file_path
        if not target.is_file():
            return {"success": False, "error": f"File '{file_path}' not found in skill '{name}'"}
    else:
        target = skill_dir / "SKILL.md"

    content = target.read_text(encoding="utf-8")
    if old_string not in content:
        return {"success": False, "error": f"String not found in {target.name}"}

    if replace_all:
        new_content = content.replace(old_string, new_string)
    else:
        new_content = content.replace(old_string, new_string, 1)

    violations = _security_scan(new_content)
    if violations:
        return {"success": False, "error": f"Security scan failed after patch: {'; '.join(violations)}"}

    tmp_path = target.with_suffix(".tmp")
    tmp_path.write_text(new_content, encoding="utf-8")
    os.replace(str(tmp_path), str(target))

    _rebuild_index(user_id, team=team)
    return {"success": True, "message": f"Patched {target.name} in skill '{name}'"}


def delete_skill(user_id: str, *, name: str, team: str = "") -> dict[str, Any]:
    """Delete a skill and its directory."""
    name = _validate_name(name)
    skill_dir = _find_skill_dir(user_id, name, team=team)
    if not skill_dir:
        return {"success": False, "error": f"Skill '{name}' not found"}

    shutil.rmtree(skill_dir, ignore_errors=True)

    # Clean up empty category dirs
    parent = skill_dir.parent
    base = _scope_skills_dir(user_id, team)
    if parent != base and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()

    _rebuild_index(user_id, team=team)
    return {"success": True, "message": f"Skill '{name}' deleted"}


def write_skill_file(
    user_id: str,
    *,
    name: str,
    file_path: str,
    file_content: str,
    team: str = "",
) -> dict[str, Any]:
    """Add or overwrite a supporting file in a skill directory."""
    name = _validate_name(name)
    skill_dir = _find_skill_dir(user_id, name, team=team)
    if not skill_dir:
        return {"success": False, "error": f"Skill '{name}' not found"}

    if len(file_content.encode("utf-8")) > _MAX_SUPPORT_FILE_SIZE:
        return {"success": False, "error": f"File exceeds {_MAX_SUPPORT_FILE_SIZE // (1024*1024)}MB limit"}

    target = _resolve_skill_relative_path(skill_dir, file_path)
    if target is None:
        return {
            "success": False,
            "error": (
                "Invalid file_path. Use a relative path inside the skill directory. "
                "Recommended folders: assets, references, scripts, templates."
            ),
        }
    target.parent.mkdir(parents=True, exist_ok=True)

    violations = _security_scan(file_content)
    if violations:
        return {"success": False, "error": f"Security scan failed: {'; '.join(violations)}"}

    target.write_text(file_content, encoding="utf-8")
    return {"success": True, "message": f"File '{file_path}' written to skill '{name}'", "path": str(target)}


def remove_skill_file(user_id: str, *, name: str, file_path: str, team: str = "") -> dict[str, Any]:
    """Remove a supporting file from a skill."""
    name = _validate_name(name)
    skill_dir = _find_skill_dir(user_id, name, team=team)
    if not skill_dir:
        return {"success": False, "error": f"Skill '{name}' not found"}

    target = _resolve_skill_relative_path(skill_dir, file_path)
    if target is None:
        return {"success": False, "error": "Invalid file_path"}
    if not target.is_file():
        return {"success": False, "error": f"File '{file_path}' not found"}

    target.unlink()
    return {"success": True, "message": f"File '{file_path}' removed from skill '{name}'"}


def list_skills(user_id: str, *, team: str = "", include_personal: bool = False) -> list[dict[str, Any]]:
    """List all skills for a user."""
    skills: list[dict[str, Any]] = []
    scopes: list[tuple[str, str, Path]] = []
    if team:
        scopes.append(("team", team, _team_skills_dir(user_id, team)))
        if include_personal:
            scopes.append(("personal", "", _skills_dir(user_id)))
    else:
        scopes.append(("personal", "", _skills_dir(user_id)))

    for scope, scope_team, base in scopes:
        for skill_md in sorted(base.rglob("SKILL.md"), key=lambda p: p.stat().st_mtime, reverse=True):
            meta, body = _parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
            skill_dir = skill_md.parent
            rel = skill_dir.relative_to(base)
            skills.append({
                "name": meta.get("name", skill_dir.name),
                "description": meta.get("description", ""),
                "category": meta.get("category", str(rel.parent) if str(rel.parent) != "." else ""),
                "path": str(skill_md),
                "dir": str(skill_dir),
                "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(skill_md.stat().st_mtime)),
                "scope": scope,
                "team": scope_team if scope == "team" else "",
            })
    skills.sort(key=lambda item: item.get("modified", ""), reverse=True)
    return skills


def get_skill(user_id: str, *, name: str, team: str = "", fallback_to_personal: bool = False) -> dict[str, Any] | None:
    """Get full skill content."""
    name = _validate_name(name)
    candidates: list[tuple[str, str, Path | None]] = []
    if team:
        candidates.append(("team", team, _find_skill_path(user_id, name, team=team)))
        if fallback_to_personal:
            candidates.append(("personal", "", _find_skill_path(user_id, name)))
    else:
        candidates.append(("personal", "", _find_skill_path(user_id, name)))

    scope = "personal"
    scope_team = ""
    skill_path: Path | None = None
    for candidate_scope, candidate_team, candidate_path in candidates:
        if candidate_path:
            scope = candidate_scope
            scope_team = candidate_team
            skill_path = candidate_path
            break
    if not skill_path:
        return None
    content = skill_path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(content)
    skill_dir = skill_path.parent

    # List supporting files
    support_files = [
        str(f.relative_to(skill_dir))
        for f in sorted(skill_dir.rglob("*"))
        if f.is_file() and f.name != "SKILL.md"
    ]

    return {
        "name": meta.get("name", skill_dir.name),
        "description": meta.get("description", ""),
        "category": meta.get("category", ""),
        "content": content,
        "body": body,
        "support_files": support_files,
        "path": str(skill_path),
        "scope": scope,
        "team": scope_team if scope == "team" else "",
    }


def build_skills_prompt(user_id: str, *, team: str = "") -> str:
    """Build compact skill index for system prompt injection."""
    team_skills = list_skills(user_id, team=team) if team else []
    personal_skills = list_skills(user_id)
    skills = team_skills + personal_skills if team else personal_skills
    if not skills:
        return ""

    lines = [
        "\n【Skills (Procedural Memory)】",
        "You have the following skills available. Use skill_view to read full content before applying.",
        "When you complete complex tasks (5+ tool calls), fix tricky errors, or discover non-trivial workflows,",
        "consider creating a new skill with skill_manage(action='create').",
        "When using a skill and finding it outdated or wrong, patch it immediately with skill_manage(action='patch').",
        "When repeated failures or fresh execution errors appear, run skill_evolution_report first, then skill_evolution_apply",
        "to refresh the managed self-evolution block and persist the new failure learnings.",
        "",
    ]
    if team:
        if team_skills:
            lines.append(f"Team skills for {team}:")
            for skill in team_skills[:20]:
                desc = skill["description"][:100] if skill["description"] else ""
                cat = f" [{skill['category']}]" if skill["category"] else ""
                path = f" ({skill['dir']})" if skill.get("dir") else ""
                lines.append(f"  - {skill['name']}{cat}{path}: {desc}")
            lines.append("")
        if personal_skills:
            lines.append("Shared personal skills:")
            for skill in personal_skills[:20]:
                desc = skill["description"][:100] if skill["description"] else ""
                cat = f" [{skill['category']}]" if skill["category"] else ""
                path = f" ({skill['dir']})" if skill.get("dir") else ""
                lines.append(f"  - {skill['name']}{cat}{path}: {desc}")
    else:
        for skill in skills[:30]:  # Cap at 30 skills in prompt
            desc = skill["description"][:100] if skill["description"] else ""
            cat = f" [{skill['category']}]" if skill["category"] else ""
            path = f" ({skill['dir']})" if skill.get("dir") else ""
            lines.append(f"  - {skill['name']}{cat}{path}: {desc}")

    return "\n".join(lines)


# ── Internal helpers ────────────────────────────────────────────────

def _find_skill_dir(user_id: str, name: str, team: str = "") -> Path | None:
    """Find a skill directory by name (searches category subdirs too)."""
    base = _scope_skills_dir(user_id, team)
    # Direct match
    if (base / name / "SKILL.md").is_file():
        return base / name
    # Search categories
    for skill_md in base.rglob("SKILL.md"):
        if skill_md.parent.name == name:
            return skill_md.parent
    return None


def _resolve_skill_relative_path(skill_dir: Path, file_path: str) -> Path | None:
    """Resolve a user-provided relative path inside a skill directory."""
    raw = (file_path or "").strip()
    if not raw:
        return None

    candidate = Path(raw)
    if candidate.is_absolute():
        return None

    resolved = (skill_dir / candidate).resolve()
    try:
        resolved.relative_to(skill_dir.resolve())
    except ValueError:
        return None
    return resolved


def _find_skill_path(user_id: str, name: str, team: str = "") -> Path | None:
    """Find a SKILL.md path by skill name."""
    skill_dir = _find_skill_dir(user_id, name, team=team)
    if skill_dir:
        return skill_dir / "SKILL.md"
    return None


def _rebuild_index(user_id: str, team: str = "") -> Path:
    """Rebuild the SKILLS_INDEX.md for a user."""
    base = _scope_skills_dir(user_id, team)
    skills = list_skills(user_id, team=team)
    lines = ["# Skills Index", "", f"Total: {len(skills)} skills", ""]
    for skill in skills:
        cat = f" [{skill['category']}]" if skill["category"] else ""
        lines.append(f"- **{skill['name']}**{cat}: {skill['description']}")
    index_path = base / "SKILLS_INDEX.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path
