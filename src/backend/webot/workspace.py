"""
Workspace resolution for WeBot sessions and subagents.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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
    folders: tuple[dict, ...] = ()

    @property
    def roots(self) -> tuple[Path, ...]:
        return tuple(Path(folder['path']) for folder in self.folders) or (self.root,)

    def containing_root(self, target: str | Path) -> Path | None:
        path = Path(target).resolve()
        matches = [root for root in self.roots if path.is_relative_to(root)]
        return max(matches, key=lambda root: len(root.parts)) if matches else None


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


DEFAULT_TEAM = '__default__'

# Launch origins live only in the Agent service. They are not Agent settings or
# disk records. MCP workers query that service so every tool sees the same origin.
_cli_directories: dict[tuple[str, str], str] = {}


def set_cli_workspace(user_id: str, session_id: str, value: str) -> None:
    if value:
        directory = normalize_workspace_config({'paths':[value]}, user_id=user_id)['paths'][0]
        _cli_directories[(user_id, session_id)] = directory


def clear_cli_workspace(user_id: str, session_id: str) -> None:
    _cli_directories.pop((user_id, session_id), None)


def cli_workspace(user_id: str, session_id: str) -> str:
    url = os.getenv('CLAWCROSS_WORKSPACE_ORIGIN_SERVICE', '')
    if url:
        from urllib.parse import quote
        import requests
        token = os.getenv('INTERNAL_TOKEN', '')
        try:
            response = requests.get(f"{url}/v1/agents/{quote(session_id, safe='')}/workspace-origin",
                headers={'Authorization':f'Bearer {token}:{user_id}'}, timeout=5)
            response.raise_for_status()
            return response.json().get('cli', '')
        except (requests.RequestException, ValueError) as exc:
            # Never silently widen or change a sandbox when the broker is down.
            raise ValueError('CLI workspace runtime is unavailable') from exc
    return _cli_directories.get((user_id, session_id), '')


def workspace_base() -> Path:
    return Path.home() / '.clawcross' / 'workspace' if WORKSPACE_DIR.resolve().is_relative_to(PROJECT_ROOT.resolve()) else WORKSPACE_DIR


def companion_workspace(user_id: str, agent_id: str) -> Path:
    key = hashlib.sha256((agent_id or 'default').encode()).hexdigest()[:24]
    root = _ensure_within(workspace_base(), workspace_base() / 'agents' / os.path.basename(user_id or 'anonymous') / key)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def team_workspace(user_id: str, team: str) -> Path:
    from teams.store import valid_team_name
    if team == DEFAULT_TEAM:
        return _user_root(user_id)
    if not valid_team_name(team):
        raise ValueError('Invalid Team workspace')
    root = _ensure_within(workspace_base(), workspace_base() / 'teams' / os.path.basename(user_id or 'anonymous') / team / 'workspace')
    from webot.command_sandbox import validate_workspace_root
    validate_workspace_root(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def normalize_workspace_config(value: dict | None, *, user_id: str = '', legacy_root: str = '') -> dict:
    if value is not None and not isinstance(value, dict):
        raise ValueError('workspaces must be an object')
    data = dict(value or {})
    allowed = {'companion', 'user_shared', 'cli', 'teams', 'paths'}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError('Unknown workspace settings: ' + ', '.join(unknown))
    result = {'companion':True, 'user_shared':False, 'cli':True, 'teams':True, 'paths':[]}
    for key in ('companion','user_shared','cli','teams'):
        if key in data:
            if not isinstance(data[key], bool):
                raise ValueError(key + ' must be a boolean')
            result[key] = data[key]
    paths = data.get('paths', [legacy_root] if legacy_root else [])
    if not isinstance(paths, list) or len(paths) > 32:
        raise ValueError('paths must contain at most 32 folders')
    def validate(value):
        path = configured_workspace_root(value)
        if not path:
            raise ValueError('Workspace path cannot be empty')
        if user_id:
            base = workspace_base().resolve()
            candidate = Path(path)
            # Framework-managed folders of another user are never custom roots.
            for namespace in ('users','agents','teams','strict'):
                top = base / namespace
                if candidate == top or candidate == base:
                    raise ValueError('Choose a specific workspace folder')
                if candidate.is_relative_to(top):
                    relative = candidate.relative_to(top)
                    if not relative.parts or relative.parts[0] != os.path.basename(user_id):
                        raise ValueError('Workspace belongs to another user')
        return path
    result['paths'] = list(dict.fromkeys(validate(path) for path in paths))
    if not any(result[key] for key in ('companion','user_shared','cli','teams')) and not result['paths']:
        raise ValueError('Enable or add at least one workspace')
    return result


def workspace_config(agent_config: dict) -> dict:
    if 'workspaces' in agent_config:
        return normalize_workspace_config(agent_config['workspaces'], legacy_root=agent_config.get('workspace_root',''))
    # Compatibility: old Agents keep their shared or custom root.
    root = agent_config.get('workspace_root','')
    return normalize_workspace_config({'companion':False, 'user_shared':not bool(root),
                                      'cli':False, 'teams':False, 'paths':[root] if root else []})


def _configured_workspace(user_id: str, session_id: str, agent_config: dict, explicit_cwd: str, *, strict: bool, cli_origin: str | None = None) -> SessionWorkspace:
    config = normalize_workspace_config(workspace_config(agent_config), user_id=user_id)
    folders = []
    def add(path, source, team=''):
        from webot.command_sandbox import validate_workspace_root
        path = Path(path).resolve()
        validate_workspace_root(path, strict=strict)
        if not any(folder['path'] == str(path) for folder in folders):
            folders.append({'path':str(path),'source':source,'team':team})
    if config['companion']:
        add(companion_workspace(user_id, session_id), 'companion')
    if config['user_shared']:
        add(_user_root(user_id), 'user', DEFAULT_TEAM)
    origin = (cli_workspace(user_id, session_id) if cli_origin is None else cli_origin) if config['cli'] else ''
    if origin:
        add(origin, 'cli')
    if config['teams']:
        for team in agent_config.get('teams') or []:
            if team != DEFAULT_TEAM:
                add(team_workspace(user_id, team), 'team', team)
    for path in config['paths']:
        add(path, 'custom')
    if not folders:
        raise ValueError('This Agent has no available workspace; enable its companion or add a folder')
    preferred = origin or (config['paths'][0] if config['paths'] else '')
    root = Path(preferred) if preferred else Path(folders[0]['path'])
    cwd = Path(explicit_cwd) if explicit_cwd and Path(explicit_cwd).is_absolute() else root / explicit_cwd if explicit_cwd else root
    cwd = cwd.resolve()
    matches = [Path(folder['path']) for folder in folders if cwd.is_relative_to(Path(folder['path']))]
    if not matches:
        raise ValueError('Command directory is outside the configured workspace folders')
    primary = max(matches, key=lambda path:len(path.parts))
    return SessionWorkspace(primary, cwd, 'strict' if strict else 'configured', '', tuple(folders))


def workspace_card(user_id: str, session_id: str, *, agent_config: dict | None = None) -> dict:
    state = resolve_session_workspace(user_id, session_id, agent_config=agent_config)
    return {'cwd':str(state.cwd), 'root':str(state.root), 'roots':[str(root) for root in state.roots],
            'folders':list(state.folders) or [{'path':str(state.root),'source':state.mode,'team':''}], 'mode':state.mode}


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
    if agent_config is None:
        from agents.store import get_store
        agent = get_store().get(user_id, session_id or 'default')
        agent_config = agent.config if agent else {}
    strict = getattr(get_runtime_settings(user_id, session_id or 'default').approval, 'sandbox_security', 'standard') == 'strict'
    subagent = get_subagent_by_session(session_id or '', user_id) if parse_subagent_session_id(session_id or '') else None
    if subagent and subagent.parent_session and subagent.workspace_mode in ('isolated', 'shared') and not subagent.workspace_root:
        from agents.store import get_store
        from webot.subagent_permissions import parent_sessions
        ancestors = parent_sessions(user_id, session_id)
        if session_id in ancestors:
            raise ValueError('Cyclic subagent workspace delegation')
        parent = get_store().get(user_id, subagent.parent_session)
        if parent and 'workspaces' in parent.config:
            parent_state = resolve_session_workspace(user_id, parent.agent_id, agent_config=parent.config)
            inherited = normalize_workspace_config(workspace_config(parent.config), user_id=user_id)
            inherited['companion'] = True
            # Directory locations remain derived from the trusted parent record,
            # never copied into the child's settings as absolute paths.
            inherited_config = {**parent.config, 'workspaces':inherited}
            origin = cli_workspace(user_id, parent.agent_id) if inherited['cli'] else ''
            child = _configured_workspace(user_id, session_id, inherited_config, explicit_cwd or subagent.cwd, strict=strict, cli_origin=origin)
            folders = list(child.folders)
            if subagent.workspace_mode == 'shared':
                folders += [f for f in parent_state.folders if f['source'] == 'companion']
            return SessionWorkspace(child.root, child.cwd, child.mode, child.remote, tuple(folders))
    if 'workspaces' in agent_config and not parse_subagent_session_id(session_id or 'default'):
        return _configured_workspace(user_id, session_id or 'default', agent_config, explicit_cwd, strict=strict)
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
    from webot.workspace_state import observe_workspace
    workspace = resolve_session_workspace(user_id, session_id, explicit_cwd=explicit_cwd)
    state = {str(root):observe_workspace(root, user_id=user_id, session_id=session_id or 'default') for root in workspace.roots}
    return (f"mode={workspace.mode} cwd={workspace.cwd} root={workspace.root} remote={workspace.remote or '(local)'}"
            '\nworkspace_folders: ' + json.dumps(list(workspace.folders) or [{'path':str(workspace.root),'source':workspace.mode}], ensure_ascii=False, sort_keys=True)
            + '\nworkspace_files: ' + json.dumps(state, ensure_ascii=False, sort_keys=True))
