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
        }
    elif tool_name == "background_command_io":
        defaults = {
            "session_id": "", "input": "", "enter": True, "wait_seconds": 2,
            "stream": "stdout", "cwd": "", "offset": 0, "limit": 0,
        }
    elif tool_name == "list_files":
        defaults = {"session_id": "", "folder": ".", "storage": "file", "team": ""}
    elif tool_name == "read_file":
        defaults = {
            "session_id": "", "offset": 0, "limit": 0, "start_line": 0,
            "line_count": 0, "encoding": "utf-8", "include_sha256": False,
            "storage": "file", "team": "",
        }
    elif tool_name == "write_file":
        defaults = {
            "content": "", "session_id": "", "mode": "overwrite", "start": 0,
            "end": 0, "encoding": "utf-8", "expected_sha256": "",
            "old_string": "", "new_string": "", "replace_all": False,
            "storage": "file", "team": "",
        }
    elif tool_name == "delete_file":
        defaults = {"session_id": "", "storage": "file", "team": ""}
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
    target = (requested if requested.is_absolute() else workspace.cwd / requested).resolve()
    bound["_resolved_path"] = str(target)
    bound["_workspace_root"] = str(workspace.root.resolve())
    return bound


def file_target_outside_workspace(args: dict) -> bool:
    target = args.get("_resolved_path")
    root = args.get("_workspace_root")
    return bool(target and root and not Path(target).is_relative_to(Path(root)))
