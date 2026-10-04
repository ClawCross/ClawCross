"""
Workspace resolution for WeBot sessions and subagents.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess

from webot.profiles import parse_subagent_session_id
from webot.subagents import get_subagent_by_session


from common.runtime_paths import PROJECT_ROOT  # noqa: E402
from common.runtime_paths import USER_FILES_DIR, WORKSPACE_DIR



@dataclass(frozen=True)
class SessionWorkspace:
    root: Path
    cwd: Path
    mode: str
    remote: str


def configured_workspace_root(value: str) -> str:
    """Validate an explicitly chosen directory without creating or moving files."""
    if not isinstance(value, str):
        raise ValueError('workspace_root 必须是目录路径')
    if not value.strip():
        return ''
    root = Path(value.strip()).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('workspace_root 必须是已存在的绝对目录路径')
    from webot.command_sandbox import SandboxUnavailable, validate_workspace_root
    try:
        validate_workspace_root(root)
    except SandboxUnavailable as exc:
        raise ValueError(str(exc)) from exc
    return str(root.resolve())


def _user_root(user_id: str) -> Path:
    safe_user = os.path.basename(user_id or "anonymous")
    base = WORKSPACE_DIR
    if base.resolve().is_relative_to(PROJECT_ROOT.resolve()):
        base = Path.home() / ".clawcross" / "workspace"
    root = _ensure_within(base, base / "users" / safe_user)
    from webot.command_sandbox import validate_workspace_root
    validate_workspace_root(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _ensure_within(base: Path, candidate: Path) -> Path:
    resolved_base = base.resolve()
    resolved_candidate = candidate.resolve()
    if not resolved_candidate.is_relative_to(resolved_base):
        raise ValueError(f"非法工作目录: {candidate}")
    return resolved_candidate


def _resolve_relative(base: Path, value: str) -> Path:
    normalized = (value or "").strip()
    if not normalized:
        return base
    candidate = Path(normalized)
    if candidate.is_absolute():
        return _ensure_within(base, candidate)
    return _ensure_within(base, base / candidate)


def _stored_root(user_id: str, value: str) -> Path:
    """Honor already configured legacy roots without moving the user's files."""
    base = _user_root(user_id)
    legacy = USER_FILES_DIR / os.path.basename(user_id or "anonymous")
    candidate = Path(value)
    legacy_candidate = candidate if candidate.is_absolute() else legacy / candidate
    if legacy_candidate.exists() and legacy_candidate.resolve().is_relative_to(legacy.resolve()):
        from webot.command_sandbox import validate_workspace_root
        validate_workspace_root(legacy_candidate)
        return legacy_candidate.resolve()
    return _resolve_relative(base, value)


def _default_subagent_root(user_id: str, agent_id: str, mode: str) -> Path:
    user_root = _user_root(user_id)
    if mode == "worktree":
        folder = "worktrees"
    elif mode == "remote":
        folder = "remotes"
    else:
        folder = "subagents"
    root = user_root / folder / agent_id / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ensure_git_worktree(base_repo: Path, worktree_root: Path) -> Path:
    if not (base_repo / ".git").exists():
        raise ValueError(f"worktree base is not a git repo: {base_repo}")
    worktree_root.parent.mkdir(parents=True, exist_ok=True)
    if (worktree_root / ".git").exists():
        return worktree_root
    if worktree_root.exists() and not any(worktree_root.iterdir()):
        worktree_root.rmdir()
    subprocess.run(
        ["git", "-C", str(base_repo), "worktree", "add", "--detach", str(worktree_root), "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return worktree_root


def resolve_session_workspace(
    user_id: str,
    session_id: str | None = None,
    *,
    explicit_cwd: str = "",
    agent_config: dict | None = None,
) -> SessionWorkspace:
    from webot.runtime_settings import get_runtime_settings
    if getattr(get_runtime_settings(user_id, session_id or 'default').approval, 'sandbox_security', 'standard') == 'strict':
        from webot.command_sandbox import validate_workspace_root
        base = WORKSPACE_DIR / 'strict'
        if base.resolve().is_relative_to(PROJECT_ROOT.resolve()):
            base = Path.home() / '.clawcross' / 'strict-workspaces'
        key = hashlib.sha256((session_id or 'default').encode()).hexdigest()[:24]
        root = _ensure_within(base, base / os.path.basename(user_id or 'anonymous') / key)
        validate_workspace_root(root, strict=True)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        cwd = _resolve_relative(root, explicit_cwd)
        cwd.mkdir(parents=True, exist_ok=True)
        return SessionWorkspace(root=root, cwd=cwd, mode='strict', remote='')
    session_key = session_id or "default"
    subagent_meta = parse_subagent_session_id(session_key)
    if not subagent_meta:
        if agent_config is None:
            from agents.store import get_store
            agent = get_store().get(user_id, session_key)
            agent_config = agent.config if agent else {}
        chosen = configured_workspace_root(agent_config.get('workspace_root', ''))
        root = Path(chosen) if chosen else _user_root(user_id)
        cwd = _resolve_relative(root, explicit_cwd)
        return SessionWorkspace(root=root, cwd=cwd, mode="custom" if chosen else "shared", remote="")

    user_root = _user_root(user_id)

    record = get_subagent_by_session(session_key, user_id)
    if record is None:
        isolated_root = _default_subagent_root(user_id, subagent_meta["agent_id"], "isolated")
        cwd = _resolve_relative(isolated_root, explicit_cwd)
        return SessionWorkspace(root=isolated_root, cwd=cwd, mode="isolated", remote="")

    mode = (record.workspace_mode or "isolated").strip().lower() or "isolated"
    remote = (record.remote or "").strip()
    workspace_root = (record.workspace_root or "").strip()
    stored_cwd = (record.cwd or "").strip()

    if mode == "shared":
        root = user_root
    elif mode == "isolated":
        root = _default_subagent_root(user_id, record.agent_id, mode)
        if workspace_root:
            root = _stored_root(user_id, workspace_root)
            root.mkdir(parents=True, exist_ok=True)
    elif mode == "worktree":
        root = _default_subagent_root(user_id, record.agent_id, mode)
        if workspace_root:
            base_repo = _stored_root(user_id, workspace_root)
            try:
                root = _ensure_git_worktree(base_repo, root)
            except Exception:
                fallback = _default_subagent_root(user_id, record.agent_id, "isolated")
                fallback.mkdir(parents=True, exist_ok=True)
                root = fallback
                mode = "isolated"
        else:
            mode = "isolated"
    elif mode == "remote":
        root = _default_subagent_root(user_id, record.agent_id, mode)
        if workspace_root:
            root = _stored_root(user_id, workspace_root)
            root.mkdir(parents=True, exist_ok=True)
    elif mode == "custom":
        base = _user_root(user_id)
        root = _stored_root(user_id, workspace_root) if workspace_root else base
        root.mkdir(parents=True, exist_ok=True)
    else:
        root = _default_subagent_root(user_id, record.agent_id, "isolated")
        mode = "isolated"

    cwd_value = explicit_cwd or stored_cwd
    cwd = _resolve_relative(root, cwd_value)
    cwd.mkdir(parents=True, exist_ok=True)
    return SessionWorkspace(root=root, cwd=cwd, mode=mode, remote=remote)


def describe_session_workspace(user_id: str, session_id: str | None = None, *, explicit_cwd: str = "") -> str:
    workspace = resolve_session_workspace(user_id, session_id, explicit_cwd=explicit_cwd)
    return f"mode={workspace.mode} cwd={workspace.cwd} root={workspace.root} remote={workspace.remote or '(local)'}"
