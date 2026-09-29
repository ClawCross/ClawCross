"""Any OpenAI-compatible endpoint: ``POST <api_url>/v1/chat/completions`` with the
agent's ``model``. The runtime keeps the conversation, so the identity is sent
only when it has not been told it yet, or when it changed."""

from __future__ import annotations

from typing import Any

from agents.messages import AgentMessage, AgentReply, build_openai_content
from agents.runtime import NO_TIMEOUT, Runtime
from agents.store import Agent, AgentStore
from external import session


def chat_completions_url(api_url: str) -> str:
    api_url = api_url.rstrip("/")
    if api_url.endswith("/v1/chat/completions"):
        return api_url
    return (api_url if api_url.endswith("/v1") else api_url + "/v1") + "/chat/completions"


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

    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, tools, response_format, timeout) -> AgentReply:
        from integrations.agent_sender import SendToAgentRequest, send_to_agent
        from utils.external_agent_history import attach_history_context

        api_url, api_key, model, headers = self.endpoint(agent)
        if not api_url:
            return AgentReply(ok=False, error=f"{agent.agent_id} has no api_url")
        messages = [{"role": "user", "content": build_openai_content(msg.text, msg.attachments)}]
        identity = session.identity_prompt(agent, context, msg.instructions)
        options: dict[str, Any] = {
            "api_url": chat_completions_url(api_url),
            "api_key": api_key,
            "headers": headers,
            "body": {"model": model, "messages": messages, "stream": False},
            "timeout": None if timeout == NO_TIMEOUT else (timeout if timeout is not None else 60),
            "identity_prompt": identity,
            "inject_identity": bool(identity) and identity != agent.runtime.get("identity_prompt"),
            "identity_injection_mode": "prepend_user",
        }
        options = attach_history_context(
            options, user_id=agent.owner, group_id=str(context.get("conversation_id") or ""), global_name=agent.agent_id,
        )
        result = await send_to_agent(SendToAgentRequest(
            prompt=messages,
            connect_type="http",
            platform=agent.platform,
            session=session.runtime_session(agent),
            options=options,
        ))
        if result.ok:
            session.remember(self._store, agent, identity_prompt=identity)
        return session.reply_of(result)

    async def status(self, agent: Agent) -> dict[str, Any]:
        last_used = agent.runtime.get("last_used_at")
        return {"state": "online" if last_used else "idle", "last_used_at": last_used}

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        if action != "reset":
            return await super().control(agent, action)
        session.forget(self._store, agent)
        return {"reset": True}

    async def history(self, agent: Agent, limit: int) -> list[dict[str, Any]]:
        return await session.history(agent, limit)

    async def destroy(self, agent: Agent) -> None:
        await session.drop_history(agent)
