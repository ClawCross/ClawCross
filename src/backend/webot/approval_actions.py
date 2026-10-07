"""Canonical execution parameters shared by the agent and MCP processes."""

import os
from pathlib import Path


FILE_TARGET_FIELDS = {
    "list_files": "folder",
    "read_file": "filename",
    "write_file": "filename",
    "delete_file": "filename",
}

def canonical_action_args(tool_name: str, args: dict) -> dict:
    defaults = {}
    if tool_name == "run_command":
        defaults = {
            "language": "shell", "mode": "foreground", "session_id": "", "cwd": "",
            "timeout_seconds": 0, "max_output_chars": 0, "notify_on_done": False,
            "sandbox_access": "default", "escalation_target": "", "escalation_reason": "",
            "sandbox_approval_chain": [],
        }
    elif tool_name == "background_command_io":
        defaults = {
            "session_id": "", "input": "", "enter": True, "wait_seconds": 2,
            "stream": "stdout", "cwd": "", "offset": 0, "limit": 0,
        }
    elif tool_name == "list_files":
        defaults = {"session_id": "", "folder": ".", "storage": "file", "team": "", "memory_scope": "workspace"}
    elif tool_name == "read_file":
        defaults = {
            "session_id": "", "offset": 0, "limit": 0, "start_line": 0,
            "line_count": 0, "encoding": "utf-8", "include_sha256": False,
            "storage": "file", "team": "", "memory_scope": "workspace",
        }
    elif tool_name == "write_file":
        defaults = {
            "content": "", "session_id": "", "mode": "overwrite", "start": 0,
            "end": 0, "encoding": "utf-8", "expected_sha256": "",
            "old_string": "", "new_string": "", "replace_all": False,
            "storage": "file", "team": "", "memory_scope": "workspace",
        }
    elif tool_name == "delete_file":
        defaults = {"session_id": "", "storage": "file", "team": "", "memory_scope": "workspace"}
    elif tool_name == "web_fetch":
        defaults = {"max_chars": 12000, "timeout": 15, "username": "", "session_id": ""}
    elif tool_name == "web_search":
        defaults = {
            "kind": "web", "format": "markdown", "max_results": 5,
            "region": os.getenv("WEB_SEARCH_REGION", "us-en"),
            "safesearch": os.getenv("WEB_SEARCH_SAFESEARCH", "moderate"),
            "freshness": "", "include_domains": "", "exclude_domains": "",
            "fetch_top": 0, "max_chars_per_page": 4000, "username": "", "session_id": "",
        }
    return {**defaults, **args}


def bind_file_target(tool_name: str, args: dict, user_id: str, session_id: str, *, workspace=None) -> dict:
    """Bind a file approval/permit to the real path, including symlink targets."""
    bound = canonical_action_args(tool_name, args)
    field = FILE_TARGET_FIELDS.get(tool_name)
    if field is None or bound.get("storage") != "file":
        return bound
    if workspace is None:
        from webot.workspace import resolve_session_workspace
        workspace = resolve_session_workspace(user_id, session_id)
    requested = Path(os.path.expanduser(str(bound.get(field) or ("." if field == "folder" else ""))))
    candidate = requested if requested.is_absolute() else workspace.cwd / requested
    from webot.runtime_settings import get_runtime_settings
    strict_windows = os.name == 'nt' and get_runtime_settings(user_id, session_id).approval.sandbox_security == 'strict'
    if strict_windows:
        from webot.windows_confined_files import validate_components
        # resolve() can follow a UNC junction and initiate SMB authentication
        # before approval. Strict Windows tools pin and reject every reparse
        # component during execution, so bind a lexical path instead.
        validate_components(str(candidate))
        target = candidate.absolute()
    else:
        target = candidate.resolve()
    bound["_resolved_path"] = str(target)
    bound["_workspace_root"] = str(workspace.root.resolve())
    bound["_workspace_roots"] = [str(root.resolve()) for root in getattr(workspace, "roots", (workspace.root,))]
    return bound


def file_target_outside_workspace(args: dict) -> bool:
    target = args.get("_resolved_path")
    root = args.get("_workspace_root")
    roots = args.get("_workspace_roots") or ([root] if root else [])
    return bool(target and roots and not any(Path(target).is_relative_to(Path(folder)) for folder in roots))


def file_access_violation(args: dict, user_id: str, session_id: str) -> str:
    target = args.get('_resolved_path')
    if not target:
        return ''
    from webot.command_sandbox import protected_control_paths
    from webot.runtime_settings import get_runtime_settings
    strict = get_runtime_settings(user_id, session_id).approval.sandbox_security == 'strict'
    path = Path(target).absolute() if os.name == 'nt' and strict else Path(target).resolve()
    if any(path.is_relative_to(control) or control.is_relative_to(path) for control in protected_control_paths()):
        return '后端安全配置属于受保护区域，Agent 文件工具不能访问或修改。'
    if strict and file_target_outside_workspace(args):
        return '严格安全模式只允许访问已配置的工作区目录，不允许提权。'
    return ''
