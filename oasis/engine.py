"""
OASIS Forum - 讨论引擎

管理讨论的完整生命周期：
  轮次循环 → 调度/并行专家参与 → 共识检查 → 总结

Three expert backends:
  1. ExpertAgent  — temp sender (stateless, name="tag#temp#N")
  2. SessionExpert — internal session agent (stateful, name="tag#oasis#name" or "#oasis#name")
     - name is resolved to session_id via internal agent JSON (internal_agents.json)
     - tag (if present) enables persona injection from presets
  3. ExternalExpert — external OpenAI-compatible API (name="tag#ext#id")
     - Directly calls external endpoints (DeepSeek, GPT-4, Ollama, etc)
     - Configured per-expert via YAML: api_url, api_key, model
     - ACP agent support: tag (codex, claude, gemini, aider, etc) determines the ACP binary;
       model "agent:<name>[:<session>]" prefers ACP persistent connection,
       falls back to HTTP API if ACP unavailable and api_url is configured.
       Session suffix defaults to clawcrosschat if not specified in model (aligned with group ACP).

专家池来源（仅 YAML，需要 schedule_file 或 schedule_yaml）：
  专家池完全从 YAML 专家名称构建（去重）。
  优先级：schedule_file > schedule_yaml（如果两者都提供，文件优先）。
  名称必须包含 '#' 来指定类型：
    "tag#temp#N"              → ExpertAgent（从预设中查找 tag 获取 name/persona）
    "tag#oasis#<name>"       → SessionExpert（name→session 查找，tag→persona）
    "#oasis#<name>"          → SessionExpert（name→session 查找，无 tag）
    "name#ext#<id>"          → ExternalExpert（YAML 中需要 api_url）
  不含 '#' 的名称会被警告并跳过。

  Session ID 通过内部代理 JSON 从代理名称解析。
  要明确确保新会话，请在名称后附加 "#new"：
    "tag#oasis#name#new"  → "#new" 被剥离，解析的 session_id 替换为随机 UUID
  这保证了不会意外重用现有会话。

  如果 YAML 使用 `all_experts: true`，池中所有专家并行发言。
  即使是简单的全并行场景，也只需最简 YAML：
    version: 1
    repeat: true
    plan:
      - all_experts: true

没有单独的专家-会话存储：session_id 通过内部代理 JSON
（internal_agents.json）从代理名称解析，然后用于访问
Agent checkpoint DB。

执行模式：
  1. 默认（repeat + all_experts）：所有专家每轮并行参与
  2. 调度模式：遵循 YAML 调度，定义每一步的发言顺序
"""

import asyncio
import json
import os
import platform
import re
import sys
import uuid

from langchain_core.messages import HumanMessage

# 确保 src/ 在 import 路径中，以便导入 llm_factory
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from services.llm_factory import create_chat_model, extract_text

from oasis.forum import DiscussionForum
from oasis.experts import ExpertAgent, SessionExpert, ExternalExpert, get_all_experts
from oasis.scheduler import (
    Schedule, ScheduleStep, StepType, Edge, ConditionalEdge, SelectorEdge,
    START, END, MAX_SUPER_STEPS,
    parse_schedule, load_schedule_file, extract_expert_names, collect_external_configs,
)

# Maximum total node executions across all super-steps (safety limit)
_MAX_TOTAL_NODE_EXECS = 500

# Project root for team-scoped paths
_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


def _load_external_agents(user_id: str, team: str = "") -> list[dict]:
    """加载 external_agents.json 列表。

    返回 {"name", "tag", "global_name", "config"?, ...} 条目列表。
    当提供 team 时，仅加载团队作用域配置；
    当 team 为空时，回退到用户级配置：
      data/user_files/{user_id}/external_agents.json
    如果文件缺失或不可读，返回 []。
    """
    if not user_id:
        return []
    if team:
        path = os.path.join(
            _PROJECT_ROOT, "data", "user_files", user_id, "teams", team, "external_agents.json"
        )
    else:
        path = os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "external_agents.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _find_external_agent_global_name(external_agents: list[dict], name: str) -> str:
    """从 external_agents.json 中按代理名称查找 'global_name' 字段。

    返回 global_name 字符串（真实的 ACP 代理名称）。
    当没有配置团队（external_agents 列表为空）时，
    回退到 *name* 本身，以便仍能创建 ACP 代理。
    """
    if not external_agents:
        return name
    name_lower = name.lower()
    for a in external_agents:
        if a.get("name", "").lower() == name_lower:
            return a.get("global_name", "")
    return ""


def _find_external_agent_record(external_agents: list[dict], name: str) -> dict:
    if not external_agents:
        return {}
    name_lower = name.lower()
    for a in external_agents:
        if isinstance(a, dict) and a.get("name", "").lower() == name_lower:
            return a
    return {}


def _canonical_external_platform(platform_name: str) -> str:
    pl = (platform_name or "").strip().lower()
    if pl in ("claude-code", "claudecode"):
        return "claude"
    if pl in ("gemini-cli", "geminicli"):
        return "gemini"
    return pl


