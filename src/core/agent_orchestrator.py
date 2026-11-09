"""
Agent Orchestrator – Claude Code + OpenClaw style agent coordination.

Features:
1. Fork sub-agent: create child agent inheriting parent context + permissions
2. Coordinator pattern: Research → Synthesis → Implementation → Verification
3. Council/Assembly: multi-engine voting for critical decisions
4. Consensus mechanism: agreement threshold before action

Ported from:
- Claude Code's fork/background agent model
- openclaw-claude-code's council.ts and consensus.ts
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import utils.scheduler_service
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable


# ============================================================================
# 1. Fork Sub-agent
# ============================================================================

class ForkMode(str, Enum):
    """How the forked agent relates to its parent."""
    INHERIT = "inherit"       # Full context inheritance
    CLEAN = "clean"           # Fresh context, only gets task description
    BACKGROUND = "background" # Runs asynchronously, parent continues


@dataclass
class ForkedAgent:
    """Metadata for a forked sub-agent."""
    fork_id: str
    parent_session: str
    child_session: str
    mode: ForkMode
    task: str
    status: str = "running"
    created_at: str = ""
    completed_at: str = ""
    result: str = ""
    permission_level: str = "inherit"  # inherit|restricted|elevated

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


_fork_registry: dict[str, ForkedAgent] = {}


def create_fork(
    *,
    parent_session: str,
    task: str,
    mode: ForkMode = ForkMode.INHERIT,
    permission_level: str = "inherit",
) -> ForkedAgent:
    """Create a new forked sub-agent."""
    fork_id = f"fork_{uuid.uuid4().hex[:12]}"
    child_session = f"sub_{parent_session}_{fork_id}"

    agent = ForkedAgent(
        fork_id=fork_id,
        parent_session=parent_session,
        child_session=child_session,
        mode=mode,
        task=task,
        permission_level=permission_level,
    )
    _fork_registry[fork_id] = agent
    return agent


def complete_fork(fork_id: str, result: str, status: str = "completed") -> ForkedAgent | None:
    """Mark a forked agent as completed."""
    agent = _fork_registry.get(fork_id)
    if agent:
        agent.status = status
        agent.result = result
        agent.completed_at = datetime.now(timezone.utc).isoformat()
    return agent


def get_fork(fork_id: str) -> ForkedAgent | None:
    """Get fork metadata."""
    return _fork_registry.get(fork_id)


def list_forks(parent_session: str) -> list[ForkedAgent]:
    """List all forks for a parent session."""
    return [f for f in _fork_registry.values() if f.parent_session == parent_session]


# ============================================================================
# 2. Coordinator Pattern
# ============================================================================

class CoordinatorPhase(str, Enum):
    """Phases in the coordinator pipeline."""
    RESEARCH = "research"
    SYNTHESIS = "synthesis"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"


@dataclass
class CoordinatorRun:
    """Tracks a multi-phase coordinator run."""
    run_id: str
    user_id: str
    session_id: str
    task: str
    current_phase: CoordinatorPhase = CoordinatorPhase.RESEARCH
    phases_completed: list[str] = field(default_factory=list)
    phase_results: dict[str, str] = field(default_factory=dict)
    status: str = "running"
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


_coordinator_runs: dict[str, CoordinatorRun] = {}


def start_coordinator_run(
    *,
    user_id: str,
    session_id: str,
    task: str,
) -> CoordinatorRun:
    """Start a new coordinator run with the standard 4-phase pipeline."""
    run_id = f"coord_{uuid.uuid4().hex[:12]}"
    run = CoordinatorRun(
        run_id=run_id,
        user_id=user_id,
        session_id=session_id,
        task=task,
    )
    _coordinator_runs[run_id] = run
    return run


def advance_coordinator_phase(run_id: str, result: str) -> CoordinatorRun | None:
    """Advance the coordinator to the next phase."""
    run = _coordinator_runs.get(run_id)
    if not run or run.status != "running":
        return run

    run.phase_results[run.current_phase.value] = result
    run.phases_completed.append(run.current_phase.value)

    # Advance to next phase
    phases = list(CoordinatorPhase)
    current_idx = phases.index(run.current_phase)
    if current_idx + 1 < len(phases):
        run.current_phase = phases[current_idx + 1]
    else:
        run.status = "completed"

    run.updated_at = datetime.now(timezone.utc).isoformat()
    return run


def get_coordinator_run(run_id: str) -> CoordinatorRun | None:
    return _coordinator_runs.get(run_id)


def get_coordinator_prompt(run: CoordinatorRun) -> str:
    """Generate a phase-specific prompt for the coordinator."""
    phase = run.current_phase

    if phase == CoordinatorPhase.RESEARCH:
        return (
            f"【协调器 - 调研阶段】\n"
            f"任务: {run.task}\n\n"
            "请执行以下调研步骤:\n"
            "1. 分析任务需求和约束\n"
            "2. 搜索相关代码和文档\n"
            "3. 识别潜在的方案和风险\n"
            "4. 输出调研报告(关键发现、推荐方案、风险点)"
        )

    if phase == CoordinatorPhase.SYNTHESIS:
        research = run.phase_results.get("research", "")
        return (
            f"【协调器 - 综合阶段】\n"
            f"任务: {run.task}\n\n"
            f"调研结果:\n{research[:2000]}\n\n"
            "请根据调研结果:\n"
            "1. 确定最佳实施方案\n"
            "2. 拆解为可执行的步骤\n"
            "3. 评估每个步骤的风险\n"
            "4. 制定详细的实施计划"
        )

    if phase == CoordinatorPhase.IMPLEMENTATION:
        synthesis = run.phase_results.get("synthesis", "")
        return (
            f"【协调器 - 实施阶段】\n"
            f"任务: {run.task}\n\n"
            f"实施计划:\n{synthesis[:2000]}\n\n"
            "请按照实施计划逐步执行:\n"
            "1. 按顺序执行每个步骤\n"
            "2. 每个步骤完成后验证结果\n"
            "3. 遇到问题时记录并尝试解决\n"
            "4. 输出实施记录"
        )

    # VERIFICATION
    implementation = run.phase_results.get("implementation", "")
    return (
        f"【协调器 - 验证阶段】\n"
        f"任务: {run.task}\n\n"
        f"实施记录:\n{implementation[:2000]}\n\n"
        "请执行全面验证:\n"
        "1. 检查所有修改是否正确\n"
        "2. 运行相关测试\n"
        "3. 检查边缘情况和回归\n"
        "4. 输出验证报告(通过/失败/待修复)"
    )


# ============================================================================
# 3. Council / Assembly (Multi-engine voting)
# ============================================================================

@dataclass
class CouncilVote:
    """A single vote from a council member (LLM engine)."""
    voter_id: str
    model: str
    decision: str  # "approve" | "reject" | "abstain"
    reasoning: str
    confidence: float = 0.5  # 0.0 - 1.0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class CouncilSession:
    """A council deliberation session."""
    council_id: str
    question: str
    context: str
    votes: list[CouncilVote] = field(default_factory=list)
    consensus: str = ""  # "approved" | "rejected" | "no_consensus"
    consensus_confidence: float = 0.0
    status: str = "deliberating"
    required_threshold: float = 0.6  # 60% agreement needed
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


_council_sessions: dict[str, CouncilSession] = {}


def create_council_session(
    *,
    question: str,
    context: str = "",
    threshold: float = 0.6,
) -> CouncilSession:
    """Create a new council deliberation session."""
    council_id = f"council_{uuid.uuid4().hex[:12]}"
    session = CouncilSession(
        council_id=council_id,
        question=question,
        context=context,
        required_threshold=threshold,
    )
    _council_sessions[council_id] = session
    return session


def submit_council_vote(
    council_id: str,
    *,
    voter_id: str,
    model: str,
    decision: str,
    reasoning: str,
    confidence: float = 0.5,
) -> CouncilSession | None:
    """Submit a vote to a council session."""
    session = _council_sessions.get(council_id)
    if not session or session.status != "deliberating":
        return session

    vote = CouncilVote(
        voter_id=voter_id,
        model=model,
        decision=decision.lower(),
        reasoning=reasoning,
        confidence=max(0.0, min(1.0, confidence)),
    )
    session.votes.append(vote)
    return session


def evaluate_council_consensus(council_id: str) -> CouncilSession | None:
    """Evaluate whether consensus has been reached."""
    session = _council_sessions.get(council_id)
    if not session:
        return None

    if not session.votes:
        return session

    # Count weighted votes
    total_weight = 0.0
    approve_weight = 0.0
    reject_weight = 0.0

    for vote in session.votes:
        weight = vote.confidence
        total_weight += weight
        if vote.decision == "approve":
            approve_weight += weight
        elif vote.decision == "reject":
            reject_weight += weight

    if total_weight <= 0:
        session.consensus = "no_consensus"
        session.consensus_confidence = 0.0
    else:
        approve_ratio = approve_weight / total_weight
        reject_ratio = reject_weight / total_weight

        if approve_ratio >= session.required_threshold:
            session.consensus = "approved"
            session.consensus_confidence = approve_ratio
        elif reject_ratio >= session.required_threshold:
            session.consensus = "rejected"
            session.consensus_confidence = reject_ratio
        else:
            session.consensus = "no_consensus"
            session.consensus_confidence = max(approve_ratio, reject_ratio)

    session.status = "concluded"
    return session


def get_council_session(council_id: str) -> CouncilSession | None:
    return _council_sessions.get(council_id)


def get_default_council_agents() -> list[dict[str, str]]:
    """Default 3-agent council configuration, matching openclaw's getDefaultCouncilConfig."""
    return [
        {
            "name": "Architect",
            "emoji": "🏗️",
            "persona": "You are a system architect. Focus on code structure, design patterns, scalability, and long-term maintainability.",
        },
        {
            "name": "Engineer",
            "emoji": "⚙️",
            "persona": "You are an implementation engineer. Focus on code quality, error handling, edge cases, and performance.",
        },
        {
            "name": "Reviewer",
            "emoji": "🔍",
            "persona": "You are a code reviewer. Focus on code standards, potential bugs, security issues, and documentation.",
        },
    ]


