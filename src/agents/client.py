"""The agent layer from another process (OASIS, the scheduler): every call goes over
the Agent service's entrances, where the runtimes are.

``/v1/agents`` to make, ask and delete an agent; ``/system_trigger`` to trigger one.
Sending to an id not seen before makes that agent (WeBot).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel

from agents.messages import AgentMessage, AgentReply, DeliveryReceipt
from agents.runtime import NO_TIMEOUT


def response_format_of(reply: type[BaseModel] | dict | None) -> dict | None:
    """The OpenAI ``response_format`` asking for a reply shaped like *reply*."""
    if not (isinstance(reply, type) and issubclass(reply, BaseModel)):
        return reply
    return {"type": "json_schema",
            "json_schema": {"name": reply.__name__, "schema": reply.model_json_schema(), "strict": True}}


def _error(response: httpx.Response) -> str:
    return f"HTTP {response.status_code}: {response.text[:300]}"


class AgentClient:
    """The agents of *owner*."""

    def __init__(self, owner: str, *, base_url: str | None = None, internal_token: str | None = None):
        self.owner = owner
        self.base_url = base_url or f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}"
        self.internal_token = os.getenv("INTERNAL_TOKEN", "") if internal_token is None else internal_token

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.internal_token}:{self.owner}"}

    async def create(self, **fields: Any) -> dict[str, Any]:
        """Make an agent (``POST /v1/agents``); an id already there is that agent."""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/v1/agents", headers=self._auth(), json=fields)
        if response.status_code == 409:
            return response.json()["detail"]["agent"]
        if response.status_code >= 400:
            raise RuntimeError(_error(response))
        return response.json()

    async def ask(self, ref: str, msg: AgentMessage, *, mode: str | None = None, tools: list[str] | None = None,
                  response_format: type[BaseModel] | dict | None = None, timeout: float | None = None) -> AgentReply:
        """Send *msg* and wait for the reply; ``NO_TIMEOUT`` waits as long as the agent takes."""
        body = {
            "text": msg.text, "attachments": list(msg.attachments), "instructions": msg.instructions,
            "mode": mode, "tools": tools, "response_format": response_format_of(response_format),
            "timeout": 0 if timeout == NO_TIMEOUT else timeout,
        }
        # The agent service keeps the time; waiting here only ends a request it has lost.
        wait = None if timeout in (None, NO_TIMEOUT) else timeout + 60
        try:
            async with httpx.AsyncClient(timeout=wait) as client:
                response = await client.post(f"{self.base_url}/v1/agents/{ref}/messages", headers=self._auth(), json=body)
        except httpx.HTTPError as exc:
            return AgentReply(ok=False, error=f"{type(exc).__name__}: {exc}")
        if response.status_code >= 400:
            return AgentReply(ok=False, error=_error(response))
        data = response.json()
        return AgentReply(ok=bool(data.get("ok")), content=data.get("content") or "", error=data.get("error") or "")

    async def trigger(self, agent_id: str, msg: AgentMessage) -> DeliveryReceipt:
        """Hand *msg* to the agent to be handled now (``/system_trigger``)."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/system_trigger", headers={"X-Internal-Token": self.internal_token},
                    json={"user_id": self.owner, "session_id": agent_id, "text": msg.text,
                          "attachments": list(msg.attachments) or None},
                )
        except httpx.HTTPError as exc:
            return DeliveryReceipt(accepted=False, error=str(exc))
        if response.status_code >= 400:
            return DeliveryReceipt(accepted=False, error=_error(response))
        return DeliveryReceipt(accepted=True)

    async def delete(self, ref: str) -> bool:
        """Delete an agent, its session and its record."""
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.delete(f"{self.base_url}/v1/agents/{ref}", headers=self._auth())
        if response.status_code >= 400 and response.status_code != 404:
            raise RuntimeError(_error(response))
        return response.status_code < 400
