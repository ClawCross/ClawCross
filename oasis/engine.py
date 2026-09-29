"""
OASIS Forum - 讨论引擎

管理讨论的完整生命周期：
  轮次循环 → 调度/并行参与者发言 → 共识检查 → 总结

参与者（仅来自 YAML，schedule_file 优先于 schedule_yaml）一律是 agent：
  agent: <ref>     用户的常驻 agent；在 team 里先按角色名找，再按 handle / 地址 / ag_ 编号
  persona: <tag>   为本话题临时创建的 agent，人设取自人设库；
                   tools: none（默认，每轮一次模型调用）| all | [工具名] （临时 WeBot 会话，话题结束即删除）
  `all_experts: true` 让计划里出现过的所有参与者并行发言。

执行：遵循 YAML 调度，定义每一步的发言顺序（repeat: true 时整轮循环）。
"""

import asyncio
import json
import os
import platform
import re
import sys

from langchain_core.messages import HumanMessage

# 确保 src/ 在 import 路径中，以便导入 llm_factory
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))
from services.llm_factory import create_chat_model, extract_text
from utils.runtime_paths import USER_FILES_DIR

from oasis.forum import DiscussionForum
from oasis.experts import get_all_experts
from oasis.participants import Participant
from oasis.scheduler import (
    Schedule, ScheduleStep, StepType, Edge, ConditionalEdge, SelectorEdge,
    START, END, MAX_SUPER_STEPS,
    parse_schedule, load_schedule_file, extract_expert_names, collect_participant_configs,
)

# Maximum total node executions across all super-steps (safety limit)
_MAX_TOTAL_NODE_EXECS = 500

# Project root for team-scoped paths
_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


def _ephemeral_session_id(topic_id: str, tag: str, instance: str) -> str:
    """``tmp__<topic>__<tag>__<n>``: unique per topic participant, safe as a session id."""
    import hashlib

    slug = re.sub(r"[^a-z0-9_-]+", "-", (tag or "").lower()).strip("-_")[:24]
    if not slug:
        slug = "p" + hashlib.sha1((tag or "").encode("utf-8")).hexdigest()[:8]
    part = re.sub(r"[^a-z0-9]+", "", str(instance).lower())[:8] or "1"
    return f"tmp__{topic_id}__{slug}__{part}"


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


def resolve_agent(user_id: str, team: str, ref: str):
    """``(agent, name)`` a workflow means by *ref*: in team mode a member's name first;
    then ``<team>.<name>`` or an agent id — an id not seen before is a new agent."""
    from agents.store import get_store, valid_agent_id
    from teams.store import get_team_store

    ref = (ref or "").strip()
    store = get_store()
    teams = get_team_store(store)
    if team and teams.exists(user_id, team):
        try:
            member = teams.member(user_id, team, ref)
            return member.agent, member.role
        except LookupError:
            pass
    agent = store.get(user_id, ref) or teams.address(user_id, ref)
    if agent is None:
        if not valid_agent_id(ref):
            raise LookupError(f"no agent {ref!r}")
        agent = store.ensure(user_id, ref)
    return agent, agent.name