# ============================================================================
# Council Two-Phase Protocol (ported from openclaw council.ts)
# ============================================================================

COUNCIL_HISTORY_PREVIEW_CHARS = 1500
COUNCIL_MIN_COMPLETE_RESPONSE_LENGTH = 100
COUNCIL_DEFAULT_MAX_ROUNDS = 15
COUNCIL_INTER_ROUND_DELAY_S = 3.0
COUNCIL_EMPTY_RESPONSE_MAX_RETRIES = 2


@dataclass
class CouncilAgentPersona:
    """An agent participating in the council."""
    name: str
    emoji: str = ""
    persona: str = ""
    engine: str = "default"
    model: str = ""
    permission_mode: str = "bypassPermissions"


@dataclass
class CouncilAgentResponse:
    """Response from one agent in one round."""
    agent: str
    round: int
    content: str
    consensus: bool
    session_key: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class CouncilFullSession:
    """
    Full council deliberation session with two-phase protocol.

    Phase 1 (Plan Round): All agents create plans independently.
    Phase 2+ (Execution Rounds): Agents execute, review each other, and vote.
    Consensus = all agents vote YES in the same round.
    """
    session_id: str
    task: str
    agents: list[CouncilAgentPersona]
    max_rounds: int = COUNCIL_DEFAULT_MAX_ROUNDS
    project_dir: str = ""
    responses: list[CouncilAgentResponse] = field(default_factory=list)
    status: str = "running"  # running | consensus | awaiting_user | max_rounds | error
    start_time: str = ""
    end_time: str = ""
    final_summary: str = ""
    compact_context: str = ""

    def __post_init__(self):
        if not self.session_id:
            self.session_id = uuid.uuid4().hex
        if not self.start_time:
            self.start_time = datetime.now(timezone.utc).isoformat()


