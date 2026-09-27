"""Path-free memory entries backed by existing SKILL.md files.

Only this module resolves entry identifiers to storage paths. Supporting files
are deliberately outside the memory interface.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re

from webot import skills


def _root(user_id: str, team: str = "") -> Path:
    if not user_id or user_id in {".", ".."} or Path(user_id).name != user_id or "\\" in user_id:
        raise ValueError("Invalid user ID")
    base = skills.USER_FILES_DIR.resolve()
    parts = [user_id, "teams", skills._validate_team(team), "skills"] if team else [user_id, "skills"]
    current = base
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Memory storage cannot use symbolic links")
    current.mkdir(parents=True, exist_ok=True)
    return current


def _identifier(team: str, key: str) -> str:
    digest = hashlib.sha256(json.dumps([team, key], ensure_ascii=False).encode()).hexdigest()[:20]
    return "mem-" + digest


def _name(value: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > 64 or value.startswith(".") or any(c in value for c in "/\\\n\r\x00"):
        raise ValueError("Memory requires an entry ID or name, never a file path")
    return value


def _entries(user_id: str, team: str = "") -> list[dict]:
    root = _root(user_id, team)
    entries = []
    for path in sorted(root.rglob("SKILL.md")):
        if path.parent == root:
            continue
        relative = path.relative_to(root)
        if any((root.joinpath(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
            continue
        if not path.resolve().is_relative_to(root) or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        meta, _ = skills._parse_frontmatter(content)
        key = relative.parent.as_posix()
        entries.append({
            "id": _identifier(team, key), "name": meta.get("name") or path.parent.name,
            "description": meta.get("description", ""), "category": meta.get("category", ""),
            "scope": "team" if team else "personal", "team": team,
            "_path": path, "_key": path.parent.name,
        })
    return entries


def public_entry(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if not key.startswith("_")}


def list_memory(user_id: str, team: str = "", *, include_personal: bool = True) -> list[dict]:
    entries = _entries(user_id, team)
    if team and include_personal:
        entries += _entries(user_id)
    return [public_entry(entry) for entry in entries]


def memory_target(user_id: str, selector: str, team: str = "", *, create: bool = False, shared: bool = False) -> dict:
    selector = _name(selector)
    entries = _entries(user_id, team)
    if team and shared:
        entries += _entries(user_id)
    matches = [entry for entry in entries if selector == entry["id"]]
    if not matches:
        matches = [entry for entry in entries if selector in {entry["name"], entry["_key"]}]
    if len(matches) > 1:
        raise ValueError("Memory name is ambiguous; use the entry ID from list_files(storage='memory')")
    if matches:
        return matches[0]
    if not create or re.fullmatch(r"mem-[0-9a-f]{20}", selector):
        raise ValueError("Memory entry not found in the selected scope")
    try:
        key = skills._validate_name(selector)
    except ValueError:
        key = "memory-" + hashlib.sha256(selector.encode()).hexdigest()[:20]
    root = _root(user_id, team)
    target = root / key / "SKILL.md"
    if target.parent.is_symlink() or target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError("Invalid memory storage")
    return {"id": _identifier(team, key), "name": selector, "description": "", "category": "",
            "scope": "team" if team else "personal", "team": team, "_path": target, "_key": key}


def prepare_content(entry: dict, content: str) -> str:
    """Accept plain Markdown; retain existing metadata when replacing a body."""
    if not content.startswith("---"):
        meta = {"name": entry["name"], "description": entry.get("description") or entry["name"]}
        if entry["_path"].exists():
            old_content = entry["_path"].read_text(encoding="utf-8")
            end = old_content.find("---", 3) if old_content.startswith("---") else -1
            if end != -1:
                content = old_content[:end + 3] + "\n\n" + content
            else:
                content = skills._build_frontmatter(meta, content)
        else:
            content = skills._build_frontmatter(meta, content)
    error = skills._validate_skill_frontmatter(content)
    if error:
        raise ValueError(error)
    if len(content.encode("utf-8")) > skills._MAX_SKILL_SIZE:
        raise ValueError("Memory entry exceeds 100KB")
    violations = skills._security_scan(content)
    if violations:
        raise ValueError("Memory content scan failed: " + "; ".join(violations))
    return content


def refresh_index(user_id: str, team: str = "") -> None:
    root = _root(user_id, team)
    path = root / "SKILLS_INDEX.md"
    if path.is_symlink():
        raise ValueError("Memory index cannot use symbolic links")
    entries = list_memory(user_id, team, include_personal=False)
    text = "# Skills Index\n\n" + "\n".join(f"- **{e['name']}**: {e['description']}" for e in entries) + "\n"
    from mcp_servers.filemanager import _atomic_write_text
    _atomic_write_text(str(path), text)


@contextmanager
def memory_lock(user_id: str, team: str = ""):
    root = _root(user_id, team)
    path = root / ".memory.lock"
    if path.is_symlink():
        raise ValueError("Memory lock cannot use symbolic links")
    with path.open("a") as handle:
        if os.name == "nt":
            import msvcrt
            handle.write(" ")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
