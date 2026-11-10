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


def _root(user_id: str, team: str = "", session_id: str = "", memory_scope: str = 'workspace') -> Path:
    if not user_id or user_id in {".", ".."} or Path(user_id).name != user_id or "\\" in user_id:
        raise ValueError("Invalid user ID")
    if session_id:
        roots = _catalog_roots(user_id, session_id, team, memory_scope=memory_scope)
        if not roots: raise ValueError('No Skill workspace is available')
        return roots[0][0]
    return skills._scope_skills_dir(user_id, team)


def _catalog_roots(user_id: str, session_id: str, team: str = '', agent_config: dict | None = None,
                   memory_scope: str = 'workspace') -> list[tuple[Path, str, str]]:
    from webot.workspace import resolve_session_workspace
    state = resolve_session_workspace(user_id, session_id, agent_config=agent_config)
    folders = list(state.folders) or [{'path':str(state.root), 'source':'user' if state.mode == 'shared' else state.mode, 'team':''}]
    if memory_scope == 'companion':
        folders = [folder for folder in folders if folder.get('source') == 'companion']
        if not folders:
            raise ValueError('Companion workspace is not enabled for this Agent')
    elif memory_scope != 'workspace':
        raise ValueError('Unsupported memory scope')
    if team:
        folders = [folder for folder in folders if folder.get('team') == team]
        if not folders:
            raise ValueError('Team workspace is not enabled for this Agent')
    result = []
    for folder in folders:
        source, selected_team = folder['source'], folder.get('team', '')
        if source == 'team':
            root = skills._team_skills_dir(user_id, selected_team)
            namespace = selected_team
        elif source == 'user':
            root = skills._skills_dir(user_id)
            namespace = ''
        else:
            root = Path(folder['path']) / 'skills'
            namespace = source + ':' + (session_id if source == 'companion' else str(Path(folder['path']).resolve()))
        if root.is_symlink() or not root.resolve().is_relative_to(Path(folder['path']).resolve()):
            raise ValueError('Skill storage must remain inside its workspace')
        result.append((root, namespace, selected_team if source == 'team' else ''))
    return result


def _catalog(user_id: str, session_id: str, team: str = '', agent_config: dict | None = None,
             memory_scope: str = 'workspace') -> list[dict]:
    entries, seen = [], set()
    for root, namespace, selected_team in _catalog_roots(user_id, session_id, team, agent_config, memory_scope):
        for entry in _entries(user_id, selected_team, root=root, namespace=namespace):
            if entry['_path'] not in seen:
                seen.add(entry['_path']); entries.append(entry)
    return entries


def _identifier(team: str, key: str) -> str:
    digest = hashlib.sha256(json.dumps([team, key], ensure_ascii=False).encode()).hexdigest()[:20]
    return "mem-" + digest


def _name(value: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > 64 or value.startswith(".") or any(c in value for c in "/\\\n\r\x00"):
        raise ValueError("Memory requires an entry ID or name, never a file path")
    return value


def _entries(user_id: str, team: str = "", *, root: Path | None = None, namespace: str | None = None) -> list[dict]:
    root = root if root is not None else _root(user_id, team)
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
            "id": _identifier(team if namespace is None else namespace, key), "name": meta.get("name") or path.parent.name,
            "description": meta.get("description", ""), "category": meta.get("category", ""),
            "scope": 'companion' if (namespace or '').startswith('companion:') else "team" if team else "personal", "team": team,
            "_path": path, "_key": path.parent.name,
        })
    return entries


def public_entry(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if not key.startswith("_")}


def list_memory(user_id: str, team: str = "", *, include_personal: bool = True, session_id: str = "", agent_config: dict | None = None, include_paths: bool = False, memory_scope: str = 'workspace') -> list[dict]:
    if session_id:
        return [{**public_entry(entry), **({'path':str(entry['_path'])} if include_paths else {})} for entry in _catalog(user_id, session_id, team, agent_config, memory_scope)]
    if memory_scope == 'companion':
        raise ValueError('Companion memory requires an active Agent session')
    entries = _entries(user_id, team)
    if team and include_personal:
        entries += _entries(user_id)
    return [public_entry(entry) for entry in entries]


def memory_target(user_id: str, selector: str, team: str = "", *, create: bool = False, shared: bool = False, session_id: str = "", memory_scope: str = 'workspace') -> dict:
    selector = _name(selector)
    if memory_scope == 'companion' and not session_id:
        raise ValueError('Companion memory requires an active Agent session')
    entries = _catalog(user_id, session_id, team, memory_scope=memory_scope) if session_id else _entries(user_id, team)
    if team and shared and not session_id:
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
    if session_id:
        root, namespace, selected_team = _catalog_roots(user_id, session_id, team, memory_scope=memory_scope)[0]
        team = selected_team
    else:
        root, namespace = _root(user_id, team), team
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / key / "SKILL.md"
    if target.parent.is_symlink() or target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError("Invalid memory storage")
    selected_scope = 'companion' if namespace.startswith('companion:') else 'team' if team else 'personal'
    return {"id": _identifier(namespace, key), "name": selector, "description": "", "category": "",
            "scope": selected_scope, "team": team, "_path": target, "_key": key}


def prepare_content(entry: dict, content: str, *, file_path=None) -> str:
    """Accept plain Markdown; retain existing metadata when replacing a body."""
    if not content.startswith("---"):
        meta = {"name": entry["name"], "description": entry.get("description") or entry["name"]}
        from webot.confined_files import file_exists, file_open
        target = file_path if file_path is not None else entry['_path']
        if file_exists(target):
            with file_open(target, 'r', encoding='utf-8') as handle:
                old_content = handle.read()
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


def refresh_index(user_id: str, team: str = "", *, session_id: str = "", memory_scope: str = 'workspace') -> None:
    root = _catalog_roots(user_id, session_id, team, memory_scope=memory_scope)[0][0] if session_id else _root(user_id, team, session_id, memory_scope)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / "SKILLS_INDEX.md"
    if path.is_symlink():
        raise ValueError("Memory index cannot use symbolic links")
    entries = list_memory(user_id, team, include_personal=False, session_id=session_id, memory_scope=memory_scope)
    text = "# Skills Index\n\n" + "\n".join(f"- **{e['name']}**: {e['description']}" for e in entries) + "\n"
    from webot.mcp.filemanager import _atomic_write_text
    _atomic_write_text(str(path), text)


@contextmanager
def memory_lock(user_id: str, team: str = "", *, session_id: str = "", memory_scope: str = 'workspace'):
    root = _root(user_id, team, session_id, memory_scope)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
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