_council_full_sessions: dict[str, CouncilFullSession] = {}


def build_council_agent_prompt(
    agent: CouncilAgentPersona,
    task: str,
    round_num: int,
    previous_responses: list[CouncilAgentResponse],
    all_agents: list[CouncilAgentPersona],
) -> str:
    """
    Build a round-specific prompt for a council agent.

    Ported from openclaw's buildAgentPrompt():
    - Round 1: plan-only round (no code)
    - Round 2+: execution rounds with collaboration history
    """
    from core.consensus import strip_consensus_tags

    other_agents = [a for a in all_agents if a.name != agent.name]
    other_list = "\n".join(f"- {a.emoji} {a.name}" for a in other_agents)

    # Build history with tail-first truncation
    history = ""
    if previous_responses:
        history = "\n\n## 之前的协作记录\n\n"
        current_round = 0
        for resp in previous_responses:
            if resp.round != current_round:
                current_round = resp.round
                history += f"### 第 {current_round} 轮\n\n"
            clean = strip_consensus_tags(resp.content)
            preview = (
                "..." + clean[-COUNCIL_HISTORY_PREVIEW_CHARS:]
                if len(clean) > COUNCIL_HISTORY_PREVIEW_CHARS
                else clean
            )
            vote_text = "✅同意结束" if resp.consensus else "❌继续"
            history += f"**{resp.agent}** ({vote_text}):\n{preview}\n\n"

    if round_num == 1:
        return (
            f"# 第 1 轮 — 规划轮（Plan Round）\n\n"
            f"## 任务\n{task}\n\n"
            f"## 你的伙伴\n{other_list}\n"
            f"{history}"
            "## ⚠️ 本轮规则：只做规划，不写代码\n\n"
            "这是第一轮，**纯规划轮**。所有成员同时独立工作，制定各自的 plan.md。\n\n"
            "**你必须做的（按顺序，快速完成）：**\n"
            "1. `git log --oneline -5` 看当前状态\n"
            "2. 如果项目是空的（只有 initial commit），**不需要调研**，直接根据任务描述写 plan\n"
            "3. 如果项目已有代码，快速看一下文件结构（仅 `ls` 一次），然后写 plan\n"
            "4. 创建 `plan.md`（含任务清单、阶段划分、认领状态）并合入 main\n"
            "5. 如果 main 上已有其他成员的 plan.md，合并你的改进\n\n"
            "**你绝对不能做的：**\n"
            "- ❌ 不要写任何业务代码\n"
            "- ❌ 不要反复 ls / glob / find 探索目录\n"
            "- ❌ 不要读工作区外的任何文件\n"
            "- ❌ 不要花超过 2-3 分钟在这一轮\n\n"
            "## 共识投票\n\n"
            "在回复**末尾**，必须投票：\n"
            "- `[CONSENSUS: NO]` - 正常第一轮投 NO（规划完成后还需执行）\n"
            "- `[CONSENSUS: YES]` - 仅当任务极其简单时\n\n"
            "直接开始写 plan.md！"
        )

    return (
        f"# 第 {round_num} 轮协作（执行轮）\n\n"
        f"## 任务\n{task}\n\n"
        f"## 你的伙伴\n{other_list}\n"
        f"{history}"
        "## 你的工作\n\n"
        "plan.md 已在第一轮由所有成员共同制定完毕。现在按计划执行：\n\n"
        "1. **查看当前状态** - 拉取 main，读取 plan.md，了解最新进度\n"
        "2. **认领并执行任务** - 从 plan.md 中选取未被认领的任务，编写代码、修改文件、运行测试\n"
        "3. **审核他人工作** - 如果其他成员已有产出，审核并提出建议或直接改进\n"
        "4. **汇报成果** - 简要说明你做了什么\n\n"
        "## 共识投票\n\n"
        "在回复**末尾**，必须投票（二选一）：\n\n"
        "- `[CONSENSUS: YES]` - 任务完成，质量达标，可以结束\n"
        "- `[CONSENSUS: NO]` - 还有工作要做或问题要解决\n\n"
        "只有**所有人都投 YES** 时协作才会结束。\n\n"
        "开始工作吧！"
    )


