import os
import json
import copy
import asyncio
import contextlib
import sys
import logging
from dataclasses import dataclass
from typing import TypedDict, Optional

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic import ValidationError

from common import llm_factory
from common.llm_factory import extract_text
from common.logging_utils import get_logger
from common.runtime_paths import PROJECT_ROOT
from webot.approval_actions import bind_file_target
from webot.approval_review import authorize_action, policy_binding, resolve_conversation_reply, review_context
from webot.checkpoint_repository import (
    get_context_compaction,
    get_context_usage_record,
    save_context_usage_record,
)
from webot.compression import (
    compression_view_from_record,
    temporary_bounded_view,
    trim_new_input_if_oversized,
)
from webot.context import (
    RUNTIME_DELTA_KEY,
    RUNTIME_STATE_KEY,
    assemble_input_messages,
    render_group_context,
    render_runtime_context_block,
    render_team_skill_context,
)
from webot.context_compressor import estimate_messages_tokens
from webot.context_limits import infer_model_context_window, resolve_history_message_limits
from webot.context_references import expand_context_references
from webot.context_store import ContextStore
from webot.context_usage import (
    compacted_components,
    compaction_key,
    difference_components,
    estimate_context_components,
    request_accounting,
    scale_components,
    tool_schemas,
    validate_context_capacity,
)
from webot.cost_tracker import get_cost_tracker
from webot.engine.agent_runtime_state import TaskRegistry, ThreadStateRegistry
from webot.engine.background_compaction import BackgroundCompressionManager
from webot.engine.lazy_tool_discovery import LazyToolRegistry
from webot.engine.lightweight_agent_runtime import LightweightAgentRuntime
from webot.engine.tool_aliases import canonical_tool_name, canonical_tool_names, resolve_tool_call
from webot.engine.tool_schema import (
    StrictSchemaError,
    decode_structured_final,
    drop_null_optionals,
    reply_format_binding,
    strict_tool_binding,
    strict_violations,
    to_strict_parameters,
)
from webot.llm_call_trace import llm_call_trace_enabled, save_llm_call
from webot.memory import get_memory_state
from webot.permission_context import resolve_permission_context
from webot.policy import ToolPolicyDecision, get_tool_policy, run_tool_policy_hooks
from webot.profiles import frame_session_identity, get_agent_profile, parse_subagent_session_id, render_profile_system_prompt
from webot.runtime import (
    PLAN_MODE_BLOCKED_TOOLS,
    REVIEW_MODE_BLOCKED_TOOLS,
    build_session_mode_message,
    build_turn_limit_message,
    effective_session_mode,
    filter_tools_for_mode,
    mode_allows_tool,
    resolve_max_turns,
    should_stop_for_turn_limit,
)
from webot.runtime_settings import get_runtime_settings, resolve_context_history_budget, resolve_context_window
from webot.runtime_store import (
    count_inbox_messages,
    get_session_state,
    get_tool_approval,
    issue_execution_permit,
    list_inbox_messages,
    list_tool_approvals,
    record_tool_execution,
    save_session_mode,
    utc_now,
)
from webot.skills import build_user_profile_block
from webot.smart_routing import resolve_turn_route
from webot.soul import build_soul_prompt
from webot.token_budget import get_session_budget
from webot.trajectory import auto_trajectory_enabled, save_trajectory
from webot.workspace import describe_session_workspace

logger = get_logger("agent")


def should_inject_new_inbox_notice(state: dict, turn_count: int) -> bool:
    """Show queued inbox metadata once, unless this turn already carries it."""
    if turn_count != 0:
        return False
    last_input = (state.get("messages") or [None])[-1]
    return not (
        state.get("trigger_source") == "system"
        and isinstance(last_input, HumanMessage)
        and isinstance(last_input.content, str)
        and last_input.content.startswith(("[收件箱通知]", "[来自 "))
    )


# --- Tools that need automatic username injection ---
USER_INJECTED_TOOLS = {
    # File management tools
    "list_files", "read_file", "write_file", "delete_file",
    # Command execution tools
    "run_command", "background_command_io", "cancel_background_command",
    "web_search", "web_fetch",
    # Alarm management tools
    "get_current_time", "add_alarm", "list_alarms", "delete_alarm",
    # Notification push tools (multi-channel)
    "set_notification_channel", "send_notification", "get_notification_status",
    "remove_notification_channel",
    # OASIS forum tools
    "start_new_oasis", "check_oasis_discussion", "cancel_oasis_discussion",
    "list_oasis_experts", "save_oasis_expert", "delete_oasis_expert",
    "save_oasis_workflow", "list_oasis_workflows", "list_oasis_agent_catalog",
    # Session management tools
    "list_sessions", "fork_session", "set_session_title",
    # LLM API access tools
    "call_llm_api", "send_to_session", "read_session_inbox", "mark_session_inbox_read",
    # Group chat tools
    "send_to_group", "join_group", "leave_group", "list_agent_groups", "get_group_details", "get_team_details",
    # WeBot subagent tools
    "spawn_subagent", "list_subagents",
    "send_subagent_message", "get_subagent_history", "cancel_subagent", "delete_subagent",
    "write_session_plan", "read_session_plan", "clear_session_plan",
    "list_tool_approvals", "resolve_tool_approval",
    "set_session_mode",
    "claude_code_status", "probe_claude_code", "configure_claude_keepalive",
    # Self-evolution tools
    "skill_evolution_report",
    "search_sessions", "usage_status",
    "manage_personality",
}

# Tools that need session_id auto-injected (in addition to username)
SESSION_INJECTED_TOOLS = {
    "web_search": "session_id",
    "web_fetch": "session_id",
    "list_files": "session_id",
    "read_file": "session_id",
    "write_file": "session_id",
    "delete_file": "session_id",
    "run_command": "session_id",
    "background_command_io": "session_id",
    "cancel_background_command": "session_id",
    "add_alarm": "session_id",
    "start_new_oasis": "notify_session",
    "list_sessions": "current_session_id",
    "fork_session": "current_session_id",
    "set_session_title": "source_session",
    "send_notification": "source_session",
    "send_to_session": "source_session",
    "read_session_inbox": "source_session",
    "mark_session_inbox_read": "source_session",
    "send_to_group": "source_session",
    "join_group": "source_session",
    "leave_group": "source_session",
    "list_agent_groups": "source_session",
    "get_group_details": "source_session",
    "spawn_subagent": "parent_session",
    "send_subagent_message": "source_session",
    "cancel_subagent": "source_session",
    "delete_subagent": "source_session",
    "write_session_plan": "source_session",
    "read_session_plan": "source_session",
    "clear_session_plan": "source_session",
    "list_tool_approvals": "source_session",
    "set_session_mode": "source_session",
    "claude_code_status": "source_session",
    "probe_claude_code": "source_session",
    "configure_claude_keepalive": "source_session",
    # Self-evolution tools
    "skill_evolution_report": "session_id",
    "search_sessions": "session_id",
}

TEAM_INJECTED_TOOLS: frozenset[str] = frozenset({
    "add_alarm",
    "list_files", "read_file", "write_file", "delete_file",
    "skill_evolution_report",
})

# Session-related tool args that must always match runtime session (model cannot override).
SESSION_FORCE_INJECTED_TOOLS: frozenset[str] = frozenset({
    "run_command", "background_command_io",
    "web_search", "web_fetch",
    "send_to_session",
    "read_session_inbox", "mark_session_inbox_read",
    "send_to_group", "join_group", "leave_group", "list_agent_groups", "get_group_details",
    "send_notification",
    "spawn_subagent",
    "send_subagent_message",
    "cancel_subagent",
    "delete_subagent",
    "set_session_mode",
    "list_sessions",
    "fork_session",
    "set_session_title",
    "start_new_oasis",
})

def hide_injected_params(tool):
    """Return *tool*'s schema for binding, minus the arguments we inject ourselves.

    ``UserAwareToolNode`` fills ``username`` and the per-tool session argument on
    every call, so the model neither needs to supply them nor can it know the
    right values. Leaving them in the bound schema costs tokens in the request's
    most expensive stable segment — the tool array renders before the system
    prompt — and invites the model to guess an identity we then overwrite.
    ``username`` alone appears in 100 of the 109 tools.

    Only arguments this process actually injects are removed, so a tool that
    resolves its own user server-side keeps its parameter. Falls back to the
    original tool whenever the schema cannot be read or has nothing to drop —
    binding must never silently lose a tool.
    """
    hidden = {name for name in (SESSION_INJECTED_TOOLS.get(tool.name),) if name}
    if tool.name == 'run_command':
        hidden.update({'sandbox_access', 'escalation_target', 'escalation_reason', 'sandbox_approval_chain'})
    if tool.name in USER_INJECTED_TOOLS:
        hidden.add("username")
    if not hidden:
        return tool
    try:
        schema = convert_to_openai_tool(tool)
        params = schema["function"]["parameters"]
        properties = params.get("properties") or {}
        if not hidden & set(properties):
            return tool
        params["properties"] = {k: v for k, v in properties.items() if k not in hidden}
        if isinstance(params.get("required"), list):
            params["required"] = [k for k in params["required"] if k not in hidden]
        return schema
    except Exception:
        return tool


def bind_tool_schema(tool, *, strict: bool):
    """The schema to bind for one of our MCP tools, strict-mode when *strict*.

    Strict mode constrains the model's decoding of the arguments to the schema.
    It needs the closed form ``to_strict_parameters`` produces (all properties
    required, optional ones nullable, no extra keys); ``UserAwareToolNode``
    drops the resulting nulls again before the tool runs. A tool whose schema
    cannot be expressed that way is bound as before, without ``strict``, and
    logged — ``test_tool_schemas`` keeps that from happening to our own tools.
    """
    bound = hide_injected_params(tool)
    if not strict:
        return bound
    try:
        schema = copy.deepcopy(bound) if isinstance(bound, dict) else convert_to_openai_tool(bound)
        function = schema["function"]
        function["parameters"] = to_strict_parameters(
            function.get("parameters") or {"type": "object", "properties": {}}
        )
        function["strict"] = True
        return schema
    except (StrictSchemaError, KeyError, TypeError, ValueError) as exc:
        logger.warning("tool %s bound without strict: %s", getattr(tool, "name", tool), exc)
        return bound


def external_tool_schema(func_def: dict, *, strict: bool) -> dict:
    """Bind a caller-supplied tool definition as given.

    Its schema is the caller's contract — the call is handed back to the caller
    unchanged, so rewriting optional arguments as nullable would change what it
    receives. It is marked strict only when it already is strict-compliant.
    """
    parameters = func_def.get("parameters") or {"type": "object", "properties": {}}
    function = {
        "name": func_def["name"],
        "description": func_def.get("description", ""),
        "parameters": parameters,
    }
    if strict and not strict_violations(parameters):
        function["strict"] = True
    return {"type": "function", "function": function}


def _external_tool_defs(state) -> list[dict]:
    """Caller-supplied function definitions, OpenAI ``{"type":"function","function":…}`` or bare."""
    defs = []
    for ext_tool in state.get("external_tools") or []:
        func_def = ext_tool.get("function", {}) if ext_tool.get("type") == "function" else ext_tool
        if func_def.get("name") and func_def["name"] not in {"tool_search", "tool_call"}:
            defs.append(func_def)
    return defs


def _external_tool_names(state) -> set[str]:
    """Names of the caller-supplied tools bound for this request."""
    return {func_def["name"] for func_def in _external_tool_defs(state)}


def tool_result_payload(tool_name: str, *, ok: bool, message: str, error_type: str = "",
                        retryable: bool = False, details: dict | None = None) -> str:
    """A structured tool result the model can parse reliably."""
    payload = {"ok": ok, "tool": tool_name, "message": message}
    if not ok:
        payload["error_type"] = error_type or "tool_error"
        payload["retryable"] = bool(retryable)
    if details:
        payload["details"] = details
    return json.dumps(payload, ensure_ascii=False)


