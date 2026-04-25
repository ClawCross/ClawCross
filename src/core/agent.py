import os
import re
import json
import copy
import asyncio
import contextlib
import sys
import logging
from typing import Annotated, TypedDict, Optional

# LangGraph related
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages

# Model related
from langchain_openai import ChatOpenAI
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import ToolNode

from core.agent_runtime_state import TaskRegistry, ThreadStateRegistry
from webot.policy import (
    ToolPolicyDecision,
    get_tool_policy,
    run_tool_policy_hooks,
)
from webot.context import (
    budget_user_messages,
    budget_tool_messages,
    compact_history_messages,
    render_runtime_context_block,
)
from webot.memory import ensure_memory_state
from webot.skills import build_skills_prompt
from webot.soul import build_soul_prompt
from webot.workflow_prompt import build_team_workflow_prompt
from webot.trajectory import save_trajectory
from utils.context_references import expand_context_references
from utils.routed_checkpoint_saver import ThreadRoutedAsyncSqliteSaver
from services.smart_routing import resolve_turn_route
from webot.permission_context import (
    create_or_reuse_permission_request,
    resolve_permission_context,
)
from webot.profiles import get_agent_profile, parse_subagent_session_id, render_profile_system_prompt
from webot.runtime import (
    build_session_mode_message,
    build_turn_limit_message,
    filter_tools_for_mode,
    normalize_session_mode,
    resolve_max_turns,
    should_stop_for_turn_limit,
)
from webot.bridge import get_bridge_runtime_payload
from webot.buddy import serialize_buddy_state
from webot.runtime_store import (
    get_session_state,
    get_session_mode,
    list_inbox_messages,
    list_runtime_artifacts,
    list_runs_for_session,
    get_session_plan,
    get_session_todos,
    list_tool_approvals,
    list_verification_records,
    update_tool_approval_status,
)
from webot.voice import get_voice_state as get_webot_voice_state
from webot.workspace import describe_session_workspace

# --- New feature modules (ported from Claude Code / openclaw / oh-my-codex) ---
from core.streaming_tool_executor import (
    StreamingToolExecutor, get_streaming_executor,
    classify_tool_access, ToolAccessMode, ToolExecutionResult,
)
from utils.token_budget import get_session_budget
from utils.context_compressor import compress_context
from utils.context_limits import resolve_history_message_limits, resolve_history_token_budget
from utils.cache_boundary import SystemPromptCacheManager
from utils.bash_safety import analyze_command, is_command_blocked, RiskLevel
from utils.logging_utils import get_logger
from core.lazy_tool_discovery import LazyToolRegistry
from core.agent_orchestrator import (
    create_fork, complete_fork, get_fork, list_forks, ForkMode,
    start_coordinator_run, advance_coordinator_phase, get_coordinator_run,
    create_council_session, submit_council_vote, evaluate_council_consensus,
)
from utils.cost_tracker import get_cost_tracker
from core.workflow_engines import (
    get_ralph_loop, create_ralph_loop, get_ralph_prompt,
    create_deep_interview, get_interview_prompt,
    get_autopilot, AutopilotConfig,
    check_context_gate,
    get_hud, update_hud,
    fork_session, get_session_fork,
)
from services.notification_system import (
    send_notification, get_notifications, NotificationLevel,
    run_ttl_cleanup, register_ttl,
    get_pending_model_swap, consume_model_swap,
    save_session_checkpoint, get_session_checkpoint, build_resume_prompt,
    create_broadcast,
)

logger = get_logger("agent")


# 调试导出（已关闭）：原 _maybe_debug_dump_llm_payload_for_minimax 在 CLAWCROSS_DEBUG_LLM_PAYLOAD=1 时
# 将 ainvoke 前消息写入 data/debug_llm_payload_last.json；实现已从默认分支移除，需排障时查 git 历史。

# --- Tools that need automatic username injection ---
USER_INJECTED_TOOLS = {
    # File management tools
    "list_files", "read_file", "write_file", "append_file", "delete_file",
    # Command execution tools
    "run_command", "run_python_code",
    "start_background_command", "get_background_command_status",
    "read_background_command_output", "cancel_background_command",
    # Alarm management tools
    "get_current_time", "add_alarm", "list_alarms", "delete_alarm",
    # Telegram push notification tools
    "set_telegram_chat_id", "send_telegram_message", "get_telegram_status", "remove_telegram_config",
    # OASIS forum tools
    "start_new_oasis", "check_oasis_discussion", "cancel_oasis_discussion",
    "list_oasis_topics",
    "list_oasis_sessions",
    "list_oasis_experts", "add_oasis_expert", "update_oasis_expert", "delete_oasis_expert",
    "set_oasis_workflow", "list_oasis_workflows", "list_oasis_python_workflows",
    "check_oasis_python_run", "yaml_to_layout",
    # Session management tools
    "list_sessions", "get_current_session",
    # LLM API access tools
    "call_llm_api", "send_internal_message",
    # Group chat tools
    "send_to_group", "send_private_cli",
    # WeBot subagent tools
    "list_webot_agent_profiles", "spawn_subagent", "list_subagents",
    "send_subagent_message", "get_subagent_history", "cancel_subagent", "delete_subagent",
    "list_webot_workflow_presets", "apply_webot_workflow_preset",
    "session_send_to", "session_inbox", "session_deliver_inbox",
    "claude_session_send_to", "claude_session_inbox", "claude_session_deliver_inbox",
    "write_session_plan", "read_session_plan", "clear_session_plan",
    "write_session_todos", "read_session_todos", "clear_session_todos",
    "record_verification", "list_verifications", "run_verification",
    "list_tool_approvals",
    "ultraplan_start", "ultraplan_status",
    "ultrareview_start", "ultrareview_status",
    "enter_plan_mode", "exit_plan_mode", "get_session_mode",
    # Self-evolution tools
    "skill_manage", "skill_view", "skill_list",
    "skill_evolution_report", "skill_evolution_apply",
    "search_sessions", "get_insights", "get_trajectory_stats",
    "manage_personality",
}

# Tools that need session_id auto-injected (in addition to username)
SESSION_INJECTED_TOOLS = {
    "list_files": "session_id",
    "read_file": "session_id",
    "write_file": "session_id",
    "append_file": "session_id",
    "delete_file": "session_id",
    "run_command": "session_id",
    "run_python_code": "session_id",
    "start_background_command": "session_id",
    "get_background_command_status": "session_id",
    "read_background_command_output": "session_id",
    "cancel_background_command": "session_id",
    "add_alarm": "session_id",
    "start_new_oasis": "notify_session",
    "get_current_session": "current_session_id",
    "send_telegram_message": "source_session",
    "send_internal_message": "source_session",
    "send_to_group": "source_session",
    "send_private_cli": "source_session",
    "spawn_subagent": "parent_session",
    "send_subagent_message": "source_session",
    "cancel_subagent": "source_session",
    "delete_subagent": "source_session",
    "apply_webot_workflow_preset": "source_session",
    "write_session_plan": "source_session",
    "read_session_plan": "source_session",
    "clear_session_plan": "source_session",
    "write_session_todos": "source_session",
    "read_session_todos": "source_session",
    "clear_session_todos": "source_session",
    "record_verification": "source_session",
    "list_verifications": "source_session",
    "run_verification": "source_session",
    "list_tool_approvals": "source_session",
    "session_send_to": "source_session",
    "session_inbox": "source_session",
    "session_deliver_inbox": "source_session",
    "claude_session_send_to": "source_session",
    "claude_session_inbox": "source_session",
    "claude_session_deliver_inbox": "source_session",
    "ultraplan_start": "source_session",
    "ultraplan_status": "source_session",
    "ultrareview_start": "source_session",
    "ultrareview_status": "source_session",
    "enter_plan_mode": "source_session",
    "exit_plan_mode": "source_session",
    "get_session_mode": "source_session",
    # Self-evolution tools
    "skill_evolution_report": "session_id",
    "skill_evolution_apply": "session_id",
    "search_sessions": "session_id",
}

