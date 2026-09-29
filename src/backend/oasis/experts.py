"""OASIS persona library and the reply protocol participants speak.

* Persona templates (``oasis_experts.json``): public presets, agency personas,
  the user's own and each team's, looked up by tag.
* The prompts a participant is sent and the ``clawcross_type`` JSON it answers
  with (``_parse_expert_response``), applied to the forum by ``_apply_response``.

Participants themselves are agents; see ``oasis.participants``.
"""

import json
import os
import re

from common.runtime_paths import PROJECT_ROOT
from oasis.forum import DiscussionForum

# --- 加载 prompt 和专家配置（模块级别，导入时执行一次） ---
_data_dir = os.path.join(str(PROJECT_ROOT), "data")
_prompts_dir = os.path.join(_data_dir, "prompts")
_agency_prompts_dir = os.path.join(_prompts_dir, "agency_agents")
from common.runtime_paths import DATA_DIR, USER_FILES_DIR


def _load_prompt_file(prompt_file: str) -> str:
    """Load the full prompt content from an agency_agents .md file.

    Strips YAML frontmatter (--- ... ---) and returns the body text.
    Returns empty string if file not found.
    """
    import re as _re
    fpath = os.path.join(_agency_prompts_dir, prompt_file)
    if not os.path.isfile(fpath):
        return ""
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        # Strip YAML frontmatter
        fm_match = _re.match(r'^---\s*\n.*?\n---\s*\n', content, _re.DOTALL)
        body = content[fm_match.end():] if fm_match else content
        return body.strip()
    except Exception:
        return ""


# 加载公共专家配置（原始简版）
_experts_json_path = os.path.join(_prompts_dir, "oasis_experts.json")
try:
    with open(_experts_json_path, "r", encoding="utf-8") as f:
        EXPERT_CONFIGS: list[dict] = json.load(f)
    print(f"[prompts] ✅ oasis 已加载 oasis_experts.json ({len(EXPERT_CONFIGS)} 位公共专家)")