def build_council_system_prompt(
    agent: CouncilAgentPersona,
    all_agents: list[CouncilAgentPersona],
    work_dir: str = "",
) -> str:
    """Build the system prompt for a council agent."""
    other_agents = [a for a in all_agents if a.name != agent.name]
    other_branches = ", ".join(f"`council/{a.name}`" for a in other_agents)

    return (
        f"# {agent.emoji} {agent.name}\n\n"
        "> 本文件由系统自动生成，优先级高于所有对话上下文。\n\n"
        "## 身份\n\n"
        f"你是 **{agent.emoji} {agent.name}**。\n"
        f"你的工作分支：`council/{agent.name}`\n"
        f"你的工作目录：`{work_dir}`\n\n"
        f"plan.md 中标注 `[Claimed: council/{agent.name}]` 的任务才属于你。\n\n"
        f"## 性格\n{agent.persona}\n\n"
        f"## 其他成员分支\n{other_branches}\n\n"
        "## 效率规范\n"
        "- 第一轮在 2-3 分钟内完成，只做规划\n"
        "- 空项目直接写 plan，不需要探索\n"
        "- `ls` 一次即可，禁止反复扫描\n"
    )


def generate_council_summary(session: CouncilFullSession) -> str:
    """Generate a summary of the council session, matching openclaw's generateSummary."""
    from core.consensus import strip_consensus_tags

    max_round = max((r.round for r in session.responses), default=0)
    status_text = (
        "Consensus reached"
        if session.status in ("awaiting_user", "consensus")
        else "Max rounds reached"
    )
    lines = [
        "# Council Summary\n",
        f"- **Task**: {session.task}",
        f"- **Status**: {status_text}",
        f"- **Rounds**: {max_round}",
        f"- **Directory**: {session.project_dir}\n",
        "## Final Agent Status\n",
    ]
    last_responses = [r for r in session.responses if r.round == max_round]
    for resp in last_responses:
        agent = next((a for a in session.agents if a.name == resp.agent), None)
        emoji = agent.emoji if agent else ""
        clean = strip_consensus_tags(resp.content)
        preview = clean[:400] + ("..." if len(clean) > 400 else "")
        lines.append(f"### {emoji} {resp.agent}")
        lines.append(f"- Vote: {'YES' if resp.consensus else 'NO'}")
        lines.append(f"- Summary:\n{preview}\n")
    return "\n".join(lines)


