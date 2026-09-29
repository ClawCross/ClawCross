"""A single model call with a persona: no tools, nothing kept between calls."""

from __future__ import annotations

from agents.messages import AgentMessage, AgentReply
from agents.runtime import Runtime
from agents.store import Agent
from external import session


class LlmRuntime(Runtime):
    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, tools, response_format, timeout) -> AgentReply:
        from integrations.agent_sender import SendToAgentRequest, send_to_agent

        options = {**(agent.config.get("llm") or {}), "_history_disabled": True}  # nothing to look back on
        spec = (response_format or {}).get("json_schema") or {}
        if isinstance(spec.get("schema"), dict):  # decoded within the schema by the model service
            options["response_schema"] = {**spec["schema"], "title": spec.get("name") or "reply"}
        prompt = f"{msg.instructions}\n\n{msg.text}" if msg.instructions else msg.text
        result = await send_to_agent(SendToAgentRequest(
            prompt=prompt, connect_type="http", platform="temp", session=agent.name, options=options,
        ))
        return session.reply_of(result)