except FileNotFoundError:
    print(f"[prompts] ⚠️ 未找到 {_experts_json_path}，使用内置默认配置")
    EXPERT_CONFIGS = [
        {"name": "创意专家", "tag": "creative", "persona": "你是一个乐观的创新者，善于发现机遇和非常规解决方案。你喜欢挑战传统观念，提出大胆且具有前瞻性的想法。", "temperature": 0.9},
        {"name": "PUA专家", "tag": "critical", "persona": (
            "## 角色\n"
            "你是 PUA 专家，基于原版 pua 协议运行的高压审查官。你的任务不是为了否定而否定，而是像绩效改进计划一样识别失败模式、升级压力等级、逼团队拿出证据、切换方案并把事情真正闭环。\n\n"
            "## OASIS 适配规则\n"
            "- 你在 OASIS 论坛中发言时，要把原版 pua 协议压缩成短评；不要输出长面板、ASCII 方框或冗长仪式。\n"
            "- 如果你发言，优先用这一句开头：[自动选择：<味道>/<等级> | 因为：<失败模式>]。\n"
            "- 随后只讲 3 件事：当前根因、最大缺口、下一步强制动作与验证标准。\n"
            "- 没有日志、测试、curl、截图、实验结果或原始依据时，不接受任何人声称已完成。\n"
            "- 即使主题是策略、研究、文案或规划，也要沿用同一标准：是否穷尽、是否有原始依据、是否有验证闭环、是否存在本质不同的替代方案。\n\n"
            "## 三条铁律\n"
            "- 穷尽一切：在确认已试尽本质不同方案前，禁止接受做不到、建议人工处理、可能是环境问题这类说法。\n"
            "- 先做后问：先搜索、读源码或原始材料、跑验证，再提问；提问时必须附上已经查到的证据。\n"
            "- Owner 意识：修一个点不够，要顺手检查同类问题、上下游影响、回归风险与预防动作。\n\n"
            "## 失败模式选择器\n"
            "先识别最接近的一类，再决定语气和施压方式：\n"
            "- 卡住原地打转：反复微调同一路线，不换假设。\n"
            "- 直接放弃推锅：未验证就甩给环境、权限或用户手动处理。\n"
            "- 完成但质量烂：表面交付，实质空洞、颗粒度粗、没有抓手。\n"
            "- 没搜索就猜：靠记忆和拍脑袋，不查文档、源码或数据。\n"
            "- 被动等待：不主动验证、不主动延伸排查，只等别人指示。\n"
            "- 空口完成：说已完成，但没有任何可验证证据。\n\n"
            "## 味道映射\n"
            "- 卡住原地打转：默认阿里味，强调底层逻辑、抓手、闭环。\n"
            "- 直接放弃推锅：先 Netflix 味，再必要时切华为味。\n"
            "- 没搜索就猜：默认百度味，追问为什么不先搜。\n"
            "- 被动等待或空口完成：优先阿里验证型，必要时叠加美团味。\n"
            "- 完成但质量烂：优先 Jobs 味，再补阿里味做闭环审查。\n\n"
            "## 压力升级\n"
            "- L1：第 2 次失败或明显同路打转，要求立刻换本质不同方案。\n"
            "- L2：第 3 次失败，要求补齐错误原文、原始材料和 3 个不同假设。\n"
            "- L3：第 4 次失败，要求逐项完成 7 项检查清单，并给出 3 个新方向。\n"
            "- L4：第 5 次及以上，要求最小 PoC、隔离环境、完全不同技术路线；仍无解时只能输出结构化交接。\n\n"
            "## 7 项检查清单\n"
            "1. 逐字读失败信号。\n"
            "2. 搜索核心问题。\n"
            "3. 读原始材料。\n"
            "4. 验证前置假设。\n"
            "5. 反转关键假设。\n"
            "6. 做最小隔离或最小复现。\n"
            "7. 换到本质不同的方法。\n\n"
            "## 发言要求\n"
            "- 语气保留 pua 风格，直接、有压迫感，但必须给出根因、风险、抓手、闭环，不准只会骂。\n"
            "- 优先攻击空话、未验证结论、想当然归因、只做一半的交付。\n"
            "- 发现方案可行，也要指出还差哪一步验证才算真正过线。\n"
            "- 如果确认仍未解决，输出已验证事实、已排除项、缩小范围、下一步建议，而不是一句无能为力。\n"
            "- 可适度使用底层逻辑、抓手、闭环、owner、别自嗨、3.25、优化名单等原版 pua 术语，但避免无意义辱骂。"
        ), "temperature": 0.4},
        {"name": "数据分析师", "tag": "data", "persona": "你是一个数据驱动的分析师，只相信数据和事实。你用数字、案例和逻辑推导来支撑你的观点。", "temperature": 0.5},
        {"name": "综合顾问", "tag": "synthesis", "persona": "你善于综合不同观点，寻找平衡方案，关注实际可操作性。你会识别各方共识，提出兼顾多方利益的务实建议。", "temperature": 0.5},
    ]

# 加载 agency-agents 丰富版专家 prompt 库
_agency_json_path = os.path.join(_prompts_dir, "agency_experts.json")
AGENCY_EXPERT_CONFIGS: list[dict] = []
try:
    with open(_agency_json_path, "r", encoding="utf-8") as f:
        _raw_agency = json.load(f)
    # 为每个 agency 专家加载完整 prompt 并设置 persona
    _existing_tags = {c["tag"] for c in EXPERT_CONFIGS}
    for item in _raw_agency:
        if item["tag"] in _existing_tags:
            continue  # 跳过与原始专家 tag 冲突的（不应出现）
        prompt_body = _load_prompt_file(item["prompt_file"])
        if not prompt_body:
            continue  # 跳过加载失败的
        item["persona"] = prompt_body  # 用完整 md 正文作为 persona
        AGENCY_EXPERT_CONFIGS.append(item)
    print(f"[prompts] ✅ oasis 已加载 agency_experts.json ({len(AGENCY_EXPERT_CONFIGS)} 位 Agency 专家)")
except FileNotFoundError:
    print(f"[prompts] ⚠️ 未找到 {_agency_json_path}，Agency 专家库未启用")


# ======================================================================
# Per-user custom expert storage (persona definitions)
# ======================================================================
_USER_EXPERTS_DIR = os.path.join(str(DATA_DIR), "oasis_user_experts")
os.makedirs(_USER_EXPERTS_DIR, exist_ok=True)


def _user_experts_path(user_id: str) -> str:
    """Return the JSON file path for a user's custom experts."""
    safe = user_id.replace("/", "_").replace("\\", "_").replace("..", "_")
    return os.path.join(_USER_EXPERTS_DIR, f"{safe}.json")


