"""Display categories for tools; classification does not grant permissions."""

TOOL_CATEGORIES = {
    "files": {"list_files", "read_file", "write_file", "delete_file"},
    "commands": {"run_command", "background_command_io", "cancel_background_command"},
    "web": {"web_search", "web_fetch", "call_llm_api"},
    "sessions": {"list_sessions", "search_sessions", "send_to_session", "read_session_inbox", "mark_session_inbox_read", "send_to_group"},
    "agents": {"spawn_subagent", "list_subagents", "send_subagent_message", "get_subagent_history", "cancel_subagent", "delete_subagent", "write_session_plan", "read_session_plan", "clear_session_plan", "list_tool_approvals", "set_session_mode", "claude_code_status", "probe_claude_code", "configure_claude_keepalive"},
    "workflows": {"list_oasis_experts", "save_oasis_expert", "delete_oasis_expert", "start_new_oasis", "check_oasis_discussion", "cancel_oasis_discussion", "save_oasis_workflow", "list_oasis_workflows", "get_workflow_rules", "list_oasis_agent_catalog", "get_publicnet_info"},
    "skills": {"manage_personality"},
    "notifications": {"get_current_time", "add_alarm", "list_alarms", "delete_alarm", "set_notification_channel", "remove_notification_channel", "send_notification", "get_notification_status"},
    "usage": {"usage_status", "skill_evolution_report"},
}


def tool_category(name: str) -> str:
    from core.tool_aliases import canonical_tool_name
    name = canonical_tool_name(name)
    return next((category for category, tools in TOOL_CATEGORIES.items() if name in tools), "other")