def _usage_tokens(usage: dict) -> tuple[int, int, int, int, int]:
    """``(total_input, fresh_input, output, cache_read, cache_write)`` from usage metadata.

    LangChain's normalized shape nests cache counts in ``input_token_details`` and
    folds them into ``input_tokens``; raw provider shapes report them at the top
    level and exclude them from ``input_tokens``. Fresh input is billed at the
    full rate, the cached portion at its own rates.
    """
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    details = usage.get("input_token_details")
    if isinstance(details, dict) and details:
        cache_read = int(details.get("cache_read", 0) or 0)
        cache_write = int(details.get("cache_creation", 0) or 0)
        return input_tokens, max(0, input_tokens - cache_read - cache_write), output_tokens, cache_read, cache_write
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    cache_write = int(usage.get("cache_creation_input_tokens", 0) or 0)
    return input_tokens + cache_read + cache_write, input_tokens, output_tokens, cache_read, cache_write


def _model_name(model) -> str:
    return getattr(model, "model_name", "") or getattr(model, "model", "") or ""


def _tool_input_schema(tool) -> dict | None:
    """The tool's original argument schema (MCP inputSchema), for decoding nulls back."""
    schema = getattr(tool, "args_schema", None)
    if isinstance(schema, dict):
        return schema
    if schema is not None and hasattr(schema, "model_json_schema"):
        try:
            return schema.model_json_schema()
        except Exception:
            return None
    return None


def available_internal_tool_names(tools, *, user_id: str, session_id: str,
                                  state: dict, find_session_meta) -> set[str]:
    """One allow set for model binding, discovery, and final execution."""
    names = {tool.name for tool in tools}
    own_tools = (find_session_meta(user_id, session_id) or {}).get("tools")
    if own_tools is not None:
        names.intersection_update(canonical_tool_names(own_tools))
    subagent = parse_subagent_session_id(session_id)
    if subagent:
        profile = get_agent_profile(subagent["agent_type"], user_id=user_id)
        if profile.allowed_tools is not None:
            names.intersection_update(profile.allowed_tools)
    if state.get("enabled_tools") is not None:
        names.intersection_update(canonical_tool_names(state["enabled_tools"]))
    mode = effective_session_mode(user_id, session_id, state.get("session_mode"))
    return set(filter_tools_for_mode(sorted(names), mode))


def _visible_tool_parameters(tool) -> dict:
    bound = hide_injected_params(tool)
    schema = bound if isinstance(bound, dict) else convert_to_openai_tool(bound)
    return schema["function"].get("parameters") or {"type": "object", "properties": {}}


def discovery_tool_schemas(registry: LazyToolRegistry, long_tail_names: set[str], *, strict: bool) -> list[dict]:
    """Two fixed schemas; the search description carries brief long-tail names."""
    if not long_tail_names:
        return []
    search = {
        "name": "tool_search",
        "description": registry.compact_tool_list(long_tail_names),
        "parameters": {
            "type": "object", "properties": {"query": {"type": "string", "description": "What capability or tool do you need?"}},
            "required": ["query"], "additionalProperties": False,
        },
    }
    call = {
        "name": "tool_call",
        "description": (
            "Call one tool returned by tool_search. Supply its exact tool name and an arguments_json "
            "string containing a JSON object that matches the parameters returned by tool_search. "
            "If any parameter is uncertain, use tool_search first; do not invent arguments."
        ),
        "parameters": {
            "type": "object", "properties": {
                "tool_name": {"type": "string", "description": "Exact tool name from tool_search"},
                "arguments_json": {"type": "string", "description": "JSON object with the selected tool's arguments"},
            },
            "required": ["tool_name", "arguments_json"], "additionalProperties": False,
        },
    }
    if strict:
        search["strict"] = True
        call["strict"] = True
    return [{"type": "function", "function": search}, {"type": "function", "function": call}]


async def _wait_for_tool_approval(approval_id: str, user_id: str) -> tuple[bool, str]:
    """Compatibility entrypoint; all live approval behavior belongs to the broker."""
    record = get_tool_approval(approval_id, user_id)
    if record is None:
        return False, "审批记录不存在"
    if record.expires_at <= utc_now() or record.status in {"used", "expired"}:
        return False, "审批记录已使用或过期，请重新申请"
    if record.status == "denied":
        return False, record.resolution_reason or "用户拒绝了该操作"
    result = await authorize_action(
        user_id=user_id, session_id=record.session_id, tool_name=record.tool_name,
        args=json.loads(record.args_json), active_approval=record,
    )
    return result.allowed, result.reason


# --- State definition ---
class AgentState(TypedDict):
    messages: list
    trigger_source: str
    enabled_tools: Optional[list[str]]
    user_id: Optional[str]
    session_id: Optional[str]
    max_turns: Optional[int]
    turn_count: Optional[int]
    # 外部调用方传入的 tools 定义（OpenAI function calling 格式）
    # 当 LLM 选择调用这些工具时，中断图执行并以 tool_calls 格式返回给调用方
    external_tools: Optional[list[dict]]
    # Per-request LLM model override (from OASIS SessionExpert per-expert config)
    # Dict with optional keys: model, api_key, base_url, provider
    llm_override: Optional[dict]
    max_tokens: Optional[int]
    # Per-request final reply format (OpenAI response_format shape). A
    # json_schema is decoded in a separate tool-free call after ReAct ends.
    response_format: Optional[dict]
    _approval_review_counters: dict
    _approval_review_blocked: bool
    _conversation_approval_prompts: list[str]
    _approval_resume_id: str
    _approval_resume_call_id: str


# Mirrors langgraph.prebuilt.ToolNode (default handle_tool_errors) so dropping
# LangGraph does not change what the model sees when a tool call goes wrong.
_TOOL_MESSAGE_BLOCK_TYPES = (
    "text", "image_url", "image", "json", "search_result",
    "custom_tool_call_output", "document", "file",
)
_INVALID_TOOL_NAME_ERROR_TEMPLATE = (
    "Error: {requested_tool} is not a valid tool, try one of [{available_tools}]."
)
_TOOL_INVOCATION_ERROR_TEMPLATE = (
    "Error invoking tool '{tool_name}' with kwargs {tool_kwargs} with error:\n"
    " {error}\n Please fix the error and try again."
)


def _tool_message_content(output) -> str | list:
    """Same normalization as LangGraph's msg_content_output."""
    if isinstance(output, str) or (
        isinstance(output, list)
        and all(isinstance(x, dict) and x.get("type") in _TOOL_MESSAGE_BLOCK_TYPES for x in output)
    ):
        return output
    try:
        return json.dumps(output, ensure_ascii=False)
    except Exception:
        return str(output)


class DirectToolNode:
    """Execute independent tool calls concurrently without a graph runtime.

    Error handling matches LangGraph ToolNode's default: an unknown tool name or
    invalid arguments becomes an error ToolMessage for that call only; any other
    exception propagates (UserAwareToolNode then reports the whole batch).
    """

    def __init__(self, tools) -> None:
        self._tools_by_name = {tool.name: tool for tool in tools}

    async def ainvoke(self, state: AgentState, config: RunnableConfig) -> dict:
        calls = state["messages"][-1].tool_calls

        async def invoke(tc: dict) -> ToolMessage:
            tool = self._tools_by_name.get(tc["name"])
            if tool is None:
                return ToolMessage(
                    content=_INVALID_TOOL_NAME_ERROR_TEMPLATE.format(
                        requested_tool=tc["name"],
                        available_tools=", ".join(self._tools_by_name),
                    ),
                    name=tc["name"],
                    tool_call_id=tc["id"],
                    status="error",
                )
            try:
                # Invoke with the full tool call (not bare args) so langchain-core
                # builds the ToolMessage itself, keeping artifact and status —
                # MCP tools use response_format="content_and_artifact".
                output = await tool.ainvoke({**tc, "type": "tool_call"}, config)
            except ValidationError as exc:
                error = "\n".join(
                    f"{'.'.join(str(loc) for loc in err.get('loc', ()))}: {err.get('msg', 'Unknown error')}"
                    for err in exc.errors()
                    if err["loc"]
                )
                return ToolMessage(
                    content=_TOOL_INVOCATION_ERROR_TEMPLATE.format(
                        tool_name=tc["name"], tool_kwargs=tc["args"], error=error,
                    ),
                    name=tc["name"],
                    tool_call_id=tc["id"],
                    status="error",
                )
            if not isinstance(output, ToolMessage):
                raise TypeError(f"Tool {tc['name']} returned unexpected type: {type(output)}")
            output.content = _tool_message_content(output.content)
            return output

        return {"messages": list(await asyncio.gather(*(invoke(tc) for tc in calls)))}


_MODE_BLOCK_MESSAGES = {
    "plan": "当前会话处于 plan 模式。请先完成调研、计划和 todo，再退出 plan 模式后执行改动。",
    "review": "当前会话处于 review 模式。请保持只读审查，避免直接修改文件或外部状态。",
}
_MODE_BLOCKED_TOOLS = {"plan": PLAN_MODE_BLOCKED_TOOLS, "review": REVIEW_MODE_BLOCKED_TOOLS}
_COMMAND_PERMIT_TOOLS = frozenset({
    "run_command", "background_command_io", "list_files", "read_file", "write_file", "delete_file",
    "web_search", "web_fetch",
})


def _mode_blocks_call(mode: str, tc: dict) -> bool:
    """Plan and review restrictions that depend on the call's arguments."""
    if mode not in _MODE_BLOCK_MESSAGES:
        return False
    args = tc.get("args") or {}
    # Typing into an interactive job runs commands; reading its output does not.
    if tc["name"] == "background_command_io" and args.get("input"):
        return True
    return (mode == "plan" and tc["name"] == "spawn_subagent"
            and str(args.get("agent_type") or "").strip().lower() in {"general", "coder"})


def _policy_decision(permission) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        allowed=permission.allowed,
        requires_approval=permission.requires_approval,
        reason=permission.reason,
        matched_rule=permission.matched_rule,
    )


