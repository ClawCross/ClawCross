"""HTTP surface of group chat.

    GET    /groups                               the caller's groups and direct chats
    POST   /groups                               create {title, kind?, agents?}
    GET    /groups/{id}                          members, primary agent, recent messages
    PATCH  /groups/{id}                          {title?, dnd?}
    DELETE /groups/{id}
    GET    /groups/{id}/messages?after_id=
    POST   /groups/{id}/messages                 {content, mentions?, reply_to?, attachments?, client_msg_id?}
    POST   /groups/{id}/members                  {agent}
    PATCH  /groups/{id}/members/{principal}      {muted?, nickname?}
    DELETE /groups/{id}/members/{principal}
    POST   /groups/{id}/mute_agents              {muted}
    PUT    /groups/{id}/primary                  {agent | null}
    GET    /groups/{id}/typing
    GET    /groups/{id}/available_agents

A person posts as themselves. A local caller holding the internal token posts
for one of the owner's agents by naming it (``agent``: id, address or handle).
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from agents.routes import authenticate
from groups.conversations import NotAMember
from groups.store import GROUP, human
from groups.service import GroupError, GroupService


class Attachment(BaseModel):
    type: str                 # "image" | "audio" | "file"
    name: str
    data: str                 # base64, without the data: prefix
    mime_type: str


class GroupCreate(BaseModel):
    title: str = ""
    kind: str = GROUP         # "group" | "direct"
    agents: list[str] = Field(default_factory=list)


class GroupPatch(BaseModel):
    title: str | None = None
    dnd: bool | None = None


class MessagePost(BaseModel):
    content: str
    expected_title: str | None = None
    mentions: list[str] | None = None
    reply_to: int | None = None
    attachments: list[Attachment] | None = None
    client_msg_id: str | None = None
    run_mode: str | None = None   # chat / readonly / auto / bypass for the agents it wakes
    agent: str | None = None      # post for this agent (internal callers only)


class MemberAdd(BaseModel):
    agent: str


class MemberPatch(BaseModel):
    muted: bool | None = None
    nickname: str | None = None


class MuteAgents(BaseModel):
    muted: bool = True


class PrimarySet(BaseModel):
    agent: str | None = None


def create_groups_router(
    *,
    internal_token: str,
    verify_password: Callable[[str, str], bool],
    service: GroupService,
) -> APIRouter:
    router = APIRouter()

    def user_of(authorization: str | None) -> str:
        return authenticate(authorization, internal_token=internal_token, verify_password=verify_password)

    def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except GroupError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc))

    @router.get("/groups")
    async def list_groups(agent_id: str = "", authorization: str | None = Header(None)):
        user = user_of(authorization)
        return {"groups": call(service.memberships, user, call(service.agent_id, user, agent_id)) if agent_id else call(service.list, user)}

    @router.post("/groups")
    async def create_group(body: GroupCreate, authorization: str | None = Header(None)):
        return call(service.create, user_of(authorization), title=body.title, kind=body.kind, agents=body.agents)

    @router.get("/groups/{conv_id}/messages")
    async def list_messages(conv_id: str, after_id: int = 0, authorization: str | None = Header(None)):
        return {"messages": call(service.messages, user_of(authorization), conv_id, after_id)}

    @router.post("/groups/{conv_id}/messages")
    async def post_message(
        conv_id: str,
        body: MessagePost,
        authorization: str | None = Header(None),
        x_internal_token: str | None = Header(None),
    ):
        user = user_of(authorization)
        sender = human(user)
        if body.agent:
            if not internal_token or x_internal_token != internal_token:
                raise HTTPException(status_code=403, detail="only local services may post for an agent")
            try:
                sender = service.agent_id(user, body.agent)
            except GroupError as exc:
                raise HTTPException(status_code=exc.status, detail=str(exc))
        try:
            return await service.post(
                user, conv_id, sender, body.content,
                mentions=body.mentions, reply_to=body.reply_to,
                attachments=[a.model_dump() for a in body.attachments or []],
                client_msg_id=body.client_msg_id, mode=body.run_mode,
                expected_title=body.expected_title,
            )
        except GroupError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc))
        except NotAMember as exc:
            raise HTTPException(status_code=403, detail=f"不在这个群里：{exc}")

    @router.post("/groups/{conv_id}/members")
    async def add_member(conv_id: str, body: MemberAdd, authorization: str | None = Header(None)):
        return call(service.add_member, user_of(authorization), conv_id, body.agent)

    @router.patch("/groups/{conv_id}/members/{principal}")
    async def update_member(conv_id: str, principal: str, body: MemberPatch, authorization: str | None = Header(None)):
        return call(service.update_member, user_of(authorization), conv_id, principal,
                    muted=body.muted, nickname=body.nickname)

    @router.delete("/groups/{conv_id}/members/{principal}")
    async def remove_member(conv_id: str, principal: str, authorization: str | None = Header(None)):
        return call(service.remove_member, user_of(authorization), conv_id, principal)

    @router.post("/groups/{conv_id}/mute_agents")
    async def mute_agents(conv_id: str, body: MuteAgents, authorization: str | None = Header(None)):
        return call(service.mute_agents, user_of(authorization), conv_id, body.muted)

    @router.put("/groups/{conv_id}/primary")
    async def set_primary(conv_id: str, body: PrimarySet, authorization: str | None = Header(None)):
        return call(service.set_primary, user_of(authorization), conv_id, body.agent)

    @router.get("/groups/{conv_id}/typing")
    async def typing(conv_id: str, authorization: str | None = Header(None)):
        return call(service.typing, user_of(authorization), conv_id)

    @router.get("/groups/{conv_id}/available_agents")
    async def available_agents(conv_id: str, authorization: str | None = Header(None)):
        return {"agents": call(service.agents_available, user_of(authorization), conv_id)}

    @router.get("/groups/{conv_id}")
    async def get_group(conv_id: str, authorization: str | None = Header(None)):
        return call(service.detail, user_of(authorization), conv_id)

    @router.patch("/groups/{conv_id}")
    async def update_group(conv_id: str, body: GroupPatch, authorization: str | None = Header(None)):
        return call(service.update, user_of(authorization), conv_id, title=body.title, dnd=body.dnd)

    @router.delete("/groups/{conv_id}")
    async def delete_group(conv_id: str, authorization: str | None = Header(None)):
        call(service.delete, user_of(authorization), conv_id)
        return {"deleted": conv_id}

    return router