class DiscussionEngine:
    """
    协调一个完整的讨论会话。

    流程：
      1. 按调度定义的顺序执行步骤
      2. 每轮后检查是否达成共识
      3. 完成后（达成共识或达到最大轮数），将最高赞帖子总结为结论

    参与者池由 YAML 中的 agent: / persona: 构建（去重），见模块说明。
    """

    def __init__(
        self,
        forum: DiscussionForum,
        schedule: Schedule | None = None,
        schedule_yaml: str | None = None,
        schedule_file: str | None = None,
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
                "For a simple round, use: version: 1\\nplan:\\n  - parallel:\\n      - persona: creative\\n      - persona: critical"
            )

        # discussion mode: API override > YAML setting > default False
        if self._discussion_override is not None:
            self._discussion = self._discussion_override
        else:
            self._discussion = self.schedule.discussion

        # ── Step 2: the participants the plan names ──
        configs = collect_participant_configs(self.schedule)
        self.experts: list[Participant] = []
        self._expert_map: dict[str, Participant] = {}
        for key in extract_expert_names(self.schedule):
            participant = self._participant(key, configs.get(key, {}), user_id, bot_enabled_tools, bot_timeout)
            if participant is not None:
                self.experts.append(participant)
                self._expert_map[key] = participant
        for participant in self.experts:
            self._expert_map.setdefault(participant.name, participant)
            if participant.tag:
                self._expert_map.setdefault(participant.tag, participant)
        self._total_node_execs = 0  # safety counter for Pregel super-step execution

        self.summarizer = _get_summarizer()

    def _unique_name(self, name: str) -> str:
        """Forum authors are told apart by name."""
        taken = {p.name for p in self.experts}
        unique, n = name, 1
        while unique in taken:
            n += 1
            unique = f"{name} ({n})"
        return unique

    def _participant(self, key: str, config: dict, user_id: str, tools: list[str] | None,
                     timeout: float | None) -> Participant | None:
        from utils.effort_controller import resolve_default_chat_max_output_tokens

        kind, _, rest = key.partition(":")
        if kind == "agent":
            try:
                agent, role = resolve_agent(user_id, self._team, rest)
            except LookupError as exc:
                print(f"  [OASIS] ⚠️ {exc}; skipping.")
                return None
            print(f"  [OASIS] 🏠 {key} → {agent.agent_id} ({agent.platform})")
            return Participant(user_id, agent.agent_id, name=self._unique_name(role or agent.name),
                               tag=agent.persona, tools=tools, timeout=timeout)

        tag, _, instance = rest.rpartition(":")
        preset = self._lookup_by_tag(tag, user_id, self._team) or {}
        title = str(preset.get("name") or tag)
        name = self._unique_name(title if instance == "1" else f"{title} #{instance}")
        persona = str(preset.get("persona") or "")
        llm = {k: preset[k] for k in ("model", "api_key", "base_url", "provider") if preset.get(k)}
        agent_id = _ephemeral_session_id(self.forum.topic_id, tag, instance)
        if not config.get("tools"):  # one model call per turn
            llm.update(temperature=float(preset.get("temperature", 0.7)),
                       max_tokens=resolve_default_chat_max_output_tokens())
            make = {"agent_id": agent_id, "name": name, "platform": "llm", "llm": llm}
            return Participant(user_id, agent_id, name=name, tag=tag, persona=persona, timeout=timeout,
                               make=make, remembers=False)
        make = {"agent_id": agent_id, "name": name, "platform": "webot", "llm": llm}
        chosen = None if config["tools"] == "all" else list(config["tools"])
        print(f"  [OASIS] 🧪 {key} → temporary WeBot session {agent_id} (tools={config['tools']})")
        return Participant(user_id, agent_id, name=name, tag=tag, persona=persona, tools=chosen, timeout=timeout,
                           make=make)

    async def _discard_ephemeral_sessions(self) -> None:
        """Delete the temporary agents this topic made (its personas)."""
        for participant in self.experts:
            if not participant.temporary:
                continue
            try:
                await participant.discard()
            except Exception as exc:
                print(f"  [OASIS] ⚠️ could not discard {participant.name}: {exc}")

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
            return os.path.join(str(USER_FILES_DIR), self._user_id, "teams", self._team)
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

        resident_count = sum(1 for e in self.experts if not e.temporary)
        mode_label = "discussion" if self._discussion else "execute"
        n_nodes = len(self.schedule.nodes)
        n_edges = len(self.schedule.edges) + len(self.schedule.conditional_edges)
        print(
            f"[OASIS] 🏛️ Discussion started: {self.forum.topic_id} "
            f"({len(self.experts)} participants [{resident_count} resident, {len(self.experts) - resident_count} temporary], "
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
            self.forum.status = "cancelled"
            self.forum.conclusion = "讨论已被用户强制终止"

        except Exception as e:
            print(f"[OASIS] ❌ Discussion error: {e}")
            self.forum.status = "error"
            self.forum.conclusion = f"讨论过程中出现错误: {str(e)}"

        finally:
            await self._discard_ephemeral_sessions()

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
                    await expert.participate(self.forum, discussion=disc, source_node_id=step.node_id, **vis)
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
                await agents[0].participate(
                    self.forum,
                    instruction=combined_instr,
                    discussion=disc,
                    source_node_id=step.node_id,
                    is_selector=step.is_selector,
                    **vis,
                )
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
                        await agent.participate(
                            self.forum,
                            instruction=instr,
                            discussion=disc,
                            source_node_id=step.node_id,
                            **vis,
                        )
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
