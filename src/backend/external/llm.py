"""A model call with a persona: no tools, remembers nothing between messages."""

from __future__ import annotations

import json
import re

from agents.messages import AgentMessage, AgentReply
from agents.runtime import Runtime
from agents.store import Agent


class LlmRuntime(Runtime):
    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, enabled_tools, response_format, timeout) -> AgentReply:
        from langchain_core.messages import HumanMessage

        from webot.engine.tool_schema import forced_tool_choice_supported
        from common.llm_factory import create_chat_model, extract_text

        options = agent.config.get("llm") or {}
        prompt = f"{msg.instructions}\n\n{msg.text}" if msg.instructions else msg.text
        spec = (response_format or {}).get("json_schema") or {}
        schema = spec.get("schema") if isinstance(spec.get("schema"), dict) else None
        try:
            llm = create_chat_model(
                temperature=float(options.get("temperature", 0.7)),
                max_tokens=int(options.get("max_tokens", 1024)),
                model=options.get("model"),
                api_key=options.get("api_key"),
                base_url=options.get("base_url"),
                provider=options.get("provider"),
            )
            if schema is None:
                return AgentReply(ok=True, content=extract_text((await llm.ainvoke([HumanMessage(content=prompt)])).content))
            name = re.sub(r"[^A-Za-z0-9_-]", "_", str(spec.get("name") or "")) or "reply"
            if not forced_tool_choice_supported(llm):
                from webot.engine.deepseek_responses import deepseek_structured_turn

                reply = await deepseek_structured_turn(llm, [HumanMessage(content=prompt)], response_format)
                return AgentReply(ok=True, content=reply.content)
            data = await llm.with_structured_output({**schema, "title": name}).ainvoke([HumanMessage(content=prompt)])
            return AgentReply(ok=True, content=json.dumps(data, ensure_ascii=False))
        except Exception as exc:
            return AgentReply(ok=False, error=str(exc))