class UserAwareToolNode:
    """Execute a model's tool calls for one session.

    Discovery calls (``tool_search``/``tool_call``) are answered here. Every
    other call gets the session's identity injected, then passes mode, enabled
    tool, policy-hook and approval checks before it runs; a blocked call becomes
    an error ToolMessage for that call only.
    """

    def __init__(self, tools, find_internal_session_meta_fn=None,
                 tool_registry: LazyToolRegistry | None = None):
        self.tool_node = DirectToolNode(tools)
        self._find_internal_session_meta_fn = find_internal_session_meta_fn
        self._tool_registry = tool_registry

    def _resolve_internal_session_meta(self, user_id: str, session_id: str) -> dict | None:
        resolver = self._find_internal_session_meta_fn
        if resolver is None or not user_id or not session_id:
            return None
        try:
            return resolver(user_id, session_id)
        except Exception as exc:
            logger.warning("resolve internal session meta failed: %s", exc)
            return None

    @staticmethod
    def _format_policy_block_message(
        tool_name: str,
        reason: str,
        requires_approval: bool,
        approval_id: str = "",
    ) -> str:
        if requires_approval:
            if reason.startswith('【操作授权请求】'):
                return reason
            approval_hint = f"\napproval_id: {approval_id}" if approval_id else ""
            return (
                f"⏸️ 工具 '{tool_name}' 当前需要人工批准。\n"
                f"原因：{reason or '当前 tool approval policy 未自动放行该调用。'}\n\n"
                f"如需继续，请先批准该请求后再重试。{approval_hint}"
            )
        return (
            f"❌ 工具 '{tool_name}' 被当前 WeBot tool policy 阻止。\n"
            f"原因：{reason or '该工具调用不满足当前策略要求。'}"
        )

    def _discover(self, tc: dict, *, tools_by_name: dict, long_tail_names: set[str]) -> ToolMessage | None:
        """Answer ``tool_search``, or resolve ``tool_call`` into its target call in place.

        Returns the ToolMessage that answers the call, or None when *tc* is now
        an ordinary call that still has to pass every check below.
        """
        if tc["name"] == "tool_search":
            if not self._tool_registry or not long_tail_names:
                return ToolMessage(content="No searchable tools are enabled in this session.",
                                   name="tool_search", tool_call_id=tc["id"], status="error")
            search_args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
            query = str(search_args.get("query") or "").strip()[:200]
            matches = self._tool_registry.search_tools(query, limit=6, enabled_names=long_tail_names)
            for match in matches:
                match["parameters"] = _visible_tool_parameters(tools_by_name[match["name"]])
            return ToolMessage(content=json.dumps({"tools": matches}, ensure_ascii=False),
                               name="tool_search", tool_call_id=tc["id"])
        if tc["name"] != "tool_call":
            return None
        call_args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        target_name = str(call_args.get("tool_name") or "")
        raw_json = call_args.get("arguments_json")
        try:
            if target_name not in long_tail_names or target_name not in tools_by_name:
                raise ValueError("This tool is not available through tool_call in the current session")
            if not isinstance(raw_json, str) or len(raw_json) > 200_000:
                raise ValueError("arguments_json must be a JSON object string under 200 KB")
            parsed_args = json.loads(raw_json)
            if not isinstance(parsed_args, dict):
                raise ValueError("arguments_json must contain a JSON object")
            schema = _visible_tool_parameters(tools_by_name[target_name])
            parsed_args = drop_null_optionals(parsed_args, schema)
            from jsonschema import validate
            validate(parsed_args, {**schema, "additionalProperties": False})
        except Exception as exc:
            return ToolMessage(content=f"Invalid tool_call: {type(exc).__name__}: {str(exc)[:300]}",
                               name="tool_call", tool_call_id=tc["id"], status="error")
        # From here on the call is the original tool: every mode, enablement,
        # policy, approval and MCP permit check applies to it.
        tc["name"], tc["args"] = target_name, parsed_args
        return None

    def _inject_identity(self, tc: dict, user_id: str, session_id: str, *, defaults: bool) -> None:
        """Set the caller identity the model must not choose.

        Forced session arguments and ``username`` always overwrite the model's
        values. With *defaults*, a missing session argument and the agent's only
        team are filled in as well.
        """
        name, args = tc["name"], tc["args"]
        if name in USER_INJECTED_TOOLS:
            args["username"] = user_id
        param = SESSION_INJECTED_TOOLS.get(name)
        if param and (name in SESSION_FORCE_INJECTED_TOOLS or (defaults and not args.get(param))):
            args[param] = session_id
        if not defaults or name not in TEAM_INJECTED_TOOLS:
            return
        memory_file_tool = name in {"list_files", "read_file", "write_file", "delete_file"}
        if "team" not in args if memory_file_tool else not args.get("team"):
            teams = (self._resolve_internal_session_meta(user_id, session_id) or {}).get("teams") or []
            if len(teams) == 1:  # in several teams, the call names the one it means
                args["team"] = teams[0]

    async def __call__(self, state, config: RunnableConfig):
        # user_id comes from state rather than thread_id: it may contain the separator.
        user_id = state.get("user_id") or "anonymous"
        session_id = state.get("session_id") or "default"
        mode = effective_session_mode(user_id, session_id, state.get("session_mode"))
        if state.get("session_mode"):
            save_session_mode(user_id, session_id, mode=mode)

        last_message = state["messages"][-1]
        if not getattr(last_message, "tool_calls", None):
            return {"messages": []}

        modified_message = copy.deepcopy(last_message)
        result_messages: list[ToolMessage] = []
        blocked_calls: list[tuple[dict, str, bool, str]] = []  # (call, reason, pending approval, approval_id)
        allowed_calls = []
        allowed_call_meta: dict[str, tuple[str, dict, object, str]] = {}
        allowed_bindings: dict[str, str] = {}
        tools_by_name = getattr(self.tool_node, "_tools_by_name", {})
        available_names = available_internal_tool_names(
            list(tools_by_name.values()), user_id=user_id, session_id=session_id,
            state=state, find_session_meta=self._resolve_internal_session_meta,
        )
        long_tail_names = available_names - (
            self._tool_registry.always_loaded_names if self._tool_registry else frozenset()
        )
        external_names = _external_tool_names(state)
        counters = state.setdefault("_approval_review_counters", {})
        review_blocked = False

        for tc in modified_message.tool_calls:
            answer = self._discover(tc, tools_by_name=tools_by_name, long_tail_names=long_tail_names)
            if answer is not None:
                result_messages.append(answer)
                continue
            # A retired tool name (merged or removed) runs as the tool that replaced it.
            if tc["name"] not in external_names and tc["name"] not in tools_by_name:
                tc["name"], tc["args"] = resolve_tool_call(tc["name"], tc.get("args"))
            # Strict binding encodes an omitted optional argument as null; drop
            # those so the tool applies its own default, as before strict mode.
            if tc["name"] in tools_by_name and isinstance(tc.get("args"), dict):
                tc["args"] = drop_null_optionals(tc["args"], _tool_input_schema(tools_by_name[tc["name"]]))
            if (tc['name'] == 'run_command' and (tc['args'].get('sandbox_access', 'default') != 'default' or tc['args'].get('sandbox_approval_chain'))
                    and tc['id'] != state.get('_approval_resume_call_id')):
                blocked_calls.append((tc, '沙盒权限由系统在执行失败后审核，不接受 Agent 自行申请提权。', False, ''))
                continue
            if not mode_allows_tool(mode, tc["name"], tc.get("args")):
                blocked_calls.append((tc, "当前模式不允许该工具操作。交流模式无工具；只读模式只允许查看和搜索。", False, ""))
                continue
            if tc["name"] in _MODE_BLOCKED_TOOLS.get(mode, ()) or _mode_blocks_call(mode, tc):
                blocked_calls.append((tc, _MODE_BLOCK_MESSAGES[mode], False, ""))
                continue
            if tc["name"] in tools_by_name and tc["name"] not in available_names:
                logger.info("blocked disabled tool call: %s", tc["name"])
                blocked_calls.append((tc, "该工具当前未在会话的 enabled_tools 列表中。", False, ""))
                continue

            self._inject_identity(tc, user_id, session_id, defaults=True)
            permission = resolve_permission_context(
                user_id=user_id, session_id=session_id, tool_name=tc["name"], args=tc["args"],
            )
            base_decision = _policy_decision(permission)
            hook_outcome = run_tool_policy_hooks(
                permission.policy, event="before", user_id=user_id, session_id=session_id,
                tool_name=tc["name"], args=tc["args"], decision=base_decision,
            )
            # Hooks may rewrite arguments. Restore the identity and recheck the
            # actual action before considering a hook's verdict.
            tc["args"] = dict(hook_outcome.args)
            self._inject_identity(tc, user_id, session_id, defaults=False)
            if not mode_allows_tool(mode, tc["name"], tc["args"]):
                blocked_calls.append((tc, "当前模式禁止执行 hook 修改后的操作。", False, ""))
                continue
            if _mode_blocks_call(mode, tc):
                blocked_calls.append((tc, _MODE_BLOCK_MESSAGES[mode], False, ""))
                continue
            permission = resolve_permission_context(
                user_id=user_id, session_id=session_id, tool_name=tc["name"], args=tc["args"],
                policy=permission.policy,
            )
            final_decision = _policy_decision(permission)
            hard_denied = not permission.allowed and not permission.requires_approval
            if not hard_denied and hook_outcome.decision is not None and hook_outcome.decision != base_decision:
                final_decision = hook_outcome.decision
            if (mode in {"yolo", "bypass"} and not final_decision.allowed
                    and getattr(final_decision, "requires_approval", False)):
                final_decision = ToolPolicyDecision(
                    allowed=True, requires_approval=False,
                    reason="YOLO mode auto-approved a manual tool-policy request.",
                    matched_rule=getattr(final_decision, "matched_rule", "") or permission.matched_rule,
                )
            outcome = await authorize_action(
                user_id=user_id, session_id=session_id, tool_name=tc["name"], args=tc["args"],
                decision=final_decision, messages=state["messages"], policy=permission.policy,
                counters=counters, active_approval=permission.approval,
                continuation={'enabled_tools': state.get('enabled_tools')},
                # Nobody watches a group- or schedule-triggered turn; leave
                # the request for the user instead of holding the session.
                wait_for_user=state.get("trigger_source") != "system",
            )
            if not outcome.allowed:
                blocked_calls.append((tc, outcome.reason, outcome.pending, outcome.approval_id))
                with contextlib.suppress(Exception):
                    run_tool_policy_hooks(
                        permission.policy, event="deny", user_id=user_id, session_id=session_id,
                        tool_name=tc["name"], args=tc["args"], decision=final_decision,
                    )
                review_blocked = review_blocked or counters.get("consecutive_denials", 0) >= 3
                continue
            allowed_calls.append(tc)
            allowed_bindings[tc["id"]] = outcome.binding_hash
            allowed_call_meta[tc["id"]] = (tc["name"], dict(tc["args"]), permission.policy, outcome.approval_id)

        # Waiting for a later approval can take minutes. Recheck earlier
        # authorizations, and issue short-lived MCP permits only now.
        for tc in list(allowed_calls):
            binding = allowed_bindings.get(tc["id"])
            if not binding:
                continue
            tool_name, tool_args, _policy, approval_id = allowed_call_meta[tc["id"]]
            if binding != policy_binding(user_id, session_id):
                allowed_calls.remove(tc)
                blocked_calls.append((tc, "审核后策略、模式或工作区发生变化，未执行，请重新审核。", False, approval_id))
                record_tool_execution(approval_id, user_id, status="not_executed")
            elif tool_name in _COMMAND_PERMIT_TOOLS:
                issue_execution_permit(user_id, session_id, tool_name,
                                       bind_file_target(tool_name, tool_args, user_id, session_id), binding)

        for tc, reason, requires_approval, approval_id in blocked_calls:
            result_messages.append(ToolMessage(
                content=self._format_policy_block_message(tc["name"], reason, requires_approval, approval_id),
                tool_call_id=tc["id"],
            ))
        update = {
            "messages": result_messages,
            "_approval_review_counters": counters,
            "_conversation_approval_prompts": [reason for _, reason, pending, _ in blocked_calls if pending],
            "_approval_resume_call_id": '',
        }
        if review_blocked:
            update["_approval_review_blocked"] = True
        if allowed_calls:
            modified_message.tool_calls = allowed_calls
            result_messages.extend(await self._execute(
                {**state, "messages": state["messages"][:-1] + [modified_message]}, config,
                allowed_calls, allowed_call_meta, user_id=user_id, session_id=session_id,
            ))
        return update

    async def _execute(self, state, config, calls, call_meta, *, user_id: str, session_id: str) -> list[ToolMessage]:
        """Run the authorized calls; record each outcome and run the after hooks."""
        try:
            tool_messages = (await self.tool_node.ainvoke(state, config)).get("messages", [])
        except Exception as exc:
            error_text = str(exc).strip() or exc.__class__.__name__
            logger.exception("tool execution failed user=%s session=%s tools=%s error=%s",
                             user_id, session_id, [tc["name"] for tc in calls], error_text)
            failed = []
            for tc in calls:
                tool_name, tool_args, tool_policy, approval_id = call_meta[tc["id"]]
                record_tool_execution(approval_id, user_id, status="error", detail=error_text)
                with contextlib.suppress(Exception):
                    run_tool_policy_hooks(tool_policy, event="after_error", user_id=user_id, session_id=session_id,
                                          tool_name=tool_name, args=tool_args, result=error_text)
                failed.append(ToolMessage(
                    content=tool_result_payload(
                        tool_name, ok=False, error_type="tool_execution_error", retryable=True,
                        message="工具调用失败。请检查参数是否符合工具定义后重试；如果问题来自工具运行期异常，请修正输入或改用更合适的工具。",
                        details={"error": error_text},
                    ),
                    tool_call_id=tc["id"], name=tool_name,
                ))
            return failed
        for msg in tool_messages:
            meta = call_meta.get(getattr(msg, "tool_call_id", ""))
            if meta is None:
                continue
            tool_name, tool_args, tool_policy, approval_id = meta
            result_text = getattr(msg, "content", "")
            preview = str(result_text)
            failed = getattr(msg, "status", "") == "error" or preview.startswith(("❌", "⚠️", "Error"))
            record_tool_execution(approval_id, user_id, status="error" if failed else "returned")
            events = ["after"] + (["after_error"] if preview.startswith(("❌", "⚠️")) else [])
            for event in events:
                try:
                    run_tool_policy_hooks(tool_policy, event=event, user_id=user_id, session_id=session_id,
                                          tool_name=tool_name, args=tool_args, result=result_text)
                except Exception as exc:
                    logger.warning("tool policy %s hook failed: %s", event, exc)
        return tool_messages


