"""The OpenAI-compatible surface of the agent layer: ``/v1/chat/completions`` and ``/v1/models``.

``session_id`` is the agent's number (or ``<team>.<name>``); a number not seen
before is a new agent of the runtime ``model`` names (WeBot when it names none,
like ``gpt-4o``). Every call goes through the gateway: a runtime that speaks the
protocol itself answers it (WeBot: streaming, the caller's tools); any other is
asked the last user message and its reply comes back in the protocol's shape.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.messages import AgentMessage, parse_openai_content
from agents.store import HTTP, WEBOT, Agent, AgentStore, canonical_platform, valid_agent_id
from common.auth_utils import extract_user_password_session, is_internal_bearer, parse_bearer_parts


class ChatMessageContent(BaseModel):
    """One part of a message's content (text / image_url / input_audio / file)."""
    type: str
    text: Optional[str] = None
    image_url: Optional[dict] = None
    input_audio: Optional[dict] = None
    file: Optional[dict] = None


class ChatMessage(BaseModel):
    role: str  # "system" | "user" | "assistant" | "tool"
    content: Optional[Any] = None  # str or list[ChatMessageContent]
    name: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[list[dict]] = None
    tool_choice: Optional[Any] = None
    user: Optional[str] = None
    session_id: Optional[str] = None  # the number of the agent to talk to
    password: Optional[str] = None
    enabled_tools: Optional[list[str]] = None
    llm_override: Optional[dict] = None  # the model for this request
    max_turns: Optional[int] = None
    # This turn's run mode, over the session's own.
    session_mode: Optional[str] = None
    # OpenAI-shaped forced reply format, e.g.
    # {"type": "json_schema", "json_schema": {"name": ..., "schema": {...}, "strict": True}}
    response_format: Optional[dict] = None


# ── the protocol's shapes ────────────────────────────────────────────────

def completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def response(content: str, *, model: str, finish_reason: str = "stop", tool_calls: list | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    return {
        "id": completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def chunk(*, completion_id: str, content: str = "", model: str, finish_reason: str | None = None,
          meta: dict | None = None) -> str:
    """One server-sent event of a streamed completion."""
    delta: dict[str, Any] = {}
    if content:
        delta["content"] = content
    if meta:
        delta["meta"] = meta
    if finish_reason is None and not content and not meta:
        delta["role"] = "assistant"
    event = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def streaming(events) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


async def answer_by_asking(gateway: Any, agent: Agent, req: ChatCompletionRequest):
    """A completion from a runtime that does not speak the protocol: it is asked the
    last user message, and the whole reply is sent back (as one delta when streamed)."""
    text, attachments = next(
        (parse_openai_content(m.content) for m in reversed(req.messages) if m.role == "user"), ("", []))
    instructions = "\n\n".join(parse_openai_content(m.content)[0] for m in req.messages
                                 if m.role in ("system", "developer"))
    reply = await gateway.ask(agent, AgentMessage(text=text, attachments=attachments, sender=f"u:{agent.owner}",
                                                  instructions=instructions),
                              context={"command_tools": req.tools or []}, enabled_tools=req.enabled_tools,
                              mode=req.session_mode, response_format=req.response_format)
    if not reply.ok:
        raise HTTPException(status_code=502, detail=reply.error or "agent call failed")
    model = req.model or agent.platform
    if not req.stream:
        return response(reply.content, model=model)
    cid = completion_id()

    async def events():
        yield chunk(model=model, completion_id=cid)
        if reply.content:
            yield chunk(content=reply.content, model=model, completion_id=cid)
        yield chunk(model=model, finish_reason="stop", completion_id=cid)
        yield "data: [DONE]\n\n"

    return streaming(events())


# ── routes ───────────────────────────────────────────────────────────────

def create_openai_router(
    *,
    internal_token: str,
    verify_password: Callable[[str, str], bool],
    store: AgentStore,
    gateway: Any,
    names: Callable[[str, str], Agent | None] | None = None,
) -> APIRouter:
    router = APIRouter()

    def caller(req: ChatCompletionRequest, authorization: str | None) -> tuple[str, str | None]:
        """``(user, session)``: ``Bearer <internal>:<user>[:<session>]``, ``Bearer
        <user>:<password>[:<session>]``, or ``user`` / ``password`` in the body."""
        user, password, session = req.user, req.password, None
        parts = parse_bearer_parts(authorization)
        if parts:
            if is_internal_bearer(parts, internal_token):
                return (parts[1] if len(parts) >= 2 else user or "system"), (parts[2] if len(parts) >= 3 else None)
            parsed = extract_user_password_session(parts, default_session="")
            if parsed:
                user, password, session = parsed
        if not user or not password or not verify_password(user, password):
            raise HTTPException(status_code=401, detail="认证失败")
        return user, session

    def target(user: str, ref: str, model: str | None) -> Agent:
        agent = store.get(user, ref) or (names(user, ref) if names else None)
        if agent is not None:
            return agent
        if not valid_agent_id(ref):
            raise HTTPException(status_code=404, detail=f"no agent {ref!r}")
        from agents.routes import runtime_of

        driver, config = runtime_of(model or "")
        if driver == HTTP:  # a model name, not a runtime: the agent is WeBot's
            driver, config = WEBOT, {}
        return store.ensure(user, ref, driver=driver, config=config)

    @router.post("/v1/chat/completions")
    async def chat_completions(req: ChatCompletionRequest, authorization: str | None = Header(None)):
        user, session = caller(req, authorization)
        ref = (session or req.session_id or "").strip()
        if not ref:
            raise HTTPException(status_code=400, detail="session_id is required: it is the number of the agent")
        return await gateway.chat(target(user, ref, req.model), req)

    @router.get("/v1/models")
    async def list_models():
        """The runtimes a new agent can have: ``webot``, each ACP agent, ``openclaw``."""
        from agents.platforms import acpx_agent_tags_with_legacy

        created = int(time.time())
        runtimes = ["webot", *sorted({canonical_platform(t) for t in acpx_agent_tags_with_legacy()}), "openclaw"]
        return {"object": "list", "data": [
            {"id": r, "object": "model", "created": created, "owned_by": "webot" if r == "webot" else "clawcross"}
            for r in runtimes
        ]}

    return router
