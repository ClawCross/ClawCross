"""Posting into a conversation and waking the members it is for.

Every member can read every message; ``delivery.select_wake_targets`` picks
the agents to wake, each is sent the same kind of envelope through the agent
gateway, and a member that was not woken for a while gets an unread digest the
next time it is. How an agent posts back is its runtime's business
(``gateway.reply_channel``); this module never looks at drivers.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from agents.gateway import AgentGateway, reply_channel
from agents.messages import AgentMessage, AgentReply
from agents.store import Agent, AgentStore
from comms.delivery import StormGuard, WakeRequest, mentions_everyone, render_digest, select_wake_targets
from comms.store import DIRECT, HUMAN_PREFIX, Conversation, ConversationStore, Message, human, is_agent

logger = logging.getLogger(__name__)

_DIGEST_LIMIT = 15
_ASCII_WORD_CHAR = re.compile(r"[A-Za-z0-9_]")
_TYPING_TIMEOUT_SEC = 120


class NotAMember(PermissionError):
    pass


def resolve_text_mentions(content: str, members: list[tuple[str, str]]) -> list[str]:
    """Principals written as ``@name`` in *content*; *members* is ``(name, principal)``.

    Longer names claim their text first (``@Code Reviewer`` is not also ``@Code``);
    a name ending in an ASCII word character needs a boundary after it (``@Codex``
    is not ``@Code``); an ``@`` glued to a preceding word (``a@b.io``) is no mention.
    """
    lowered = (content or "").lower()
    claimed = [False] * len(lowered)
    found: list[str] = []
    for name, principal in sorted(members, key=lambda item: len(item[0]), reverse=True):
        needle = "@" + name.lower()
        start = 0
        while (idx := lowered.find(needle, start)) >= 0:
            start = idx + 1
            end = idx + len(needle)
            if any(claimed[idx:end]):
                continue
            if idx > 0 and _ASCII_WORD_CHAR.match(lowered[idx - 1]):
                continue
            if _ASCII_WORD_CHAR.match(needle[-1]) and end < len(lowered) and _ASCII_WORD_CHAR.match(lowered[end]):
                continue
            claimed[idx:end] = [True] * (end - idx)
            if principal not in found:
                found.append(principal)
    return found


@dataclass(frozen=True, slots=True)
class MemberView:
    principal: str
    name: str
    agent: Agent | None
    muted: bool
    nickname: str


class Conversations:
    def __init__(self, store: ConversationStore, agents: AgentStore, gateway: AgentGateway, *,
                 is_busy: Callable[[Agent], bool] | None = None):
        self.store = store
        self.agents = agents
        self.gateway = gateway
        self.is_busy = is_busy
        self.storm_guard = StormGuard()
        self._typing: dict[str, dict[str, float]] = {}

    # ── members ──────────────────────────────────────────────────────────

    def _agent(self, conv_id: str, principal: str) -> Agent | None:
        """A member agent, looked up in the conversation owner's space."""
        conversation = self.store.get(conv_id)
        return self.agents.get(conversation.owner, principal) if conversation and is_agent(principal) else None

    def members(self, conv_id: str) -> list[MemberView]:
        views = []
        for m in self.store.members(conv_id):
            agent = self._agent(conv_id, m.principal)
            if is_agent(m.principal) and agent is None:
                continue
            name = m.nickname or (agent.name if agent else m.principal[len(HUMAN_PREFIX):])
            views.append(MemberView(m.principal, name, agent, m.muted, m.nickname))
        return views

    def name_of(self, conv_id: str, principal: str) -> str:
        for m in self.members(conv_id):
            if m.principal == principal:
                return m.name
        agent = self._agent(conv_id, principal)
        return agent.name if agent else principal.removeprefix(HUMAN_PREFIX)

    def require_member(self, conv_id: str, principal: str) -> Conversation:
        conversation = self.store.get(conv_id)
        if conversation is None or self.store.membership(conv_id, principal) is None:
            raise NotAMember(f"{principal} is not in {conv_id}")
        return conversation

    # ── typing ───────────────────────────────────────────────────────────

    def _typing_start(self, conv_id: str, principal: str) -> None:
        self._typing.setdefault(conv_id, {})[principal] = time.time()

    def _typing_stop(self, conv_id: str, principal: str) -> None:
        self._typing.get(conv_id, {}).pop(principal, None)

    def typing(self, conv_id: str) -> list[str]:
        """Agents woken here that have not answered yet."""
        bucket = self._typing.get(conv_id, {})
        now = time.time()
        for principal, since in list(bucket.items()):
            agent = self._agent(conv_id, principal)
            done = agent is None or now - since > _TYPING_TIMEOUT_SEC
            if not done and self.is_busy is not None and now - since > 5:
                done = not self.is_busy(agent)
            if done:
                bucket.pop(principal, None)
        return list(bucket)

    # ── posting ──────────────────────────────────────────────────────────

    async def post(
        self,
        conv_id: str,
        sender: str,
        content: str,
        *,
        mentions: list[str] | None = None,
        reply_to: int | None = None,
        attachments: list[dict] | None = None,
        client_msg_id: str | None = None,
        mode: str | None = None,
    ) -> tuple[Message, bool]:
        """Store the message and wake whom it is for; ``created`` is False for a repeat."""
        conversation = self.require_member(conv_id, sender)
        members = self.members(conv_id)
        found = list(mentions or [])
        for principal in resolve_text_mentions(content, [(m.name, m.principal) for m in members]):
            if principal not in found:
                found.append(principal)
        message, created = self.store.add_message(
            conv_id, sender, content, mentions=found, reply_to=reply_to,
            attachments=attachments or [], client_msg_id=client_msg_id,
        )
        self._typing_stop(conv_id, sender)
        if is_agent(sender):
            self.store.advance_cursor(conv_id, sender, message.id)
        if created:
            await self._wake(conversation, members, message, mode=mode)
        return message, created

    async def _wake(self, conversation: Conversation, members: list[MemberView], message: Message, *,
                    mode: str | None) -> None:
        conv_id = conversation.conv_id
        if conversation.dnd:
            return
        agents = [m for m in members if m.agent is not None and not m.muted]
        sender_is_agent = is_agent(message.sender)
        targets = select_wake_targets(WakeRequest(
            agent_ids=[m.principal for m in agents],
            sender_id=message.sender if sender_is_agent else "",
            mentions=message.mentions,
            mention_all=mentions_everyone(message.content),
            primary_id=conversation.primary_agent,
            direct=conversation.kind == DIRECT,
        ))
        if not sender_is_agent:
            self.storm_guard.human_spoke(conv_id)
        elif targets and not self.storm_guard.allow(conv_id, len(targets)):
            logger.warning("conversation %s: agents woke each other too often; waiting for a human", conv_id)
            return
        for member in agents:
            if member.principal in targets:
                await self._deliver(conversation, members, member, message, mode=mode)

    def _envelope(self, conversation: Conversation, members: list[MemberView], member: MemberView,
                  message: Message) -> str:
        conv_id = conversation.conv_id
        sender = self.name_of(conv_id, message.sender)
        missed = self.store.messages(
            conv_id, after_id=self.store.membership(conv_id, member.principal).read_cursor,
            before_id=message.id, limit=_DIGEST_LIMIT, latest=True,
        )
        digest = render_digest([
            {"sender": self.name_of(conv_id, m.sender), "content": m.content}
            for m in missed if m.sender != member.principal
        ])
        attached = "".join(f"\n  📎 {a.get('name', '')} ({a.get('mime_type', a.get('type', ''))})"
                           for a in message.attachments)
        attach_block = f"\n\n[随消息附件]{attached}" if attached else ""
        reply = reply_channel(member.agent, conv_id)

        if conversation.kind == DIRECT:
            return (f"{digest}[私聊 group_id={conv_id}] {sender} 说:\n{message.content}{attach_block}\n\n"
                    f"（你是「{member.name}」。）\n回复方式：{reply}")

        mentioned = member.principal in message.mentions
        head = f"[群聊「{conversation.title}」 group_id={conv_id} 成员数:{len(members)}] {sender}"
        head += " @你 说:" if mentioned else " 说:"
        role = f"你在本群的身份是「{member.name}」"
        lead = conversation.primary_agent
        if lead and lead == member.principal:
            others = "、".join(f"「{m.name}」" for m in members if m.agent is not None and m.principal != lead)
            role += f"，是本群的主 agent，其他 agent 是你的 sub-agent：{others or '（暂无）'}；它们的发言都只送达你"
        elif lead:
            role += f"，是 sub-agent；你的发言（含 @ 任何人）都只送达主 agent「{self.name_of(conv_id, lead)}」"
        must = "这条消息 @ 了你，必须回复。" if mentioned else "与你相关时再回复。"
        return (f"{digest}{head}\n{message.content}{attach_block}\n\n"
                f"（{role}。{must}群里人人可见你的发言，但只唤醒你 @ 的成员。）\n回复方式：{reply}")

    async def _deliver(self, conversation: Conversation, members: list[MemberView], member: MemberView,
                       message: Message, *, mode: str | None) -> None:
        conv_id = conversation.conv_id
        text = self._envelope(conversation, members, member, message)
        self._typing_start(conv_id, member.principal)

        def settled(_reply: AgentReply) -> None:
            self._typing_stop(conv_id, member.principal)

        try:
            receipt = await self.gateway.deliver(
                member.agent,
                AgentMessage(text=text, attachments=list(message.attachments), sender=message.sender),
                context={"conversation_id": conv_id},
                mode=mode,
                coalesce_key=f"conversation:{conv_id}:{member.principal}",
                on_complete=settled,
            )
        except Exception:
            logger.exception("delivery to %s in %s crashed", member.principal, conv_id)
            self._typing_stop(conv_id, member.principal)
            return
        if not receipt.accepted:
            logger.warning("delivery to %s in %s failed: %s", member.principal, conv_id, receipt.error)
            self._typing_stop(conv_id, member.principal)
        self.store.advance_cursor(conv_id, member.principal, message.id)


def message_card(conversations: Conversations, message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "sender": message.sender,
        "sender_name": conversations.name_of(message.conv_id, message.sender),
        "content": message.content,
        "mentions": message.mentions,
        "reply_to": message.reply_to,
        "attachments": message.attachments,
        "created_at": message.created_at,
    }


def member_card(member: MemberView) -> dict[str, Any]:
    from agents.routes import agent_card
    return {
        "principal": member.principal,
        "name": member.name,
        "is_agent": member.agent is not None,
        "agent": agent_card(member.agent) if member.agent is not None else None,
        "muted": member.muted,
        "nickname": member.nickname,
    }


__all__ = ["Conversations", "NotAMember", "human", "message_card", "member_card", "resolve_text_mentions"]