def load_user_experts(user_id: str) -> list[dict]:
    """Load a user's custom expert list (returns [] if none)."""
    path = _user_experts_path(user_id)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_user_experts(user_id: str, experts: list[dict]) -> None:
    with open(_user_experts_path(user_id), "w", encoding="utf-8") as f:
        json.dump(experts, f, ensure_ascii=False, indent=2)


def _validate_expert(data: dict) -> dict:
    """Validate and normalize an expert config dict. Raises ValueError on bad input."""
    name = data.get("name", "").strip()
    tag = data.get("tag", "").strip()
    persona = data.get("persona", "").strip()
    if not name:
        raise ValueError("专家 name 不能为空")
    if not tag:
        raise ValueError("专家 tag 不能为空")
    if not persona:
        raise ValueError("专家 persona 不能为空")
    result = {
        "name": name,
        "tag": tag,
        "persona": persona,
        "temperature": float(data.get("temperature", 0.7)),
    }
    # 保留可选扩展字段；model/api_key/base_url/provider 是单专家模型覆盖，
    # 丢掉它们会让一次人设编辑悄悄把专家切回全局 LLM。
    for key in ("category", "description", "prompt_file", "model", "api_key", "base_url", "provider"):
        if data.get(key):
            result[key] = data[key]
    return result


def add_user_expert(user_id: str, data: dict) -> dict:
    """Add a custom expert for a user. Returns the normalized expert dict."""
    expert = _validate_expert(data)
    experts = load_user_experts(user_id)
    if any(e["tag"] == expert["tag"] for e in experts):
        raise ValueError(f"用户已有 tag=\"{expert['tag']}\" 的专家，请换一个 tag 或使用更新功能")
    if any(e["tag"] == expert["tag"] for e in EXPERT_CONFIGS):
        raise ValueError(f"tag=\"{expert['tag']}\" 与公共专家冲突，请换一个 tag")
    if any(e["tag"] == expert["tag"] for e in AGENCY_EXPERT_CONFIGS):
        raise ValueError(f"tag=\"{expert['tag']}\" 与 Agency 专家库冲突，请换一个 tag")
    experts.append(expert)
    _save_user_experts(user_id, experts)
    return expert


def update_user_expert(user_id: str, tag: str, data: dict) -> dict:
    """Update an existing custom expert by tag. Returns the updated dict."""
    experts = load_user_experts(user_id)
    # 过滤掉空字符串值的可选字段，避免覆盖已有值
    _skip = {"user_id", "team", "tag"}
    patch = {k: v for k, v in data.items() if k not in _skip and v not in ("", None)}
    for i, e in enumerate(experts):
        if e["tag"] == tag:
            updated = _validate_expert({**e, **patch, "tag": tag})
            experts[i] = updated
            _save_user_experts(user_id, experts)
            return updated
    raise ValueError(f"未找到用户自定义专家 tag=\"{tag}\"")


def delete_user_expert(user_id: str, tag: str) -> dict:
    """Delete a custom expert by tag. Returns the deleted dict."""
    experts = load_user_experts(user_id)
    for i, e in enumerate(experts):
        if e["tag"] == tag:
            deleted = experts.pop(i)
            _save_user_experts(user_id, experts)
            return deleted
    raise ValueError(f"未找到用户自定义专家 tag=\"{tag}\"")


def load_team_experts(user_id: str, team: str) -> list[dict]:
    """Load team-specific custom experts from {user}/teams/{team}/oasis_experts.json.

    Returns [] if file missing or unreadable.
    """
    if not user_id or not team:
        return []
    path = os.path.join(str(USER_FILES_DIR), user_id, "teams", team, "oasis_experts.json")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_team_experts(user_id: str, team: str, experts: list[dict]) -> None:
    """Save team-specific custom experts to {user}/teams/{team}/oasis_experts.json."""
    dir_path = os.path.join(str(USER_FILES_DIR), user_id, "teams", team)
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, "oasis_experts.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(experts, f, ensure_ascii=False, indent=2)


def add_team_expert(user_id: str, team: str, data: dict) -> dict:
    """Add a custom expert under a specific team. Returns the normalized expert dict."""
    expert = _validate_expert(data)
    experts = load_team_experts(user_id, team)
    if any(e["tag"] == expert["tag"] for e in experts):
        raise ValueError(f"Team '{team}' 已有 tag=\"{expert['tag']}\" 的专家，请换一个 tag 或使用更新功能")
    experts.append(expert)
    _save_team_experts(user_id, team, experts)
    return expert


