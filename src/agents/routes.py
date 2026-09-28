"""HTTP surface of the agent layer: every agent on this machine, one flat API.

    GET    /v1/agents                  the caller's agents (?status=1 adds live status)
    POST   /v1/agents                  create: {name, platform, …}
    GET    /v1/agents/{ref}            one agent (with live status)
    PATCH  /v1/agents/{ref}            rename / change settings
    DELETE /v1/agents/{ref}            delete (also leaves every team and conversation)
    POST   /v1/agents/{ref}/messages   ask (wait for the reply) or deliver
    POST   /v1/agents/{ref}/control    status / cancel / reset
    GET    /v1/agents/{ref}/history    the agent's own conversation (?limit=)

``ref`` is an ``ag_…`` id, an address (``alice/coder``) or a handle.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from agents.control import AgentControl, ControlError
from agents.gateway import AgentGateway
from agents.messages import AgentMessage
from agents.store import (
    WEBOT,
    Agent,
    AgentExists,
    AgentNotFound,
    AgentStore,
    canonical_platform,
    driver_for_platform,
    new_webot_session,
)
from utils.auth_utils import extract_user_password_session, is_internal_bearer, parse_bearer_parts

# Settings a caller may set; everything else in a driver's config is its own.
_SHARED_SETTINGS = ("persona", "team")
_WEBOT_SETTINGS = ("tools",)
_EXTERNAL_SETTINGS = ("api_url", "api_key", "model", "headers", "meta")


class AgentCreate(BaseModel):
    name: str
    platform: str = WEBOT
    handle: str = ""
    persona: str = ""
    team: str = ""
    tools: Any = None
    session: str = ""        # webot: name an existing chat session instead of starting a new one
    global_name: str = ""    # external: the runtime's own agent name
    api_url: str = ""
    api_key: str = ""
    model: str = ""
    headers: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class AgentPatch(BaseModel):
    name: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


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


def authenticate(authorization: str | None, *, internal_token: str, verify_password: Callable[[str, str], bool]) -> str:
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


def agent_card(agent: Agent) -> dict[str, Any]:
    """What a caller sees of an agent. Secrets never leave; the driver is the agent's business."""
    config = agent.config
    settings = {key: config.get(key, "") for key in _SHARED_SETTINGS}
    if agent.driver == WEBOT:
        settings["tools"] = config.get("tools")
        settings["session"] = config.get("session", "")  # the WeBot UI opens and compacts it
    else:
        settings.update({key: config.get(key) for key in _EXTERNAL_SETTINGS if key != "api_key"})
        settings["global_name"] = config.get("global_name", "")
        settings["has_api_key"] = bool(config.get("api_key"))
    return {
        "agent_id": agent.agent_id,
        "address": agent.address,
        "handle": agent.handle,
        "name": agent.name,
        "platform": agent.platform,
        "settings": settings,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
    }


def new_agent_config(body: AgentCreate) -> tuple[str, dict[str, Any]]:
    driver = driver_for_platform(body.platform)
    config: dict[str, Any] = {"persona": body.persona.strip(), "team": body.team.strip()}
    if driver == WEBOT:
        config["session"] = body.session.strip() or new_webot_session()
        if body.tools is not None:
            config["tools"] = body.tools
        return driver, config
    config.update({
        "platform": canonical_platform(body.platform),
        "global_name": body.global_name.strip(),
        "api_url": body.api_url.strip(),
        "api_key": body.api_key,
        "model": body.model.strip(),
        "headers": dict(body.headers),
        "meta": dict(body.meta),
    })
    return driver, config


