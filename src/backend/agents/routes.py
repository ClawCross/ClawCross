"""HTTP surface of the agent layer: every agent on this machine by its number.

    GET    /v1/agents                  the caller's agents (?status=1 adds live status, ?platform= one runtime's)
    POST   /v1/agents                  create: {agent_id?, name?, platform, …}
    GET    /v1/agents/{ref}            one agent (with live status)
    PATCH  /v1/agents/{ref}            rename / change settings
    DELETE /v1/agents/{ref}            delete (also leaves every team and conversation)
    POST   /v1/agents/{ref}/messages   ask and wait for the reply
    POST   /v1/agents/{ref}/inbox      put a message in its inbox
    POST   /v1/agents/{ref}/control    status, or one of the runtime's actions (cancel, reset, …)
    GET    /v1/agents/{ref}/history    the agent's own conversation (?limit=)

``ref`` is an agent id (its session number) or ``<team>.<name>``. Sending to an
id that is not there yet makes that agent — ``platform`` says of which runtime
(WeBot when not given).

"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from agents.gateway import AgentGateway
from agents.messages import AgentMessage
from agents.runtime import NO_TIMEOUT, ControlError
from agents.store import (
    HTTP,
    LLM,
    WEBOT,
    ACPX,
    Agent,
    AgentExists,
    AgentStore,
    canonical_platform,
    driver_for_platform,
    new_agent_id,
    valid_agent_id,
)
from common.auth_utils import extract_user_password_session, is_internal_bearer, parse_bearer_parts

# Settings a caller may set; everything else in a driver's config is its own.
# ``title`` names the work the session is doing (the agent or the user sets it).
_SHARED_SETTINGS = ("persona", "title")
TITLE_MAX = 80


def session_title(text: str) -> str:
    """A one-line display title: whitespace collapsed, at most ``TITLE_MAX`` characters."""
    return " ".join(str(text or "").split())[:TITLE_MAX]
_WEBOT_SETTINGS = ("tools",)
_EXTERNAL_SETTINGS = ("api_url", "api_key", "model", "headers", "meta")


class AgentCreate(BaseModel):
    agent_id: str = ""       # its session number; a new ag_… when not given
    name: str = ""
    platform: str = WEBOT
    persona: str = ""        # its persona: the text itself (a library persona is copied in)
    tools: list[str] | None = None  # the tools it has; none: all of them
    api_url: str = ""
    api_key: str = ""
    model: str = ""
    headers: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    llm: dict[str, Any] = Field(default_factory=dict)  # webot / llm: model, api_key, base_url, provider, temperature, max_tokens


class AgentPatch(BaseModel):
    name: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class AgentMessageRequest(BaseModel):
    text: str
    attachments: list[dict] = Field(default_factory=list)
    instructions: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    mode: str | None = None
    enabled_tools: list[str] | None = None  # the tools this turn may use (none: the agent's own)
    response_format: dict | None = None  # OpenAI response_format
    timeout: float | None = None  # seconds; 0 waits as long as the agent takes; none: the runtime's default
    platform: str = ""       # the runtime of a new agent
    inbox_sender: str = Field('', max_length=160)  # trusted local composition only
    inbox_summary: str = Field('', max_length=256)


class AgentControlBody(BaseModel):
    action: str


class AgentForkBody(BaseModel):
    agent_id: str = Field("", max_length=64)
    name: str = Field("", max_length=160)
    reason: str = Field("", max_length=500)


class NativeSessionImport(BaseModel):
    ticket: str = Field(max_length=160)
    name: str = Field('', max_length=160)


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
    settings["teams"] = agent.teams  # changed only by joining or leaving a team
    if agent.driver == WEBOT:
        settings["tools"] = config.get("tools")
    elif agent.driver != LLM:
        settings.update({key: config.get(key) for key in _EXTERNAL_SETTINGS if key != "api_key"})
        settings["has_api_key"] = bool(config.get("api_key"))
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "platform": agent.platform,
        "settings": settings,
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
    }


def runtime_of(platform: str) -> tuple[str, dict[str, Any]]:
    """The driver and base config of a new agent of *platform* (WeBot when empty)."""
    driver = driver_for_platform(platform)
    return driver, ({} if driver == WEBOT else {"platform": canonical_platform(platform)})


def new_agent_config(body: AgentCreate) -> tuple[str, dict[str, Any]]:
    driver, config = runtime_of(body.platform)
    config.update({"persona": body.persona.strip()})
    if driver == WEBOT and body.tools is not None:
        config["tools"] = body.tools
    if driver in (WEBOT, LLM):
        if body.llm:
            config["llm"] = dict(body.llm)
        return driver, config
    config.update({
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
    names: Callable[[str, str], Agent | None] | None = None,
    on_delete: Iterable[Callable[[Agent], None]] = (),
    memberships: Callable[[str, str], list[dict]] | None = None,
) -> APIRouter:
    """``names`` finds an agent by a name other than its id (``<team>.<name>``);
    ``on_delete`` is what else holds agent ids (teams, conversations) forgetting one."""
    router = APIRouter()

    def user_of(authorization: str | None) -> str:
        return authenticate(authorization, internal_token=internal_token, verify_password=verify_password)

    def find(user: str, ref: str) -> Agent | None:
        ref = (ref or "").strip()
        return store.get(user, ref) or (names(user, ref) if names else None)

    def lookup(user: str, ref: str) -> Agent:
        agent = find(user, ref)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"no agent {ref!r}")
        return agent

    def target(user: str, ref: str, platform: str) -> Agent:
        """The agent a message goes to; an id not seen before is a new agent."""
        agent = find(user, ref)
        if agent is not None:
            return agent
        if not valid_agent_id(ref):
            raise HTTPException(status_code=404, detail=f"no agent {ref!r}")
        driver, config = runtime_of(platform)
        if driver == HTTP:
            raise HTTPException(status_code=400, detail=f"{platform!r} needs an endpoint: create it with POST /v1/agents")
        return store.ensure(user, ref, driver=driver, config=config)

    async def with_status(agent: Agent) -> dict[str, Any]:
        return {**agent_card(agent), "groups": memberships(agent.owner, agent.agent_id) if memberships else [],
                "status": await gateway.status(agent)}

    def message(user: str, body: AgentMessageRequest) -> AgentMessage:
        return AgentMessage(text=body.text, attachments=body.attachments, sender=f"u:{user}",
                            instructions=body.instructions)

    @router.get("/v1/agents")
    async def list_agents(authorization: str | None = Header(None), status: bool = Query(False),
                          platform: str = Query("")):
        agents = store.list(user_of(authorization))
        if platform:
            agents = [a for a in agents if a.platform == canonical_platform(platform)]
        if status:
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
            agent = store.create(user, driver=driver, config=config, name=body.name, agent_id=body.agent_id)
        except AgentExists as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc), "agent": agent_card(exc.agent)})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return agent_card(agent)

    def host_access(user: str, authorization: str | None, proof: str | None):
        from agents.native_sessions import allowed
        import hmac
        authenticated_host = bool(internal_token and proof and hmac.compare_digest(proof, internal_token)
                                  and is_internal_bearer(parse_bearer_parts(authorization) or [], internal_token))
        if not authenticated_host and not allowed(user):
            raise HTTPException(403, '原生会话属于主机账户，仅供本机访问；远程需主机配置 CLAWCROSS_NATIVE_SESSION_USERS。')

    @router.get('/v1/agents/native-sessions')
    async def native_sessions(platform: str = 'codex', cursor: str = '', authorization: str | None = Header(None),
                              x_clawcross_host_browse: str | None = Header(None)):
        user = user_of(authorization)
        host_access(user, authorization, x_clawcross_host_browse)
        from agents.native_sessions import catalog
        from external.acpx import AcpxError
        try:
            return await catalog(user, platform, cursor=cursor, store=store)
        except (AcpxError, ValueError) as exc:
            raise HTTPException(502, str(exc)) from None

    @router.post('/v1/agents/native-sessions')
    async def import_native_session(body: NativeSessionImport, authorization: str | None = Header(None),
                                    x_clawcross_host_browse: str | None = Header(None)):
        user = user_of(authorization)
        host_access(user, authorization, x_clawcross_host_browse)
        from agents.native_sessions import register
        try:
            return agent_card(register(user, body.ticket, body.name, store))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/v1/agents/{ref}/capabilities")
    async def agent_capabilities(ref: str, authorization: str | None = Header(None)):
        agent = lookup(user_of(authorization), ref)
        if agent.driver == ACPX:
            from external.acp_settings import capability_card
            return capability_card(agent)
        return {"platform": agent.platform, "transport": agent.driver,
                "supports": {"clawcross_compaction": agent.driver == WEBOT, "tool_bridge": False}}

    @router.post("/v1/agents/{ref}/test-connection")
    async def test_agent_connection(ref: str, authorization: str | None = Header(None)):
        agent = lookup(user_of(authorization), ref)
        if agent.driver != ACPX:
            raise HTTPException(400, "This Agent does not use ACP")
        runtime = gateway.runtime(agent)
        if runtime.is_busy(agent):
            raise HTTPException(409, "Agent 正在运行，请在本轮结束后测试连接")
        try:
            await runtime.test_connection(agent)
        except ControlError as exc:
            raise HTTPException(502, str(exc)) from exc
        from external.acp_settings import capability_card
        return capability_card(store.require(agent.owner, agent.agent_id))

    @router.patch("/v1/agents/{ref}/acp-settings")
    async def acp_settings(ref: str, body: dict[str, Any], authorization: str | None = Header(None)):
        agent = lookup(user_of(authorization), ref)
        if agent.driver != ACPX:
            raise HTTPException(400, "This Agent does not use ACP")
        from external.acp_settings import validate_settings, capability_card
        try:
            settings = validate_settings(agent, body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        meta = {**(agent.config.get('meta') or {})}
        meta['acp'] = {**(meta.get('acp') or {}), **settings}
        model = (settings.get('config_options') or {}).get('model', agent.config.get('model', ''))
        updated = store.update(agent.owner, agent.agent_id, config={**agent.config, 'meta': meta, 'model': model})
        return capability_card(updated)

    @router.get('/v1/agents/{ref}/events')
    async def agent_events(ref: str, after: int = Query(0, ge=0), authorization: str | None = Header(None)):
        agent = lookup(user_of(authorization), ref)
        runtime = gateway.runtime(agent)
        return runtime.events(agent, after) if agent.driver == ACPX else {'events': [], 'cursor': 0}

    @router.post("/v1/agents/{ref}/fork")
    async def fork_agent(ref: str, body: AgentForkBody, authorization: str | None = Header(None)):
        user = user_of(authorization)
        parent = lookup(user, ref)
        child_id = body.agent_id.strip() or new_agent_id()
        config = {key: value for key, value in parent.config.items() if key not in {"teams", "fork"}}
        config["fork"] = {"parent_agent_id": parent.agent_id, "reason": body.reason.strip()}
        try:
            child = store.create(user, driver=parent.driver, config=config,
                                 name=body.name.strip() or f"{parent.name} fork", agent_id=child_id)
        except AgentExists as exc:
            raise HTTPException(status_code=409, detail={"error": str(exc), "agent": agent_card(exc.agent)})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        try:
            message_count = await gateway.fork(parent, child)
            child = store.update(user, child_id, config={
                **config, "fork": {**config["fork"], "source_message_count": message_count},
            })
        except Exception as exc:
            await gateway.destroy(child)
            store.delete(user, child_id)
            if isinstance(exc, ControlError):
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if isinstance(exc, ValueError):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise
        return {"agent": agent_card(child), "fork": child.config["fork"]}

    @router.get("/v1/agents/{ref}/forks")
    async def list_agent_forks(ref: str, authorization: str | None = Header(None)):
        user = user_of(authorization)
        parent = lookup(user, ref)
        children = [agent for agent in store.list(user)
                    if (agent.config.get("fork") or {}).get("parent_agent_id") == parent.agent_id]
        return {"object": "list", "data": [
            {"agent": agent_card(child), "fork": child.config["fork"]} for child in children
        ]}

    @router.post("/v1/agents/{ref}/messages")
    async def message_agent(ref: str, body: AgentMessageRequest, authorization: str | None = Header(None)):
        user = user_of(authorization)
        agent = target(user, ref, body.platform)
        reply = await gateway.ask(
            agent, message(user, body), context=body.context, mode=body.mode, enabled_tools=body.enabled_tools,
            response_format=body.response_format, timeout=NO_TIMEOUT if body.timeout == 0 else body.timeout,
        )
        return {"agent": agent_card(agent), "ok": reply.ok, "content": reply.content, "error": reply.error}

    @router.post("/v1/agents/{ref}/inbox")
    async def post_to_inbox(ref: str, body: AgentMessageRequest, authorization: str | None = Header(None)):
        user = user_of(authorization)
        if (body.inbox_sender or body.inbox_summary or body.context.get('group_human_requests')) and (not internal_token or authorization != f'Bearer {internal_token}:{user}'):
            raise HTTPException(403, '只有本机服务可以指定 inbox 来源')
        agent = target(user, ref, body.platform)
        msg = message(user, body)
        msg.sender = body.inbox_sender or msg.sender
        msg.summary = body.inbox_summary
        receipt = await gateway.inbox(agent, msg, context=body.context, mode=body.mode)
        return {"agent": agent_card(agent), "accepted": receipt.accepted, "error": receipt.error}

    @router.post("/v1/agents/{ref}/control")
    async def control_agent(ref: str, body: AgentControlBody, authorization: str | None = Header(None)):
        agent = lookup(user_of(authorization), ref)
        try:
            return {"agent": agent_card(agent), **await gateway.control(agent, body.action)}
        except ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/v1/agents/{ref}/history")
    async def agent_history(ref: str, limit: int = Query(200, ge=1, le=1000), authorization: str | None = Header(None)):
        agent = lookup(user_of(authorization), ref)
        try:
            return {"agent": agent_card(agent), "messages": await gateway.history(agent, limit)}
        except ControlError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/v1/agents/{ref}")
    async def describe_agent(ref: str, authorization: str | None = Header(None)):
        return await with_status(lookup(user_of(authorization), ref))

    @router.patch("/v1/agents/{ref}")
    async def update_agent(ref: str, body: AgentPatch, authorization: str | None = Header(None)):
        user = user_of(authorization)
        agent = lookup(user, ref)
        allowed = _SHARED_SETTINGS + (_WEBOT_SETTINGS if agent.driver == WEBOT else _EXTERNAL_SETTINGS)
        unknown = sorted(set(body.settings) - set(allowed))
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown settings for {agent.platform}: {unknown}")
        config = {**agent.config, **body.settings}
        if "title" in body.settings:
            config["title"] = session_title(body.settings["title"])
        if agent.driver != WEBOT and body.settings.get("api_key") == "":
            config["api_key"] = agent.config.get("api_key", "")  # an empty field keeps the saved key
        return agent_card(store.update(user, agent.agent_id, name=body.name, config=config))

    @router.delete("/v1/agents/{ref}")
    async def delete_agent(ref: str, authorization: str | None = Header(None)):
        agent = lookup(user_of(authorization), ref)
        try:
            await gateway.destroy(agent)
        except ControlError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.delete(agent.owner, agent.agent_id)
        from agents.native_sessions import release_agent
        release_agent(agent.owner, agent.agent_id)
        for forget in on_delete:
            forget(agent)
        return {"deleted": agent.agent_id}

    return router


def create_bridge_router():
    """Assemble the scoped ACP tool bridge at the agent layer."""
    from external.tool_bridge import bridge_router
    return bridge_router()