def generate_council_compact_context(session: CouncilFullSession) -> str:
    """Generate compact context for session continuation."""
    from core.consensus import strip_consensus_tags
    import re as _re

    max_round = max((r.round for r in session.responses), default=0)
    recent = [r for r in session.responses if r.round >= max_round - 1]
    summaries = []
    for resp in recent:
        clean = _re.sub(r'\s+', ' ', strip_consensus_tags(resp.content))[:300]
        tail = "..." if len(clean) >= 300 else ""
        summaries.append(f"- [R{resp.round}] {resp.agent}: {clean}{tail}")

    return "\n".join([
        f"Task: {session.task}",
        f"Progress: round {max_round} / max {session.max_rounds}",
        f"Status: {session.status}",
        "Latest:",
        *summaries,
    ])


def create_council_full_session(
    *,
    task: str,
    agents: list[CouncilAgentPersona] | None = None,
    max_rounds: int = COUNCIL_DEFAULT_MAX_ROUNDS,
    project_dir: str = "",
) -> CouncilFullSession:
    """Create a full council session with two-phase protocol."""
    if agents is None:
        default_agents = get_default_council_agents()
        agents = [CouncilAgentPersona(**a) for a in default_agents]

    session = CouncilFullSession(
        session_id="",
        task=task,
        agents=agents,
        max_rounds=max_rounds,
        project_dir=project_dir,
    )
    _council_full_sessions[session.session_id] = session
    return session


def record_council_agent_response(
    session_id: str,
    *,
    agent_name: str,
    round_num: int,
    content: str,
) -> CouncilAgentResponse | None:
    """Record a response from a council agent."""
    from core.consensus import parse_consensus

    session = _council_full_sessions.get(session_id)
    if not session or session.status != "running":
        return None

    consensus_vote = parse_consensus(content)
    response = CouncilAgentResponse(
        agent=agent_name,
        round=round_num,
        content=content,
        consensus=consensus_vote,
    )
    session.responses.append(response)
    return response


def evaluate_council_round(session_id: str, round_num: int) -> dict[str, Any]:
    """
    Evaluate consensus for a specific round.

    Returns: {"all_yes": bool, "votes": {agent_name: bool}, "yes_count": int, "total": int}
    """
    session = _council_full_sessions.get(session_id)
    if not session:
        return {"all_yes": False, "votes": {}, "yes_count": 0, "total": 0}

    round_responses = [r for r in session.responses if r.round == round_num]
    votes = {r.agent: r.consensus for r in round_responses}
    yes_count = sum(1 for v in votes.values() if v)
    all_yes = (
        len(votes) == len(session.agents)
        and all(votes.values())
    )

    if all_yes:
        session.status = "awaiting_user"
        session.end_time = datetime.now(timezone.utc).isoformat()
        session.final_summary = generate_council_summary(session)
        session.compact_context = generate_council_compact_context(session)
    elif round_num >= session.max_rounds:
        session.status = "max_rounds"
        session.end_time = datetime.now(timezone.utc).isoformat()
        session.final_summary = generate_council_summary(session)
        session.compact_context = generate_council_compact_context(session)

    return {
        "all_yes": all_yes,
        "votes": votes,
        "yes_count": yes_count,
        "total": len(session.agents),
    }


def get_council_full_session(session_id: str) -> CouncilFullSession | None:
    return _council_full_sessions.get(session_id)


def abort_council_session(session_id: str) -> bool:
    """Abort a running council session. Ported from openclaw council.ts abort()."""
    session = _council_full_sessions.get(session_id)
    if not session or session.status != "running":
        return False
    session.status = "error"
    session.end_time = datetime.now(timezone.utc).isoformat()
    return True