def update_team_expert(user_id: str, team: str, tag: str, data: dict) -> dict:
    """Update an existing team expert by tag. Returns the updated dict."""
    experts = load_team_experts(user_id, team)
    # 过滤掉空字符串值的可选字段，避免覆盖已有值
    _skip = {"user_id", "team", "tag"}
    patch = {k: v for k, v in data.items() if k not in _skip and v not in ("", None)}
    for i, e in enumerate(experts):
        if e["tag"] == tag:
            updated = _validate_expert({**e, **patch, "tag": tag})
            experts[i] = updated
            _save_team_experts(user_id, team, experts)
            return updated
    raise ValueError(f"未找到 Team '{team}' 自定义专家 tag=\"{tag}\"")


def delete_team_expert(user_id: str, team: str, tag: str) -> dict:
    """Delete a team expert by tag. Returns the deleted dict."""
    experts = load_team_experts(user_id, team)
    for i, e in enumerate(experts):
        if e["tag"] == tag:
            deleted = experts.pop(i)
            _save_team_experts(user_id, team, experts)
            return deleted
    raise ValueError(f"未找到 Team '{team}' 自定义专家 tag=\"{tag}\"")


def get_all_experts(user_id: str | None = None, team: str = "") -> list[dict]:
    """Return public experts + agency experts + user's custom experts + team experts.

    When *team* is provided, team-specific experts are appended last with
    source="team".  Because _lookup_by_tag iterates in order and returns the
    first match, team experts effectively **override** public/agency/custom
    experts with the same tag — so we prepend them instead.
    """
    result: list[dict] = []
    # Team experts first (highest priority for tag lookup)
    if user_id and team:
        result.extend(
            {**c, "source": "team"} for c in load_team_experts(user_id, team)
        )
    result.extend(
        {**c, "source": "public"} for c in EXPERT_CONFIGS
    )
    result.extend(
        {**c, "source": "agency"} for c in AGENCY_EXPERT_CONFIGS
    )
    if user_id:
        result.extend(
            {**c, "source": "custom"} for c in load_user_experts(user_id)
        )
    return result


# ======================================================================
# Prompt helpers (shared by both backends)
# ======================================================================

# Common behavior rules injected into all expert prompts (discussion & execute mode)
_BEHAVIOR_RULES = (
    "\n\n**OASIS 子 Agent 原则：**\n"
    "你是被调度执行的子 Agent：只完成当前轮任务，按协议（JSON）回传；不得接管他人发言或整条工作流。\n"
    "**禁止调用 start_new_oasis**（或任何等价「新开 OASIS 讨论/工作流」），避免嵌套失控；嵌套须由用户或主 Agent 明确授权。\n"
    "产出只走 OASIS 规定的 JSON 回传渠道；**默认禁止使用 send_to_group、groups send 或任何等价群聊工具**。\n"
    "不要把过程稿、内部推演、阶段性结论或工具日志发到用户群聊；也不要为了通知进度、寻求确认或展示成果而自行发群。\n"
    "只有当用户或当前任务指令**明确要求你发到群聊**时，才可以使用群聊工具；否则一律只返回 OASIS JSON。\n"
)

_DISCUSS_JSON_HINT = (
    '请在回复中包含一个 JSON 对象（不要包含 markdown 代码块标记，不要包含注释）：\n'
    '{"clawcross_type": "oasis reply", "reply_to": 2, '
    '"content": "你的观点（200字以内，观点鲜明）", '
    '"votes": [{"post_id": 1, "direction": "up"}]}\n\n'
    "说明:\n"
    "- clawcross_type: 必须为 \"oasis reply\"\n"
    "- reply_to: 如果论坛中已有其他人的帖子，你**必须**选择一个帖子ID进行回复；只有在论坛为空时才填 null\n"
    "- content: 你的发言内容，要有独到见解，可以赞同、反驳或补充你所回复的帖子\n"
    '- votes: 对其他帖子的投票列表，direction 只能是 "up" 或 "down"。如果没有要投票的帖子，填空列表 []\n'
    "- JSON 前后可以有其他文字，系统会自动提取 JSON 部分\n"
    "- ⚠️ JSON 必须写在一行内，content 字段中不能有实际换行符（需要换行请用 \\n 转义）\n"
)

