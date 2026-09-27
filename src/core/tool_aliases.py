"""Names of MCP tools that were merged or removed, and where their calls go now.

Tool names live on outside the code: in saved team presets and personas, in a
session's stored ``enabled_tools``, in custom subagent profiles, and in what a
model remembers from an older prompt. A call under a retired name is rewritten
to the tool that now does the job, so none of those break.
"""

from __future__ import annotations

from typing import Any, NamedTuple


class ToolAlias(NamedTuple):
    target: str
    # Arguments set when the caller did not give them (e.g. the mode an old tool implied).
    defaults: dict[str, Any] = {}
    # Arguments the old tool always meant, whatever the caller passed.
    forced: dict[str, Any] = {}
    # Old argument name -> new argument name.
    renames: dict[str, str] = {}
    # The target can do more than the old tool did (set_session_mode can also
    # switch to yolo; enter_plan_mode could not). A list that granted the old
    # name must not grant the target.
    widens_access: bool = False


TOOL_ALIASES: dict[str, ToolAlias] = {
    # filemanager
    "append_file": ToolAlias("write_file", forced={"mode": "append"}),
    "list_images": ToolAlias("list_files"),
    # read_file reads any file, not just images.
    "attach_image_to_context": ToolAlias("read_file", widens_access=True),
    # commander
    # run_command runs shell as well as Python; background_command_io can also
    # type into interactive jobs, which the read-only tools could not.
    "run_python_code": ToolAlias(
        "run_command", forced={"language": "python"}, renames={"code": "command"}, widens_access=True
    ),
    "start_background_command": ToolAlias("run_command", forced={"mode": "background"}),
    "read_background_command": ToolAlias("background_command_io", widens_access=True),
    "get_background_command_status": ToolAlias("background_command_io", widens_access=True),
    "read_background_command_output": ToolAlias("background_command_io", widens_access=True),
    # notifier / llmapi
    "list_notification_channels": ToolAlias("get_notification_status"),
    "set_default_notification_channel": ToolAlias("set_notification_channel", forced={"make_default": True}),
    "send_private_cli": ToolAlias("send_to_group"),
    # Messages between sessions: one tool; wait means "wait for the reply".
    "send_internal_message": ToolAlias(
        "send_to_session", defaults={"wait": True}, renames={"target_session": "target"}
    ),
    "session_send_to": ToolAlias("send_to_session", renames={"target_ref": "target"}),
    # oasis
    "list_oasis_topics": ToolAlias("check_oasis_discussion"),
    "list_oasis_python_runs": ToolAlias("check_oasis_discussion"),
    "check_oasis_python_run": ToolAlias("check_oasis_discussion", renames={"run_id": "topic_id"}),
    "cancel_oasis_python_run": ToolAlias("cancel_oasis_discussion", renames={"run_id": "topic_id"}),
    # save_oasis_workflow also saves Python workflows (runnable code), which the
    # YAML-only tool could not.
    "set_oasis_yaml_workflow": ToolAlias(
        "save_oasis_workflow", forced={"kind": "yaml"}, renames={"schedule_yaml": "content"}, widens_access=True
    ),
    "set_oasis_python_workflow": ToolAlias(
        "save_oasis_workflow", forced={"kind": "python"}, renames={"python_code": "content"}
    ),
    "list_oasis_python_workflows": ToolAlias("list_oasis_workflows", forced={"kind": "python"}),
    "get_workflow_writing_rules": ToolAlias("get_workflow_rules", forced={"kind": "python"}),
    "get_yaml_workflow_rules": ToolAlias("get_workflow_rules", forced={"kind": "yaml"}),
    "add_oasis_expert": ToolAlias("save_oasis_expert"),
    "update_oasis_expert": ToolAlias("save_oasis_expert"),
    # search / session / skills
    "web_research_brief": ToolAlias("web_search", defaults={"fetch_top": 2, "max_results": 6}),
    "get_current_session": ToolAlias("list_sessions"),
    "skill_list": ToolAlias("list_files", forced={"storage": "memory"}, widens_access=True),
    "skill_view": ToolAlias("read_file", forced={"storage": "memory"}, renames={"name": "filename"}, widens_access=True),
    "skill_manage": ToolAlias("write_file", forced={"storage": "memory"}, renames={"name": "filename"}, widens_access=True),
    # Automatic evolution is now an analysis only; applying a change uses write_file.
    "skill_evolution_apply": ToolAlias("skill_evolution_report"),
    "get_trajectory_stats": ToolAlias("usage_status"),
    "get_insights": ToolAlias("usage_status"),
    # webot
    "read_session_todos": ToolAlias("read_session_plan"),
    "write_session_todos": ToolAlias("write_session_plan", forced={"target": "todos"}),
    "clear_session_todos": ToolAlias("clear_session_plan", forced={"target": "todos"}),
    "run_claude_keepalive_once": ToolAlias("probe_claude_code"),
    "enter_plan_mode": ToolAlias("set_session_mode", forced={"mode": "plan"}, widens_access=True),
    "exit_plan_mode": ToolAlias("set_session_mode", forced={"mode": "execute"}, widens_access=True),
    "list_webot_agent_profiles": ToolAlias("list_subagents"),
}


def canonical_tool_name(name: str) -> str:
    """The current name for *name* (itself if it was not retired)."""
    alias = TOOL_ALIASES.get(name)
    return alias.target if alias else name


def canonical_tool_names(names):
    """Map a list of granted tool names (enabled_tools, a profile's allowed_tools) to current names.

    Keeps order and drops duplicates. A retired name whose replacement can do
    more than it could is dropped rather than mapped, so the grant never widens.
    """
    seen: dict[str, None] = {}
    for name in names or ():
        alias = TOOL_ALIASES.get(str(name))
        if alias is not None and alias.widens_access:
            continue
        seen.setdefault(alias.target if alias else str(name), None)
    return list(seen)


def resolve_tool_call(name: str, args: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """Rewrite a call under a retired name to the current tool and arguments."""
    alias = TOOL_ALIASES.get(name)
    args = dict(args or {})
    if alias is None:
        return name, args
    if name in {"skill_view", "skill_list"}:
        selector = args.get("name") or args.get("filename") or ""
        if name == "skill_list" or not selector:
            return "list_files", {**{key: args[key] for key in ("username", "team", "session_id") if key in args}, "storage": "memory"}
    if name == "skill_manage":
        action = args.get("action", "")
        common = {key: args[key] for key in ("username", "team", "session_id") if key in args}
        common.update(storage="memory", filename=args.get("name", ""))
        if action == "delete":
            return "delete_file", common
        if action in {"create", "edit"}:
            return "write_file", {**common, "mode": "create" if action == "create" else "update", "content": args.get("content", "")}
        if action == "patch" and not args.get("file_path"):
            return "write_file", {**common, "mode": "str_replace", **{key: args[key] for key in ("old_string", "new_string", "replace_all") if key in args}}
        # Never reinterpret a legacy supporting-file path as a memory body write.
        return "write_file", {**common, "mode": "unsupported_legacy_skill_action"}
    if name == "skill_evolution_apply":
        args.pop("source", None)
    for old, new in alias.renames.items():
        if old in args:
            args.setdefault(new, args.pop(old))
    for key, value in alias.defaults.items():
        if args.get(key) in (None, ""):
            args[key] = value
    args.update(alias.forced)
    return alias.target, args