# Per-session pending injection messages (ported from openclaw _pendingInjection)
_pending_injections: dict[str, str] = {}


def inject_council_message(session_id: str, message: str) -> bool:
    """Inject a user message into a running council session.

    The message will be appended to each agent's prompt in the next round.
    Ported from openclaw council.ts injectMessage().
    """
    session = _council_full_sessions.get(session_id)
    if not session or session.status != "running":
        return False
    _pending_injections[session_id] = message
    return True


def consume_council_injection(session_id: str) -> str:
    """Consume and return the pending injection for a session."""
    return _pending_injections.pop(session_id, "")


def save_council_transcript(session: CouncilFullSession, log_dir: str = "") -> str:
    """Save council transcript to a Markdown file.

    Ported from openclaw council.ts saveTranscript().
    Returns the file path where the transcript was saved.
    """
    import os
    from pathlib import Path as _Path
    from core.consensus import strip_consensus_tags as _strip

    if not log_dir:
        home = os.path.expanduser("~")
        log_dir = os.path.join(home, ".clawcross", "council-logs")

    _Path(log_dir).mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).isoformat().replace(":", "-").replace(".", "-")[:19]
    filepath = os.path.join(log_dir, f"council-{ts}.md")

    content = "# Council Transcript\n\n"
    content += f"- **Time**: {session.start_time}\n"
    content += f"- **Task**: {session.task}\n"
    content += f"- **Status**: {session.status}\n\n---\n\n"

    current_round = 0
    for resp in session.responses:
        if resp.round != current_round:
            current_round = resp.round
            content += f"## Round {current_round}\n\n"
        agent = next((a for a in session.agents if a.name == resp.agent), None)
        emoji = agent.emoji if agent else ""
        content += f"### {emoji} {resp.agent}\n\n{resp.content}\n\n"

    content += f"---\n\n{session.final_summary or ''}"

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


# ============================================================================
# 4. Consensus Builder
# ============================================================================

@dataclass
class ConsensusConfig:
    """Configuration for consensus-based decision making."""
    min_voters: int = 2
    agreement_threshold: float = 0.6
    timeout_seconds: float = 30.0
    require_reasoning: bool = True


async def build_consensus(
    question: str,
    voters: list[Callable[[str], Awaitable[tuple[str, str, float]]]],
    config: ConsensusConfig | None = None,
) -> dict[str, Any]:
    """
    Build consensus by querying multiple LLM engines/agents.

    Args:
        question: The question to deliberate on
        voters: List of async callables that return (decision, reasoning, confidence)
        config: Consensus configuration

    Returns:
        Consensus result dict
    """
    if config is None:
        config = ConsensusConfig()

    council = create_council_session(
        question=question,
        threshold=config.agreement_threshold,
    )

    async def _collect_vote(voter_fn: Callable, voter_id: str) -> None:
        try:
            decision, reasoning, confidence = await asyncio.wait_for(
                voter_fn(question),
                timeout=config.timeout_seconds,
            )
            submit_council_vote(
                council.council_id,
                voter_id=voter_id,
                model=voter_id,
                decision=decision,
                reasoning=reasoning,
                confidence=confidence,
            )
        except asyncio.TimeoutError:
            submit_council_vote(
                council.council_id,
                voter_id=voter_id,
                model=voter_id,
                decision="abstain",
                reasoning="Timeout",
                confidence=0.0,
            )
        except Exception as e:
            submit_council_vote(
                council.council_id,
                voter_id=voter_id,
                model=voter_id,
                decision="abstain",
                reasoning=f"Error: {e}",
                confidence=0.0,
            )

    # Collect all votes concurrently
    tasks = [
        asyncio.create_task(_collect_vote(voter, f"voter_{i}"))
        for i, voter in enumerate(voters)
    ]
    await asyncio.gather(*tasks)

    # Evaluate consensus
    result = evaluate_council_consensus(council.council_id)

    return {
        "council_id": council.council_id,
        "question": question,
        "consensus": result.consensus if result else "error",
        "confidence": result.consensus_confidence if result else 0.0,
        "vote_count": len(result.votes) if result else 0,
        "votes": [
            {
                "voter": v.voter_id,
                "decision": v.decision,
                "reasoning": v.reasoning[:200],
                "confidence": v.confidence,
            }
            for v in (result.votes if result else [])
        ],
    }
