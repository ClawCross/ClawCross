"""WeBot as a runtime of the agent layer.

WeBot runs in the Agent service. Calls reach it over its HTTP entrances: ``ask``
is ``/v1/chat/completions``, ``trigger`` and ``inbox`` are ``/system_trigger``
(an inbox entry names its sender; WeBot takes it when the session is free).
The control plane reads the engine itself, so it is there only in the Agent
service (``engine``). An agent's session is the WeBot thread ``<owner>#<agent_id>``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import BaseModel

from agents.messages import AgentMessage, AgentReply, DeliveryReceipt, build_openai_content
from agents.runtime import NO_TIMEOUT, ControlError, Runtime
from agents.store import Agent


def _fields(mode: str | None, tools: list[str] | None) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if tools is not None:
        fields["enabled_tools"] = list(tools)
    if mode:
        fields["session_mode"] = mode
        if mode == "chat":
            fields["enabled_tools"] = []  # chat: no tool calls at all
    return fields


def _response_format(response_format: Any) -> Any:
    """A Pydantic model as the ``json_schema`` ``response_format`` WeBot enforces."""
    if not (isinstance(response_format, type) and issubclass(response_format, BaseModel)):
        return response_format
    from core.tool_schema import to_strict_parameters

    return {"type": "json_schema", "json_schema": {
        "name": response_format.__name__,
        "schema": to_strict_parameters(response_format.model_json_schema()),
        "strict": True,
    }}


class WebotRuntime(Runtime):
    controls = ("cancel", "reset")

    def __init__(self, *, base_url: str | None = None, internal_token: str | None = None, engine: Any = None):
        super().__init__()
        self.base_url = base_url or f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}"
        self.internal_token = os.getenv("INTERNAL_TOKEN", "") if internal_token is None else internal_token
        self.engine = engine

    @staticmethod
    def thread(agent: Agent) -> str:
        return f"{agent.owner}#{agent.agent_id}"

    # ── calls ────────────────────────────────────────────────────────────

    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, tools, response_format, timeout) -> AgentReply:
        from integrations.agent_sender import SendToAgentRequest, send_to_agent

        messages: list[dict] = []
        if msg.instructions:
            messages.append({"role": "system", "content": msg.instructions})
        messages.append({"role": "user", "content": build_openai_content(msg.text, msg.attachments)})
        body: dict[str, Any] = {"model": "webot", "messages": messages, "stream": False, **_fields(mode, tools)}
        if response_format is not None:
            body["response_format"] = _response_format(response_format)
        if agent.config.get("llm"):
            body["llm_override"] = agent.config["llm"]
        result = await send_to_agent(SendToAgentRequest(
            prompt=messages,
            connect_type="http",
            platform="internal",
            session=agent.agent_id,
            options={
                "api_url": f"{self.base_url}/v1/chat/completions",
                "headers": {"Authorization": f"Bearer {self.internal_token}:{agent.owner}"},
                "body": body,
                "timeout": None if timeout == NO_TIMEOUT else (timeout if timeout is not None else 500),
                "_history_disabled": True,  # WeBot keeps its own history
            },
        ))
        return AgentReply(ok=result.ok, content=result.content or "", error=result.error or "", meta=result.meta or {})

    async def _post(self, path: str, body: dict[str, Any]) -> DeliveryReceipt:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(f"{self.base_url}{path}", headers={"X-Internal-Token": self.internal_token},
                                             json=body)
        except httpx.HTTPError as exc:
            return DeliveryReceipt(accepted=False, error=str(exc))
        if response.status_code >= 400:
            return DeliveryReceipt(accepted=False, error=f"HTTP {response.status_code}: {response.text[:300]}")
        return DeliveryReceipt(accepted=True)

    async def trigger(self, agent: Agent, msg: AgentMessage, *, context, mode, coalesce_key, on_complete) -> DeliveryReceipt:
        body: dict[str, Any] = {
            "user_id": agent.owner,
            "session_id": agent.agent_id,
            "text": f"{msg.text}\n\n{msg.instructions}" if msg.instructions else msg.text,
            **_fields(mode, None),
        }
        if coalesce_key:
            body["coalesce_key"] = coalesce_key
        if msg.attachments:
            body["attachments"] = list(msg.attachments)
        return await self._post("/system_trigger", body)

    async def inbox(self, agent: Agent, msg: AgentMessage, *, context, on_complete) -> DeliveryReceipt:
        return await self._post("/system_trigger", {
            "user_id": agent.owner,
            "session_id": agent.agent_id,
            "text": msg.text,
            "inbox_source_session": msg.sender or "system",
        })

    # ── control plane ────────────────────────────────────────────────────

    def _engine(self) -> Any:
        if self.engine is None:
            raise ControlError("WeBot is controlled in the Agent service")
        return self.engine

    def _thread_state(self, agent: Agent) -> dict[str, Any]:
        return self._engine().get_all_thread_status(f"{agent.owner}#").get(self.thread(agent), {})

    def is_busy(self, agent: Agent) -> bool:
        running = self._engine().list_active_task_keys(f"{agent.owner}#")
        return bool(self._thread_state(agent).get("busy")) or self.thread(agent) in set(running)

    async def status(self, agent: Agent) -> dict[str, Any]:
        usage = getattr(self._engine(), "get_thread_context_usage", None)
        return {
            "state": "running" if self.is_busy(agent) else "idle",
            "context": usage(self.thread(agent)) if callable(usage) else None,
            "pending": self._thread_state(agent).get("pending_system", 0),
        }

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        engine, thread = self._engine(), self.thread(agent)
        if action == "cancel":
            return {"cancelled": bool(await engine.cancel_task(thread))}
        if action != "reset":
            return await super().control(agent, action)
        from utils.checkpoint_repository import delete_thread_records

        await engine.cancel_task(thread)
        close = getattr(engine, "close_thread_checkpoint", None)
        if callable(close):
            await close(thread)
        if getattr(engine, "_db_path", ""):
            await delete_thread_records(engine._db_path, thread)
        return {"reset": True}

    async def history(self, agent: Agent, limit: int) -> list[dict[str, Any]]:
        """The session's messages, tool calls included."""
        from services.llm_factory import extract_text

        snapshot = await self._engine().agent_app.aget_state({"configurable": {"thread_id": self.thread(agent)}})
        out: list[dict[str, Any]] = []
        for msg in (snapshot.values.get("messages", []) if snapshot and snapshot.values else []):
            kind = type(msg).__name__
            if kind == "HumanMessage":
                out.append({"role": "user", "content": extract_text(msg.content)})
            elif kind in ("AIMessage", "AIMessageChunk"):
                calls = [{"name": c.get("name", ""), "args": c.get("args", {})} for c in (getattr(msg, "tool_calls", None) or [])]
                content = extract_text(msg.content)
                if content or calls:
                    out.append({"role": "assistant", "content": content, **({"tool_calls": calls} if calls else {})})
            elif kind == "ToolMessage":
                out.append({"role": "tool", "content": extract_text(msg.content), "tool_name": getattr(msg, "name", "")})
        return out[-limit:]

    async def destroy(self, agent: Agent) -> None:
        receipt = await self._post("/delete_session", {"user_id": agent.owner, "session_id": agent.agent_id})
        if not receipt.accepted:
            raise RuntimeError(receipt.error)
