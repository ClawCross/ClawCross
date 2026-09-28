"""Group chat: WeChat-style conversations between a person and their agents.

A group is a conversation whose members are agent ids (and the owner): posting
in it sends the message on to the members the rules wake. Only the owner
manages a group; members read and post.
"""

from __future__ import annotations

from typing import Any, Callable

from agents.routes import agent_card
from agents.store import Agent, AgentStore
from comms.conversations import Conversations, member_card, message_card
from comms.store import DIRECT, GROUP, Conversation, human, is_agent


class GroupError(Exception):
    status = 400


class GroupNotFound(GroupError):
    status = 404


class Forbidden(GroupError):
    status = 403


class GroupService:
    def __init__(self, conversations: Conversations, *, names: Callable[[str, str], Agent | None] | None = None):
        """``names`` finds an agent by a name other than its id (``<team>.<name>``)."""
        self.conversations = conversations
        self.store = conversations.store
        self.agents: AgentStore = conversations.agents
        self.names = names

    # ── access ───────────────────────────────────────────────────────────

    def _get(self, user: str, conv_id: str, *, manage: bool = False) -> Conversation:
        conversation = self.store.get(conv_id)
        if conversation is None:
            raise GroupNotFound("群聊不存在")
        if manage and conversation.owner != user:
            raise Forbidden("只有群主可以管理群聊")
        if self.store.membership(conv_id, human(user)) is None:
            raise Forbidden("你不在这个群里")
        return conversation

    def agent_id(self, user: str, ref: str) -> str:
        ref = (ref or "").strip()
        agent = self.agents.get(user, ref) or (self.names(user, ref) if self.names else None)
        if agent is None:
            raise GroupNotFound(f"no agent {ref!r}")
        return agent.agent_id

    # ── cards ────────────────────────────────────────────────────────────

    def _summary(self, conversation: Conversation) -> dict[str, Any]:
        members = self.conversations.members(conversation.conv_id)
        last = self.store.last_message(conversation.conv_id)
        return {
            "group_id": conversation.conv_id,
            "title": conversation.title,
            "kind": conversation.kind,
            "owner": conversation.owner,
            "member_count": len(members),
            "member_names": [m.name for m in sorted(members, key=lambda m: m.agent is None)][:4],
            "message_count": self.store.message_count(conversation.conv_id),
            "last_message": message_card(self.conversations, last) if last else None,
            "dnd": bool(conversation.dnd),
            "updated_at": conversation.updated_at,
        }

    def _detail(self, conversation: Conversation) -> dict[str, Any]:
        recent = self.store.messages(conversation.conv_id, limit=100, latest=True)
        return {
            **self._summary(conversation),
            "primary_agent": conversation.primary_agent,
            "members": [member_card(m) for m in self.conversations.members(conversation.conv_id)],
            "messages": [message_card(self.conversations, m) for m in recent],
        }

    # ── groups ───────────────────────────────────────────────────────────

    def create(self, user: str, *, title: str, kind: str = GROUP, agents: list[str] = ()) -> dict:
        agent_ids = [self.agent_id(user, ref) for ref in agents]
        if kind == DIRECT:
            if len(agent_ids) != 1:
                raise GroupError("私聊只能有一个 agent")
            for existing in self.store.list_for(human(user)):
                if existing.kind == DIRECT and self.store.membership(existing.conv_id, agent_ids[0]):
                    return self._detail(existing)
            title = title or self.agents.get(user, agent_ids[0]).name  # type: ignore[union-attr]
        if not (title or "").strip():
            raise GroupError("群聊需要名称")
        return self._detail(self.store.create(user, title.strip(), kind, members=agent_ids))

    def list(self, user: str) -> list[dict]:
        return [self._summary(c) for c in self.store.list_for(human(user))]

    def detail(self, user: str, conv_id: str) -> dict:
        return self._detail(self._get(user, conv_id))

    def messages(self, user: str, conv_id: str, after_id: int = 0) -> list[dict]:
        self._get(user, conv_id)
        return [message_card(self.conversations, m) for m in self.store.messages(conv_id, after_id=after_id)]

    def update(self, user: str, conv_id: str, *, title: str | None = None, dnd: bool | None = None) -> dict:
        self._get(user, conv_id, manage=True)
        changes: dict[str, Any] = {}
        if title is not None and title.strip():
            changes["title"] = title.strip()
        if dnd is not None:
            changes["dnd"] = int(dnd)
        if changes:
            self.store.update(conv_id, **changes)
        return self.detail(user, conv_id)

    def delete(self, user: str, conv_id: str) -> None:
        self._get(user, conv_id, manage=True)
        self.store.delete(conv_id)

    # ── members ──────────────────────────────────────────────────────────

    def add_member(self, user: str, conv_id: str, agent_ref: str) -> dict:
        conversation = self._get(user, conv_id, manage=True)
        if conversation.kind == DIRECT:
            raise GroupError("私聊不能加人")
        self.store.add_member(conv_id, self.agent_id(user, agent_ref))
        return self.detail(user, conv_id)

    def remove_member(self, user: str, conv_id: str, principal: str) -> dict:
        conversation = self._get(user, conv_id, manage=True)
        if principal == human(conversation.owner):
            raise GroupError("群主不能移出自己的群")
        self.store.remove_member(conv_id, principal)
        return self.detail(user, conv_id)

    def update_member(self, user: str, conv_id: str, principal: str, *, muted: bool | None = None,
                      nickname: str | None = None) -> dict:
        self._get(user, conv_id, manage=True)
        if self.store.membership(conv_id, principal) is None:
            raise GroupNotFound("群里没有这个成员")
        if muted is not None:
            self.store.set_muted(conv_id, principal, muted)
        if nickname is not None:
            self.store.set_nickname(conv_id, principal, nickname)
        return self.detail(user, conv_id)

    def mute_agents(self, user: str, conv_id: str, muted: bool) -> dict:
        self._get(user, conv_id, manage=True)
        for m in self.store.members(conv_id):
            if is_agent(m.principal):
                self.store.set_muted(conv_id, m.principal, muted)
        return self.detail(user, conv_id)

    def set_primary(self, user: str, conv_id: str, agent_ref: str | None) -> dict:
        self._get(user, conv_id, manage=True)
        agent_id = self.agent_id(user, agent_ref) if agent_ref else None
        if agent_id and self.store.membership(conv_id, agent_id) is None:
            raise GroupError("主 agent 必须是群成员")
        self.store.update(conv_id, primary_agent=agent_id)
        return self.detail(user, conv_id)

    # ── talking ──────────────────────────────────────────────────────────

    async def post(self, user: str, conv_id: str, sender: str, content: str, **fields: Any) -> dict:
        self._get(user, conv_id)
        message, created = await self.conversations.post(conv_id, sender, content, **fields)
        return {"message": message_card(self.conversations, message), "created": created}

    def typing(self, user: str, conv_id: str) -> dict:
        self._get(user, conv_id)
        principals = self.conversations.typing(conv_id)
        return {"typing": principals, "names": [self.conversations.name_of(conv_id, p) for p in principals]}

    def agents_available(self, user: str, conv_id: str) -> list[dict]:
        """The owner's agents not yet in the group."""
        self._get(user, conv_id)
        inside = {m.principal for m in self.store.members(conv_id)}
        return [agent_card(a) for a in self.agents.list(user) if a.agent_id not in inside]