TEAM_INJECTED_TOOLS: frozenset[str] = frozenset({
    "add_alarm",
    "skill_manage",
    "skill_view",
    "skill_list",
    "skill_evolution_report",
    "skill_evolution_apply",
})

# Session-related tool args that must always match runtime session (model cannot override).
SESSION_FORCE_INJECTED_TOOLS: frozenset[str] = frozenset({
    "send_to_group",
    "send_private_cli",
    "send_internal_message",
    "send_telegram_message",
    "spawn_subagent",
    "send_subagent_message",
    "cancel_subagent",
    "delete_subagent",
    "session_send_to",
    "session_inbox",
    "session_deliver_inbox",
    "claude_session_send_to",
    "claude_session_inbox",
    "claude_session_deliver_inbox",
    "enter_plan_mode",
    "exit_plan_mode",
    "get_session_mode",
    "get_current_session",
    "start_new_oasis",
})

# --- State definition ---
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
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


class UserAwareToolNode:
    """
    Custom tool node:
    1. Reads thread_id from RunnableConfig, auto-injects as username for file/command tools
    2. Intercepts calls to disabled tools at runtime, returns error ToolMessage
    """
    def __init__(self, tools, get_mcp_tools_fn, find_internal_session_meta_fn=None):
        self.tool_node = ToolNode(tools)
        self._get_mcp_tools = get_mcp_tools_fn
        self._find_internal_session_meta_fn = find_internal_session_meta_fn

    def _resolve_internal_session_meta(self, user_id: str, session_id: str) -> dict | None:
        resolver = self._find_internal_session_meta_fn
        if resolver is None or not user_id or not session_id:
            return None
        try:
            return resolver(user_id, session_id)
        except Exception as exc:
            print(f">>> [tools] ⚠️ resolve internal session meta failed: {exc}")
            return None

    @staticmethod
    def _format_policy_block_message(
        tool_name: str,
        reason: str,
        requires_approval: bool,
        approval_id: str = "",
    ) -> str:
        if requires_approval:
            approval_hint = f"\napproval_id: {approval_id}" if approval_id else ""
            return (
                f"⏸️ 工具 '{tool_name}' 当前需要人工批准。\n"
                f"原因：{reason or '当前 tool approval policy 未自动放行该调用。'}\n\n"
                f"如需继续，请先批准该请求后再重试。{approval_hint}"
            )
        return (
            f"❌ 工具 '{tool_name}' 被当前 WeBot tool policy 拒绝。\n"
            f"原因：{reason or '该工具调用不满足当前策略要求。'}"
        )

    async def __call__(self, state, config: RunnableConfig):
        # Get user_id directly from state (injected by mainagent) instead of
        # parsing thread_id, because user_id itself may contain the separator.
        user_id = state.get("user_id") or "anonymous"
        session_id = state.get("session_id") or "default"
        runtime_mode_name = normalize_session_mode(get_session_mode(user_id, session_id).get("mode"))

        last_message = state["messages"][-1]
        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            return {"messages": []}

        # Get currently enabled tool set
        enabled_names = state.get("enabled_tools")
        if enabled_names is not None:
            enabled_set = set(enabled_names)
        else:
            enabled_set = None  # None = all allowed

        # Separate blocked and allowed calls
        modified_message = copy.deepcopy(last_message)
        blocked_calls: list[tuple[dict, str, bool, str]] = []
        allowed_calls = []
        allowed_call_meta: dict[str, tuple[str, dict, object, str]] = {}
        for tc in modified_message.tool_calls:
            if runtime_mode_name == "plan":
                requested_agent_type = str(tc.get("args", {}).get("agent_type") or "").strip().lower()
                if tc["name"] in PLAN_MODE_BLOCKED_TOOLS or (
                    tc["name"] == "spawn_subagent" and requested_agent_type in {"general", "coder"}
                ):
                    blocked_calls.append((
                        tc,
                        "当前会话处于 plan 模式。请先完成调研、计划和 todo，再退出 plan 模式后执行改动。",
                        False,
                        "",
                    ))
                    continue
            elif runtime_mode_name == "review":
                if tc["name"] in REVIEW_MODE_BLOCKED_TOOLS:
                    blocked_calls.append((
                        tc,
                        "当前会话处于 review 模式。请保持只读审查，避免直接修改文件或外部状态。",
                        False,
                        "",
                    ))
                    continue
            if enabled_set is not None and tc["name"] not in enabled_set:
                blocked_calls.append((
                    tc,
                    "该工具当前未在会话的 enabled_tools 列表中。",
                    False,
                    "",
                ))
                print(f">>> [tools] 🚫 拦截禁用工具调用: {tc['name']}")
            else:
                # --- Bash safety check (deny invariants) ---
                if tc["name"] == "run_command":
                    cmd_text = str(tc.get("args", {}).get("command", ""))
                    if cmd_text and is_command_blocked(cmd_text):
                        cmd_analysis = analyze_command(cmd_text)
                        blocked_calls.append((
                            tc,
                            f"DENY INVARIANT: {'; '.join(cmd_analysis.reasons)}",
                            False,
                            "",
                        ))
                        print(f">>> [tools] 🛡️ bash safety blocked: {cmd_text[:80]}")
                        continue
                    # High-risk commands need approval
                    cmd_analysis = analyze_command(cmd_text)
                    if cmd_analysis.risk_level == RiskLevel.HIGH:
                        blocked_calls.append((
                            tc,
                            f"高风险命令需要人工批准: {'; '.join(cmd_analysis.reasons)}",
                            True,
                            "",
                        ))
                        print(f">>> [tools] ⚠️ bash high-risk: {cmd_text[:80]}")
                        continue

                if tc["name"] in USER_INJECTED_TOOLS:
                    tc["args"]["username"] = user_id
                if tc["name"] in TEAM_INJECTED_TOOLS and not tc["args"].get("team"):
                    session_meta = self._resolve_internal_session_meta(user_id, session_id)
                    if session_meta and session_meta.get("team"):
                        tc["args"]["team"] = session_meta["team"]
                # Auto-inject session-related args; SESSION_FORCE_INJECTED_TOOLS always overwrites model args.
                if tc["name"] in SESSION_INJECTED_TOOLS:
                    param_name = SESSION_INJECTED_TOOLS[tc["name"]]
                    if tc["name"] in SESSION_FORCE_INJECTED_TOOLS:
                        tc["args"][param_name] = session_id
                    elif not tc["args"].get(param_name):
                        tc["args"][param_name] = session_id

                permission = resolve_permission_context(
                    user_id=user_id,
                    session_id=session_id,
                    tool_name=tc["name"],
                    args=tc["args"],
                )
                base_decision = ToolPolicyDecision(
                    allowed=permission.allowed,
                    requires_approval=permission.requires_approval,
                    reason=permission.reason,
                    matched_rule=permission.matched_rule,
                )
                hook_outcome = run_tool_policy_hooks(
                    permission.policy,
                    event="before",
                    user_id=user_id,
                    session_id=session_id,
                    tool_name=tc["name"],
                    args=tc["args"],
                    decision=base_decision,
                )
                tc["args"] = dict(hook_outcome.args)
                if hook_outcome.decision is not None:
                    final_decision = hook_outcome.decision
                else:
                    refreshed_permission = resolve_permission_context(
                        user_id=user_id,
                        session_id=session_id,
                        tool_name=tc["name"],
                        args=tc["args"],
                        policy=permission.policy,
                    )
                    permission = refreshed_permission
                    final_decision = ToolPolicyDecision(
                        allowed=permission.allowed,
                        requires_approval=permission.requires_approval,
                        reason=permission.reason,
                        matched_rule=permission.matched_rule,
                    )
                approval_id = permission.approval.approval_id if permission.approval else ""
                if not final_decision.allowed:
                    if getattr(final_decision, "requires_approval", False):
                        approval = permission.approval or create_or_reuse_permission_request(
                            user_id=user_id,
                            session_id=session_id,
                            tool_name=tc["name"],
                            args=tc["args"],
                            reason=getattr(final_decision, "reason", "") or permission.reason,
                        )
                        approval_id = approval.approval_id
                        try:
                            run_tool_policy_hooks(
                                permission.policy,
                                event="permission_request",
                                user_id=user_id,
                                session_id=session_id,
                                tool_name=tc["name"],
                                args=tc["args"],
                                decision=final_decision,
                                result={"approval_id": approval_id},
                            )
                        except Exception as exc:
                            print(f">>> [tools] ⚠️ tool policy permission_request hook failed: {exc}")
                    else:
                        try:
                            run_tool_policy_hooks(
                                permission.policy,
                                event="deny",
                                user_id=user_id,
                                session_id=session_id,
                                tool_name=tc["name"],
                                args=tc["args"],
                                decision=final_decision,
                            )
                        except Exception as exc:
                            print(f">>> [tools] ⚠️ tool policy deny hook failed: {exc}")
                    blocked_calls.append(
                        (
                            tc,
                            getattr(final_decision, "reason", "") or permission.reason,
                            getattr(final_decision, "requires_approval", False),
                            approval_id,
                        )
                    )
                    print(f">>> [tools] 🚫 policy blocked: {tc['name']} reason={permission.reason}")
                    continue
                try:
                    if permission.approval is not None and permission.approval.status == "approved":
                        update_tool_approval_status(
                            permission.approval.approval_id,
                            user_id,
                            status="used",
                            resolution_reason=permission.approval.resolution_reason,
                        )
                except Exception:
                    pass
                allowed_calls.append(tc)
                allowed_call_meta[tc["id"]] = (tc["name"], dict(tc["args"]), permission.policy, approval_id)
                print(f">>> [tools] ✅ 调用工具: {tc['name']}")

        result_messages = []

        # For blocked tools, return error ToolMessages directly
        for tc, reason, requires_approval, approval_id in blocked_calls:
            result_messages.append(
                ToolMessage(
                    content=self._format_policy_block_message(
                        tc["name"],
                        reason,
                        requires_approval,
                        approval_id,
                    ),
                    tool_call_id=tc["id"],
                )
            )

        # For allowed tools, execute normally via ToolNode
        if allowed_calls:
            modified_message.tool_calls = allowed_calls
            modified_state = {**state, "messages": state["messages"][:-1] + [modified_message]}
            try:
                tool_result = await self.tool_node.ainvoke(modified_state, config)
            except Exception as exc:
                error_text = str(exc).strip() or exc.__class__.__name__
                logger.exception(
                    "tool execution failed user=%s session=%s tools=%s error=%s",
                    user_id,
                    session_id,
                    [tc["name"] for tc in allowed_calls],
                    error_text,
                )
                for tc in allowed_calls:
                    meta = allowed_call_meta.get(tc["id"])
                    if meta is None:
                        continue
                    tool_name, tool_args, tool_policy, _approval_id = meta
                    with contextlib.suppress(Exception):
                        run_tool_policy_hooks(
                            tool_policy,
                            event="after_error",
                            user_id=user_id,
                            session_id=session_id,
                            tool_name=tool_name,
                            args=tool_args,
                            result=error_text,
                        )
                    result_messages.append(
                        ToolMessage(
                            content=(
                                f"工具调用失败: {tool_name}\n"
                                f"错误: {error_text}\n"
                                "这通常表示传入参数不符合工具定义，"
                                "请检查必填字段、字段名和参数类型后重试。"
                            ),
                            tool_call_id=tc["id"],
                            name=tool_name,
                        )
                    )
                return {"messages": result_messages}
            tool_messages = tool_result.get("messages", [])
            result_messages.extend(tool_messages)
            for msg in tool_messages:
                tool_call_id = getattr(msg, "tool_call_id", "")
                meta = allowed_call_meta.get(tool_call_id)
                if meta is None:
                    continue
                tool_name, tool_args, tool_policy, approval_id = meta
                result_text = getattr(msg, "content", "")
                try:
                    run_tool_policy_hooks(
                        tool_policy,
                        event="after",
                        user_id=user_id,
                        session_id=session_id,
                        tool_name=tool_name,
                        args=tool_args,
                        result=result_text,
                    )
                except Exception as exc:
                    print(f">>> [tools] ⚠️ tool policy after hook failed: {exc}")
                try:
                    result_preview = str(result_text)
                    if result_preview.startswith(("❌", "⚠️")):
                        run_tool_policy_hooks(
                            tool_policy,
                            event="after_error",
                            user_id=user_id,
                            session_id=session_id,
                            tool_name=tool_name,
                            args=tool_args,
                            result=result_text,
                        )
                except Exception as exc:
                    print(f">>> [tools] ⚠️ tool policy after_error hook failed: {exc}")

        return {"messages": result_messages}