def _load_internal_agents(user_id: str, team: str = "") -> list[dict]:
    """加载用户的内部代理 JSON 列表。

    读取 internal_agents.json：
      [{"name": ..., "tag": ..., "session": "sid"}, ...]

    如果指定了团队，从团队作用域路径加载：
      data/user_files/{user_id}/teams/{team}/internal_agents.json
    否则从以下路径加载：
      data/user_files/{user_id}/internal_agents.json

    返回 {"session": "<id>", "meta": {"name": ..., "tag": ...}} 条目列表。
    如果文件缺失或不可读，返回 []。
    """
    if team:
        base_dir = os.path.join(_PROJECT_ROOT, "data", "user_files", user_id, "teams", team)
    else:
        base_dir = os.path.join(_PROJECT_ROOT, "data", "user_files", user_id)

    ia_path = os.path.join(base_dir, "internal_agents.json")

    if not os.path.isfile(ia_path):
        return []

    try:
        with open(ia_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        agents_list = data if isinstance(data, list) else []
    except Exception:
        return []

    result: list[dict] = []
    for a in agents_list:
        if not isinstance(a, dict) or "name" not in a:
            continue
        sid = a.get("session", "")
        meta = {k: v for k, v in a.items() if k != "session"}
        result.append({"session": sid, "meta": meta})
    return result


def _resolve_session_by_name(agents: list[dict], name: str) -> str | None:
    """通过匹配代理 meta.name 查找 session_id（不区分大小写）。

    返回 session_id 字符串，如果未找到则返回 None。
    """
    name_lower = name.lower()
    for a in agents:
        meta = a.get("meta", {})
        if meta.get("name", "").lower() == name_lower:
            return a.get("session", "")
    return None


def _find_tag_in_internal_agents(agents: list[dict], session_id: str) -> str:
    """通过 session_id 从内部代理 JSON 中查找 tag。

    返回 tag 字符串，如果未找到则返回 ""。
    """
    for a in agents:
        if a.get("session") == session_id:
            return a.get("meta", {}).get("tag", "")
    return ""

def _extract_selector_choice(content: str) -> int | None:
    """从帖子的内容中提取选择器选择编号。

    解析 clawcross_type JSON：{"clawcross_type": "oasis choose", "choose": N, ...}
    读取 'choose' 字段，可以是：
      - int：直接使用（当前提示词要求的标准格式）
      - str：如果为数字则转换为 int
      - dict：兼容旧格式，只接受 option/choice 中的数字

    返回选择编号（int），如果未找到有效选择则返回 None。
    """
    # Scan for top-level { ... } candidates using brace-depth tracking
    depth = 0
    start_idx = -1
    candidates = []
    for i, ch in enumerate(content):
        if ch == '{':
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start_idx >= 0:
                candidates.append(content[start_idx:i + 1])
                start_idx = -1

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict) and obj.get("clawcross_type") == "oasis choose":
                choose_val = obj.get("choose")
                if isinstance(choose_val, int):
                    return choose_val
                if isinstance(choose_val, str) and choose_val.strip().isdigit():
                    return int(choose_val.strip())
                if isinstance(choose_val, dict):
                    # e.g. {"option": 1} or {"option": "2"}
                    opt = choose_val.get("option", choose_val.get("choice"))
                    if opt is not None:
                        return int(opt)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    return None

# 加载总结 prompt 模板（模块级别，导入时执行一次）
_prompts_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "prompts")
_summary_tpl_path = os.path.join(_prompts_dir, "oasis_summary.txt")
try:
    with open(_summary_tpl_path, "r", encoding="utf-8") as f:
        _SUMMARY_PROMPT_TPL = f.read().strip()
    print("[prompts] ✅ oasis 已加载 oasis_summary.txt")
except FileNotFoundError:
    print(f"[prompts] ⚠️ 未找到 {_summary_tpl_path}，使用内置默认模板")
    _SUMMARY_PROMPT_TPL = ""


def _get_summarizer():
    """创建低温 LLM 用于可靠的总结生成。"""
    return create_chat_model(temperature=0.3, max_tokens=2048)


