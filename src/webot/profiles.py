"""
WeBot agent profiles.

This module introduces Claude-Code-style agent profiles for WeBot. A profile
defines:
- what kind of delegated work the subagent should do
- which tools it may use by default
- whether it should inherit user profile / skills context
- optional model preference
- optional max-turn guard
- whether the profile is built-in or user-defined
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import uuid


PROJECT_ROOT = Path(__file__).resolve().parents[2]
USER_FILES_DIR = PROJECT_ROOT / "data" / "user_files"

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_SESSION_RE = re.compile(r"^subagent__([a-z0-9_-]+)__([a-z0-9_-]+)$")

DEFAULT_SUBAGENT_PROFILE = "general"
DEFAULT_PROFILE_FILENAME = "webot_agent_profiles.json"

SESSION_CONTEXT_TOOLS = (
    "get_current_session",
    "list_sessions",
)

SEARCH_TOOLS = (
    "web_search",
    "web_news",
    "web_search_json",
    "web_news_json",
    "web_fetch_url",
    "web_browser_search",
    "web_browser_fetch",
    "web_research_brief",
    "call_llm_api",
)

FILE_READ_TOOLS = (
    "list_files",
    "read_file",
)

FILE_WRITE_TOOLS = (
    "write_file",
    "append_file",
    "delete_file",
)

COMMAND_TOOLS = (
    "run_command",
    "run_python_code",
    "list_allowed_commands",
)

MESSAGE_TOOLS = (
    "send_internal_message",
    "send_to_group",
    "send_private_cli",
    "set_telegram_chat_id",
    "remove_telegram_config",
    "send_telegram_message",
    "get_telegram_status",
)

SCHEDULER_TOOLS = (
    "add_alarm",
    "list_alarms",
    "delete_alarm",
)

OASIS_READ_TOOLS = (
    "list_oasis_experts",
    "list_oasis_sessions",
    "list_oasis_topics",
    "list_oasis_workflows",
    "check_oasis_discussion",
    "yaml_to_layout",
    "get_publicnet_info",
)

OASIS_WRITE_TOOLS = (
    "add_oasis_expert",
    "update_oasis_expert",
    "delete_oasis_expert",
    "start_new_oasis",
    "cancel_oasis_discussion",
    "set_oasis_workflow",
)

WEBOT_SUBAGENT_TOOLS = (
    "list_webot_agent_profiles",
    "spawn_subagent",
    "list_subagents",
    "send_subagent_message",
    "get_subagent_history",
    "cancel_subagent",
)

WEBOT_RUNTIME_TOOLS = (
    "enter_plan_mode",
    "exit_plan_mode",
    "get_session_mode",
    "list_webot_workflow_presets",
    "apply_webot_workflow_preset",
    "write_session_plan",
    "read_session_plan",
    "clear_session_plan",
    "write_session_todos",
    "read_session_todos",
    "clear_session_todos",
    "record_verification",
    "list_verifications",
    "run_verification",
    "session_send_to",
    "session_inbox",
    "session_deliver_inbox",
    "claude_session_send_to",
    "claude_session_inbox",
    "claude_session_deliver_inbox",
    "ultraplan_start",
    "ultraplan_status",
    "ultrareview_start",
    "ultrareview_status",
    "list_tool_approvals",
    "resolve_tool_approval",
)


def _dedupe_tools(*tool_groups: tuple[str, ...]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for group in tool_groups:
        for name in group:
            if name not in seen:
                seen.add(name)
                ordered.append(name)
    return tuple(ordered)


GENERAL_SUBAGENT_TOOLS = _dedupe_tools(
    SESSION_CONTEXT_TOOLS,
    SEARCH_TOOLS,
    FILE_READ_TOOLS,
    FILE_WRITE_TOOLS,
    COMMAND_TOOLS,
    OASIS_READ_TOOLS,
    OASIS_WRITE_TOOLS,
    MESSAGE_TOOLS,
    SCHEDULER_TOOLS,
    WEBOT_RUNTIME_TOOLS,
)


def _normalize_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _normalize_max_turns(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _normalize_allowed_tools(
    value: object,
    *,
    disallowed_tools: object = None,
) -> tuple[str, ...]:
    if value is None or value == "*" or value == ["*"]:
        candidate_tools = GENERAL_SUBAGENT_TOOLS
    elif not isinstance(value, list):
        candidate_tools = tuple()
    else:
        normalized: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                normalized.append(item.strip())
        candidate_tools = tuple(normalized)

    blocked = {
        item.strip()
        for item in (disallowed_tools or [])
        if isinstance(item, str) and item.strip()
    }
    blocked.update(WEBOT_SUBAGENT_TOOLS)
    return tuple(tool_name for tool_name in candidate_tools if tool_name not in blocked)


def slugify(value: str, default: str = "agent") -> str:
    cleaned = _SLUG_RE.sub("-", (value or "").strip().lower()).strip("-_")
    return cleaned or default


def build_subagent_session_id(agent_type: str, agent_name: str = "") -> str:
    normalized_type = slugify(agent_type, DEFAULT_SUBAGENT_PROFILE)
    normalized_name = slugify(agent_name, uuid.uuid4().hex[:8])
    return f"subagent__{normalized_type}__{normalized_name}"


def parse_subagent_session_id(session_id: str) -> dict | None:
    match = _SESSION_RE.match((session_id or "").strip())
    if not match:
        return None
    return {
        "agent_type": match.group(1),
        "agent_id": match.group(2),
    }


def is_subagent_session(session_id: str | None) -> bool:
    return parse_subagent_session_id(session_id or "") is not None


def get_agent_profiles_path(
    user_id: str | None,
    *,
    project_root: str | Path | None = None,
) -> Path | None:
    if not user_id:
        return None
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    return root / "data" / "user_files" / user_id / DEFAULT_PROFILE_FILENAME


@dataclass(frozen=True)
class WeBotAgentProfile:
    agent_type: str
    display_name: str
    description: str
    system_prompt: str
    allowed_tools: tuple[str, ...] | None = None
    preferred_model: str | None = None
    include_user_profile: bool = False
    include_user_skills: bool = False
    background_default: bool = False
    max_turns: int | None = None
    source: str = "built-in"
    definition_path: str | None = None


BUILTIN_AGENT_PROFILES: dict[str, WeBotAgentProfile] = {
    "general": WeBotAgentProfile(
        agent_type="general",
        display_name="General Executor",
        description="通用执行型子 Agent，适合处理中等复杂度的独立任务。",
        system_prompt=(
            "你是 WeBot 的通用执行子 Agent。你的目标是直接完成被委派的任务，"
            "必要时阅读文件、执行命令、查询资料，并给出可交付结果。"
            "除非上级会话明确要求，否则不要再发起新的子 Agent。"
        ),
        allowed_tools=GENERAL_SUBAGENT_TOOLS,
        max_turns=12,
    ),
    "research": WeBotAgentProfile(
        agent_type="research",
        display_name="Research Explorer",
        description="只读研究型子 Agent，适合搜集资料、梳理代码与输出发现。",
        system_prompt=(
            "你是 WeBot 的研究型子 Agent。默认只做检索、阅读、归纳和对比，"
            "不要修改文件，不要做破坏性操作，重点产出事实、证据和结论。"
        ),
        allowed_tools=_dedupe_tools(
            SESSION_CONTEXT_TOOLS,
            SEARCH_TOOLS,
            FILE_READ_TOOLS,
            OASIS_READ_TOOLS,
            WEBOT_RUNTIME_TOOLS,
        ),
        max_turns=8,
    ),
    "planner": WeBotAgentProfile(
        agent_type="planner",
        display_name="Planner",
        description="规划型子 Agent，适合阅读上下文后拆解实现路线与关键文件。",
        system_prompt=(
            "你是 WeBot 的规划型子 Agent。你的工作是理解需求、调研现状、识别关键模块，"
            "然后输出可执行的步骤方案、依赖关系、风险和落地顺序。默认只读。"
        ),
        allowed_tools=_dedupe_tools(
            SESSION_CONTEXT_TOOLS,
            SEARCH_TOOLS,
            FILE_READ_TOOLS,
            COMMAND_TOOLS,
            OASIS_READ_TOOLS,
            WEBOT_RUNTIME_TOOLS,
        ),
        max_turns=6,
    ),
    "coder": WeBotAgentProfile(
        agent_type="coder",
        display_name="Coder",
        description="实现型子 Agent，适合编码、脚本编写和本地执行验证。",
        system_prompt=(
            "你是 WeBot 的实现型子 Agent。优先直接落地代码或脚本，遵循现有模式，"
            "必要时用命令和文件工具做修改与验证。不要做与任务无关的扩散搜索。"
        ),
        allowed_tools=_dedupe_tools(
            SESSION_CONTEXT_TOOLS,
            SEARCH_TOOLS,
            FILE_READ_TOOLS,
            FILE_WRITE_TOOLS,
            COMMAND_TOOLS,
            MESSAGE_TOOLS,
            WEBOT_RUNTIME_TOOLS,
        ),
        max_turns=16,
    ),
    "reviewer": WeBotAgentProfile(
        agent_type="reviewer",
        display_name="Reviewer",
        description="审查型子 Agent，适合代码走查、风险识别和回归点扫描。",
        system_prompt=(
            "你是 WeBot 的代码审查子 Agent。你主要识别 bug、回归风险、边界条件和遗漏测试。"
            "默认只读，不要修改文件；除非明确要求，否则不要给泛泛建议。"
        ),
        allowed_tools=_dedupe_tools(
            SESSION_CONTEXT_TOOLS,
            SEARCH_TOOLS,
            FILE_READ_TOOLS,
            COMMAND_TOOLS,
            OASIS_READ_TOOLS,
            WEBOT_RUNTIME_TOOLS,
        ),
        max_turns=8,
    ),
    "verifier": WeBotAgentProfile(
        agent_type="verifier",
        display_name="Verifier",
        description="验证型子 Agent，适合运行命令、重现问题、检查输出与收敛结论。",
        system_prompt=(
            "你是 WeBot 的验证子 Agent。你的职责不是复述代码，而是运行可验证的检查，"
            "尽可能通过命令、接口调用、状态读取来确认实现是否真实可用。默认不要修改文件。"
        ),
        allowed_tools=_dedupe_tools(
            SESSION_CONTEXT_TOOLS,
            SEARCH_TOOLS,
            FILE_READ_TOOLS,
            COMMAND_TOOLS,
            OASIS_READ_TOOLS,
            MESSAGE_TOOLS,
            WEBOT_RUNTIME_TOOLS,
        ),
        max_turns=10,
    ),
}


def _profile_from_dict(
    agent_type: str,
    raw: dict,
    *,
    source: str,
    definition_path: str | None,
) -> WeBotAgentProfile | None:
    prompt = raw.get("system_prompt") or raw.get("prompt")
    description = raw.get("description")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    if not isinstance(description, str) or not description.strip():
        return None

    normalized_type = slugify(agent_type, DEFAULT_SUBAGENT_PROFILE)
    display_name = raw.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = normalized_type.replace("-", " ").title()

    preferred_model = raw.get("preferred_model")
    if not isinstance(preferred_model, str) or not preferred_model.strip():
        preferred_model = None

    return WeBotAgentProfile(
        agent_type=normalized_type,
        display_name=display_name.strip(),
        description=description.strip(),
        system_prompt=prompt.strip(),
        allowed_tools=_normalize_allowed_tools(
            raw.get("allowed_tools"),
            disallowed_tools=raw.get("disallowed_tools"),
        ),
        preferred_model=preferred_model,
        include_user_profile=_normalize_bool(raw.get("include_user_profile")),
        include_user_skills=_normalize_bool(raw.get("include_user_skills")),
        background_default=_normalize_bool(raw.get("background_default")),
        max_turns=_normalize_max_turns(raw.get("max_turns")),
        source=source,
        definition_path=definition_path,
    )


def load_custom_agent_profiles(
    user_id: str | None,
    *,
    project_root: str | Path | None = None,
) -> dict[str, WeBotAgentProfile]:
    path = get_agent_profiles_path(user_id, project_root=project_root)
    if path is None or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(raw, dict) and isinstance(raw.get("profiles"), dict):
        profile_map = raw.get("profiles", {})
    elif isinstance(raw, dict):
        profile_map = raw
    else:
        return {}

    profiles: dict[str, WeBotAgentProfile] = {}
    for agent_type, definition in profile_map.items():
        if not isinstance(agent_type, str) or not isinstance(definition, dict):
            continue
        profile = _profile_from_dict(
            agent_type,
            definition,
            source="user",
            definition_path=str(path),
        )
        if profile is not None:
            profiles[profile.agent_type] = profile
    return profiles


def resolve_agent_profiles(
    user_id: str | None = None,
    *,
    project_root: str | Path | None = None,
) -> dict[str, WeBotAgentProfile]:
    profiles = dict(BUILTIN_AGENT_PROFILES)
    profiles.update(load_custom_agent_profiles(user_id, project_root=project_root))
    return profiles


def get_agent_profile(
    agent_type: str | None,
    user_id: str | None = None,
    *,
    project_root: str | Path | None = None,
) -> WeBotAgentProfile:
    normalized = slugify(agent_type or DEFAULT_SUBAGENT_PROFILE, DEFAULT_SUBAGENT_PROFILE)
    profiles = resolve_agent_profiles(user_id, project_root=project_root)
    return profiles.get(normalized, profiles[DEFAULT_SUBAGENT_PROFILE])


def list_agent_profiles(
    user_id: str | None = None,
    *,
    project_root: str | Path | None = None,
) -> list[WeBotAgentProfile]:
    profiles = resolve_agent_profiles(user_id, project_root=project_root)
    return [profiles[key] for key in sorted(profiles.keys())]


def render_profile_system_prompt(profile: WeBotAgentProfile) -> str:
    tool_summary = "全部工具" if profile.allowed_tools is None else ", ".join(profile.allowed_tools)
    turn_limit = "无限制" if profile.max_turns is None else str(profile.max_turns)
    return (
        f"【子 Agent 类型】{profile.display_name} ({profile.agent_type})\n"
        f"【来源】{profile.source}\n"
        f"【定位】{profile.description}\n"
        f"【能力边界】{tool_summary}\n"
        f"【默认执行模式】{'后台' if profile.background_default else '前台'}\n"
        f"【最大轮次】{turn_limit}\n"
        f"{profile.system_prompt}"
    )
