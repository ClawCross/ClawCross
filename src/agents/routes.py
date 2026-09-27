"""HTTP surface of the agent layer: list agents and talk to any one of them.

    GET  /v1/agents                      the caller's agents (flat, all runtimes)
    GET  /v1/agents/{ref}                one agent card
    POST /v1/agents/{ref}/messages       ask (wait for the reply) or deliver
    POST /v1/agents/{ref}/control        status / cancel / stop / reset / new / delete

``ref`` is an ``ag_…`` id or an address such as ``alice/coder``.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from agents.gateway import AgentGateway, agent_card
from agents.messages import AgentMessage
from agents.registry import AgentNotFound, AmbiguousAgentRef
from utils.auth_utils import extract_user_password_session, is_internal_bearer, parse_bearer_parts


class AgentMessageRequest(BaseModel):
    text: str
    attachments: list[dict] = Field(default_factory=list)
    instructions: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    mode: str | None = None
    tools: list[str] | None = None
    response_format: dict | None = None
    timeout: float | None = None
    deliver: bool = False  # true: put it in the agent's inbox and return at once


class AgentControlBody(BaseModel):
    action: str


_CONTROL_ACTIONS = {"status", "cancel", "stop", "reset", "new", "delete"}


def authenticate(
    authorization: str | None,
    *,
    internal_token: str,
    verify_password: Callable[[str, str], bool],
) -> str:
    """The user a request acts for: ``Bearer <internal>:<user>`` or ``Bearer <user>:<password>``."""
    parts = parse_bearer_parts(authorization)
    if internal_token and parts and is_internal_bearer(parts, internal_token) and len(parts) >= 2 and parts[1]:
        return parts[1]
    parsed = extract_user_password_session(parts, default_session="") if parts else None
    if parsed:
        user_id, password, _session = parsed
        if user_id and password and verify_password(user_id, password):
            return user_id
    raise HTTPException(status_code=401, detail="认证失败")


def create_agents_router(
    *,
    internal_token: str,
    verify_password: Callable[[str, str], bool],
    gateway: AgentGateway | None = None,
) -> APIRouter:
    router = APIRouter()
    state: dict[str, AgentGateway] = {}

    def get_gateway() -> AgentGateway:
        if "gateway" not in state:
            state["gateway"] = gateway or AgentGateway(internal_token=internal_token)
        return state["gateway"]

    def user_of(authorization: str | None) -> str:
        return authenticate(authorization, internal_token=internal_token, verify_password=verify_password)

    def lookup(user: str, ref: str):
        try:
            return get_gateway().resolve(user, ref)
        except AmbiguousAgentRef as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc), "candidates": exc.candidates})
        except AgentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @router.get("/v1/agents")
    async def list_agents(authorization: str | None = Header(None)):
        user = user_of(authorization)
        cards = get_gateway().list(user)
        return {"object": "list", "data": cards}

    @router.post("/v1/agents/{ref:path}/messages")
    async def message_agent(ref: str, body: AgentMessageRequest, authorization: str | None = Header(None)):
        user = user_of(authorization)
        record = lookup(user, ref)
        msg = AgentMessage(
            text=body.text,
            attachments=body.attachments,
            sender=f"u:{user}",
            instructions=body.instructions,
        )
        if body.deliver:
            receipt = await get_gateway().deliver(user, record, msg, context=body.context, mode=body.mode)
            return {"agent": agent_card(record), "accepted": receipt.accepted, "error": receipt.error}
        reply = await get_gateway().ask(
            user, record, msg,
            context=body.context,
            mode=body.mode,
            tools=body.tools,
            response_format=body.response_format,
            timeout=body.timeout,
        )
        return {"agent": agent_card(record), "ok": reply.ok, "content": reply.content, "error": reply.error}

    @router.post("/v1/agents/{ref:path}/control")
    async def control_agent(ref: str, body: AgentControlBody, authorization: str | None = Header(None)):
        user = user_of(authorization)
        if body.action not in _CONTROL_ACTIONS:
            raise HTTPException(status_code=400, detail=f"unsupported action: {body.action}")
        record = lookup(user, ref)
        return await get_gateway().control(user, record, body.action)

    @router.get("/v1/agents/{ref:path}")
    async def describe_agent(ref: str, authorization: str | None = Header(None)):
        user = user_of(authorization)
        return agent_card(lookup(user, ref))

    return router
