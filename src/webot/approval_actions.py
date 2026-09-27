"""Canonical execution parameters shared by the agent and MCP processes."""

def canonical_action_args(tool_name: str, args: dict) -> dict:
    defaults = {}
    if tool_name == "run_command":
        defaults = {
            "language": "shell", "mode": "foreground", "session_id": "", "cwd": "",
            "timeout_seconds": 0, "max_output_chars": 0, "notify_on_done": False,
        }
    elif tool_name == "background_command_io":
        defaults = {
            "session_id": "", "input": "", "enter": True, "wait_seconds": 2,
            "stream": "stdout", "cwd": "", "offset": 0, "limit": 0,
        }
    return {**defaults, **args}