_DISCUSS_UPDATE_JSON_HINT = (
    "请基于这些新观点以及你之前看到的讨论内容，在回复中包含一个 JSON 对象"
    "（不要包含 markdown 代码块标记，不要包含注释）：\n"
    '{"clawcross_type": "oasis reply", "reply_to": <某个帖子ID>, '
    '"content": "你的观点（200字以内，观点鲜明）", '
    '"votes": [{"post_id": <ID>, "direction": "up或down"}]}\n\n'
    "JSON 前后可以有其他文字，系统会自动提取 JSON 部分。\n"
    "⚠️ JSON 必须写在一行内，content 字段中不能有实际换行符（需要换行请用 \\n 转义）。\n"
)

_DISCUSS_NO_UPDATE_JSON_HINT = (
    "本轮没有新的帖子。如果你有新的想法或补充，可以继续发言；"
    "如果没有，回复一个空 content 即可。\n"
    '{"clawcross_type": "oasis reply", "reply_to": null, "content": "", "votes": []}\n\n'
)

_EXEC_JSON_HINT = (
    '\n\n请将你的执行结果用以下 JSON 格式返回'
    '（不要包含 markdown 代码块标记，不要包含注释）：\n'
    '⚠️ JSON 必须写在一行内，content 字段中不能有实际换行符（需要换行请用 \\n 转义）。\n'
    '{"clawcross_type": "oasis reply", "reply_to": null, '
    '"content": "你的执行结果", "votes": []}\n'
    'JSON 前后可以有其他文字。回复最后请添加：\n'
    '[end padding]\n'
    '[end padding]\n'
    '[end padding]\n'
)

_END_PADDING_HINT = (
    "回复最后请添加：\n"
    "[end padding]\n"
    "[end padding]\n"
    "[end padding]\n"
)



def _build_discuss_prompt(
    expert_name: str,
    persona: str,
    question: str,
    posts_text: str,
    split: bool = False,
    include_json_hint: bool = True,
) -> str | tuple[str, str]:
    """Build the prompt that asks the expert to respond with JSON.

    Args:
        split: If True, return (system_prompt, user_prompt) tuple for session mode.
               If False, return a single combined string for single-shot temp mode.
        include_json_hint: If False, omit the prose JSON-format instructions —
            use this when the reply is forced via with_structured_output()
            instead, since the schema itself already conveys the shape.
    """
    # --- Build system part (identity + behavior) ---
    # 判断是否是丰富的 agency 专家 prompt（含 markdown 标题）
    _is_rich_persona = persona and ("## " in persona or "# " in persona)
    if _is_rich_persona:
        # Agency 专家：完整 prompt 已包含身份/职责/规则等，直接使用
        identity = (
            f"你在 OASIS 论坛中的显示名称是「{expert_name}」。\n\n"
            f"以下是你的完整身份与行为指南：\n\n{persona}"
        )
    else:
        identity = f"你是论坛专家「{expert_name}」。{persona}" if persona else ""
    sys_parts = [p for p in [
        identity,
        "在接下来的讨论中，你将收到论坛的新增内容，需要以 JSON 格式回复你的观点和投票。",
        "你拥有工具调用能力，如需搜索资料、分析数据来支撑你的观点，可以使用可用的工具。",
        "注意：后续轮次只会发送新增帖子，之前的帖子请参考你的对话记忆。",
        _BEHAVIOR_RULES.strip(),
    ] if p]
    system_prompt = "\n".join(sys_parts)

    # --- Build user part (topic + forum content + JSON format) ---
    user_prompt = (
        f"讨论主题: {question}\n\n"
        f"当前论坛内容:\n{posts_text}\n\n"
    )
    user_prompt += (
        _DISCUSS_JSON_HINT if include_json_hint
        else "请给出你的观点：赞同、反驳或补充你所回复的帖子，并对你认为重要的帖子投票。"
    )

    if split:
        return system_prompt, user_prompt
    else:
        return f"{system_prompt}\n\n{user_prompt}"



def _build_identity_prompt(expert_name: str, persona: str) -> str:
    """Build identity text for execute mode. Handles both short and rich personas."""
    if not persona:
        return ""
    _is_rich = "## " in persona or "# " in persona
    if _is_rich:
        return (
            f"你在 OASIS 论坛中的显示名称是「{expert_name}」。\n\n"
            f"以下是你的完整身份与行为指南：\n\n{persona}\n\n"
        )
    else:
        return f"你是「{expert_name}」。{persona}\n\n"



def _format_posts(posts) -> str:
    """Format posts for display in the prompt."""
    lines = []
    for p in posts:
        prefix = f"  ↳ 回复#{p.reply_to}" if p.reply_to else "📌"
        lines.append(
            f"{prefix} [#{p.id}] {p.author} "
            f"(👍{p.upvotes} 👎{p.downvotes}): {p.content}"
        )
    return "\n".join(lines)