class DiscussionEngine:
    """
    协调一个完整的讨论会话。

    流程：
      1. 按调度定义的顺序执行步骤
      2. 每轮后检查是否达成共识
      3. 完成后（达成共识或达到最大轮数），将最高赞帖子总结为结论

    专家池构建（仅 YAML）：
      专家池完全从 YAML 专家名称构建（去重）。
      "tag#temp#N"          → ExpertAgent（tag→从预设中查找 name/persona）
      "tag#oasis#name"      → SessionExpert（name→session 查找，tag→persona）
      "#oasis#name"         → SessionExpert（name→session 查找，无 tag）
      "name#ext#id"         → ExternalExpert（从 YAML 获取 api_url/api_key/model）
      任意名称 + "#new"     → 强制新会话（id 替换为随机 UUID）
    """

    def __init__(
        self,
        forum: DiscussionForum,
        schedule: Schedule | None = None,
        schedule_yaml: str | None = None,
        schedule_file: str | None = None,
        bot_base_url: str | None = None,
        bot_enabled_tools: list[str] | None = None,
        bot_timeout: float | None = None,
        user_id: str = "anonymous",
        early_stop: bool = False,
        discussion: bool | None = None,
        team: str = "",
    ):
        self.forum = forum
        self._cancelled = False
        self._early_stop = early_stop
        self._discussion_override = discussion  # API-level override (None = use YAML)
        self._team = team  # Team name for scoped agent storage
        self._user_id = user_id
        self._bot_timeout = bot_timeout

        # ── Step 1: Parse schedule (required) ──
        self.schedule: Schedule | None = None
        if schedule:
            self.schedule = schedule
        elif schedule_file:
            self.schedule = load_schedule_file(schedule_file)
        elif schedule_yaml:
            self.schedule = parse_schedule(schedule_yaml)

        if not self.schedule:
            raise ValueError(
                "schedule_yaml or schedule_file is required. "
                "For simple all-parallel, use: version: 1\\nrepeat: true\\nplan:\\n  - all_experts: true"
            )

        # discussion mode: API override > YAML setting > default False
        if self._discussion_override is not None:
            self._discussion = self._discussion_override
        else:
            self._discussion = self.schedule.discussion

        # ── Step 2: Build expert pool from YAML ──
        experts_list: list[ExpertAgent | SessionExpert | ExternalExpert] = []

        yaml_names = extract_expert_names(self.schedule)
        ext_configs = collect_external_configs(self.schedule)
        internal_agents = _load_internal_agents(user_id, self._team)  # for name→session lookup
        external_agents = _load_external_agents(user_id, self._team)  # for name→oc_agent_name lookup
        seen: set[str] = set()
        # Map YAML original names → expert (built during pool construction)
        yaml_to_expert: dict[str, ExpertAgent | SessionExpert | ExternalExpert] = {}
        for full_name in yaml_names:
            if full_name in seen:
                continue
            seen.add(full_name)

            if "#" not in full_name:
                print(f"  [OASIS] ⚠️ YAML expert name '{full_name}' has no '#', skipping. "
                      f"Use 'tag#temp#N' or 'tag#oasis#name' or '#oasis#name' or 'name#ext#id'.")
                continue

            # Handle #new suffix: strip only "new", keep the '#' separator
            force_new = full_name.endswith("#new")
            working_name = full_name[:-3] if force_new else full_name  # strip "new" only

            first, sid = working_name.split("#", 1)
            expert: ExpertAgent | SessionExpert | ExternalExpert
            # Current format:
            # Internal: tag#oasis#name or #oasis#name
            # Temp:     tag#temp#N
            # External: tag#ext#id   (preferred)
            # Legacy external: tag#platform#id (still accepted for compatibility)
            if "#oasis#" in sid or sid.startswith("oasis#") or sid == "oasis":
                # --- Internal session agent: tag#oasis#name ---
                # Extract the part after 'oasis#'
                if "oasis#" in sid:
                    oasis_rest = sid.split("oasis#", 1)[1]
                else:
                    oasis_rest = ""
                # Resolve oasis_rest as an agent name
                resolved_sid = _resolve_session_by_name(internal_agents, oasis_rest) if oasis_rest else None
                if not resolved_sid:
                    print(f"  [OASIS] ⚠️ Cannot resolve agent name '{oasis_rest}' from internal agents JSON, skipping '{full_name}'.")
                    continue
                agent_name = oasis_rest
                tag_for_lookup = first
                if force_new:
                    actual_sid = uuid.uuid4().hex[:8]
                    print(f"  [OASIS] 🆕 #new: '{full_name}' → new session '{actual_sid}'")
                else:
                    actual_sid = resolved_sid
                persona = ""
                expert_name = agent_name
                ia_tag = ""
                if tag_for_lookup:
                    config = self._lookup_by_tag(tag_for_lookup, user_id, self._team)
                    if config:
                        expert_name = config.get("name", agent_name)
                        persona = config.get("persona", "")
                        print(f"  [OASIS] 🏷️ Tag '{tag_for_lookup}' → persona for '{expert_name}'")
                else:
                    ia_tag = _find_tag_in_internal_agents(internal_agents, resolved_sid)
                    if ia_tag:
                        config = self._lookup_by_tag(ia_tag, user_id, self._team)
                        if config:
                            persona = config.get("persona", "")
                            print(f"  [OASIS] 🏷️ Auto-detected tag '{ia_tag}' → persona for '{expert_name}'")
                cfg = ext_configs.get(full_name, {})
                _oasis_config = config if config else {}
                expert = SessionExpert(
                    name=expert_name,
                    session_id=actual_sid,
                    user_id=user_id,
                    persona=persona,
                    bot_base_url=bot_base_url,
                    enabled_tools=bot_enabled_tools,
                    timeout=bot_timeout,
                    tag=tag_for_lookup or ia_tag,
                    extra_headers=cfg.get("headers"),
                    model=_oasis_config.get("model"),
                    api_key=_oasis_config.get("api_key"),
                    base_url=_oasis_config.get("base_url"),
                    provider=_oasis_config.get("provider"),
                )
                if _oasis_config.get("model"):
                    print(f"  [OASIS] 💬 Session agent (name): '{agent_name}' → session '{actual_sid}' [model={_oasis_config.get('model')}]")
                else:
                    print(f"  [OASIS] 💬 Session agent (name): '{agent_name}' → session '{actual_sid}'")
            elif sid.startswith("temp#"):
                # e.g. "creative#temp#1" → ExpertAgent with explicit temp_id
                config = self._lookup_by_tag(first, user_id, self._team)
                expert_name = config["name"] if config else first
                persona = config.get("persona", "") if config else ""
                temp_num = sid.split("#", 1)[1]
                # Per-expert model override: read optional model/api_key/base_url/provider
                # from the persona config in oasis_experts.json
                expert_temperature = float(config.get("temperature", 0.7)) if config else 0.7
                expert_model = config.get("model") if config else None
                expert_api_key = config.get("api_key") if config else None
                expert_base_url = config.get("base_url") if config else None
                expert_provider = config.get("provider") if config else None
                expert = ExpertAgent(
                    name=expert_name,
                    persona=persona,
                    temperature=expert_temperature,
                    temp_id=int(temp_num) if temp_num.isdigit() else None,
                    tag=first,
                    model=expert_model,
                    api_key=expert_api_key,
                    base_url=expert_base_url,
                    provider=expert_provider,
                )
            else:
                # --- External agent ---
                # Preferred: tag#ext#id
                # Legacy:    tag#platform#id
                parts = sid.split("#", 1)
                external_mode = parts[0] if parts else ""
                ext_name = parts[1] if len(parts) > 1 else ""
                if not ext_name:
                    print(f"  [OASIS] ⚠️ External expert '{full_name}' missing name, skipping.")
                    continue
                if force_new:
                    ext_name = uuid.uuid4().hex[:8]
                    print(f"  [OASIS] 🆕 #new: '{full_name}' → new external session '{ext_name}'")
                # Prefer the external_agents.json record as the source of truth for platform/global_name.
                # Only fall back to legacy YAML-embedded platform hints when no record is available.
                external_agent = _find_external_agent_record(external_agents, ext_name)
                platform_name = ""
                if external_agent and external_agent.get("platform"):
                    platform_name = _canonical_external_platform(str(external_agent.get("platform", "") or platform_name))
                elif external_mode and external_mode != "ext":
                    platform_name = _canonical_external_platform(external_mode)
                elif first:
                    # Compatibility fallback: older teams sometimes encoded the effective platform in the tag.
                    platform_name = _canonical_external_platform(first)
                is_acp_agent = platform_name in ExternalExpert._ACP_TOOL_TAGS
                is_openclaw_http = platform_name == "openclaw"
                # Public OpenClaw: YAML name IS global_name, no JSON lookup needed
                if is_openclaw_http and not self._team:
                    oc_name = ext_name
                    external_agent = {}
                    cfg = ext_configs.get(full_name, {})
                    config = self._lookup_by_tag(first, user_id, self._team)
                    expert_name = config["name"] if config else first
                    persona = config.get("persona", "") if config else ""
                    api_url = cfg.get("api_url", "") or os.getenv("OPENCLAW_API_URL", "") or ""
                    model_str = cfg.get("model", "gpt-3.5-turbo")
                else:
                    cfg = ext_configs.get(full_name, {})
                    has_http_url = bool(cfg.get("api_url")) or (is_openclaw_http and bool(os.getenv("OPENCLAW_API_URL", "")))
                    if not has_http_url and not is_acp_agent:
                        print(f"  [OASIS] ⚠️ External expert '{full_name}' missing platform/api_url, skipping.")
                        continue
                    api_url = cfg.get("api_url", "") or ""
                    model_str = cfg.get("model", "gpt-3.5-turbo")
                    config = self._lookup_by_tag(first, user_id, self._team)
                    if is_acp_agent:
                        expert_name = ext_name
                        persona = config.get("persona", "") if config else ""
                    else:
                        expert_name = config["name"] if config else first
                        persona = config.get("persona", "") if config else ""
                    oc_name = str(external_agent.get("global_name", "") or "") if external_agent else ""
                expert = ExternalExpert(
                    name=expert_name,
                    ext_id=ext_name,
                    api_url=api_url,
                    api_key=cfg.get("api_key", "") or os.getenv("OPENCLAW_GATEWAY_TOKEN", ""),
                    model=model_str,
                    persona=persona,
                    timeout=bot_timeout,
                    tag=first,
                    platform=platform_name,
                    extra_headers=cfg.get("headers"),
                    acp_options=cfg.get("acp") if isinstance(cfg.get("acp"), dict) else None,
                    oc_agent_name=oc_name,
                    team=self._team,
                )
                if is_acp_agent:
                    print(f"  [OASIS] 🔌 ACP agent: {expert.name} (platform={platform_name})")
                elif api_url:
                    print(f"  [OASIS] 🌐 External expert: {expert.name} → {api_url}")
                else:
                    print(f"  [OASIS] 🌐 External expert: {expert.name} (no api_url)")

            experts_list.append(expert)
            # Register YAML original name → expert immediately (handles #new correctly)
            yaml_to_expert[full_name] = expert

        self.experts = experts_list
        self._total_node_execs = 0  # safety counter for Pregel super-step execution

        # Build lookup map: YAML original names first (highest priority for scheduling),
        # then register by internal name, title, tag, session_id as shortcuts
        self._expert_map: dict[str, ExpertAgent | SessionExpert | ExternalExpert] = {}
        self._expert_map.update(yaml_to_expert)
        for e in self.experts:
            self._expert_map.setdefault(e.name, e)       # expert display name
            self._expert_map.setdefault(e.title, e)      # "创意专家" (first-come wins)
            if e.tag:
                self._expert_map.setdefault(e.tag, e)    # "creative" (first-come wins)
            if hasattr(e, "session_id"):
                self._expert_map.setdefault(e.session_id, e)  # session_id shortcut
            if hasattr(e, "ext_id"):
                self._expert_map.setdefault(e.ext_id, e)  # ext_id shortcut

        self.summarizer = _get_summarizer()

    @staticmethod
    def _lookup_by_tag(tag: str, user_id: str, team: str = "") -> dict | None:
        """通过 tag 查找专家配置。返回 {"name", "persona", ...} 或 None。

        当提供 *team* 时，团队特定专家优先
        （它们出现在 get_all_experts 返回列表的前面）。
        """
        for c in get_all_experts(user_id, team=team):
            if c["tag"] == tag:
                return c
        return None

    def _resolve_experts(self, names: list[str]) -> list:
        """将专家引用解析为 Expert 对象。

        匹配优先级：全名 > title > tag > session_id。
        跳过未知名称。
        """
        resolved = []
        for name in names:
            agent = self._expert_map.get(name)
            if agent:
                resolved.append(agent)
            else:
                print(f"  [OASIS] ⚠️ Schedule references unknown expert: '{name}', skipping")
        return resolved

    def cancel(self):
        """请求优雅取消。在下一轮之前生效。"""
        self._cancelled = True

    def _check_cancelled(self):
        """检查是否已请求取消，若是则抛出 CancelledError。"""
        if self._cancelled:
            raise asyncio.CancelledError("Discussion cancelled by user")

    def _team_root(self) -> str:
        if self._user_id and self._team:
            return os.path.join(_PROJECT_ROOT, "data", "user_files", self._user_id, "teams", self._team)
        return _PROJECT_ROOT

    def _resolve_script_cwd(self, cwd: str) -> str:
        """Resolve and constrain script cwd to the project root or current team root."""
        base_root = os.path.realpath(self._team_root())
        project_root = os.path.realpath(_PROJECT_ROOT)
        raw = (cwd or "").strip()
        if not raw:
            return base_root if os.path.isdir(base_root) else project_root

        if os.path.isabs(raw):
            resolved = os.path.realpath(raw)
        else:
            resolved = os.path.realpath(os.path.join(base_root, raw))

        for allowed in (base_root, project_root):
            if os.path.isdir(allowed) and os.path.commonpath([resolved, allowed]) == allowed:
                return resolved
        raise RuntimeError(f"Script cwd '{cwd}' is outside allowed roots")

    def _script_timeout_for_step(self, step: ScheduleStep) -> float:
        timeout = step.script_timeout
        if timeout is None:
            timeout = self._bot_timeout
        if timeout is None:
            timeout = 300.0
        return max(float(timeout), 0.1)

    @staticmethod
    def _truncate_text(text: str, limit: int = 4000) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n... (已截断，原始长度 {len(text)} 字符)"

    def _resolve_script_command(self, step: ScheduleStep) -> tuple[list[str], str]:
        """Return subprocess argv and human-readable shell label for a script node."""
        is_windows = platform.system().lower().startswith("win")
        command = (
            step.script_windows_command if is_windows and step.script_windows_command else
            step.script_unix_command if (not is_windows) and step.script_unix_command else
            step.script_command
        ).strip()
        if not command:
            raise RuntimeError(f"Script node '{step.node_id}' has no command for this platform")

        if is_windows:
            return (["powershell", "-NoProfile", "-Command", command], "powershell")
        return (["bash", "-lc", command], "bash")

    async def _execute_script_node(self, step: ScheduleStep) -> None:
        """Execute a script node and publish the result as a forum post."""
        argv, shell_name = self._resolve_script_command(step)
        cwd = self._resolve_script_cwd(step.script_cwd)
        timeout = self._script_timeout_for_step(step)
        command_preview = step.script_command or step.script_unix_command or step.script_windows_command

        self.forum.log_event("script_start", agent=step.node_id, detail=command_preview[:120])
        print(f"  [OASIS] 🧪 Script node {step.node_id}: {command_preview}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                self.forum.log_event("script_timeout", agent=step.node_id, detail=f"timeout={timeout}s")
                await self.forum.publish(
                    author=f"script:{step.node_id}",
                    content=(
                        f"[脚本超时]\n"
                        f"shell: {shell_name}\n"
                        f"cwd: {cwd}\n"
                        f"timeout: {timeout}s\n"
                        f"command: {command_preview}"
                    ),
                    source_node_id=step.node_id,
                )
                return

            stdout_text = self._truncate_text(stdout.decode("utf-8", errors="replace").strip())
            stderr_text = self._truncate_text(stderr.decode("utf-8", errors="replace").strip())
            exit_code = proc.returncode if proc.returncode is not None else -1
            status_label = "成功" if exit_code == 0 else "失败"
            body_parts = [
                f"[脚本{status_label}]",
                f"shell: {shell_name}",
                f"cwd: {cwd}",
                f"exit_code: {exit_code}",
                f"command: {command_preview}",
            ]
            if stdout_text:
                body_parts.append(f"\nstdout:\n{stdout_text}")
            if stderr_text:
                body_parts.append(f"\nstderr:\n{stderr_text}")

            self.forum.log_event("script_done", agent=step.node_id, detail=f"exit={exit_code}")
            await self.forum.publish(
                author=f"script:{step.node_id}",
                content="\n".join(body_parts),
                source_node_id=step.node_id,
            )
        except Exception as e:
            self.forum.log_event("script_done", agent=step.node_id, detail=f"error={str(e)[:80]}")
            await self.forum.publish(
                author=f"script:{step.node_id}",
                content=(
                    f"[脚本执行异常]\n"
                    f"cwd: {cwd}\n"
                    f"command: {command_preview}\n"
                    f"error: {e}"
                ),
                source_node_id=step.node_id,
            )

    async def _execute_human_node(self, step: ScheduleStep) -> None:
        """Pause the workflow until a human reply is submitted or timeout expires."""
        author = step.human_author or "主持人"
        self.forum.log_event("human_wait", agent=step.node_id, detail=step.human_prompt[:120])
        prompt_post = await self.forum.publish(
            author=author,
            content=step.human_prompt,
            reply_to=step.human_reply_to,
            source_node_id=step.node_id,
        )
        await self.forum.set_pending_human_reply(
            node_id=step.node_id,
            prompt=step.human_prompt,
            author=author,
            round_num=self.forum.current_round,
            reply_to=prompt_post.id,
        )
        self.forum.save()

        timeout = self._script_timeout_for_step(step)
        reply_post = await self.forum.wait_for_human_reply(
            node_id=step.node_id,
            round_num=self.forum.current_round,
            timeout=timeout,
        )
        await self.forum.clear_pending_human_reply()

        if reply_post is not None:
            self.forum.log_event("human_reply", agent=step.node_id, detail=f"post_id={reply_post.id}")
            return

        self.forum.log_event("human_timeout", agent=step.node_id, detail=f"timeout={timeout}s")
        await self.forum.publish(
            author=f"human:{step.node_id}",
            content=f"[人类节点超时]\n等待 {timeout}s 后未收到回复。",
            reply_to=prompt_post.id,
            source_node_id=step.node_id,
        )

    async def run(self):
        """运行完整的讨论循环（作为后台任务调用）。"""
        self.forum.status = "discussing"
        self.forum.discussion = self._discussion
        self.forum.start_clock()

        session_count = sum(1 for e in self.experts if isinstance(e, SessionExpert))
        external_count = sum(1 for e in self.experts if isinstance(e, ExternalExpert))
        direct_count = len(self.experts) - session_count - external_count
        mode_label = "discussion" if self._discussion else "execute"
        n_nodes = len(self.schedule.nodes)
        n_edges = len(self.schedule.edges) + len(self.schedule.conditional_edges)
        print(
            f"[OASIS] 🏛️ Discussion started: {self.forum.topic_id} "
            f"({len(self.experts)} experts [{direct_count} direct, {session_count} session, {external_count} external], "
            f"graph: {n_nodes} nodes, {n_edges} edges, mode={mode_label})"
        )

        try:
            max_repeats = 1
            if self.schedule.repeat:
                max_repeats = self.schedule.max_repeat if self.schedule.max_repeat > 0 else self.forum.max_rounds

            can_early_stop = self._early_stop and self._discussion

            for repeat_round in range(max_repeats):
                self._check_cancelled()
                if max_repeats > 1:
                    self.forum.current_round = repeat_round + 1
                    self.forum.log_event("repeat", detail=f"Repeat {repeat_round + 1}/{max_repeats}")
                    print(f"[OASIS] 📢 Repeat round {repeat_round + 1}/{max_repeats}")

                await self._run_graph()

                if can_early_stop and repeat_round >= 1 and await self._consensus_reached():
                    print(f"[OASIS] 🤝 Consensus reached at repeat round {repeat_round + 1}")
                    break

            if self._discussion:
                self.forum.conclusion = await self._summarize()
            else:
                # Execute mode: just collect outputs, no LLM summary
                all_posts = await self.forum.browse()
                if all_posts:
                    self.forum.conclusion = "\n\n".join(
                        f"【{p.author}】\n{p.content}" for p in all_posts
                    )
                else:
                    self.forum.conclusion = "执行完成，无输出。"
            self.forum.log_event("conclude", detail="Discussion concluded")
            self.forum.status = "concluded"
            print(f"[OASIS] ✅ Discussion concluded: {self.forum.topic_id}")

        except asyncio.CancelledError:
            print(f"[OASIS] 🛑 Discussion cancelled: {self.forum.topic_id}")
            self.forum.status = "error"
            self.forum.conclusion = "讨论已被用户强制终止"

        except Exception as e:
            print(f"[OASIS] ❌ Discussion error: {e}")
            self.forum.status = "error"
            self.forum.conclusion = f"讨论过程中出现错误: {str(e)}"

        finally:
            pass

    async def _run_graph(self):
        """使用 Pregel 风格超步迭代执行图。

        算法：
          1. 初始化：激活所有入口节点（没有入边的节点）
          2. 超步循环：
             a. 并行执行所有已激活节点
             b. 对于每个完成的节点，评估出边：
                - 固定边：始终触发 → 激活目标
                - 条件边：评估条件 → 激活选中的目标
             c. 收集新激活的节点用于下一超步
             d. 如果没有新激活或到达 END → 停止
          3. 安全限制：MAX_SUPER_STEPS 后停止以防止无限循环
        """
        sched = self.schedule
        node_map = sched.node_map

        # Track which nodes have been completed in this execution
        # For cycles: a node can be activated multiple times
        completed_set: set[str] = set()     # tracks last-completed nodes (for trigger checking)
        super_step = 0

        # Start with entry nodes
        activated: set[str] = set(sched.entry_nodes)
        reached_end = False

        print(f"  [OASIS] 🚀 Graph engine start: {len(sched.nodes)} nodes, entry={list(activated)}")
        self.forum.log_event("graph_start", detail=f"nodes={len(sched.nodes)}, entries={list(activated)}")

        while activated and super_step < MAX_SUPER_STEPS:
            self._check_cancelled()
            super_step += 1

            # Safety limit on total node executions
            self._total_node_execs += len(activated)
            if self._total_node_execs > _MAX_TOTAL_NODE_EXECS:
                raise RuntimeError(
                    f"Safety limit reached: {self._total_node_execs} total node executions "
                    f"(max {_MAX_TOTAL_NODE_EXECS}). Possible infinite loop in graph."
                )

            activated_list = sorted(activated)  # deterministic order
            print(f"  [OASIS] ⚡ Super-step {super_step}: executing {activated_list}")
            self.forum.log_event("super_step", detail=f"step={super_step}, nodes={activated_list}")

            # Update forum progress
            self.forum.current_round = super_step
            self.forum.max_rounds = max(super_step, len(sched.nodes))
            await self.forum.clear_round_waiting_experts()

            # Execute all activated nodes in parallel
            async def _exec_node(node_id: str):
                node = node_map[node_id]
                # Build visibility: in execute mode, only see posts from upstream nodes
                vis = self._build_visibility_filter_graph(node_id, completed_set)
                await self._execute_node(node, vis)

            if len(activated_list) == 1:
                # Single node: execute directly (avoids gather overhead)
                await _exec_node(activated_list[0])
            else:
                # Multiple nodes: execute in parallel
                results = await asyncio.gather(
                    *[_exec_node(nid) for nid in activated_list],
                    return_exceptions=True,
                )
                for nid, r in zip(activated_list, results):
                    if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                        print(f"  [OASIS] ❌ Node '{nid}' error: {r}")
                        # Continue with other nodes; don't propagate error to stop entire graph

            # Mark nodes as completed
            for nid in activated_list:
                completed_set.add(nid)

            # Evaluate outgoing edges to determine next activated nodes
            next_activated: set[str] = set()

            for nid in activated_list:
                # Fixed edges: always fire
                for edge in sched.out_edges.get(nid, []):
                    if edge.target == END:
                        reached_end = True
                        continue
                    # Check if ALL incoming sources of target are completed
                    target_in = sched.in_sources.get(edge.target, set())
                    if target_in.issubset(completed_set):
                        next_activated.add(edge.target)
                    # For cycles: if this is a back-edge, activate immediately
                    # (the node was completed before, so re-activate it)
                    elif edge.target in completed_set:
                        # Back-edge: re-activate for next iteration
                        completed_set.discard(edge.target)
                        next_activated.add(edge.target)

                # Conditional edges: evaluate condition to pick target
                for ce in sched.out_cond_edges.get(nid, []):
                    cond_result = await self._eval_condition(ce.condition)
                    if cond_result:
                        target = ce.then_target
                        print(f"  [OASIS] 🔀 Condition '{ce.condition}' → TRUE → {target}")
                    else:
                        target = ce.else_target
                        print(f"  [OASIS] 🔀 Condition '{ce.condition}' → FALSE → {target or 'none'}")

                    if not target:
                        continue
                    if target == END:
                        reached_end = True
                        continue

                    self.forum.log_event("condition", detail=f"'{ce.condition}' → {target}")

                    # Conditional edge respects the same AND-trigger rule as fixed edges:
                    # the target is only activated when ALL its fixed-edge in_sources
                    # are satisfied.  (in_sources no longer contains conditional-edge
                    # sources, so this check won't be blocked by unresolved back-edges.)
                    target_in = sched.in_sources.get(target, set())
                    if target in completed_set:
                        # Back-edge / loop: re-activate the already-completed node
                        completed_set.discard(target)
                        next_activated.add(target)
                    elif target_in.issubset(completed_set):
                        next_activated.add(target)
                    else:
                        # Fixed-edge predecessors not yet done — defer activation.
                        # The target will be picked up later when its fixed-edge
                        # sources complete.
                        print(f"  [OASIS] ⏳ Conditional target '{target}' deferred: "
                              f"waiting for fixed-edge sources {target_in - completed_set}")

                # Selector edges: parse LLM output to pick target
                se = sched.out_selector_edges.get(nid)
                if se:
                    # Get the last post from this node's agent to find the choice
                    all_posts = await self.forum.browse()
                    node_step = sched.node_map.get(nid)
                    node_agents = self._resolve_experts(node_step.expert_names) if node_step else []
                    node_author_names = {a.name for a in node_agents}
                    # Find the last post from this node's agents
                    selector_output = ""
                    for p in reversed(all_posts):
                        if p.author in node_author_names:
                            selector_output = p.content
                            break
                    # Extract choice number from clawcross_type JSON ("oasis choose")
                    print(f"  [OASIS] 🔍 Selector '{nid}' raw output (first 200 chars): {selector_output[:200]!r}")
                    choice_num = _extract_selector_choice(selector_output)
                    if choice_num is not None:
                        target = se.choices.get(choice_num, "")
                        print(f"  [OASIS] 🎯 Selector '{nid}' chose [{choice_num}] → {target or 'invalid'}")
                        self.forum.log_event("selector", detail=f"chose [{choice_num}] → {target}")
                        if target and target != END:
                            target_in = sched.in_sources.get(target, set())
                            if target in completed_set:
                                completed_set.discard(target)
                                next_activated.add(target)
                            elif target_in.issubset(completed_set):
                                next_activated.add(target)
                            else:
                                print(f"  [OASIS] ⏳ Selector target '{target}' deferred")
                        elif target == END:
                            reached_end = True
                    else:
                        # No valid choice found — default to first choice
                        if se.choices:
                            first_key = min(se.choices.keys())
                            target = se.choices[first_key]
                            print(f"  [OASIS] ⚠️ Selector '{nid}' no valid choice found in output, defaulting to [{first_key}] → {target}")
                            self.forum.log_event("selector_default", detail=f"default [{first_key}] → {target}")
                            if target and target != END:
                                target_in = sched.in_sources.get(target, set())
                                if target in completed_set:
                                    completed_set.discard(target)
                                    next_activated.add(target)
                                elif target_in.issubset(completed_set):
                                    next_activated.add(target)
                            elif target == END:
                                reached_end = True

            # If END was reached and no other nodes activated, stop
            if reached_end and not next_activated:
                print(f"  [OASIS] 🏁 Reached END at super-step {super_step}")
                break

            # Check: nodes with no outgoing edges that just completed = implicit END
            if not next_activated:
                # All activated nodes had no outgoing edges → implicit end
                all_terminal = all(
                    not sched.out_edges.get(nid) and not sched.out_cond_edges.get(nid) and not sched.out_selector_edges.get(nid)
                    for nid in activated_list
                )
                if all_terminal:
                    print(f"  [OASIS] 🏁 All terminal nodes completed at super-step {super_step}")
                    break

            activated = next_activated

        if super_step >= MAX_SUPER_STEPS:
            print(f"  [OASIS] ⚠️ Max super-steps ({MAX_SUPER_STEPS}) reached, stopping graph")
            self.forum.log_event("graph_max_steps", detail=f"stopped at {super_step}")

        self.forum.log_event("graph_end", detail=f"completed in {super_step} super-steps")
        print(f"  [OASIS] 🏁 Graph completed in {super_step} super-steps, {self._total_node_execs} node executions")

    def _build_visibility_filter_graph(self, node_id: str, completed_set: set[str]) -> dict:
        """根据节点的上游节点构建可见性过滤器。

        在执行模式（非讨论模式）：
          Agent 只能看到来自直接上游（入边源）节点的帖子。
        在讨论模式下：不进行过滤（返回空字典）。
        """
        if self._discussion:
            return {}

        sched = self.schedule
        # Find all nodes that have edges pointing TO this node
        upstream_ids = sched.in_sources.get(node_id, set())
        # Only include completed upstream nodes
        active_upstream = upstream_ids & completed_set

        if not active_upstream:
            return {"visible_authors": set()}

        upstream_authors: set[str] = set()
        for uid in active_upstream:
            up_node = sched.node_map.get(uid)
            if up_node:
                agents = self._resolve_experts(up_node.expert_names)
                for a in agents:
                    upstream_authors.add(a.name)
        for post in self.forum.posts:
            if post.source_node_id in active_upstream:
                upstream_authors.add(post.author)
        return {"visible_authors": upstream_authors}

    async def _eval_condition(self, condition: str) -> bool:
        """根据当前论坛状态评估条件表达式。

        支持的表达式：
          last_post_contains:<keyword>       — 最后一帖内容包含关键字
          last_post_not_contains:<keyword>   — 最后一帖内容不包含关键字
          post_count_gte:<N>                 — 总帖数 >= N
          post_count_lt:<N>                  — 总帖数 < N
          always                             — 始终为真
          !<expr>                            — 取反任意表达式
        """
        expr = condition.strip()

        # Handle negation prefix
        if expr.startswith("!"):
            inner = expr[1:].strip()
            return not await self._eval_condition(inner)

        if expr == "always":
            return True

        # Get last post for content-based conditions
        all_posts = await self.forum.browse()
        last_post_content = all_posts[-1].content if all_posts else ""

        if expr.startswith("last_post_contains:"):
            keyword = expr.split(":", 1)[1]
            return keyword in last_post_content

        if expr.startswith("last_post_not_contains:"):
            keyword = expr.split(":", 1)[1]
            return keyword not in last_post_content

        if expr.startswith("post_count_gte:"):
            n = int(expr.split(":", 1)[1])
            return len(all_posts) >= n

        if expr.startswith("post_count_lt:"):
            n = int(expr.split(":", 1)[1])
            return len(all_posts) < n

        print(f"  [OASIS] ⚠️ Unknown condition expression: '{expr}', treating as false")
        return False

    async def _execute_node(self, step: ScheduleStep, vis: dict | None = None):
        """执行单个图节点。"""
        disc = self._discussion
        if vis is None:
            vis = {}

        if step.step_type == StepType.MANUAL:
            print(f"  [OASIS] 📝 Manual post by {step.manual_author}")
            self.forum.log_event("manual_post", agent=step.manual_author)
            await self.forum.publish(
                author=step.manual_author,
                content=step.manual_content,
                reply_to=step.manual_reply_to,
                source_node_id=step.node_id,
            )

        elif step.step_type == StepType.SCRIPT:
            await self._execute_script_node(step)

        elif step.step_type == StepType.HUMAN:
            await self._execute_human_node(step)

        elif step.step_type == StepType.ALL:
            print(f"  [OASIS] 👥 All experts speak")
            for expert in self.experts:
                self.forum.log_event("agent_call", agent=expert.name)

            async def _tracked_participate(expert):
                try:
                    await expert.participate(self.forum, discussion=disc, **vis)
                finally:
                    self.forum.log_event("agent_done", agent=expert.name)

            await asyncio.gather(
                *[_tracked_participate(e) for e in self.experts],
                return_exceptions=True,
            )

        elif step.step_type == StepType.EXPERT:
            agents = self._resolve_experts(step.expert_names)
            if agents:
                instr = step.instructions.get(step.expert_names[0], "")
                # For selector nodes: inject choice prompt
                selector_instr = ""
                if step.is_selector:
                    se = self.schedule.out_selector_edges.get(step.node_id)
                    if se and se.choices:
                        choices_desc = []
                        for num in sorted(se.choices.keys()):
                            target_id = se.choices[num]
                            target_node = self.schedule.node_map.get(target_id)
                            target_name = target_node.expert_names[0] if target_node and target_node.expert_names else target_id
                            choices_desc.append(f"  选择 {num} → {target_name} ({target_id})")
                        selector_instr = (
                            "\n\n⚠️ SELECTOR INSTRUCTION:\n"
                            "你需要根据上下文选择下一步操作。可选路径如下：\n"
                            + "\n".join(choices_desc) +
                            '\n\n🔴 关键格式要求（必须严格遵守）：\n'
                            '你可以先进行分析和推理，但在回复中必须包含一个 JSON 对象来表达你的最终选择，'
                            '格式如下（不要包含 markdown 代码块标记，不要包含注释）：\n'
                            '{"clawcross_type": "oasis choose", "choose": N, "content": "选择理由"}\n\n'
                            '其中 N 是你选择的编号（数字），例如：\n'
                            '{"clawcross_type": "oasis choose", "choose": 1, "content": "选择路径1，因为..."}\n'
                            '{"clawcross_type": "oasis choose", "choose": 2, "content": "选择路径2，因为..."}\n\n'
                            '⚠️ 重要：\n'
                            '- JSON 前后可以有其他文字（分析推理等），系统会自动提取 JSON 部分\n'
                            '- "clawcross_type" 必须为 "oasis choose"\n'
                            '- "choose" 必须是一个数字，对应上面的选择编号\n'
                            '- 不输出此格式将导致默认选择第一项\n'
                        )
                combined_instr = (instr + selector_instr) if instr else selector_instr
                print(f"  [OASIS] 🎤 {agents[0].name} speaks" + (f" (instruction: {combined_instr[:60]}...)" if combined_instr else "") + (" [SELECTOR]" if step.is_selector else ""))
                self.forum.log_event("agent_call", agent=agents[0].name, detail=combined_instr[:80] if combined_instr else "")
                await agents[0].participate(self.forum, instruction=combined_instr, discussion=disc, **vis)
                self.forum.log_event("agent_done", agent=agents[0].name)

        elif step.step_type == StepType.PARALLEL:
            agents = self._resolve_experts(step.expert_names)
            if agents:
                names = ", ".join(a.name for a in agents)
                print(f"  [OASIS] 🎤 Parallel: {names}")
                for agent, yaml_name in zip(agents, step.expert_names):
                    par_instr = step.instructions.get(yaml_name, "")
                    self.forum.log_event("agent_call", agent=agent.name, detail=par_instr[:80] if par_instr else "")

                async def _run_with_instr(agent, yaml_name):
                    instr = step.instructions.get(yaml_name, "")
                    try:
                        await agent.participate(self.forum, instruction=instr, discussion=disc, **vis)
                    finally:
                        self.forum.log_event("agent_done", agent=agent.name, detail=instr[:80] if instr else "")

                await asyncio.gather(
                    *[_run_with_instr(a, n) for a, n in zip(agents, step.expert_names)],
                    return_exceptions=True,
                )

    async def _consensus_reached(self) -> bool:
        """检查是否达成共识（最高赞帖子获得 >= 70% 专家点赞）。"""
        top = await self.forum.get_top_posts(1)
        if not top:
            return False
        threshold = len(self.experts) * 0.7
        return top[0].upvotes >= threshold

    async def _summarize(self) -> str:
        """总结讨论：将最高赞帖子发送给 LLM 生成综合结论。"""
        top_posts = await self.forum.get_top_posts(5)
        all_posts = await self.forum.browse()

        if not top_posts:
            return "讨论未产生有效观点。"

        posts_text = "\n".join([
            f"[👍{p.upvotes} 👎{p.downvotes}] {p.author}: {p.content}"
            for p in top_posts
        ])

        if _SUMMARY_PROMPT_TPL:
            prompt = _SUMMARY_PROMPT_TPL.format(
                question=self.forum.question,
                post_count=len(all_posts),
                round_count=self.forum.current_round,
                posts_text=posts_text,
            )
        else:
            prompt = (
                f"你是一个讨论总结专家。以下是关于「{self.forum.question}」的多专家讨论结果。\n\n"
                f"共 {len(all_posts)} 条帖子，经过 {self.forum.current_round} 轮讨论。\n\n"
                f"获得最高认可的观点:\n{posts_text}\n\n"
                "请综合以上高赞观点，给出一个全面、平衡、有结论性的最终回答（300字以内）。\n"
                "要求:\n"
                "1. 清晰概括各方核心观点\n"
                "2. 指出主要共识和分歧\n"
                "3. 给出明确的结论性建议\n"
            )

        try:
            resp = await self.summarizer.ainvoke([HumanMessage(content=prompt)])
            return extract_text(resp.content)
        except Exception as e:
            return f"总结生成失败: {str(e)}"