def _mcp_instance_env() -> dict[str, str]:
    """Variables that tell an MCP server which ClawCross instance it belongs to."""
    return {
        key: value
        for key, value in os.environ.items()
        if key.startswith(("CLAWCROSS_", "PORT_")) or key in {"INTERNAL_TOKEN", "OASIS_BASE_URL"}
    }


@dataclass(frozen=True)
class _Turn:
    """What one model call resolves once from the agent state."""
    user_id: str
    session_id: str
    is_subagent: bool
    profile: object | None  # the subagent profile, for a subagent session
    mode: str
    mode_payload: dict
    policy: object
    turn_count: int
    max_turns: int | None
    max_tokens: int | None

    @property
    def thread_id(self) -> str:
        return f"{self.user_id}#{self.session_id}"


class TeamAgent:
    """
    Encapsulates the lightweight agent runtime: MCP tool loading, loop execution,
    invoke/stream interface, task & tool-state management.
    """

    def __init__(self, src_dir: str, db_path: str):
        """
        Args:
            src_dir:  The backend import root (the MCP servers are webot/mcp/*.py under it)
            db_path:  Path to SQLite checkpoint database
        """
        self._src_dir = src_dir
        self._db_path = db_path

        # Populated during startup
        self._mcp_tools: list = []
        self._agent_app = None
        self._mcp_client: Optional[MultiServerMCPClient] = None
        self._context_store = None
        self._context_store_ctx = None
        self._background_compression = BackgroundCompressionManager(db_path)

        # Per-thread execution state
        self._task_registry = TaskRegistry()
        # Per-thread lock: 防止 system_trigger 和用户对话并发操作同一 checkpoint
        self._thread_state_registry = ThreadStateRegistry()

        self._tool_registry = LazyToolRegistry()

    @property
    def _measurements(self) -> dict[str, dict]:
        """thread_id -> the last API usage measurement and the view it measured."""
        return self.__dict__.setdefault("_usage_measurements", {})

    @property
    def _projections(self) -> dict[str, tuple]:
        """thread_id -> (summary key, measurement) of the last projected compacted usage."""
        return self.__dict__.setdefault("_compacted_usage_projections", {})

    # ------------------------------------------------------------------
    # Prompt loader (每次模型请求重新读取)
    # ------------------------------------------------------------------
    @staticmethod
    def _load_prompts() -> dict[str, str]:
        """从 data/prompts/ 加载所有 prompt 模板文件，每次模型请求调用。"""
        prompts_dir = os.path.join(str(PROJECT_ROOT), "data", "prompts")
        prompt_files = {
            "base_system": "base_system.txt",
            "base_system_subagent": "base_system_subagent.txt",
            "system_trigger": "system_trigger.txt",
            "conversation_rules": "conversation_rules.txt",
        }
        loaded = {}
        for key, filename in prompt_files.items():
            filepath = os.path.join(prompts_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    loaded[key] = f.read().strip()
            except FileNotFoundError:
                logger.warning("prompt template %s not found; using an empty template", filepath)
                loaded[key] = ""

        return loaded

    @staticmethod
    def _find_invalid_tool_feedback(response) -> tuple[ToolMessage, str] | None:
        """A repair ToolMessage for the first tool call whose arguments are not JSON."""
        for list_name in ("tool_calls", "invalid_tool_calls"):
            for call in getattr(response, list_name, None) or []:
                args = call.get("args") if list_name == "tool_calls" else call.get("args", "")
                if args is None or args == "" or args == {}:
                    if list_name == "tool_calls":
                        call["args"] = {}
                    continue
                if not isinstance(args, str):
                    continue
                try:
                    json.loads(args)
                    continue
                except (ValueError, TypeError):
                    pass
                call_id, name = call.get("id", "unknown"), call.get("name", "unknown")
                logger.warning("tool_call arguments are not valid JSON (possibly truncated): "
                               "name=%s id=%s args_len=%d; repairing in this turn", name, call_id, len(args))
                content = tool_result_payload(
                    name, ok=False, error_type="invalid_tool_arguments", retryable=True,
                    message="本次工具调用参数不是合法 JSON。请仅重发同一个工具调用，确保 args 是完整且合法的 JSON，不要输出额外解释。",
                    details={"tool_call_id": call_id, "raw_args": args},
                )
                return ToolMessage(content=content, tool_call_id=call_id, name=name), name
        return None

    def _get_user_skills(self, user_id: str, teams: list[str] | tuple[str, ...] = ()) -> str:
        """Read the current Skill/Memory catalog without paths."""
        from webot.skills import build_user_skills_listing

        return build_user_skills_listing(user_id, teams=teams)

    def _find_internal_session_meta(self, user_id: str, session_id: str) -> dict | None:
        """``{"teams", "name", "persona", "tools"}`` of the agent this session is: the
        teams it is in, its persona text, and the tools it has (None: all of them)."""
        if not user_id or not session_id:
            return None
        from agents.store import get_store

        agent = get_store().get(user_id, session_id)
        if agent is None:
            return None
        return {"teams": agent.teams, "name": agent.name,
                "persona": agent.config.get("persona", ""), "tools": agent.config.get("tools")}

    def _get_internal_session_persona_prompt(self, user_id: str, session_id: str) -> str:
        """The identity of the agent this session is, from its own persona text."""
        meta = self._find_internal_session_meta(user_id, session_id) or {}
        persona = str(meta.get("persona") or "").strip()
        return frame_session_identity(meta.get("name") or session_id, "", persona) if persona else ""

    def _build_live_system_prompt(self, user_id: str, session_id: str, is_subagent: bool) -> tuple[str, dict[str, str]]:
        """Read current templates/persona/profile/SOUL for every provider request."""
        prompts = self._load_prompts()
        subagent_meta = parse_subagent_session_id(session_id) if session_id else None
        profile = get_agent_profile(subagent_meta["agent_type"], user_id=user_id) if subagent_meta else None
        workspace = (f"【Workspace】\nsession_id: {session_id or 'default'}\n"
                     f"{describe_session_workspace(user_id, session_id)}")
        if not is_subagent:
            from common.agent_prompt import identity_sections, join_sections
            persona = self._get_internal_session_persona_prompt(user_id, session_id) if user_id and session_id else ""
            return join_sections(identity_sections(
                base=prompts["base_system"], conversation=prompts["conversation_rules"],
                persona=persona, user_profile=build_user_profile_block(user_id),
                soul=build_soul_prompt(user_id), session=workspace)), prompts
        base = prompts["base_system_subagent"]
        if profile:
            base += "\n\n" + render_profile_system_prompt(profile)
        base += f"\n{workspace}\n"
        if profile and profile.include_user_profile:
            base += build_user_profile_block(user_id)
        return base, prompts

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def mcp_tools(self) -> list:
        return self._mcp_tools

    @property
    def agent_app(self):
        return self._agent_app

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def startup(self):
        """Initialize MCP client, load tools, and build the lightweight loop."""
        # 1. Open checkpoint DB
        self._context_store_ctx = ContextStore(self._db_path)
        self._context_store = await self._context_store_ctx.__aenter__()

        # 2. Start MCP servers
        python_command = sys.executable
        mcp_servers = {
            "scheduler_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "webot", "mcp", "scheduler.py")],
                "transport": "stdio",
            },
            "search_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "webot", "mcp", "search.py")],
                "transport": "stdio",
            },
            "file_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "webot", "mcp", "filemanager.py")],
                "transport": "stdio",
            },
            "commander_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "webot", "mcp", "commander.py")],
                "transport": "stdio",
            },
            "oasis_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "webot", "mcp", "oasis.py")],
                "transport": "stdio",
            },
            "session_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "webot", "mcp", "session.py")],
                "transport": "stdio",
            },
            "ui_panel_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "webot", "mcp", "ui_panel.py")],
                "transport": "stdio",
            },
            "notifier_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "webot", "mcp", "notifier.py")],
                "transport": "stdio",
            },
            "llmapi_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "webot", "mcp", "llmapi.py")],
                "transport": "stdio",
            },
            "webot_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "webot", "mcp", "webot.py")],
                "transport": "stdio",
            },
            "self_evolution_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "webot", "mcp", "skills.py")],
                "transport": "stdio",
            },
        }
        # The MCP SDK starts stdio servers with only HOME/PATH/SHELL/TERM/USER/LOGNAME,
        # so without this an instance running on its own CLAWCROSS_HOME or ports would
        # have its tools read the default home's .env and call the default ports —
        # another ClawCross instance.
        instance_env = _mcp_instance_env()
        for server in mcp_servers.values():
            if server.get("transport") == "stdio":
                server["env"] = {**instance_env, **server.get("env", {})}
        self._mcp_client = MultiServerMCPClient(mcp_servers)

        # 3. Fetch tool definitions (new API: no context manager needed)
        self._mcp_tools = await self._mcp_client.get_tools()

        # 3.5 Register tools in lazy discovery registry (new)
        self._tool_registry.register_tools(self._mcp_tools)
        # Mark essential tools as always-loaded
        self._tool_registry.set_always_loaded({
            # No "search_files" — no server defines one; grep through run_command.
            "read_file", "write_file", "list_files", "run_command", "show_ui_panel", "set_session_title",
        })

        # 4. Build the fixed model -> tools -> model loop.  A general-purpose
        # graph engine is unnecessary because ClawCross has no dynamic graph,
        # joins, interrupts, or parallel graph branches here.
        # 收集所有内部 MCP 工具名称，用于条件路由
        self._internal_tool_names = frozenset(t.name for t in self._mcp_tools) | {"tool_search", "tool_call"}

        tool_node = UserAwareToolNode(
            self._mcp_tools,
            find_internal_session_meta_fn=self._find_internal_session_meta,
            tool_registry=self._tool_registry,
        )
        self._agent_app = LightweightAgentRuntime(
            call_model=self._call_model,
            call_tools=tool_node,
            should_continue=self._should_continue,
            context_store=self._context_store,
            on_turn_complete=self._queue_background_compression,
        )

        print("--- Agent 服务已启动，外部定时/用户输入双兼容就绪 ---")
        print(f"    工具注册: {self._tool_registry.tool_count} tools"
              f" ({len(self._tool_registry._always_loaded)} always-loaded)")


    async def shutdown(self):
        """Clean up MCP client and checkpoint DB."""
        await self._background_compression.close()
        if self._context_store_ctx:
            try:
                await self._context_store_ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def close_thread_checkpoint(self, thread_id: str) -> None:
        """Close one thread-specific checkpoint handle so its shard can be deleted safely."""
        await self.invalidate_background_compression(thread_id)
        if not self._context_store_ctx:
            return
        try:
            await self._context_store_ctx.aclose_thread(thread_id)
        except Exception as e:
            logger.warning("close_thread_checkpoint failed for %s: %s", thread_id, e)

    async def invalidate_background_compression(self, thread_id: str) -> None:
        await self._background_compression.invalidate(thread_id)

    def get_background_compaction_status(self, thread_id: str) -> dict:
        return self._background_compression.status(thread_id)

    def forget_thread_state(self, thread_id: str) -> None:
        self._thread_state_registry.forget(thread_id)
        self._measurements.pop(thread_id, None)
        self._projections.pop(thread_id, None)

    def _queue_background_compression(self, state: dict) -> None:
        """Schedule summarization only after the final reply is persisted."""
        config = state.get("_background_compaction_config")
        if not config:
            return
        user_id = state.get("user_id") or ""
        session_id = state.get("session_id") or ""
        if not user_id or not session_id:
            return
        self._background_compression.schedule(
            user_id=user_id, session_id=session_id,
            messages=list(state.get("messages") or []),
            history_token_budget=config["history_token_budget"],
            preserve_recent=config["preserve_recent"],
            settings=config["settings"],
            measured_input_tokens=self.get_thread_last_context_tokens(f"{user_id}#{session_id}"),
            measured_budget=config["context_window"],
            model=config.get("model", ""),
        )

    async def purge_checkpoints(self, thread_id: str, keep: int = 1) -> int:
        """
        清理指定 thread 的旧 checkpoint，只保留最近 `keep` 个。
        应在每次 graph 执行完成后调用。
        """
        from webot.checkpoint_repository import purge_old_checkpoints
        try:
            return await purge_old_checkpoints(self._db_path, thread_id, keep=keep)
        except Exception as e:
            logger.warning("purge_checkpoints failed for %s: %s", thread_id, e)
            return 0

    # ------------------------------------------------------------------
    # Loop routing
    # ------------------------------------------------------------------
    def _should_continue(self, state: AgentState) -> bool:
        """Run tools only when every call is one of ours; a caller-supplied
        (external) tool call ends the loop and goes back to the caller."""
        last_msg = state["messages"][-1]
        if not getattr(last_msg, "tool_calls", None):
            return False
        external_names = _external_tool_names(state)
        for tc in last_msg.tool_calls:
            name = tc["name"]
            if name in external_names or canonical_tool_name(name) not in self._internal_tool_names:
                logger.info("external tool call %s: returning it to the caller", name)
                return False
        return True

    # ------------------------------------------------------------------
    # Model step
    # ------------------------------------------------------------------
    async def _call_model(self, state: AgentState, config: RunnableConfig | None = None):
        """One model step: build the request from live state, call the model, record usage."""
        if (not state.get('_approval_resume_id') and not state.get('turn_count')
                and state.get('messages') and isinstance(state['messages'][-1], HumanMessage)):
            # CLI/social transports may send the button equivalent as text.
            # Resolve it before inference and use the same exact-action retry.
            resolution = resolve_conversation_reply(state['user_id'], state['session_id'], review_context(state['messages']))
            if resolution.startswith('已批准 '):
                state = {**state, '_approval_resume_id': resolution.split(' ', 1)[1]}
            elif resolution.startswith('已拒绝 '):
                original = state['messages'][-1]
                notification = original.model_copy(update={'content': '[操作授权结果] ' + resolution + '。不要重试或绕过此拒绝；说明未执行的原因。',
                    'additional_kwargs': {**original.additional_kwargs, 'input_origin': 'system'}})
                state = {**state, 'messages': state['messages'][:-1] + [notification]}
        if state.get('_approval_resume_id'):
            from uuid import uuid4
            record = get_tool_approval(state['_approval_resume_id'], state['user_id'])
            if (record is not None and record.session_id == state['session_id']
                    and record.status == 'approved' and record.expires_at > utc_now()
                    and json.loads(record.review_metadata_json or '{}').get('human_resolution') == 'approved'):
                # Retry the saved operation directly, without asking the model to
                # reconstruct it or repeat other completed tool calls.
                call_id = 'approval-resume-' + uuid4().hex
                return {'messages': [AIMessage(content='', tool_calls=[{
                    'id': call_id, 'name': record.tool_name,
                    'args': {k: v for k, v in json.loads(record.args_json or '{}').items() if not k.startswith('_')},
                }])], '_approval_resume_id': '', '_approval_resume_call_id': call_id}
            return {'messages': [AIMessage(content='授权已失效，未继续执行。请重新发起操作。')],
                    '_approval_resume_id': ''}
        if state.get('_conversation_approval_prompts'):
            return {'messages': [AIMessage(content='\n\n'.join(state['_conversation_approval_prompts']))],
                    '_conversation_approval_prompts': []}
        if state.get("_approval_review_blocked"):
            return {"messages": [AIMessage(content="自动审核连续拒绝三次，本轮已停止执行。请查看拒绝原因并给出新的指示。")],
                    "_approval_review_blocked": False}

        turn = self._begin_turn(state)
        external_defs = _external_tool_defs(state)
        external_tool_names = {func_def["name"] for func_def in external_defs}

        # Tool arguments are constrained at decode time by their JSON schema.
        # Only tools this session may call this turn are sent.
        base_model, strict_tools, bind_kwargs = strict_tool_binding(self._select_model(state, turn))
        bind_tools_list = self._turn_tool_schemas(state, turn, external_defs, strict=strict_tools)
        llm = base_model.bind_tools(bind_tools_list, **bind_kwargs) if bind_tools_list else base_model

        # A JSON schema applies only to the terminal text answer, after ReAct
        # finishes using tools, and is decoded with provider constraints.
        response_format = state.get("response_format")
        structured_final = bool(response_format and response_format.get("type") == "json_schema")
        deepseek_structured = structured_final and any(
            cls.__name__ == "ChatDeepSeek" for cls in type(base_model).__mro__
        )
        reply_format_hint = ""
        if response_format and not structured_final:
            format_kwargs, reply_format_hint = reply_format_binding(base_model, response_format)
            if format_kwargs:
                llm = llm.bind(**format_kwargs)
        # Anthropic caches only at an explicit breakpoint; marking the request
        # tail every turn lets the next, longer prefix hit the cache.
        from langchain_anthropic import ChatAnthropic
        if isinstance(base_model, ChatAnthropic):
            llm = llm.bind(cache_control={"type": "ephemeral"})
        model_name = _model_name(llm)
        context_tool_schemas = tool_schemas(bind_tools_list)

        # Rebuilt from live files and Agent metadata on every request. Anything
        # that changes per turn goes in the dynamic block, never in this prefix.
        base_prompt, prompts = self._build_live_system_prompt(turn.user_id, turn.session_id, turn.is_subagent)
        dynamic_context = self._dynamic_context(state, turn, reply_format_hint)
        history = await self._prepare_history(state, turn)

        settings = get_runtime_settings(turn.user_id, turn.session_id).context
        prefix_tokens = sum(estimate_context_components(
            system_prompt=base_prompt, tools=context_tool_schemas, runtime_state=dynamic_context, messages=[],
        ).values())
        # Budgets follow the model actually used this turn (route, override).
        context_window = resolve_context_window(settings, model_name or None)
        output_reserve = max(2048, int(turn.max_tokens or getattr(base_model, "max_tokens", 0) or 0))
        history_budget = resolve_context_history_budget(
            settings, is_subagent=turn.is_subagent, model=model_name or None,
            prefix_tokens=prefix_tokens, output_reserve=output_reserve,
        )
        _, preserve_recent = resolve_history_message_limits(is_subagent=turn.is_subagent, token_budget=history_budget)
        compaction_config = {
            "history_token_budget": history_budget,
            "preserve_recent": preserve_recent,
            "settings": settings,
            "context_window": context_window,
            "model": model_name,
        }
        # Static paths (session history / status) read the model used last.
        self._thread_state_registry.set_thread_model(turn.thread_id, model_name)
        history, view_record, measured_context = await self._history_view(
            state, turn, history, settings=settings, history_budget=history_budget,
            preserve_recent=preserve_recent, prefix_tokens=prefix_tokens,
            output_reserve=output_reserve, context_window=context_window, model_name=model_name,
        )

        # Runtime pressure follows this turn's view: a previous API total may
        # predate compaction. The UI shows the estimate until the API measures.
        session_budget = get_session_budget(turn.user_id, turn.session_id)
        cost_tracker = get_cost_tracker(turn.user_id, turn.session_id)
        context_used = prefix_tokens + estimate_messages_tokens(history)
        session_budget.update_current_context(used_tokens=context_used, budget_tokens=context_window)
        if measured_context <= 0:
            # Once the API has measured the context, an estimate never replaces it.
            self.set_thread_context_usage(turn.thread_id, context_used, context_window)
        for notice in (session_budget.format_budget_notice(), cost_tracker.format_cost_notice()):
            if notice:
                dynamic_context += f"\n{notice}\n"

        history = self._mark_system_trigger(state, history, prompts)
        # Validate tool sequences last, after compaction and rewriting, so a
        # summary cut cannot leave orphan tool results or dangling tool calls.
        history = self._sanitize_messages(history, external_tool_names)
        for msg in history:
            if (isinstance(msg, ToolMessage) and isinstance(msg.content, list)
                    and not self._tool_message_content_has_image(msg.content)):
                msg.content = extract_text(msg.content)
        # The dynamic block is sent as the change since the snapshot visible in
        # history; compaction that drops the snapshot sends it whole again.
        input_messages, injected_runtime_state = assemble_input_messages(
            base_prompt=base_prompt, history=history, runtime_state=dynamic_context,
        )

        next_turn_count = turn.turn_count + 1
        while True:
            base_prompt, prompts = self._build_live_system_prompt(turn.user_id, turn.session_id, turn.is_subagent)
            input_messages[0] = SystemMessage(content=base_prompt)
            validate_context_capacity(
                system_prompt=base_prompt, tools=context_tool_schemas,
                messages=input_messages[1:], context_window=context_window, output_reserve=output_reserve,
            )
            response = await self._invoke(
                llm, base_model, input_messages, config, response_format=response_format,
                tools=context_tool_schemas, structured_final=structured_final,
                deepseek_structured=deepseek_structured,
            )
            # Commit the injected change only once the provider returned it to
            # the model, so a failed call retries with the same injection.
            if injected_runtime_state:
                await self._commit_runtime_state(state, turn.thread_id, history[-1],
                                                 dynamic_context, injected_runtime_state)
                injected_runtime_state = ""
            usage_meta = getattr(response, "usage_metadata", None) or {}
            await self._record_call_usage(
                turn, usage_meta, response=response, model_name=model_name, base_prompt=base_prompt,
                tools=context_tool_schemas, history=history, context_window=context_window,
                view_record=view_record, session_budget=session_budget, cost_tracker=cost_tracker,
            )
            self._trace_call(turn, model_name, input_messages, response, usage_meta, next_turn_count)
            invalid_feedback = self._find_invalid_tool_feedback(response)
            if invalid_feedback is None:
                break
            error_tool_msg, invalid_tool_name = invalid_feedback
            logger.warning("invalid tool call repair retrying: name=%s", invalid_tool_name)
            history = self._sanitize_messages(history + [response, error_tool_msg], external_tool_names)
            input_messages, _ = assemble_input_messages(
                base_prompt=base_prompt, history=history, runtime_state=dynamic_context,
            )

        if should_stop_for_turn_limit(next_turn_count, turn.max_turns,
                                      getattr(response, "tool_calls", None), self._internal_tool_names):
            self._session_hook(turn, "stop", {"reason": "max_turns"},
                               result={"next_turn_count": next_turn_count, "max_turns": turn.max_turns})
            response = AIMessage(content=build_turn_limit_message(extract_text(response.content), turn.max_turns))

        if structured_final and not deepseek_structured and not getattr(response, "tool_calls", None):
            base_prompt, _ = self._build_live_system_prompt(turn.user_id, turn.session_id, turn.is_subagent)
            input_messages[0] = SystemMessage(content=base_prompt)
            validate_context_capacity(
                system_prompt=base_prompt, tools=[response_format],
                messages=[*input_messages[1:], response,
                          HumanMessage(content="Give the final answer in the requested schema.")],
                context_window=context_window, output_reserve=output_reserve,
            )
            response = await decode_structured_final(base_model, response_format, [*input_messages, response], config)
            final_usage = getattr(response, "usage_metadata", None) or {}
            if isinstance(final_usage, dict) and self._bill_usage(final_usage, session_budget, cost_tracker, model_name):
                usage_meta = {
                    "input_tokens": int(usage_meta.get("input_tokens", 0) or 0) + int(final_usage.get("input_tokens", 0) or 0),
                    "output_tokens": int(usage_meta.get("output_tokens", 0) or 0) + int(final_usage.get("output_tokens", 0) or 0),
                }

        self._session_hook(turn, "session_end", {
            "turn_count": next_turn_count,
            "has_tool_calls": bool(getattr(response, "tool_calls", None)),
        }, result={"content": extract_text(response.content)[:500]})
        if auto_trajectory_enabled() and not getattr(response, "tool_calls", None) and next_turn_count > 1:
            self._save_trajectory(turn, model_name, history, response, usage_meta)

        # The tool node recomputes the same allow set. The caller's requested
        # enabled_tools stay intact so a mode change can expose tools again.
        return {
            "messages": [response],
            "turn_count": next_turn_count,
            "_background_compaction_config": compaction_config,
        }

    def _begin_turn(self, state: AgentState) -> "_Turn":
        """Resolve who is calling in which mode, and run the session-level hooks."""
        user_id = state.get("user_id", "__global__")
        session_id = state.get("session_id", "")
        resolve_conversation_reply(user_id, session_id, review_context(state.get("messages") or []))
        subagent_meta = parse_subagent_session_id(session_id) if session_id else None
        profile = get_agent_profile(subagent_meta["agent_type"], user_id=user_id) if subagent_meta else None
        turn_count = state.get("turn_count") or 0
        stored = get_session_state(user_id, session_id)
        mode = effective_session_mode(user_id, session_id, state.get("session_mode"))
        if state.get("session_mode") and turn_count == 0:
            save_session_mode(user_id, session_id, mode=mode)
        turn = _Turn(
            user_id=user_id,
            session_id=session_id,
            is_subagent=bool(subagent_meta) or session_id.startswith("oasis_"),
            profile=profile,
            mode=mode,
            mode_payload={"mode": mode, "status": stored.status, "reason": stored.summary},
            policy=get_tool_policy(user_id),
            turn_count=turn_count,
            max_turns=resolve_max_turns(state.get("max_turns"), profile.max_turns if profile else None),
            max_tokens=state.get("max_tokens"),
        )
        if turn_count == 0 or len(state.get("messages") or []) <= 1:
            self._session_hook(turn, "session_start", {"is_subagent": turn.is_subagent})
        last_input = state["messages"][-1] if state.get("messages") else None
        if isinstance(last_input, HumanMessage):
            self._session_hook(turn, "user_prompt_submit", {
                "trigger_source": state.get("trigger_source") or "user",
                "content": str(last_input.content)[:500],
            })
        return turn

    @staticmethod
    def _session_hook(turn: "_Turn", event: str, args: dict, result=None) -> None:
        """A session-level policy hook; its failure never affects the turn."""
        with contextlib.suppress(Exception):
            run_tool_policy_hooks(turn.policy, event=event, user_id=turn.user_id, session_id=turn.session_id,
                                  tool_name="__session__", args={"mode": turn.mode, **args}, result=result)

    @staticmethod
    def _select_model(state: AgentState, turn: "_Turn") -> BaseChatModel:
        """The model for this call: per-request override, else the subagent's
        preferred model, else a cheap route for a simple user message, else the default."""
        max_tokens = turn.max_tokens
        effort = get_runtime_settings(turn.user_id, turn.session_id).inference.reasoning_effort
        inference = {"reasoning_effort": effort} if effort else {}
        override = state.get("llm_override")
        if override:
            return llm_factory.create_chat_model(
                model=override.get("model"), api_key=override.get("api_key"),
                base_url=override.get("base_url"), provider=override.get("provider"),
                max_tokens=max_tokens or 2048, **inference,
            )
        if turn.profile and turn.profile.preferred_model:
            return llm_factory.create_chat_model(model=turn.profile.preferred_model, max_tokens=max_tokens or 2048, **inference)
        last_input = (state.get("messages") or [None])[-1]
        if (not turn.is_subagent and isinstance(last_input, HumanMessage)
                and isinstance(last_input.content, str) and last_input.content):
            route = resolve_turn_route(last_input.content)
            if route and route.get("model"):
                logger.info("cheap model route: %s reason=%s", route["model"], route.get("routing_reason"))
                return llm_factory.create_chat_model(
                    model=route["model"], provider=route.get("provider"), api_key=route.get("api_key"),
                    base_url=route.get("base_url"), max_tokens=max_tokens or 2048, **inference,
                )
        if max_tokens is not None and max_tokens > 0:
            return llm_factory.create_chat_model(max_tokens=max_tokens, **inference)
        return llm_factory.create_chat_model(**inference)

    def _turn_tool_schemas(self, state: AgentState, turn: "_Turn", external_defs: list[dict], *, strict: bool) -> list:
        """Core tool schemas, discovery for the eligible long tail, and caller tools.

        The tool node recomputes the same allow set before it executes a call.
        """
        allowed = available_internal_tool_names(
            self._mcp_tools, user_id=turn.user_id, session_id=turn.session_id,
            state=state, find_session_meta=self._find_internal_session_meta,
        )
        always_loaded = self._tool_registry.always_loaded_names
        schemas = [bind_tool_schema(tool, strict=strict) for tool in self._mcp_tools
                   if tool.name in allowed and tool.name in always_loaded]
        schemas.extend(discovery_tool_schemas(self._tool_registry, allowed - always_loaded, strict=strict))
        if turn.mode not in {"chat", "readonly"}:
            schemas.extend(external_tool_schema(func_def, strict=strict) for func_def in external_defs)
        return schemas

    def _dynamic_context(self, state: AgentState, turn: "_Turn", reply_format_hint: str) -> str:
        """Per-turn state sent with the current message: mode, inbox, approvals,
        memory, groups and team skills. Never part of the stable system prefix."""
        user_id, session_id = turn.user_id, turn.session_id
        session_meta = self._find_internal_session_meta(user_id, session_id) if (user_id and session_id) else None
        session_teams = sorted({str(team).strip() for team in ((session_meta or {}).get("teams") or []) if str(team).strip()})
        show_skills = (not turn.is_subagent) or (turn.profile and turn.profile.include_user_skills)
        team_skill_context = render_team_skill_context(
            session_teams, self._get_user_skills(user_id, session_teams) if show_skills else "",
        )
        # The inbox worker's HumanMessage already carries its digest. Any other
        # turn surfaces newly queued messages once, on its first model call.
        inbox, inbox_unread, inbox_new = [], 0, 0
        if should_inject_new_inbox_notice(state, turn.turn_count):
            inbox_new = count_inbox_messages(user_id, session_id, status="queued")
            if inbox_new:
                inbox_unread = count_inbox_messages(user_id, session_id, status="unread")
                inbox = [
                    {"message_id": item.message_id, "source_session": item.source_session,
                     "source_label": item.source_label, "summary": item.summary, "status": item.status}
                    for item in list_inbox_messages(user_id, session_id, status="queued", limit=3)
                ]
        pending_approvals = [
            {"approval_id": approval.approval_id, "tool_name": approval.tool_name, "status": approval.status}
            for approval in list_tool_approvals(user_id, session_id, status="pending", limit=5)
        ]
        runtime_block = render_runtime_context_block(
            mode=turn.mode_payload, pending_approvals=pending_approvals, inbox=inbox,
            inbox_unread_count=inbox_unread, inbox_new_count=inbox_new,
            # Reading memory must not rewrite its index when no entry changed.
            memory=get_memory_state(user_id, session_id),
        )
        from common.conversation_context import group_memberships
        block = (
            f"【Session Mode】\n{build_session_mode_message(turn.mode, turn.mode_payload['reason'])}\n\n"
            f"{runtime_block}\n"
            "\n" + render_group_context(state["messages"], memberships=group_memberships(user_id, session_id)) + "\n"
        )
        if team_skill_context:
            block += f"\n{team_skill_context}\n"
        if reply_format_hint:
            block += f"\n[回复格式] {reply_format_hint}\n"
        return block

    async def _prepare_history(self, state: AgentState, turn: "_Turn") -> list:
        """The stored history with @references in the newest user message expanded
        and older attachments reduced to text placeholders."""
        history = list(state["messages"])
        last = history[-1] if history else None
        if isinstance(last, HumanMessage) and isinstance(last.content, str) and "@" in last.content:
            from webot.workspace import resolve_session_workspace
            workspace = resolve_session_workspace(turn.user_id, turn.session_id)
            cwd_path = str(workspace.cwd or workspace.root or "")
            if cwd_path:
                expanded = await expand_context_references(
                    last.content, cwd=cwd_path, context_limit=12000 if turn.is_subagent else 24000,
                    allowed_root=cwd_path,
                )
                if expanded.references_expanded > 0:
                    history[-1] = last.model_copy(update={"content": expanded.expanded_message})
                    if expanded.warnings:
                        logger.info("context reference warnings: %s", expanded.warnings)
        # Old binary attachments are not resent every turn; the current input keeps its parts.
        if len(history) > 1:
            history = self._strip_multimodal_parts(history[:-1]) + [history[-1]]
        return history

    async def _history_view(self, state: AgentState, turn: "_Turn", history: list, *, settings,
                            history_budget: int, preserve_recent: int, prefix_tokens: int,
                            output_reserve: int, context_window: int, model_name: str):
        """``(view, compaction record, measured context)``: the history to send,
        built on the newest completed summary and bounded to the budget."""
        thread_id = turn.thread_id
        # After a restart, the last API measurement is read back from disk.
        await self.restore_context_usage(thread_id)
        measured_context = self.get_thread_last_context_tokens(thread_id)
        # Each model call can pick up a newly completed summary, also within a
        # long tool loop. API usage measured against an older summary is ignored.
        current = get_context_compaction(self._db_path, thread_id)
        measurement = self._measurements.get(thread_id, {})
        record = await self._background_compression.prepare_for_model(
            user_id=turn.user_id, session_id=turn.session_id, messages=history,
            history_token_budget=history_budget, preserve_recent=preserve_recent,
            settings=settings, prefix_tokens=prefix_tokens, output_reserve=output_reserve,
            context_window=context_window, model=model_name,
            measured_input_tokens=measured_context if measurement.get("compaction_key") == compaction_key(current) else 0,
        )
        if compaction_key(current) != compaction_key(record):
            self.project_compacted_context_usage(thread_id, record, state["messages"])
        view = temporary_bounded_view(compression_view_from_record(record, history), history_budget)
        # The previous API total may describe a larger, pre-compaction history:
        # judge the new input against the view actually sent this turn.
        view = trim_new_input_if_oversized(
            view, user_id=turn.user_id, session_id=turn.session_id,
            current_context_tokens=prefix_tokens + output_reserve + estimate_messages_tokens(view[:-1]),
            context_window=context_window,
        )
        return view, record, measured_context

    @staticmethod
    def _mark_system_trigger(state: AgentState, history: list, prompts: dict[str, str]) -> list:
        """An internal trigger still arrives as a HumanMessage; prefix its notice
        and keep its original (possibly multimodal) content."""
        if state.get("trigger_source") != "system" or not history or not isinstance(history[-1], HumanMessage):
            return history
        original = history[-1]
        if isinstance(original.content, list):
            content = [{"type": "text", "text": prompts["system_trigger"].format(original_text="")}, *original.content]
        else:
            content = prompts["system_trigger"].format(original_text=original.content)
        return history[:-1] + [original.model_copy(update={"content": content})]

    @staticmethod
    async def _invoke(llm, base_model, input_messages: list, config, *, response_format, tools,
                      structured_final: bool, deepseek_structured: bool) -> AIMessage:
        """Call the provider. Streaming fires per-token callbacks for SSE clients;
        a structured turn never streams its unconstrained ReAct draft."""
        if structured_final:
            if deepseek_structured:
                from webot.engine.deepseek_responses import deepseek_structured_turn
                return await deepseek_structured_turn(base_model, input_messages, response_format, tools)
            return await llm.ainvoke(input_messages, config=config)
        full_response = None
        async for chunk in llm.astream(input_messages, config=config):
            full_response = chunk if full_response is None else full_response + chunk
        if full_response is None:
            raise RuntimeError("LLM stream produced no chunks")
        # Keep the concrete AIMessage class for session history readers.
        return AIMessage(**{field: getattr(full_response, field) for field in AIMessage.model_fields if field != "type"})

    async def _commit_runtime_state(self, state: AgentState, thread_id: str, carrier,
                                    dynamic_context: str, delta: str) -> None:
        """Keep the sent runtime-state change in the carrier's metadata, so tool
        calls, later turns and restarts rebuild the same model input."""
        await self._context_store.record_runtime_state(
            thread_id, source_message=carrier, state=dynamic_context, delta=delta,
        )
        carrier.additional_kwargs[RUNTIME_STATE_KEY] = dynamic_context
        carrier.additional_kwargs[RUNTIME_DELTA_KEY] = delta
        for message in reversed(state["messages"]):
            same_id = carrier.id and message.id == carrier.id
            same_tool = (isinstance(carrier, ToolMessage) and isinstance(message, ToolMessage)
                         and message.tool_call_id == carrier.tool_call_id)
            if same_id or same_tool:
                message.additional_kwargs[RUNTIME_STATE_KEY] = dynamic_context
                message.additional_kwargs[RUNTIME_DELTA_KEY] = delta
                break

    @staticmethod
    def _bill_usage(usage: dict, session_budget, cost_tracker, model_name: str):
        """Record one call's tokens in the session budget and cost; the parsed
        counts, or None when the provider reported nothing."""
        if not usage:
            return None
        counts = _usage_tokens(usage)
        total_input, fresh_input, output, cache_read, cache_write = counts
        if not (total_input or output or cache_read or cache_write):
            return None
        session_budget.record_turn(input_tokens=total_input, output_tokens=output,
                                   cache_creation_tokens=cache_write, cache_read_tokens=cache_read)
        cost_tracker.record(model=model_name, input_tokens=fresh_input, output_tokens=output,
                            cache_read_tokens=cache_read, cache_write_tokens=cache_write)
        return counts

    async def _record_call_usage(self, turn: "_Turn", usage: dict, *, response, model_name: str,
                                 base_prompt: str, tools: list, history: list, context_window: int,
                                 view_record, session_budget, cost_tracker) -> None:
        """Bill the call and keep its API-measured context occupancy (input +
        output: this output is not yet part of any measured input)."""
        if not isinstance(usage, dict):
            return
        counts = self._bill_usage(usage, session_budget, cost_tracker, model_name)
        if counts is None:
            return
        total_input, _, output, cache_read, _ = counts
        try:
            components = await asyncio.to_thread(
                # The current delta is on the carrier message now, with earlier
                # deltas restored from history.
                estimate_context_components, system_prompt=base_prompt, tools=tools,
                runtime_state="", messages=list(history),
            )
        except Exception as exc:
            logger.warning("context breakdown failed: %s", exc)
            components = {}
        accounting = request_accounting(base_prompt, tools, history, response, model_name)
        differential = difference_components(self._measurements.get(turn.thread_id, {}),
                                             accounting, history, total_input)
        if differential is not None:
            accounting["difference_breakdown"] = differential
        await self.record_context_usage(
            turn.thread_id, input_tokens=total_input, output_tokens=output, cache_read_tokens=cache_read,
            model=model_name, context_window=context_window, components=components,
            accounting=accounting, view_key=compaction_key(view_record),
        )

    @staticmethod
    def _trace_call(turn: "_Turn", model_name: str, input_messages: list, response, usage: dict, turn_number: int) -> None:
        """One record per provider call, when CLAWCROSS_LLM_CALL_TRACE is on; never blocks the agent."""
        if not llm_call_trace_enabled():
            return
        with contextlib.suppress(Exception):
            asyncio.create_task(asyncio.to_thread(
                save_llm_call,
                user_id=turn.user_id, session_id=turn.session_id, model=model_name,
                input_messages=[{"role": type(m).__name__.replace("Message", "").lower(),
                                 "content": extract_text(m.content)} for m in input_messages],
                output=extract_text(response.content),
                tool_calls=[{"name": tc.get("name"), "args": tc.get("args"), "id": tc.get("id")}
                            for tc in (getattr(response, "tool_calls", None) or [])],
                token_usage=usage if isinstance(usage, dict) else {},
                turn=turn_number,
            ))

    @staticmethod
    def _save_trajectory(turn: "_Turn", model_name: str, history: list, response, usage: dict) -> None:
        """Save the finished conversation in the background; failures are ignored."""
        messages = [{"role": type(m).__name__.replace("Message", "").lower(), "content": extract_text(m.content)}
                    for m in history[:20]]
        messages.append({"role": "assistant", "content": extract_text(response.content)[:2000]})
        with contextlib.suppress(Exception):
            asyncio.create_task(asyncio.to_thread(
                save_trajectory,
                user_id=turn.user_id, session_id=turn.session_id, messages=messages, model=model_name,
                completed=True, tool_calls_count=turn.turn_count,
                token_usage={"input_tokens": usage.get("input_tokens", 0) if isinstance(usage, dict) else 0,
                             "output_tokens": usage.get("output_tokens", 0) if isinstance(usage, dict) else 0},
            ))

    # ------------------------------------------------------------------
    # Public interface: tools info
    # ------------------------------------------------------------------
    @staticmethod
    def _tool_use_blocks_from_content(msg) -> list[dict]:
        """Extract Anthropic/Claude-style tool_use blocks from message content."""
        blocks = []
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            return blocks
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                blocks.append(
                    {
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                    }
                )
        return blocks

    @classmethod
    def _message_tool_calls(cls, msg) -> list[dict]:
        """Return all OpenAI/LangChain and Claude-style tool calls on an AIMessage."""
        tc_list: list = []
        seen_ids: set[str] = set()
        for tc in list(getattr(msg, "tool_calls", None) or []):
            tid = (tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)) or ""
            name = (tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)) or ""
            rec = {"id": tid, "name": name}
            if isinstance(tc, dict):
                rec = {**tc, **rec}
            tc_list.append(rec)
            if tid:
                seen_ids.add(tid)
        for itc in getattr(msg, "invalid_tool_calls", None) or []:
            tid = itc.get("id", "") if isinstance(itc, dict) else ""
            rec = {"id": tid, "name": itc.get("name", ""), **itc} if isinstance(itc, dict) else {"id": "", "name": ""}
            tc_list.append(rec)
            if tid:
                seen_ids.add(tid)
        for rec in cls._tool_use_blocks_from_content(msg):
            tid = rec.get("id", "")
            if tid and tid not in seen_ids:
                tc_list.append(rec)
                seen_ids.add(tid)
        return tc_list

    @classmethod
    def cancelled_tool_messages_for_last_ai(cls, last_msg) -> list[ToolMessage]:
        """Build cancellation ToolMessages for OpenAI and Claude-style tool calls."""
        if not isinstance(last_msg, AIMessage):
            return []
        messages: list[ToolMessage] = []
        for tc in cls._message_tool_calls(last_msg):
            tool_call_id = tc.get("id", "")
            if not tool_call_id:
                continue
            messages.append(
                ToolMessage(
                    content="⚠️ 工具调用被用户终止",
                    tool_call_id=tool_call_id,
                    name=tc.get("name", "") or "",
                )
            )
        return messages

    @staticmethod
    def _sanitize_messages(messages: list, external_tool_names: set[str] | None = None) -> list:
        """
        清理消息列表，确保每条带 tool_calls 的 AI 消息后面都有对应的 ToolMessage。

        两轮扫描：
        1. 末尾截断：从后往前移除悬空的 tool_calls AIMessage（保留外部工具等待回传）
        2. 位置校验的全序列扫描：每条 AIMessage 的 tool 调用必须在**紧随其后**的连续
           ToolMessage 中全部出现；否则剥离该轮的工具块，并丢弃同批次 tool_call_id 的孤儿
           ToolMessage（避免 2013）。

        注意：MiniMax/Anthropic 适配下，tool_use 可能只留在 ``content`` 列表里而 ``tool_calls``
        为空（checkpoint/合并异常）；必须通过 content 里的 ``type=="tool_use"`` 块一并检测。
        """
        _log = logging.getLogger("agent.sanitize")

        if not external_tool_names:
            external_tool_names = set()

        def _strip_ai_tool_blocks(msg: AIMessage) -> AIMessage:
            """移除 tool_calls / invalid 及 content 中的 tool_use 块，仅保留 thinking、text 等。"""
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                kept = [b for b in content if not (isinstance(b, dict) and b.get("type") == "tool_use")]
                content = kept if kept else "（工具调用序列异常，已省略未完成的工具块）"
            elif content is None or content == "":
                content = "（工具调用序列异常，已清理）"

            kwargs = {
                "content": content,
                "tool_calls": [],
                "invalid_tool_calls": [],
            }
            for attr in ("additional_kwargs", "response_metadata", "usage_metadata", "id", "name"):
                value = getattr(msg, attr, None)
                if value is not None:
                    kwargs[attr] = value
            return AIMessage(**kwargs)

        # 收集所有已存在的 tool_call_id 回复（用于末尾外部工具启发式）
        answered_ids = set()
        for msg in messages:
            if isinstance(msg, ToolMessage) and hasattr(msg, "tool_call_id"):
                tid = getattr(msg, "tool_call_id", None)
                if tid:
                    answered_ids.add(tid)

        # --- 第一轮：从末尾截断悬空的 tool_calls ---
        clean = list(messages)
        while clean:
            last = clean[-1]
            if not isinstance(last, AIMessage):
                break
            all_tc = TeamAgent._message_tool_calls(last)
            if not all_tc:
                break
            pending_ids = {tc["id"] for tc in all_tc if tc.get("id")}
            if pending_ids.issubset(answered_ids):
                break
            # 检查未回复的是否全属于外部工具
            unanswered = [tc for tc in all_tc if tc.get("id") not in answered_ids]
            if external_tool_names and all(tc.get("name") in external_tool_names for tc in unanswered):
                break
            _log.warning("sanitize: 截断末尾悬空 AIMessage, tool_calls=%s",
                         [tc.get("name") for tc in all_tc])
            clean.pop()

        # --- 第二轮：按「紧跟的 ToolMessage」校验；失败则剥离并记下待删除的 tool_call_id ---
        drop_tool_ids: set[str] = set()
        result: list = []
        for i, msg in enumerate(clean):
            if not isinstance(msg, AIMessage):
                result.append(msg)
                continue
            all_tc = TeamAgent._message_tool_calls(msg)
            if not all_tc:
                result.append(msg)
                continue
            pending_ids = {tc["id"] for tc in all_tc if tc.get("id")}
            if not pending_ids:
                result.append(msg)
                continue
            got_ids: set[str] = set()
            j = i + 1
            while j < len(clean) and isinstance(clean[j], ToolMessage):
                tid = getattr(clean[j], "tool_call_id", "") or ""
                if tid:
                    got_ids.add(tid)
                j += 1
            if pending_ids.issubset(got_ids):
                result.append(msg)
                continue
            _log.warning(
                "sanitize: AIMessage 工具调用未紧跟 ToolMessage（或 content 内残留 tool_use），"
                "剥离 tools=%s, got=%s, pending=%s",
                [tc.get("name") for tc in all_tc],
                got_ids,
                pending_ids,
            )
            drop_tool_ids.update(pending_ids)
            result.append(_strip_ai_tool_blocks(msg))

        # --- 第三轮：移除孤儿 ToolMessage（其 id 属于已剥离的未完成调用）---
        if drop_tool_ids:
            result = [
                m for m in result
                if not (
                    isinstance(m, ToolMessage)
                    and (getattr(m, "tool_call_id", "") or "") in drop_tool_ids
                )
            ]

        # --- 第四轮：清除仍然游离的 ToolMessage ---
        # Claude/Anthropic 要求 tool_result 必须紧跟其 tool_use 所在 assistant 消息。
        # 如果取消/清理只处理了 tool_calls 或 content.tool_use 的一边，可能留下不属于
        # 前一条 AIMessage 工具调用批次的孤儿 ToolMessage；这些会触发 2013。
        final: list = []
        expected_tool_ids: set[str] = set()
        for msg in result:
            if isinstance(msg, AIMessage):
                final.append(msg)
                expected_tool_ids = {
                    tc.get("id", "")
                    for tc in TeamAgent._message_tool_calls(msg)
                    if tc.get("id")
                }
                continue
            if isinstance(msg, ToolMessage):
                tid = getattr(msg, "tool_call_id", "") or ""
                if not tid:
                    final.append(msg)
                elif tid in expected_tool_ids:
                    final.append(msg)
                    expected_tool_ids.discard(tid)
                else:
                    _log.warning("sanitize: 移除孤儿 ToolMessage, tool_call_id=%s", tid)
                continue
            final.append(msg)
            expected_tool_ids = set()

        return final

    @staticmethod
    def _strip_multimodal_parts(messages: list) -> list:
        """
        将所有 HumanMessage 中的多模态 content（list 格式）转为纯文本。
        - type:"text" 的 part 保留文本
        - type:"file" 中的媒体文件（视频/音频）保留原始 file part
        - type:"file" 中的其他文件替换为 "[用户上传了文件: {filename}]"
        - type:"image_url" 替换为 "[用户上传了图片]"
        - type:"input_audio" 替换为 "[用户发送了语音]"
        - 其他未知 type 丢弃
        """
        _MEDIA_EXTS = {".avi", ".mp4", ".mkv", ".mov", ".webm", ".mp3", ".wav", ".flac", ".ogg", ".aac"}

        result = []
        for msg in messages:
            if isinstance(msg, HumanMessage) and isinstance(msg.content, list):
                new_parts = []  # 可能混合 str 和 dict（保留的 file part）
                for part in msg.content:
                    if not isinstance(part, dict):
                        new_parts.append(str(part))
                        continue
                    ptype = part.get("type", "")
                    if ptype == "text":
                        new_parts.append(part.get("text", ""))
                    elif ptype == "file":
                        fname = part.get("file", {}).get("filename", "附件")
                        ext = os.path.splitext(fname)[1].lower() if fname else ""
                        if ext in _MEDIA_EXTS:
                            # 媒体文件：保留原始 file part
                            new_parts.append(part)
                        else:
                            new_parts.append(f"[用户上传了文件: {fname}]")
                    elif ptype == "image_url":
                        new_parts.append("[用户上传了图片]")
                    elif ptype == "input_audio":
                        new_parts.append("[用户发送了语音]")

                # 如果只剩纯文本，合并为 str；否则保持 list 格式
                has_dict = any(isinstance(p, dict) for p in new_parts)
                if has_dict:
                    # 保持 content list 格式，把纯文本 wrap 成 text part
                    content_list = []
                    for p in new_parts:
                        if isinstance(p, dict):
                            content_list.append(p)
                        elif p:
                            content_list.append({"type": "text", "text": p})
                    result.append(msg.model_copy(update={"content": content_list or [{"type": "text", "text": "(空消息)"}]}))
                else:
                    combined = "\n".join(p for p in new_parts if isinstance(p, str) and p)
                    result.append(msg.model_copy(update={"content": combined or "(空消息)"}))
            else:
                result.append(msg)
        return result

    @staticmethod
    def _tool_message_content_has_image(content: list) -> bool:
        return any(isinstance(part, dict) and part.get("type") == "image" for part in content)

    def get_tools_info(self) -> list[dict]:
        """Return serializable tool metadata list."""
        from webot.engine.tool_catalog import tool_category
        return [{"name": t.name, "description": t.description or "", "category": tool_category(t.name)} for t in self._mcp_tools]

    # ------------------------------------------------------------------
    # Public interface: task management
    # ------------------------------------------------------------------
    async def cancel_task(self, user_id: str) -> bool:
        """Cancel the active streaming task for a user.

        Returns ``True`` if a running task was found and cancelled.
        """
        return await self._task_registry.cancel(user_id)

    def register_task(self, user_id: str, task: asyncio.Task):
        """Register an active streaming task for a user."""
        self._task_registry.register(user_id, task)

    def unregister_task(self, user_id: str):
        """Remove a finished task from the registry."""
        self._task_registry.unregister(user_id)

    def list_active_task_keys(self, prefix: str = "") -> list[str]:
        """Return active task keys, optionally filtered by prefix."""
        return self._task_registry.list_keys(prefix)

    # ------------------------------------------------------------------
    # Thread lock: 防止同一 thread 的并发 checkpoint 操作
    # ------------------------------------------------------------------
    async def get_thread_lock(self, thread_id: str) -> asyncio.Lock:
        """获取指定 thread 的锁（懒创建）。"""
        return await self._thread_state_registry.get_lock(thread_id)

    def add_pending_system_message(self, thread_id: str):
        """标记该 thread 有新的系统触发消息。"""
        self._thread_state_registry.add_pending_system_message(thread_id)

    def consume_pending_system_messages(self, thread_id: str) -> int:
        """消费并返回待处理的系统消息计数，归零。"""
        return self._thread_state_registry.consume_pending_system_messages(thread_id)

    def has_pending_system_messages(self, thread_id: str) -> bool:
        """检查是否有未读的系统触发消息。"""
        return self._thread_state_registry.has_pending_system_messages(thread_id)

    def is_thread_busy(self, thread_id: str) -> bool:
        """检查该 thread 的锁是否被占用（有操作进行中）。"""
        return self._thread_state_registry.is_thread_busy(thread_id)

    def set_thread_busy_source(self, thread_id: str, source: str):
        """设置当前持有锁的来源（"user" 或 "system"）。"""
        self._thread_state_registry.set_thread_busy_source(thread_id, source)

    def clear_thread_busy_source(self, thread_id: str):
        """清除锁来源记录。"""
        self._thread_state_registry.clear_thread_busy_source(thread_id)

    def get_thread_busy_source(self, thread_id: str) -> str:
        """返回锁来源: "user"、"system"、或 "" (未占用)。"""
        return self._thread_state_registry.get_thread_busy_source(thread_id)

    def set_thread_context_usage(self, thread_id: str, tokens: int, budget: int, **kwargs):
        """设置该 thread 的当前上下文用量（kwargs: source / breakdown / cache_read_tokens）。"""
        self._thread_state_registry.set_thread_context_usage(thread_id, tokens, budget, **kwargs)

    def get_thread_context_usage(self, thread_id: str) -> dict[str, int]:
        """返回该 thread 的当前压缩上下文用量。"""
        return self._thread_state_registry.get_thread_context_usage(thread_id)

    def set_thread_last_usage_tokens(self, thread_id: str, input_tokens: int, output_tokens: int = 0) -> None:
        """记录该 thread 上一轮 API 真实 input/output token。"""
        self._thread_state_registry.set_thread_last_usage_tokens(thread_id, input_tokens, output_tokens)

    def get_thread_last_input_tokens(self, thread_id: str) -> int:
        """返回该 thread 上一轮真实输入 token 数；从未记录时返回 0。"""
        return self._thread_state_registry.get_thread_last_input_tokens(thread_id)

    def get_thread_last_context_tokens(self, thread_id: str) -> int:
        """返回该 thread 上一轮真实上下文占用 (input+output)；从未记录时返回 0。"""
        return self._thread_state_registry.get_thread_last_context_tokens(thread_id)

    async def record_context_usage(
        self,
        thread_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        model: str = "",
        context_window: int = 0,
        components: dict[str, int] | None = None,
        accounting: dict | None = None,
        view_key: str | None = None,
    ) -> None:
        """把一次 LLM 调用的 API 实测用量记为该 thread 的上下文占用，并落盘。

        占用 = input + output；稳定输入采用差分归因，其余按本地分词比例分摊。
        测量绑定输入压缩视图，落盘后服务重启也能读回。
        """
        input_tokens = max(0, int(input_tokens or 0))
        if input_tokens <= 0:
            return
        output_tokens = max(0, int(output_tokens or 0))
        cache_read_tokens = max(0, int(cache_read_tokens or 0))
        context_window = max(0, int(context_window or 0))
        tokens = input_tokens + output_tokens
        # The caller computes the stable-message delta before persisting this measurement.
        breakdown = (accounting or {}).get("difference_breakdown") or scale_components(components or {}, input_tokens)
        if not breakdown:
            breakdown = {"messages": input_tokens}
        breakdown["output"] = output_tokens

        registry = self._thread_state_registry
        registry.set_thread_last_usage_tokens(thread_id, input_tokens, output_tokens)
        registry.set_thread_context_usage(
            thread_id,
            tokens,
            context_window or tokens,
            source="api",
            breakdown=breakdown,
            cache_read_tokens=cache_read_tokens,
        )
        record = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "context_window": context_window,
            "model": model or "",
            "breakdown": breakdown,
            "components": components or {},
            "compaction_key": view_key if view_key is not None else compaction_key(get_context_compaction(self._db_path, thread_id)),
            "request_accounting": accounting or {},
        }
        self._measurements[thread_id] = record
        try:
            await asyncio.to_thread(save_context_usage_record, self._db_path, thread_id, record)
        except Exception as exc:
            logger.warning("persist context usage failed for %s: %s", thread_id, exc)

    async def refresh_compacted_context_usage(self, thread_id: str) -> bool:
        await self.restore_context_usage(thread_id)
        record = await asyncio.to_thread(get_context_compaction, self._db_path, thread_id)
        if not record:
            return False
        measured = self._measurements.get(thread_id, {})
        key = compaction_key(record)
        if measured.get("compaction_key") == key:
            return False
        projected = self._projections.get(thread_id)
        if projected and projected[0] == key and projected[1] is measured:
            return False  # This exact summary and API baseline were already projected.
        snapshot = await self.agent_app.aget_state({"configurable": {"thread_id": thread_id}})
        messages = list(snapshot.values.get("messages", [])) if snapshot and snapshot.values else []
        if self._measurements.get(thread_id, {}) is not measured:
            return False  # A newer API measurement arrived while reading the view.
        self.project_compacted_context_usage(thread_id, record, messages)
        return True

    def project_compacted_context_usage(self, thread_id, record, messages):
        """Publish a prepared view without waiting for another history read."""
        measured = self._measurements.get(thread_id, {})
        parts = compacted_components(record, messages)
        local_before = sum((measured.get("components") or {}).values())
        ratio = int(measured.get("input_tokens", 0)) / local_before if local_before else 1
        parts = {name: round(value * ratio) for name, value in parts.items()}
        for name in ("system_prompt", "tools"):
            parts[name] = int((measured.get("breakdown") or {}).get(name, 0))
        previous = self.get_thread_context_usage(thread_id)
        self.set_thread_context_usage(thread_id, sum(parts.values()), previous.get("budget", 0),
            source="estimate", breakdown=parts, cache_read_tokens=0)
        self._projections[thread_id] = (compaction_key(record), measured)

    async def restore_context_usage(self, thread_id: str) -> bool:
        """内存里没有真值时，从磁盘读回上一轮 API 用量；每个 thread 每个进程只读一次库。"""
        registry = self._thread_state_registry
        if not registry.claim_context_usage_restore(thread_id):
            return False
        try:
            record = await asyncio.to_thread(get_context_usage_record, self._db_path, thread_id)
        except Exception as exc:
            logger.warning("load context usage failed for %s: %s", thread_id, exc)
            return False
        record = record or {}
        self._measurements[thread_id] = record
        input_tokens = max(0, int(record.get("input_tokens") or 0))
        if input_tokens <= 0:
            return False
        output_tokens = max(0, int(record.get("output_tokens") or 0))
        model = str(record.get("model") or "")
        if model and not registry.get_thread_model(thread_id):
            registry.set_thread_model(thread_id, model)
        context_window = int(record.get("context_window") or 0) or infer_model_context_window(model or None)
        tokens = input_tokens + output_tokens
        registry.set_thread_last_usage_tokens(thread_id, input_tokens, output_tokens)
        registry.set_thread_context_usage(
            thread_id,
            tokens,
            context_window or tokens,
            source="api",
            breakdown=dict(record.get("breakdown") or {}),
            cache_read_tokens=int(record.get("cache_read_tokens") or 0),
        )
        return True

    def get_thread_model(self, thread_id: str) -> str:
        """返回该 thread 上一次推理实际使用的模型名（用于静态路径反推 budget）。"""
        return self._thread_state_registry.get_thread_model(thread_id)

    def get_all_thread_status(self, prefix: str) -> dict[str, dict]:
        """返回指定前缀下所有已知 thread 的状态。"""
        return self._thread_state_registry.get_all_thread_status(prefix)
