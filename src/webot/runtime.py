"""
Small pure helpers for WeBot delegated runtime behavior.
"""

from __future__ import annotations


PLAN_MODE_BLOCKED_TOOLS = frozenset(
    {
        "write_file",
        "append_file",
        "delete_file",
        "run_command",
        "run_python_code",
        "cancel_subagent",
        "delete_subagent",
        "set_oasis_workflow",
        "add_oasis_expert",
        "update_oasis_expert",
        "delete_oasis_expert",
        "start_new_oasis",
        "cancel_oasis_discussion",
    }
)

REVIEW_MODE_BLOCKED_TOOLS = frozenset(
    {
        "write_file",
        "append_file",
        "delete_file",
        "start_new_oasis",
        "set_oasis_workflow",
    }
)


def normalize_session_mode(mode: str | None) -> str:
    normalized = (mode or "execute").strip().lower()
    if normalized not in {"execute", "plan", "review"}:
        return "execute"
    return normalized


def filter_tools_for_mode(tool_names: list[str], mode: str | None) -> list[str]:
    normalized_mode = normalize_session_mode(mode)
    if normalized_mode == "execute":
        return list(tool_names)
    blocked = PLAN_MODE_BLOCKED_TOOLS if normalized_mode == "plan" else REVIEW_MODE_BLOCKED_TOOLS
    return [tool_name for tool_name in tool_names if tool_name not in blocked]


def build_session_mode_message(mode: str | None, reason: str = "") -> str:
    normalized_mode = normalize_session_mode(mode)
    if normalized_mode == "execute":
        base = "当前会话处于 execute 模式。优先直接落地实现、运行验证，并及时维护 plan/todo。"
    elif normalized_mode == "plan":
        base = (
            "当前会话处于 plan 模式。你必须先调研、拆解、记录计划和 todo，"
            "不要修改文件或执行会改变环境状态的命令。"
        )
    else:
        base = (
            "当前会话处于 review 模式。优先做只读审查、验证和风险识别，"
            "除非用户明确要求，不要直接修改文件。"
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
