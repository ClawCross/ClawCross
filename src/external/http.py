"""Any OpenAI-compatible endpoint: ``POST <api_url>/v1/chat/completions`` with the
agent's ``model``. The runtime keeps the conversation, so the identity is sent
only when it has not been told it yet, or when it changed."""

from __future__ import annotations

from typing import Any

import httpx

from agents.messages import AgentMessage, AgentReply, build_openai_content
from agents.runtime import NO_TIMEOUT, Runtime
from agents.store import Agent, AgentStore
from external import session


def chat_completions_url(api_url: str) -> str:
    api_url = api_url.rstrip("/")
    if api_url.endswith("/v1/chat/completions"):
        return api_url
    return (api_url if api_url.endswith("/v1") else api_url + "/v1") + "/chat/completions"


def _reply_text(data: Any) -> str:
    """The reply in an OpenAI-shaped response (or a plain ``content``/``text``/… field)."""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        for key in ("message", "delta"):
            part = choices[0].get(key)
            if isinstance(part, dict) and isinstance(part.get("content"), str):
                return part["content"]
    return next((data[k] for k in ("content", "text", "message", "reply") if isinstance(data.get(k), str)), "")


class HttpRuntime(Runtime):
    controls = ("reset",)

    def __init__(self, store: AgentStore | None = None) -> None:
        super().__init__()
        self._store = store

    def endpoint(self, agent: Agent) -> tuple[str, str, str, dict[str, str]]:
        """``(api_url, api_key, model, headers)`` for this agent's session."""
        config = agent.config
        api_key = str(config.get("api_key") or "")
        headers = {"Content-Type": "application/json", **dict(config.get("headers") or {})}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return str(config.get("api_url") or ""), api_key, str(config.get("model") or "") or "gpt-3.5-turbo", headers

    def session_fields(self, agent: Agent) -> dict[str, str]:
        """How the request names the agent's session: a body field."""
        return {"session_id": session.runtime_session(agent)}

    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, enabled_tools, response_format, timeout) -> AgentReply:
        api_url, _api_key, model, headers = self.endpoint(agent)
        if not api_url:
            return AgentReply(ok=False, error=f"{agent.agent_id} has no api_url")
        identity = session.identity_prompt(agent, context, msg.instructions)
        # The runtime keeps the conversation: it is told who it is once, and again when that changes.
        inject = bool(identity) and identity != agent.runtime.get("identity_prompt")
        text = f"{identity}\n\n{msg.text}".strip() if inject else msg.text
        messages = [{"role": "user", "content": build_openai_content(text, msg.attachments)}]
        body = {"model": model, "messages": messages, "stream": False, **self.session_fields(agent)}
        wait = None if timeout == NO_TIMEOUT else (timeout if timeout is not None else 60)

        async def send() -> session.Sent:
            try:
                async with httpx.AsyncClient(timeout=wait) as client:
                    response = await client.post(chat_completions_url(api_url), json=body, headers=headers)
            except httpx.HTTPError as exc:
                return session.Sent(ok=False, error=f"{type(exc).__name__}: {exc}")
            if response.status_code != 200:
                return session.Sent(ok=False, error=f"HTTP {response.status_code}: {response.text[:300]}")
            data = response.json()
            return session.Sent(ok=True, content=_reply_text(data), raw=data)

        reply = await session.exchange(agent, connect_type="http", prompt=messages, context=context, send=send)
        if reply.ok:
            session.remember(self._store, agent, identity_prompt=identity)
        return reply

    async def status(self, agent: Agent) -> dict[str, Any]:
        last_used = agent.runtime.get("last_used_at")
        return {"state": "online" if last_used else "idle", "last_used_at": last_used}

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        if action != "reset":
            return await super().control(agent, action)
        session.forget(self._store, agent)
        return {"reset": True}

    async def history(self, agent: Agent, limit: int) -> list[dict[str, Any]]:
        return await session.log(agent, limit)

    async def destroy(self, agent: Agent) -> None:
        await session.drop_log(agent)