def create_agents_router(
    *,
    internal_token: str,
    verify_password: Callable[[str, str], bool],
    store: AgentStore,
    gateway: AgentGateway,
    control: AgentControl | None = None,
) -> APIRouter:
    router = APIRouter()

    def user_of(authorization: str | None) -> str:
        return authenticate(authorization, internal_token=internal_token, verify_password=verify_password)

    def lookup(user: str, ref: str) -> Agent:
        try:
            return store.resolve(user, ref)
        except AgentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    async def with_status(agent: Agent) -> dict[str, Any]:
        card = agent_card(agent)
        if control is not None:
            card["status"] = await control.status(agent)
        return card

    @router.get("/v1/agents")
    async def list_agents(
        authorization: str | None = Header(None),
        status: bool = Query(False),
        runtime: str = Query("", description="webot:<session> — the agent that owns a runtime"),
    ):
        user = user_of(authorization)
        agents = store.list(user)
        if runtime:
            driver, _, ident = runtime.partition(":")
            key = "session" if driver == WEBOT else "global_name"
            found = store.find(user, driver, {key: ident})
            agents = [found] if found else []
        if status and control is not None:
            import asyncio
            cards = await asyncio.gather(*(with_status(a) for a in agents))
        else:
            cards = [agent_card(a) for a in agents]
        return {"object": "list", "data": list(cards)}

    @router.post("/v1/agents")
    async def create_agent(body: AgentCreate, authorization: str | None = Header(None)):
        user = user_of(authorization)
        try:
            driver, config = new_agent_config(body)
            agent = store.create(user, name=body.name, driver=driver, config=config, handle=body.handle)
        except AgentExists as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc), "agent": agent_card(exc.agent)})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return agent_card(agent)

    @router.post("/v1/agents/{ref:path}/messages")
    async def message_agent(ref: str, body: AgentMessageRequest, authorization: str | None = Header(None)):
        user = user_of(authorization)
        agent = lookup(user, ref)
        msg = AgentMessage(text=body.text, attachments=body.attachments, sender=f"u:{user}",
                           instructions=body.instructions)
        if body.deliver:
            receipt = await gateway.deliver(agent, msg, context=body.context, mode=body.mode)
            return {"agent": agent_card(agent), "accepted": receipt.accepted, "error": receipt.error}
        reply = await gateway.ask(
            agent, msg, context=body.context, mode=body.mode, tools=body.tools,
            response_format=body.response_format, timeout=body.timeout,
        )
        return {"agent": agent_card(agent), "ok": reply.ok, "content": reply.content, "error": reply.error}

    @router.post("/v1/agents/{ref:path}/control")
    async def control_agent(ref: str, body: AgentControlBody, authorization: str | None = Header(None)):
        user = user_of(authorization)
        agent = lookup(user, ref)
        if control is None:
            raise HTTPException(status_code=503, detail="agent control runs in the Agent service")
        try:
            return {"agent": agent_card(agent), **await control.run(agent, body.action)}
        except ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/v1/agents/{ref:path}/history")
    async def agent_history(ref: str, limit: int = Query(200, ge=1, le=1000), authorization: str | None = Header(None)):
        agent = lookup(user_of(authorization), ref)
        if control is None:
            raise HTTPException(status_code=503, detail="agent history is kept by the Agent service")
        return {"agent": agent_card(agent), "messages": await control.history(agent, limit)}

    @router.get("/v1/agents/{ref:path}")
    async def describe_agent(ref: str, authorization: str | None = Header(None)):
        return await with_status(lookup(user_of(authorization), ref))

    @router.patch("/v1/agents/{ref:path}")
    async def update_agent(ref: str, body: AgentPatch, authorization: str | None = Header(None)):
        agent = lookup(user_of(authorization), ref)
        allowed = _SHARED_SETTINGS + (_WEBOT_SETTINGS if agent.driver == WEBOT else _EXTERNAL_SETTINGS)
        unknown = sorted(set(body.settings) - set(allowed))
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown settings for {agent.platform}: {unknown}")
        config = {**agent.config, **body.settings}
        if agent.driver != WEBOT and body.settings.get("api_key") == "":
            config["api_key"] = agent.config.get("api_key", "")  # an empty field keeps the saved key
        try:
            return agent_card(store.update(agent.agent_id, name=body.name, config=config))
        except AgentExists as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.delete("/v1/agents/{ref:path}")
    async def delete_agent(ref: str, authorization: str | None = Header(None)):
        agent = lookup(user_of(authorization), ref)
        if control is not None:
            await control.cleanup(agent)
        store.delete(agent.agent_id)
        return {"deleted": agent.agent_id}

    return router
