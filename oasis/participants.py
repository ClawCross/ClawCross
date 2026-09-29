"""An OASIS participant: an agent taking part in a topic.

Every participant is an agent reached through the agent gateway — a resident
agent of the user (``agent: <ref>``), or a temporary one made for the topic
(``persona: <tag>``; a single model call, or a throwaway WeBot session when it
needs tools). The participant only decides what to say to its agent and what
to post from the answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from agents.gateway import NO_TIMEOUT, get_gateway
from agents.messages import AgentMessage, AgentReply
from agents.store import Agent
from oasis.experts import (
    _BEHAVIOR_RULES,
    _DISCUSS_JSON_HINT,
    _DISCUSS_NO_UPDATE_JSON_HINT,
    _DISCUSS_UPDATE_JSON_HINT,
    _EXEC_JSON_HINT,
    _apply_response,
    _build_discuss_prompt,
    _build_identity_prompt,
    _format_posts,
    _parse_expert_response,
)
from oasis.forum import DiscussionForum
from oasis.schemas import OasisChooseOut, OasisReplyOut

logger = logging.getLogger(__name__)


async def _ask(agent: Agent, msg: AgentMessage, **kwargs: Any) -> AgentReply:
    """Ask on a worker thread, so a slow agent never stalls the forum."""
    return await asyncio.to_thread(lambda: asyncio.run(get_gateway().ask(agent, msg, **kwargs)))


class Participant:
    """One seat in a topic, held by one agent.

    ``persona`` is set for temporary agents, which have no identity of their
    own; a resident agent speaks as itself. An agent that remembers the topic
    (anything but a single model call) is sent only what is new after its first turn.
    """

    def __init__(
        self,
        agent: Agent,
        *,
        name: str,
        tag: str = "",
        persona: str = "",
        tools: list[str] | None = None,
        timeout: float | None = None,
    ):
        self.agent = agent
        self.name = name
        self.title = name
        self.tag = tag
        self.persona = persona
        self.tools = tools
        self.timeout = timeout
        self.agent_id = agent.agent_id
        self.temporary = agent.temporary
        self._remembers = agent.remembers
        self._started = False
        self._seen: set[int] = set()

    # ── prompt ───────────────────────────────────────────────────────────

    def _identity(self) -> str:
        return _build_identity_prompt(self.title, self.persona).strip() if self.persona else ""

    def _execute_prompt(self, forum: DiscussionForum, instruction: str, others: list, new: list) -> str:
        if self._started and self._remembers:
            parts = [f"【第 {forum.current_round} 轮】"]
            if instruction:
                parts.append(f"执行指令: {instruction}")
            if new:
                parts.append(f"其他 agent 的新结果:\n{_format_posts(new)}")
            parts.append("请继续执行任务并返回结果。")
        else:
            parts = [f"任务主题: {forum.question}"]
            if instruction:
                parts.append(f"\n执行指令: {instruction}")
            if others:
                parts.append(f"\n前序 agent 的执行结果:\n{_format_posts(others)}")
            parts.append("\n请直接执行任务并返回结果。")
        return "\n".join([*parts, _BEHAVIOR_RULES, _EXEC_JSON_HINT])

    def _discuss_prompt(self, forum: DiscussionForum, instruction: str, others: list, new: list) -> str:
        focus = f"\n\n📋 本轮你的专项指令：{instruction}\n请在回复中重点关注和执行这个指令。" if instruction else ""
        if self._started and self._remembers:
            if new:
                return (f"【第 {forum.current_round} 轮讨论更新】\n以下是自你上次发言后的 {len(new)} 条新帖子：\n\n"
                        f"{_format_posts(new)}\n\n{_BEHAVIOR_RULES.strip()}\n{_DISCUSS_UPDATE_JSON_HINT}{focus}")
            return f"【第 {forum.current_round} 轮讨论更新】\n{_BEHAVIOR_RULES.strip()}\n{_DISCUSS_NO_UPDATE_JSON_HINT}{focus}"
        posts = _format_posts(others) if others else "(还没有其他人发言，你来开启讨论吧)"
        if self.persona:
            _system, user = _build_discuss_prompt(self.title, self.persona, forum.question, posts, split=True)
            return user + focus
        return (
            "你被 OASIS 工作流邀请参加一场多专家讨论，请从你自身的专业视角发表观点，"
            "不要代替其他专家发言或接管整个讨论。\n\n"
            f"讨论主题: {forum.question}\n\n当前论坛内容:\n{posts}\n\n{_BEHAVIOR_RULES.strip()}\n"
            f"{_DISCUSS_JSON_HINT}{focus}"
        )

    # ── one turn ─────────────────────────────────────────────────────────

    async def _reply(self, text: str, *, is_selector: bool, execute: bool) -> dict | str:
        """The agent's answer as parsed OASIS JSON, or its raw text when it gives none."""
        schema = OasisChooseOut if is_selector else OasisReplyOut
        options = {"tools": self.tools, "response_format": schema, "timeout": NO_TIMEOUT if execute else self.timeout}
        reply = await _ask(self.agent, AgentMessage(text=text, instructions=self._identity()), **options)
        if not reply.ok:
            raise RuntimeError(reply.error or "agent call failed")
        try:
            return _parse_expert_response(reply.content or "")
        except json.JSONDecodeError:
            return reply.content or ""

    async def participate(
        self,
        forum: DiscussionForum,
        instruction: str = "",
        discussion: bool = True,
        visible_authors: set[str] | None = None,
        from_round: int | None = None,
        source_node_id: str | None = None,
        is_selector: bool = False,
    ):
        """Speak once: in a discussion (reply and vote) or on a task (execute mode)."""
        others = await forum.browse(
            viewer=self.name,
            exclude_self=True,
            visible_authors=visible_authors if not discussion else None,
            from_round=from_round if not discussion else None,
        )
        new = [p for p in others if p.id not in self._seen]
        self._seen.update(p.id for p in others)
        build = self._discuss_prompt if discussion else self._execute_prompt
        text = build(forum, instruction, others, new)
        self._started = True
        try:
            result = await self._reply(text, is_selector=is_selector, execute=not discussion)
        except Exception as exc:
            print(f"  [OASIS] ❌ {self.name} error: {exc}")
            await forum.publish(author=self.name, content=f"[调用失败] {str(exc)[:2000]}",
                                source_node_id=source_node_id, author_id=self.agent_id)
            return
        if isinstance(result, dict):
            await _apply_response(result, self.name, forum, others, source_node_id=source_node_id,
                                  author_id=self.agent_id)
        elif result.strip():
            await forum.publish(author=self.name, content=result.strip()[:2000],
                                source_node_id=source_node_id, author_id=self.agent_id)
