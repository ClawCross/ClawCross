"""
Small pure helpers for WeBot delegated runtime behavior.
"""

from __future__ import annotations


RUN_MODES = ("chat", "readonly", "manual", "auto", "bypass")
VALID_SESSION_MODES = frozenset({*RUN_MODES, "execute", "agent", "plan", "review", "yolo"})
MODE_ALIASES = {"read-only": "readonly"}
# Reading inbox content records a read receipt, but remains a viewing action
# permitted by session modes.
READ_ONLY_TOOLS = frozenset({
    'get_session_details',
    "list_agent_groups", "get_group_details", "get_team_details",
    "read_file", "list_files", "web_search", "web_fetch", "search_sessions", "list_sessions",
    "list_subagents", "get_subagent_history", "read_session_plan", "list_tool_approvals", "read_session_inbox",
    "list_oasis_experts", "check_oasis_discussion", "list_oasis_workflows",
    "get_workflow_rules", "list_oasis_agent_catalog", "get_publicnet_info", "get_current_time",
    "list_alarms", "get_notification_status", "get_channel_setup", "get_configuration", "get_clawcross_help", "skill_evolution_report", "usage_status", "claude_code_status",
})

PLAN_MODE_BLOCKED_TOOLS = frozenset(
    {
        "write_file",
        "delete_file",
        "fork_session",
        "mark_session_inbox_read",
        "run_command",
        "cancel_subagent",
        "delete_subagent",
        "save_oasis_workflow",
        "save_oasis_expert",
        "delete_oasis_expert",
        "start_new_oasis",
        "cancel_oasis_discussion",
    }
)

REVIEW_MODE_BLOCKED_TOOLS = frozenset(
    {
        "write_file",
        "delete_file",
        "fork_session",
        "mark_session_inbox_read",
        "start_new_oasis",
        "save_oasis_workflow",
    }
)


def normalize_session_mode(mode: str | None) -> str:
    normalized = (mode or "execute").strip().lower()
    normalized = MODE_ALIASES.get(normalized, normalized)
    if normalized not in VALID_SESSION_MODES:
        return "execute"
    return normalized


def effective_session_mode(user_id: str, session_id: str, requested: str | None = None) -> str:
    from webot.runtime_store import get_session_mode
    from webot.runtime_settings import get_runtime_settings
    stored = normalize_session_mode(requested or get_session_mode(user_id, session_id).get("mode"))
    from webot.subagent_permissions import parent_sessions
    parents = parent_sessions(user_id, session_id)
    if parents:
        inherited = get_runtime_settings(user_id, session_id).approval.mode
        if inherited == "chat" or stored == "chat":
            return "chat"
        if inherited == "readonly" or stored == "readonly":
            return "readonly"
        # A stored/requested Bypass must not override the parent's reviewer.
        if stored not in {"plan", "review"}:
            return inherited
    if requested:
        return stored
    # Legacy execute is the store's sentinel for a session without an override.
    if stored == "execute":
        return get_runtime_settings(user_id, session_id).approval.mode
    return stored


def mode_allows_tool(mode: str | None, tool_name: str, args: dict | None = None) -> bool:
    mode = normalize_session_mode(mode)
    args = args or {}
    if mode == "chat":
        return False
    if mode == "readonly":
        return tool_name in READ_ONLY_TOOLS or (tool_name == "background_command_io" and not args.get("input"))
    return True


def filter_tools_for_mode(tool_names: list[str], mode: str | None) -> list[str]:
    normalized_mode = normalize_session_mode(mode)
    if normalized_mode == "chat":
        return []
    if normalized_mode == "readonly":
        return [name for name in tool_names if mode_allows_tool(normalized_mode, name)]
    if normalized_mode in {"execute", "agent", "yolo", "manual", "bypass", "auto"}:
        return list(tool_names)
    blocked = PLAN_MODE_BLOCKED_TOOLS if normalized_mode == "plan" else REVIEW_MODE_BLOCKED_TOOLS
    return [tool_name for tool_name in tool_names if tool_name not in blocked]


def build_session_mode_message(mode: str | None, reason: str = "") -> str:
    normalized_mode = normalize_session_mode(mode)
    if normalized_mode == "chat":
        base = "当前会话处于交流模式。仅通过文字交流，不调用任何工具。"
    elif normalized_mode == "readonly":
        base = "当前会话处于只读模式。只能查看、搜索和分析；不修改文件、不执行命令、不向其他会话或外部服务发消息。"
    elif normalized_mode == "auto":
        base = "当前会话处于 Auto 模式。工具策略标记为需要批准的操作由独立模型代审；允许和禁止规则保持原样，依据不足或审核失败时拒绝；用户在后续对话明确授权后可重新审核。沙盒命令先运行，权限拒绝由系统申请有限提权，不由 Agent 调用提权工具。"
    elif normalized_mode == "manual":
        base = "当前会话处于 Manual 模式。全部工具可用；允许的操作直接执行，需要批准的操作交给人类审核。通过批准按钮或当前对话中的 Y/N/KEEP Y 确认；待批准或被拒绝的操作不执行。沙盒权限拒绝由系统申请有限提权并交给人类审核。"
    elif normalized_mode == "bypass":
        base = "当前会话处于 Bypass 模式。工具操作跳过批准确认；显式禁止规则、命令硬拦截和沙盒限制仍然生效。"
    elif normalized_mode == "execute":
        base = "当前会话处于 execute 模式。优先直接落地实现、运行验证，并及时维护 plan/todo。"
    elif normalized_mode == "agent":
        base = (
            "当前会话处于 agent 模式。你可以执行必要工具推进任务；"
            "遇到当前 tool policy 标记为 manual 的操作时仍需等待人工批准。"
        )
    elif normalized_mode == "plan":
        base = (
            "当前会话处于 plan 模式。你必须先调研、拆解、记录计划和 todo，"
            "不要修改文件或执行会改变环境状态的命令。"
        )
    elif normalized_mode == "review":
        base = (
            "当前会话处于 review 模式。优先做只读审查、验证和风险识别，"
            "除非用户明确要求，不要直接修改文件。"
        )
    else:
        base = (
            "当前会话处于 yolo 模式。你可以自动执行当前 tool policy 中需要 manual approval 的操作；"
            "显式 deny 规则仍然必须遵守。"
        )
    reason_text = (reason or "").strip()
    if not reason_text:
        return base
    return f"{base}\n\nmode_reason: {reason_text}"


def resolve_max_turns(
    requested_max_turns: int | None,
    profile_max_turns: int | None,
) -> int | None:
    if isinstance(requested_max_turns, int) and requested_max_turns > 0:
        return requested_max_turns
    if isinstance(profile_max_turns, int) and profile_max_turns > 0:
        return profile_max_turns
    return None


def should_stop_for_turn_limit(
    next_turn_count: int,
    max_turns: int | None,
    tool_calls: list[dict] | None,
    internal_tool_names: set[str] | frozenset[str],
) -> bool:
    if max_turns is None or next_turn_count < max_turns:
        return False
    if not tool_calls:
        return False
    for tool_call in tool_calls:
        if tool_call.get("name") not in internal_tool_names:
            return False
    return True


def build_turn_limit_message(
    content_text: str,
    max_turns: int,
) -> str:
    content_text = (content_text or "").strip()
    limit_text = (
        f"已达到该 Agent 的最大执行轮次限制 max_turns={max_turns}。"
        "请先总结当前进展和阻塞点，不要继续调用更多内部工具。"
    )
    if not content_text:
        return limit_text
    return f"{content_text}\n\n{limit_text}"
