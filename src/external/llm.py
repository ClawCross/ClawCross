"""A model call with a persona: no tools, remembers nothing between messages.

A reply format is decoded by the model service: ``with_structured_output`` where
a tool call can be forced, otherwise the schema is offered as the only tool.
"""

from __future__ import annotations

import json
import re

from agents.messages import AgentMessage, AgentReply
from agents.runtime import Runtime
from agents.store import Agent


async def _reply_through_optional_tool(llm, prompt: str, schema: dict, name: str) -> str:
    """A structured reply from a model that cannot be forced to call a tool.

    The schema is offered as the only tool, strict, with ``tool_choice="auto"``:
    a call is decoded inside the schema. A model that answers in text anyway
    was also given the schema in the prompt, and its text is returned for the
    caller to parse.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from webot.engine.tool_schema import drop_null_optionals, reply_schema_hint, strict_tool_binding, to_strict_parameters
    from services.llm_factory import extract_text

    model, strict, bind_kwargs = strict_tool_binding(llm)
    tool = {
        "type": "function",
        "function": {
            "name": name,
            "description": str(schema.get("description") or "Your reply."),
            "parameters": to_strict_parameters(schema) if strict else schema,
            **({"strict": True} if strict else {}),
        },
    }
    reply = await model.bind_tools([tool], tool_choice="auto", **bind_kwargs).ainvoke([
        SystemMessage(content=f"Give your answer by calling {name}. {reply_schema_hint(schema)}"),
        HumanMessage(content=prompt),
    ])
    calls = [c for c in reply.tool_calls or [] if c.get("name") == name]
    if not calls:
        if reply.response_metadata.get("finish_reason") == "length":
            raise RuntimeError("reply was cut off at max_tokens before it was complete")
        return extract_text(reply.content)
    return json.dumps(drop_null_optionals(calls[0]["args"], schema), ensure_ascii=False)


class LlmRuntime(Runtime):
    async def ask(self, agent: Agent, msg: AgentMessage, *, context, mode, tools, response_format, timeout) -> AgentReply:
        from langchain_core.messages import HumanMessage

        from webot.engine.tool_schema import forced_tool_choice_supported
        from services.llm_factory import create_chat_model, extract_text

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
                return AgentReply(ok=True, content=await _reply_through_optional_tool(llm, prompt, schema, name))
            data = await llm.with_structured_output({**schema, "title": name}).ainvoke([HumanMessage(content=prompt)])
            return AgentReply(ok=True, content=json.dumps(data, ensure_ascii=False))
        except Exception as exc:
            return AgentReply(ok=False, error=str(exc))