class TeamAgent:
    """
    Encapsulates the full LangGraph agent: MCP tool loading, graph building,
    invoke/stream interface, task & tool-state management.
    """

    def __init__(self, src_dir: str, db_path: str):
        """
        Args:
            src_dir:  Path to src/ directory (where mcp_*.py live)
            db_path:  Path to SQLite checkpoint database
        """
        self._src_dir = src_dir
        self._db_path = db_path

        # Populated during startup
        self._mcp_tools: list = []
        self._agent_app = None
        self._mcp_client: Optional[MultiServerMCPClient] = None
        self._memory = None
        self._memory_ctx = None

        # Per-thread tool-state cache
        self._task_registry = TaskRegistry()
        self._tool_state_cache: dict[str, frozenset[str]] = {}

        # Per-thread lock: 防止 system_trigger 和用户对话并发操作同一 checkpoint
        self._thread_state_registry = ThreadStateRegistry()

        # --- New feature instances ---
        self._streaming_executor = get_streaming_executor()
        self._tool_registry = LazyToolRegistry()
        self._cache_manager = SystemPromptCacheManager()

        # 启动时一次性加载 prompt 模板
        self._prompts = self._load_prompts()

    # ------------------------------------------------------------------
    # Prompt loader (启动时读取一次)
    # ------------------------------------------------------------------
    @staticmethod
    def _load_prompts() -> dict[str, str]:
        """从 data/prompts/ 加载所有 prompt 模板文件，服务启动时调用一次。"""
        prompts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "prompts")
        prompt_files = {
            "base_system": "base_system.txt",
            "base_system_subagent": "base_system_subagent.txt",
            "system_trigger": "system_trigger.txt",
            "tool_status": "tool_status.txt",
            "group_chat_rules": "group_chat_rules.txt",
            "group_chat_small": "group_chat_small.txt",
            "group_chat_large": "group_chat_large.txt",
            "private_chat_rules": "private_chat_rules.txt",
        }
        loaded = {}
        for key, filename in prompt_files.items():
            filepath = os.path.join(prompts_dir, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    loaded[key] = f.read().strip()
                print(f"[prompts] ✅ 已加载 {filename}")
            except FileNotFoundError:
                print(f"[prompts] ⚠️ 未找到 {filepath}，将使用内置默认值")
                loaded[key] = ""

        # 记录 user_files 根目录路径（用户画像存在各用户目录下）
        loaded["_user_files_dir"] = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "user_files"
        )

        return loaded

    def _get_user_profile(self, user_id: str) -> str:
        """从 data/user_files/{user_id}/user_profile.txt 读取用户画像。"""
        user_files_dir = self._prompts.get("_user_files_dir", "")
        fpath = os.path.join(user_files_dir, user_id, "user_profile.txt")
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                return f.read().strip()
        except FileNotFoundError:
            return ""

    def _get_user_skills(self, user_id: str, team: str = "") -> str:
        """
        从 webot.skills 读取用户的 managed skills，
        并返回格式化的 skill 信息字符串。
        即使没有 skill，也会返回位置信息。
        """
        from webot.skills import list_skills

        user_files_dir = self._prompts.get("_user_files_dir", "")
        skills_dir = os.path.join(user_files_dir, user_id, "skills")
        team_skills = list_skills(user_id, team=team) if team else []
        personal_skills = list_skills(user_id)

        # 格式化 skill 信息（即使为空也返回位置信息）
        skill_lines = ["\n【用户技能列表】"]
        skill_lines.append(f"技能文件目录位置: {skills_dir}")
        if team:
            skill_lines.append(f"团队技能目录位置: {os.path.join(user_files_dir, user_id, 'teams', team, 'skills')}")

        def _append_section(title: str, items: list[dict]) -> None:
            if not items:
                return
            skill_lines.append(title)
            for skill in items:
                if not isinstance(skill, dict):
                    continue
                skill_name = skill.get("name", "未命名技能")
                skill_desc = skill.get("description", "无描述")
                skill_file = skill.get("path", "")
                skill_lines.append(f"  - {skill_name}: {skill_desc}")
                if skill_file:
                    skill_lines.append(f"    文件: {skill_file}")

        if team:
            _append_section("团队技能：", team_skills)
            _append_section("共享技能：", personal_skills)
        elif personal_skills:
            _append_section("可用技能：", personal_skills)

        if team_skills or personal_skills:
            skill_lines.append("如需使用某个技能，请优先使用 skill_view 查看完整内容。")
        else:
            skill_lines.append("当前暂无已注册的技能。")
            skill_lines.append("如需添加技能，请使用 skill_manage(action='create') 创建。")

        return "\n".join(skill_lines)

    def _find_internal_session_meta(self, user_id: str, session_id: str) -> dict | None:
        """Resolve an internal agent session to its stored meta and owning team.

        Returns {"team", "name", "tag"} or None if the session is not registered
        in any internal_agents.json file.
        """
        if not user_id or not session_id:
            return None

        user_files_dir = self._prompts.get("_user_files_dir", "")
        if not user_files_dir:
            return None

        user_root = os.path.join(user_files_dir, user_id)
        candidates: list[tuple[str, str]] = []

        teams_dir = os.path.join(user_root, "teams")
        if os.path.isdir(teams_dir):
            for team_name in sorted(os.listdir(teams_dir)):
                team_root = os.path.join(teams_dir, team_name)
                if os.path.isdir(team_root):
                    candidates.append((team_name, os.path.join(team_root, "internal_agents.json")))

        candidates.append(("", os.path.join(user_root, "internal_agents.json")))

        for team_name, path in candidates:
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue

            if not isinstance(data, list):
                continue

            for item in data:
                if not isinstance(item, dict):
                    continue
                if item.get("session", "") != session_id:
                    continue
                return {
                    "team": team_name,
                    "name": (item.get("name") or "").strip(),
                    "tag": (item.get("tag") or "").strip(),
                }
        return None

    @staticmethod
    def _load_json_list(path: str) -> list[dict]:
        """Best-effort JSON list loader used by persona resolution."""
        if not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    @staticmethod
    def _load_agency_prompt_body(prompt_file: str) -> str:
        """Load rich agency persona prompt body without importing oasis.experts."""
        if not prompt_file:
            return ""
        agency_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data",
            "prompts",
            "agency_agents",
        )
        path = os.path.join(agency_dir, prompt_file)
        if not os.path.isfile(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            return ""
        frontmatter = re.match(r'^---\s*\n.*?\n---\s*\n', content, re.DOTALL)
        body = content[frontmatter.end():] if frontmatter else content
        return body.strip()

    def _find_internal_session_expert_config(self, user_id: str, team: str, tag: str) -> dict | None:
        """Resolve tag -> expert config locally, avoiding cross-package imports.

        Lookup order mirrors the important runtime sources:
        team experts -> public experts -> agency experts -> user custom experts.
        """
        if not user_id or not tag:
            return None

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        data_dir = os.path.join(project_root, "data")
        user_files_dir = self._prompts.get("_user_files_dir", "")

        candidates: list[dict] = []

        if team and user_files_dir:
            candidates.extend(
                self._load_json_list(
                    os.path.join(user_files_dir, user_id, "teams", team, "oasis_experts.json")
                )
            )

        candidates.extend(
            self._load_json_list(os.path.join(data_dir, "prompts", "oasis_experts.json"))
        )

        for item in self._load_json_list(os.path.join(data_dir, "prompts", "agency_experts.json")):
            if not isinstance(item, dict):
                continue
            item = dict(item)
            if not item.get("persona"):
                item["persona"] = self._load_agency_prompt_body(item.get("prompt_file", ""))
            candidates.append(item)

        safe_user_id = user_id.replace("/", "_").replace("\\", "_").replace("..", "_")
        candidates.extend(
            self._load_json_list(os.path.join(data_dir, "oasis_user_experts", f"{safe_user_id}.json"))
        )

        for item in candidates:
            if not isinstance(item, dict):
                continue
            if (item.get("tag") or "").strip() != tag:
                continue
            return item
        return None

    def _get_internal_session_persona_prompt(self, user_id: str, session_id: str) -> str:
        """Build a stable identity prompt for internal sessions with a stored tag.

        This lifts tag -> persona resolution into TeamAgent runtime so group chat
        and OASIS regular session invocations share the same session identity.
        """
        meta = self._find_internal_session_meta(user_id, session_id)
        if not meta:
            return ""

        tag = meta.get("tag", "")
        if not tag:
            return ""

        expert_cfg = self._find_internal_session_expert_config(
            user_id,
            meta.get("team", ""),
            tag,
        )
        if not expert_cfg:
            return ""

        persona = (expert_cfg.get("persona") or "").strip()
        if not persona:
            return ""

        display_name = (meta.get("name") or expert_cfg.get("name") or tag or session_id).strip()
        is_rich_persona = "## " in persona or "# " in persona
        if is_rich_persona:
            return (
                "【当前会话身份设定】\n"
                f"你当前会话的唯一身份/角色是「{display_name}」，tag 为 \"{tag}\"。\n"
                "从现在开始，你必须始终以该身份思考、说话和行动。\n"
                "除非用户明确要求你切换角色，否则不得退回通用助手口吻，不得否认自己的身份，不得自称只是普通 AI 助手。\n"
                "当用户询问“你是谁”“你的身份是什么”“你在扮演谁”这类问题时，必须优先依据本身份设定回答。\n\n"
                f"以下是你必须遵守的完整身份与行为指南：\n\n{persona}\n"
            )
        return (
            "【当前会话身份设定】\n"
            f"你当前会话的唯一身份/角色是「{display_name}」，tag 为 \"{tag}\"。"
            "从现在开始，你必须始终按这个身份回应；除非用户明确要求切换，否则不得退回默认通用助手身份。"
            f"{persona}\n"
        )

    def _build_chat_rules(self, state: AgentState) -> str:
        """根据消息上下文动态组装聊天行为规则。

        - 检测最后一条 HumanMessage 是否包含 [私聊] 或 [群聊 xxx 成员数:N] 前缀
        - 私聊：注入私聊规则
        - 群聊：注入群聊通用规则 + 根据成员数选择小群/大群规则
        """
        messages = state.get("messages", [])
        # 从最后一条 HumanMessage 中检测场景标记
        last_human_text = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                last_human_text = m.content if isinstance(m.content, str) else str(m.content)
                break

        # 优先检测私聊标记
        if "[私聊]" in last_human_text:
            return self._prompts.get("private_chat_rules", "")

        # 匹配 [群聊 xxx 成员数:N] 格式
        group_match = re.search(r"\[群聊\s+\S+\s+成员数:(\d+)\]", last_human_text)
        if group_match:
            member_count = int(group_match.group(1))
            # 选择小群或大群规则
            if member_count <= 5:
                size_rules = self._prompts.get("group_chat_small", "")
            else:
                size_rules = self._prompts.get("group_chat_large", "")

            group_rules = self._prompts.get("group_chat_rules", "")
            return group_rules.replace("{size_specific_rules}", size_rules)
        else:
            # 兼容旧格式 [群聊 xxx]（不含成员数）
            if "[群聊" in last_human_text:
                size_rules = self._prompts.get("group_chat_large", "")
                group_rules = self._prompts.get("group_chat_rules", "")
                return group_rules.replace("{size_specific_rules}", size_rules)
            # 默认私聊场景
            return self._prompts.get("private_chat_rules", "")

    def _build_fixed_chat_rules(self) -> str:
        """构造稳定的聊天规则系统提示，避免按每条消息切换 system prompt。"""
        group_rules = self._prompts.get("group_chat_rules", "")
        small_rules = self._prompts.get("group_chat_small", "")
        large_rules = self._prompts.get("group_chat_large", "")
        private_rules = self._prompts.get("private_chat_rules", "")

        rendered_group_rules = group_rules.replace(
            "{size_specific_rules}",
            "\n\n".join(part for part in (small_rules, large_rules) if part),
        )
        return "\n\n".join(part for part in (rendered_group_rules, private_rules) if part)

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
        """Initialize MCP client, load tools, build LangGraph workflow."""
        # 1. Open checkpoint DB
        self._memory_ctx = ThreadRoutedAsyncSqliteSaver(self._db_path)
        self._memory = await self._memory_ctx.__aenter__()

        # 2. Start MCP servers
        python_command = sys.executable
        self._mcp_client = MultiServerMCPClient({
            "scheduler_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "mcp_servers", "scheduler.py")],
                "transport": "stdio",
            },
            "search_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "mcp_servers", "search.py")],
                "transport": "stdio",
            },
            "file_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "mcp_servers", "filemanager.py")],
                "transport": "stdio",
            },
            "commander_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "mcp_servers", "commander.py")],
                "transport": "stdio",
            },
            "oasis_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "mcp_servers", "oasis.py")],
                "transport": "stdio",
            },
            "session_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "mcp_servers", "session.py")],
                "transport": "stdio",
            },
            "telegram_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "mcp_servers", "telegram.py")],
                "transport": "stdio",
            },
            "llmapi_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "mcp_servers", "llmapi.py")],
                "transport": "stdio",
            },
            "webot_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "mcp_servers", "webot.py")],
                "transport": "stdio",
            },
            "self_evolution_service": {
                "command": python_command,
                "args": [os.path.join(self._src_dir, "mcp_servers", "skills.py")],
                "transport": "stdio",
            },
        })

        # 3. Fetch tool definitions (new API: no context manager needed)
        self._mcp_tools = await self._mcp_client.get_tools()

        # 3.5 Register tools in lazy discovery registry (new)
        self._tool_registry.register_tools(self._mcp_tools)
        # Mark essential tools as always-loaded
        self._tool_registry.set_always_loaded({
            "read_file", "write_file", "list_files", "run_command",
            "search_files", "run_python_code",
        })

        # 4. Build LangGraph workflow
        # 收集所有内部 MCP 工具名称，用于条件路由
        self._internal_tool_names = frozenset(t.name for t in self._mcp_tools)

        workflow = StateGraph(AgentState)
        workflow.add_node("chatbot", self._call_model)
        workflow.add_node(
            "tools",
            UserAwareToolNode(
                self._mcp_tools,
                lambda: self._mcp_tools,
                find_internal_session_meta_fn=self._find_internal_session_meta,
            ),
        )
        workflow.add_edge(START, "chatbot")
        workflow.add_conditional_edges("chatbot", self._should_continue)
        workflow.add_edge("tools", "chatbot")

        self._agent_app = workflow.compile(checkpointer=self._memory)

        # 5. Run initial TTL cleanup (new)
        with contextlib.suppress(Exception):
            cleanup_counts = run_ttl_cleanup()
            if cleanup_counts:
                print(f"[startup] TTL cleanup: {cleanup_counts}")

        print("--- Agent 服务已启动，外部定时/用户输入双兼容就绪 ---")
        print(f"    工具注册: {self._tool_registry.tool_count} tools"
              f" ({len(self._tool_registry._always_loaded)} always-loaded)")


    async def shutdown(self):
        """Clean up MCP client and checkpoint DB."""
        if self._memory_ctx:
            try:
                await self._memory_ctx.__aexit__(None, None, None)
            except Exception:
                pass

    async def close_thread_checkpoint(self, thread_id: str) -> None:
        """Close one thread-specific checkpoint handle so its shard can be deleted safely."""
        if not self._memory_ctx:
            return
        try:
            await self._memory_ctx.aclose_thread(thread_id)
        except Exception as e:
            logging.getLogger("agent").warning("close_thread_checkpoint failed for %s: %s", thread_id, e)

    async def purge_checkpoints(self, thread_id: str, keep: int = 1) -> int:
        """
        清理指定 thread 的旧 checkpoint，只保留最近 `keep` 个。
        应在每次 graph 执行完成后调用。
        """
        from utils.checkpoint_repository import purge_old_checkpoints
        try:
            return await purge_old_checkpoints(self._db_path, thread_id, keep=keep)
        except Exception as e:
            import logging
            logging.getLogger("agent").warning("purge_checkpoints failed for %s: %s", thread_id, e)
            return 0

    # ------------------------------------------------------------------
    # Model factory
    # ------------------------------------------------------------------
    # 模型名 -> 厂商 映射已移至 src/llm_factory.py（全局共享）

    @staticmethod
    def _get_model(max_tokens: int | None = None) -> BaseChatModel:
        from services.llm_factory import create_chat_model
        if max_tokens is not None and max_tokens > 0:
            return create_chat_model(max_tokens=max_tokens)
        return create_chat_model()

    # ------------------------------------------------------------------
    # Conditional edge: route internal tools vs external tools vs end
    # ------------------------------------------------------------------
    def _should_continue(self, state: AgentState) -> str:
        """
        条件路由：
        - 无 tool_calls → "end" (正常结束)
        - 所有 tool_calls 都是内部工具 → "tools" (继续内部循环)
        - 存在外部工具调用 → "end" (中断返回 tool_calls 给调用方)
        """
        last_msg = state["messages"][-1]
        if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
            return END

        for tc in last_msg.tool_calls:
            if tc["name"] not in self._internal_tool_names:
                # 发现外部工具调用，中断循环让调用方处理
                print(f">>> [route] 🔀 外部工具调用检测: {tc['name']}，中断返回给调用方")
                return END
        return "tools"

    # ------------------------------------------------------------------
    # Core graph node
    # ------------------------------------------------------------------
    async def _call_model(self, state: AgentState):
        """LangGraph node: invoke LLM with dynamic tool binding & tool-state notification."""

        user_id = state.get("user_id", "__global__")
        session_id = state.get("session_id", "")
        subagent_meta = parse_subagent_session_id(session_id) if session_id else None
        subagent_profile = (
            get_agent_profile(subagent_meta["agent_type"], user_id=user_id) if subagent_meta else None
        )
        is_subagent = bool(subagent_meta) or (session_id.startswith("oasis_") if session_id else False)
        response_max_tokens = state.get("max_tokens")
        effective_max_turns = resolve_max_turns(
            state.get("max_turns"),
            subagent_profile.max_turns if subagent_profile else None,
        )
        current_turn_count = state.get("turn_count") or 0
        runtime_mode = get_session_state(user_id, session_id)
        runtime_mode_name = normalize_session_mode(
            runtime_mode.get("mode") if isinstance(runtime_mode, dict) else getattr(runtime_mode, "mode", "execute")
        )
        runtime_mode_payload = {
            "mode": runtime_mode_name,
            "status": runtime_mode.get("status", "active") if isinstance(runtime_mode, dict) else getattr(runtime_mode, "status", "active"),
            "reason": runtime_mode.get("summary", "") if isinstance(runtime_mode, dict) else getattr(runtime_mode, "summary", ""),
        }
        session_policy = get_tool_policy(user_id)
        if current_turn_count == 0 or len(state.get("messages") or []) <= 1:
            with contextlib.suppress(Exception):
                run_tool_policy_hooks(
                    session_policy,
                    event="session_start",
                    user_id=user_id,
                    session_id=session_id,
                    tool_name="__session__",
                    args={
                        "mode": runtime_mode_name,
                        "is_subagent": is_subagent,
                    },
                )
        last_input_message = state["messages"][-1] if state.get("messages") else None
        if isinstance(last_input_message, HumanMessage):
            with contextlib.suppress(Exception):
                run_tool_policy_hooks(
                    session_policy,
                    event="user_prompt_submit",
                    user_id=user_id,
                    session_id=session_id,
                    tool_name="__session__",
                    args={
                        "mode": runtime_mode_name,
                        "trigger_source": state.get("trigger_source") or "user",
                        "content": str(last_input_message.content)[:500],
                    },
                )

        # Dynamic tool binding based on enabled_tools + external_tools
        all_tools = self._mcp_tools
        enabled_names = state.get("enabled_tools")
        effective_enabled_names = enabled_names
        if effective_enabled_names is None and subagent_profile and subagent_profile.allowed_tools is not None:
            effective_enabled_names = list(subagent_profile.allowed_tools)
        if effective_enabled_names is None:
            effective_enabled_names = [tool.name for tool in all_tools]
        effective_enabled_names = filter_tools_for_mode(list(effective_enabled_names), runtime_mode_name)

        filtered_tools = [t for t in all_tools if t.name in set(effective_enabled_names)]

        # 将外部工具定义（OpenAI function format）转为 LangChain 可绑定的格式
        external_tools_defs = state.get("external_tools") or []
        bind_tools_list: list = list(filtered_tools)
        external_tool_names: set[str] = set()
        for ext_tool in external_tools_defs:
            # 支持 OpenAI 标准格式: {"type":"function","function":{...}} 或简化格式 {"name":...,"parameters":...}
            if ext_tool.get("type") == "function":
                func_def = ext_tool.get("function", {})
            else:
                func_def = ext_tool
            if func_def.get("name"):
                external_tool_names.add(func_def["name"])
                # 以 OpenAI function 格式传入 bind_tools（LangChain 支持 dict 格式）
                bind_tools_list.append({
                    "type": "function",
                    "function": {
                        "name": func_def["name"],
                        "description": func_def.get("description", ""),
                        "parameters": func_def.get("parameters", {"type": "object", "properties": {}}),
                    },
                })

        base_model = self._get_model(response_max_tokens)

        # Per-request LLM override (OASIS SessionExpert, etc.) — read before cheap routing
        llm_ov = state.get("llm_override")

        # --- Smart model routing (new: ported from Hermes Agent) ---
        # Route simple messages to cheaper model if configured
        if not is_subagent and not llm_ov and isinstance(last_input_message, HumanMessage):
            msg_text = str(last_input_message.content) if isinstance(last_input_message.content, str) else ""
            if msg_text:
                cheap_route = resolve_turn_route(msg_text)
                if cheap_route and cheap_route.get("model"):
                    from services.llm_factory import create_chat_model as _create_cheap
                    base_model = _create_cheap(
                        model=cheap_route["model"],
                        provider=cheap_route.get("provider"),
                        api_key=cheap_route.get("api_key"),
                        base_url=cheap_route.get("base_url"),
                        max_tokens=response_max_tokens or 2048,
                    )
                    print(f">>> [routing] cheap model route: {cheap_route['model']} reason={cheap_route.get('routing_reason')}")

        # --- Model hot-swap: check for pending model swap request ---
        model_swap = consume_model_swap(user_id, session_id)
        if model_swap:
            from services.llm_factory import create_chat_model as _create
            base_model = _create(
                model=model_swap.target_model,
                max_tokens=response_max_tokens or 2048,
            )
            print(f">>> [model] 🔄 hot-swap to {model_swap.target_model} reason={model_swap.reason}")

        # Apply llm_override (already read above for routing guard)
        if llm_ov:
            from services.llm_factory import create_chat_model as _create
            base_model = _create(
                model=llm_ov.get("model"),
                api_key=llm_ov.get("api_key"),
                base_url=llm_ov.get("base_url"),
                provider=llm_ov.get("provider"),
                max_tokens=response_max_tokens or 2048,
            )
        elif subagent_profile and subagent_profile.preferred_model:
            from services.llm_factory import create_chat_model as _create
            base_model = _create(
                model=subagent_profile.preferred_model,
                max_tokens=response_max_tokens or 2048,
            )

        llm = base_model.bind_tools(bind_tools_list) if bind_tools_list else base_model

        all_names = sorted(t.name for t in all_tools)
        visible_names = sorted(t.name for t in filtered_tools)
        visible_tool_list_str = ", ".join(visible_names)

        if is_subagent:
            profile_prompt = render_profile_system_prompt(subagent_profile) if subagent_profile else ""
            base_prompt = self._prompts["base_system_subagent"]
            if profile_prompt:
                base_prompt += "\n\n" + profile_prompt
            base_prompt += f"\n\n【可用工具列表】\n{visible_tool_list_str}\n"
        else:
            chat_rules = self._build_fixed_chat_rules()
            base_system_text = self._prompts["base_system"].replace("{chat_rules}", chat_rules)
            base_prompt = (
                base_system_text + "\n\n"
                f"【默认可用工具列表】\n{visible_tool_list_str}\n"
                "以上工具默认全部启用。如果后续有工具状态变更，系统会另行通知。\n"
            )
        base_prompt += f"\n【Session Mode】\n{build_session_mode_message(runtime_mode_name, runtime_mode_payload.get('reason', ''))}\n"

        # Detect tool state change
        user_id = state.get("user_id", "__global__")
        current_enabled = frozenset(visible_names)
        tool_state_key = f"{user_id}#{session_id or 'default'}"
        session_persona_prompt = self._get_internal_session_persona_prompt(
            user_id,
            session_id or "",
        ) if (not is_subagent and user_id and session_id) else ""

        if not is_subagent and session_persona_prompt:
            base_prompt += f"\n{session_persona_prompt}\n"

        session_meta = self._find_internal_session_meta(user_id, session_id or "") if (user_id and session_id) else None
        session_team = (session_meta or {}).get("team", "")

        if (not is_subagent) or (subagent_profile and subagent_profile.include_user_profile):
            # 注入用户专属画像
            user_profile = self._get_user_profile(user_id)
            if user_profile:
                base_prompt += f"\n{user_profile}\n"

        if (not is_subagent) or (subagent_profile and subagent_profile.include_user_skills):
            # 注入用户技能列表（总是显示位置信息）
            base_prompt += self._get_user_skills(user_id, team=session_team) + "\n"

        # --- SOUL.md personality injection (new: ported from Hermes Agent) ---
        if not is_subagent:
            soul_prompt = build_soul_prompt(user_id)
            if soul_prompt:
                base_prompt += soul_prompt

        # --- Self-evolution skills prompt injection (new: ported from Hermes Agent) ---
        if not is_subagent:
            skills_prompt = build_skills_prompt(user_id, team=session_team)
            if skills_prompt:
                base_prompt += skills_prompt + "\n"
            workflow_prompt = build_team_workflow_prompt(user_id, team=session_team)
            if workflow_prompt:
                base_prompt += workflow_prompt + "\n"

        runtime_plan = get_session_plan(user_id, session_id)
        runtime_todos = get_session_todos(user_id, session_id)
        runtime_verifications = list_verification_records(user_id, session_id, limit=5)
        runtime_inbox = [
            {
                "message_id": item.message_id,
                "source_session": item.source_session,
                "source_label": item.source_label,
                "body": item.body,
                "status": item.status,
            }
            for item in list_inbox_messages(user_id, session_id, status="queued", limit=5)
        ]
        runtime_artifacts = [
            {
                "artifact_kind": item.kind,
                "title": item.title,
                "path": item.path,
                "summary": item.summary,
            }
            for item in list_runtime_artifacts(user_id, session_id, limit=5)
        ]
        runtime_runs = [
            {
                "run_id": item.run_id,
                "run_kind": item.run_kind,
                "status": item.status,
                "title": item.title,
            }
            for item in list_runs_for_session(user_id, session_id, limit=5)
        ]
        pending_approvals = [
            {
                "approval_id": approval.approval_id,
                "tool_name": approval.tool_name,
                "status": approval.status,
            }
            for approval in list_tool_approvals(user_id, session_id, status="pending", limit=5)
        ]
        memory_state = ensure_memory_state(user_id, session_id)
        bridge_state = get_bridge_runtime_payload(user_id, session_id)
        voice_state = get_webot_voice_state(user_id, session_id or "default")
        buddy_state = serialize_buddy_state(user_id)
        runtime_context_block = render_runtime_context_block(
            workspace=describe_session_workspace(user_id, session_id),
            mode=runtime_mode_payload,
            plan=runtime_plan,
            todos=runtime_todos,
            verifications=runtime_verifications,
            pending_approvals=pending_approvals,
            inbox=runtime_inbox,
            recent_artifacts=runtime_artifacts,
            recent_runs=runtime_runs,
            memory=memory_state,
            bridge=bridge_state,
            voice=voice_state,
            buddy=buddy_state,
        )
        base_prompt += f"\n{runtime_context_block}\n"

        last_state = self._tool_state_cache.get(tool_state_key)

        tool_status_prompt = ""
        if last_state is not None and current_enabled != last_state:
            all_names_set = set(all_names)
            enabled_set = set(current_enabled)
            disabled_names_set = all_names_set - enabled_set
            tool_status_prompt = self._prompts["tool_status"].format(
                enabled_tools=', '.join(sorted(enabled_set & all_names_set)) if (enabled_set & all_names_set) else '无',
                disabled_tools=', '.join(sorted(disabled_names_set)) if disabled_names_set else '无',
            )
        elif last_state is None and effective_enabled_names is not None:
            all_names_set = set(all_names)
            enabled_set = set(current_enabled)
            disabled_names_set = all_names_set - enabled_set
            if disabled_names_set:
                tool_status_prompt = self._prompts["tool_status"].format(
                    enabled_tools=', '.join(sorted(enabled_set & all_names_set)) if (enabled_set & all_names_set) else '无',
                    disabled_tools=', '.join(sorted(disabled_names_set)),
                )

        # Update cache
        self._tool_state_cache[tool_state_key] = current_enabled

        history_messages = list(state["messages"])

        # --- Context references expansion (new: ported from Hermes Agent) ---
        # Expand @file:, @diff, @staged, @folder:, @git: references in latest user message
        if history_messages and isinstance(history_messages[-1], HumanMessage):
            last_content = history_messages[-1].content
            if isinstance(last_content, str) and "@" in last_content:
                from webot.workspace import resolve_session_workspace
                ws = resolve_session_workspace(user_id, session_id)
                cwd_path = str(ws.cwd or ws.root or "")
                if cwd_path:
                    ctx_result = expand_context_references(
                        last_content,
                        cwd=cwd_path,
                        context_limit=12000 if is_subagent else 24000,
                        allowed_root=cwd_path,
                    )
                    if ctx_result.references_expanded > 0:
                        history_messages[-1] = HumanMessage(content=ctx_result.expanded_message)
                        if ctx_result.warnings:
                            print(f">>> [context-ref] warnings: {ctx_result.warnings}")

        # 清理历史消息中的多模态内容（file/image/audio parts），只保留文本
        # 避免旧的二进制附件在后续轮次反复发送给 LLM 导致上游 API 报错
        # 注意：保留最后一条 HumanMessage 的多模态内容（当前轮用户输入）
        if len(history_messages) > 1:
            history_messages = self._strip_multimodal_parts(history_messages[:-1]) + [history_messages[-1]]

        history_messages = budget_user_messages(
            user_id=user_id,
            session_id=session_id,
            messages=history_messages,
        )
        history_messages = budget_tool_messages(
            user_id=user_id,
            session_id=session_id,
            messages=history_messages,
        )
        history_token_budget = resolve_history_token_budget(is_subagent=is_subagent)
        max_history_messages, preserve_recent_messages = resolve_history_message_limits(
            is_subagent=is_subagent,
            token_budget=history_token_budget,
        )

        with contextlib.suppress(Exception):
            run_tool_policy_hooks(
                session_policy,
                event="pre_compact",
                user_id=user_id,
                session_id=session_id,
                tool_name="__session__",
                args={
                    "mode": runtime_mode_name,
                    "message_count": len(history_messages),
                },
                result={
                    "context_token_budget": history_token_budget,
                },
            )
        history_messages = compact_history_messages(
            history_messages,
            max_messages=max_history_messages,
            preserve_recent=preserve_recent_messages,
            context_token_budget=history_token_budget,
            user_id=user_id,
            session_id=session_id,
        )

        # --- 5-level compression pipeline (new) ---
        token_budget_val = history_token_budget
        history_messages, compression_stats = compress_context(
            history_messages,
            token_budget=token_budget_val,
            preserve_recent=preserve_recent_messages,
        )
        if compression_stats.level_applied != "none":
            print(f">>> [compress] applied level={compression_stats.level_applied} "
                  f"{compression_stats.original_messages}→{compression_stats.final_messages} msgs")

        # --- Token budget tracking (new) ---
        session_budget = get_session_budget(user_id, session_id)
        budget_notice = session_budget.format_budget_notice()
        if budget_notice:
            base_prompt += f"\n{budget_notice}\n"

        # --- Cost tracking notice (new) ---
        cost_tracker = get_cost_tracker(user_id, session_id)
        cost_notice = cost_tracker.format_cost_notice()
        if cost_notice:
            base_prompt += f"\n{cost_notice}\n"

        # --- HUD update (new) ---
        hud = get_hud(user_id, session_id)
        if hud.active:
            hud.update(
                turns_completed=current_turn_count,
                turns_remaining=max(0, (effective_max_turns or 50) - current_turn_count),
            )

        # --- Session resume prompt (new) ---
        if current_turn_count == 0:
            checkpoint = get_session_checkpoint(user_id, session_id)
            if checkpoint:
                resume_prompt = build_resume_prompt(checkpoint)
                base_prompt += f"\n{resume_prompt}\n"

        # 如果是系统触发，且最后一条不是 ToolMessage（非工具回调轮），给它加上系统触发说明
        is_system = state.get("trigger_source") == "system"
        if is_system and history_messages and isinstance(history_messages[-1], HumanMessage):
            original_text = history_messages[-1].content
            system_trigger_prompt = self._prompts["system_trigger"].format(
                original_text=original_text
            )
            history_messages = history_messages[:-1] + [HumanMessage(content=system_trigger_prompt)]

        # 发往 LLM 前最后一次 tool 序列校验：须在 compact/compress 与系统触发改写之后，
        # 否则摘要截断可能再次产生「孤儿 Tool / 悬空 tool_calls」。
        history_messages = self._sanitize_messages(history_messages, external_tool_names)
        from services.llm_factory import extract_text
        for msg in history_messages:
            if isinstance(msg, ToolMessage) and isinstance(msg.content, list):
                msg.content = extract_text(msg.content)

        # 正常对话流程（用户和系统触发共用）
        # 工具状态通知只能并入「最后一条 HumanMessage」。若最后一条是 ToolMessage / AIMessage
        #（例如刚执行完工具、准备让模型继续），绝不能把 ToolMessage 替换成 HumanMessage，
        # 否则会破坏 Anthropic/MiniMax 要求的 tool_calls → ToolMessage 顺序，触发 2013。
        if tool_status_prompt and len(history_messages) >= 1:
            last_msg = history_messages[-1]
            if isinstance(last_msg, HumanMessage):
                if isinstance(last_msg.content, list):
                    notification = {"type": "text", "text": f"[系统通知] {tool_status_prompt}\n\n---\n"}
                    augmented_content = [notification] + list(last_msg.content)
                    augmented_msg = HumanMessage(content=augmented_content)
                else:
                    augmented_content = f"[系统通知] {tool_status_prompt}\n\n---\n{last_msg.content}"
                    augmented_msg = HumanMessage(content=augmented_content)
                input_messages = (
                    [SystemMessage(content=base_prompt)]
                    + history_messages[:-1]
                    + [augmented_msg]
                )
            else:
                notice_block = f"\n\n[系统通知] {tool_status_prompt}\n"
                input_messages = (
                    [SystemMessage(content=base_prompt + notice_block)]
                    + history_messages
                )
        else:
            input_messages = [SystemMessage(content=base_prompt)] + history_messages

        # # === DEBUG: dump full raw input to file for diagnosis ===
        # try:
        #     import json, datetime, os as _os
        #     _dump_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "test")
        #     _dump_path = _os.path.join(_dump_dir, "llm_input_dump.txt")
        #     with open(_dump_path, "w", encoding="utf-8") as _f:
        #         _f.write(f"=== LLM INPUT DUMP @ {datetime.datetime.now().isoformat()} ===\n")
        #         _f.write(f"Thread: {state.get('user_id','?')}#{state.get('session_id','?')}\n")
        #         _f.write(f"Total messages: {len(input_messages)}\n")
        #         _f.write(f"LLM model: {llm.model_name if hasattr(llm, 'model_name') else '?'}\n")
        #         _f.write(f"LLM base_url: {llm.openai_api_base if hasattr(llm, 'openai_api_base') else '?'}\n\n")
        #         # Dump each message as full dict via langchain serialization
        #         from langchain_openai.chat_models.base import _convert_message_to_dict
        #         for _i, _m in enumerate(input_messages):
        #             _f.write(f"--- [{_i}] {type(_m).__name__} ---\n")
        #             try:
        #                 _d = _convert_message_to_dict(_m)
        #                 _f.write(json.dumps(_d, ensure_ascii=False, indent=2))
        #             except Exception as _e:
        #                 _f.write(f"(serialization error: {_e})\n")
        #                 _f.write(f"raw __dict__: {_m.__dict__}")
        #             _f.write("\n\n")
        # except Exception:
        #     pass
        # # === END DEBUG ===

        # _maybe_debug_dump_llm_payload_for_minimax(
        #     input_messages,
        #     llm,
        #     user_id=user_id,
        #     session_id=session_id,
        # )

        response = await llm.ainvoke(input_messages)
        next_turn_count = current_turn_count + 1

        # --- Record token usage for budget tracking (new) ---
        usage_meta = getattr(response, "usage_metadata", None) or {}
        if isinstance(usage_meta, dict) and usage_meta:
            session_budget.record_turn(
                input_tokens=usage_meta.get("input_tokens", 0),
                output_tokens=usage_meta.get("output_tokens", 0),
                cache_creation_tokens=usage_meta.get("cache_creation_input_tokens", 0),
                cache_read_tokens=usage_meta.get("cache_read_input_tokens", 0),
            )
            model_name = getattr(llm, "model_name", "") or getattr(llm, "model", "") or ""
            cost_tracker.record(
                model=model_name,
                input_tokens=usage_meta.get("input_tokens", 0),
                output_tokens=usage_meta.get("output_tokens", 0),
                cache_read_tokens=usage_meta.get("cache_read_input_tokens", 0),
                cache_write_tokens=usage_meta.get("cache_creation_input_tokens", 0),
            )

        # --- Auto-continue check based on token budget (new) ---
        if not session_budget.should_auto_continue(min_utility=0.15):
            print(f">>> [budget] ⚡ marginal utility low ({session_budget.marginal_utility():.2f}), "
                  f"pressure={session_budget.context_pressure:.1%}")

        if should_stop_for_turn_limit(
            next_turn_count,
            effective_max_turns,
            getattr(response, "tool_calls", None),
            self._internal_tool_names,
        ):
            with contextlib.suppress(Exception):
                run_tool_policy_hooks(
                    session_policy,
                    event="stop",
                    user_id=user_id,
                    session_id=session_id,
                    tool_name="__session__",
                    args={
                        "mode": runtime_mode_name,
                        "reason": "max_turns",
                    },
                    result={
                        "next_turn_count": next_turn_count,
                        "max_turns": effective_max_turns,
                    },
                )
            response = AIMessage(
                content=build_turn_limit_message(
                    extract_text(response.content),
                    effective_max_turns,
                )
            )

        # --- 检测 tool_calls arguments 是否为合法 JSON（截断/超长会导致不完整）---
        import json as _json
        for _tc_list_name in ("tool_calls", "invalid_tool_calls"):
            for _tc in getattr(response, _tc_list_name, None) or []:
                _args = _tc.get("args") if _tc_list_name == "tool_calls" else _tc.get("args", "")
                # 兜底：args 缺失(None) 或空字符串 → 视为空参数 {}，不报错
                if _args is None or _args == "" or _args == {}:
                    if _tc_list_name == "tool_calls":
                        _tc["args"] = {}
                    continue
                # tool_calls 的 args 已被 LangChain 解析为 dict；如果仍是 str 说明解析失败
                # invalid_tool_calls 的 args 是原始 str
                if isinstance(_args, str):
                    try:
                        _json.loads(_args)
                    except (ValueError, TypeError):
                        import logging
                        logging.getLogger("agent.call_model").warning(
                            "LLM 返回的 tool_call arguments 不是合法 JSON (可能被截断), "
                            "name=%s, id=%s, args_len=%d, 剥离 tool_calls 改为纯文本回复",
                            _tc.get("name", "?"), _tc.get("id", "?"), len(_args) if _args else 0,
                        )
                        # 将无效的 tool_call 替换为错误 ToolMessage，保持消息序列合法
                        _tc_id = _tc.get("id", "unknown")
                        _tc_name = _tc.get("name", "unknown")
                        error_tool_msg = ToolMessage(
                            content=f"无效tool格式: {_tc_name} 的参数被截断，不是合法JSON",
                            tool_call_id=_tc_id,
                        )
                        # 保留原始 AIMessage（带 tool_calls），后跟错误 ToolMessage
                        return {"messages": [response, error_tool_msg], "turn_count": next_turn_count}

        with contextlib.suppress(Exception):
            run_tool_policy_hooks(
                session_policy,
                event="session_end",
                user_id=user_id,
                session_id=session_id,
                tool_name="__session__",
                args={
                    "mode": runtime_mode_name,
                    "turn_count": next_turn_count,
                    "has_tool_calls": bool(getattr(response, "tool_calls", None)),
                },
                result={
                    "content": extract_text(response.content)[:500],
                },
            )

        # --- Trajectory saving (new: ported from Hermes Agent) ---
        # Save conversation trajectory when no more tool calls (session ending)
        # Fire-and-forget: spawn in background thread to never block the agent
        if not getattr(response, "tool_calls", None) and next_turn_count > 1:
            model_name = getattr(llm, "model_name", "") or getattr(llm, "model", "") or ""
            traj_messages = [
                {"role": type(m).__name__.replace("Message", "").lower(), "content": extract_text(m.content)}
                for m in history_messages[:20]
            ]
            traj_messages.append({"role": "assistant", "content": extract_text(response.content)[:2000]})
            token_usage = {
                "input_tokens": usage_meta.get("input_tokens", 0) if isinstance(usage_meta, dict) else 0,
                "output_tokens": usage_meta.get("output_tokens", 0) if isinstance(usage_meta, dict) else 0,
            }
            # Non-blocking: run save in thread pool so agent can continue immediately
            # Fire-and-forget: failures are silently ignored to avoid impacting the agent
            try:
                asyncio.create_task(
                    asyncio.to_thread(
                        save_trajectory,
                        user_id=user_id,
                        session_id=session_id,
                        messages=traj_messages,
                        model=model_name,
                        completed=True,
                        tool_calls_count=current_turn_count,
                        token_usage=token_usage,
                    )
                )
            except Exception:
                pass

        return {"messages": [response], "turn_count": next_turn_count}

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
        import logging
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
                    result.append(HumanMessage(content=content_list or [{"type": "text", "text": "(空消息)"}]))
                else:
                    combined = "\n".join(p for p in new_parts if isinstance(p, str) and p)
                    result.append(HumanMessage(content=combined or "(空消息)"))
            else:
                result.append(msg)
        return result

    def get_tools_info(self) -> list[dict]:
        """Return serializable tool metadata list."""
        return [{"name": t.name, "description": t.description or ""} for t in self._mcp_tools]

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

    def get_all_thread_status(self, prefix: str) -> dict[str, dict]:
        """返回指定前缀下所有已知 thread 的状态。"""
        return self._thread_state_registry.get_all_thread_status(prefix)
