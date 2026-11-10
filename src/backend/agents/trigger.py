"""The system trigger entrance: ``POST /system_trigger`` (internal token only).

A message for agent ``session_id`` of ``user_id`` — a number not seen before is a
new WeBot agent — handed to the gateway by what the caller wants:

* ``wait_reply``           → ``ask``: handled after the agent's current turn, the reply returned;
* ``inbox_source_session`` → ``inbox``: put in its inbox, from that session;
* otherwise                → ``trigger``: handled now (``coalesce_key`` merges waiting ones).
"""

from __future__ import annotations

import secrets
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from agents.messages import AgentMessage
from agents.store import AgentStore, valid_agent_id


class TriggerAttachment(BaseModel):
    type: str          # "image" | "audio" | "file"
    name: str
    data: str          # base64
    mime_type: str


class TriggerRequest(BaseModel):
    user_id: str
    session_id: str  # the number of the agent it is for
    text: str = "summary"
    attachments: Optional[list[TriggerAttachment]] = None
    coalesce_key: str = ""
    session_mode: Optional[str] = None  # this message's run mode
    # With wait_reply: the tools this turn may use (none: the agent's own) and the reply's format.
    enabled_tools: Optional[list[str]] = None
    response_format: Optional[dict] = None
    wait_reply: bool = False
    # Who sent it, for the inbox: their session, user (when another), label and a one-line summary.
    inbox_source_session: str = ""
    inbox_source_user: str = ""
    inbox_source_label: str = ""
    inbox_summary: str = ""


def create_trigger_router(*, internal_token: str, store: AgentStore, gateway: Any) -> APIRouter:
    router = APIRouter()

    @router.post("/system_trigger")
    async def system_trigger(req: TriggerRequest, x_internal_token: str | None = Header(None)):
        if not internal_token or not secrets.compare_digest(x_internal_token or "", internal_token):
            raise HTTPException(status_code=403, detail="无效的内部通信凭证")
        if not valid_agent_id(req.session_id):
            raise HTTPException(status_code=400, detail=f"invalid agent id {req.session_id!r}")
        agent = store.ensure(req.user_id, req.session_id)
        msg = AgentMessage(text=req.text, attachments=[a.model_dump() for a in req.attachments or []],
                           sender=req.inbox_source_session or "system", summary=req.inbox_summary)
        if req.wait_reply:
            reply = await gateway.ask(agent, msg, mode=req.session_mode, enabled_tools=req.enabled_tools,
                                      response_format=req.response_format)
            return {"status": "completed", "reply": reply.content if reply.ok else f"❌ {reply.error}"}
        if req.inbox_source_session:
            receipt = await gateway.inbox(agent, msg, context={
                "source_user": req.inbox_source_user or req.user_id, "source_label": req.inbox_source_label,
            })
            status = "queued"
        else:
            receipt = await gateway.trigger(agent, msg, mode=req.session_mode, coalesce_key=req.coalesce_key or None)
            status = "received"
        if not receipt.accepted:
            raise HTTPException(status_code=502, detail=receipt.error)
        return {"status": status, "message": f"已交给 {agent.agent_id}"}

    return router