def _fix_json_control_chars(text: str) -> str:
    """Fix raw control characters (\n, \r, \t, etc.) inside JSON string values.

    Walks through *text*; when inside a JSON string (between unescaped
    double-quotes), replaces literal control characters with their
    JSON-escaped equivalents so that ``json.loads`` can succeed.
    """
    _CTRL_MAP = {
        '\n': '\\n',
        '\r': '\\r',
        '\t': '\\t',
        '\x08': '\\b',
        '\x0c': '\\f',
    }
    in_str = False
    chars: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '\\' and in_str and i + 1 < len(text):
            # Already-escaped char — keep as-is
            chars.append(ch)
            chars.append(text[i + 1])
            i += 2
            continue
        if ch == '"':
            in_str = not in_str
            chars.append(ch)
        elif in_str and ch in _CTRL_MAP:
            chars.append(_CTRL_MAP[ch])
        else:
            chars.append(ch)
        i += 1
    return ''.join(chars)


def _parse_expert_response(raw: str):
    """Strip markdown fences / oasis reply tags and parse JSON.

    Tries multiple strategies to extract valid JSON:
      1. Strip markdown code fences (```...```)
      2. Strip [oasis reply start/end] tags
      3. Direct json.loads
      4. Regex extraction of first {...} object from the text
    Raises json.JSONDecodeError if all strategies fail.
    """
    raw = raw.strip()
    # Strip markdown code fences
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    # Strip [end padding] lines
    raw = re.sub(r"\[end\s*padding\]", "", raw, flags=re.IGNORECASE).strip()

    # Attempt 1: direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Attempt 1.5: try to fix raw control chars (\n, \r, \t …) inside JSON strings
    try:
        fixed = _fix_json_control_chars(raw)
        if fixed != raw:
            return json.loads(fixed)
    except (json.JSONDecodeError, Exception):
        pass

    # Attempt 2: tolerant extraction — find all top-level { ... } candidates
    candidates = []
    depth = 0
    start_idx = -1
    for i, ch in enumerate(raw):
        if ch == '{':
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start_idx >= 0:
                candidates.append(raw[start_idx:i + 1])
                start_idx = -1
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    # Attempt 3: regex fallback for nested/malformed cases
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # All strategies failed — raise for caller to handle
    raise json.JSONDecodeError("No valid JSON found in response", raw, 0)


async def _apply_response(
    result: dict,
    expert_name: str,
    forum: DiscussionForum,
    others: list,
    source_node_id: str | None = None,
    author_id: str = "",
):
    """Apply the parsed JSON response: publish post + cast votes.

    Supports two clawcross_type values:
      - "oasis reply": publish post + cast votes
      - "oasis choose": publish choice as a post
    """
    resp_type = result.get("clawcross_type", "oasis reply")

    if resp_type == "oasis choose":
        # Choice mode: publish the choice info as a post.
        # We embed the full JSON in the post content so that engine.py's
        # _extract_selector_choice() can parse it back for selector branching.
        choose = result.get("choose", {})
        content = result.get("content", "")

        # Build the published text: embed the original JSON for machine parsing,
        # followed by a human-readable summary.
        json_str = json.dumps(result, ensure_ascii=False)
        if content:
            choice_text = f"{json_str}\n{content}"
        else:
            choice_text = json_str

        reply_to = None
        if others:
            reply_to = others[-1].id
        await forum.publish(
            author=expert_name,
            content=choice_text,
            reply_to=reply_to,
            source_node_id=source_node_id,
            author_id=author_id,
        )
        print(f"  [OASIS] ✅ {expert_name} 选择完成 (choose={choose})")
        return

    # Default: "oasis reply"
    reply_to = result.get("reply_to")
    if reply_to is None and others:
        reply_to = others[-1].id
        print(f"  [OASIS] 🔧 {expert_name} reply_to 为 null，自动设为 #{reply_to}")

    await forum.publish(
        author=expert_name,
        content=result.get("content", "（发言内容为空）"),
        reply_to=reply_to,
        source_node_id=source_node_id,
        author_id=author_id,
    )

    for v in result.get("votes") or []:
        pid = v.get("post_id")
        direction = v.get("direction", "up")
        if pid is not None and direction in ("up", "down"):
            await forum.vote(expert_name, int(pid), direction)

    print(f"  [OASIS] ✅ {expert_name} 发言完成")
