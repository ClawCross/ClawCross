from __future__ import annotations

import contextlib
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from core.tool_schema import (
    drop_null_optionals,
    forced_tool_choice_supported,
    reply_schema_hint,
    strict_tool_binding,
    to_strict_parameters,
)

from integrations.base import (
    ResetAgentRequest,
    ResetAgentResult,
    SendToAgentRequest,
    SendToAgentResult,
)
from integrations.connectors._base import AgentConnector
from integrations.registry import register
from services.llm_factory import create_chat_model, extract_text


class TempConnector(AgentConnector):
    """In-process LLM connector (stateless)."""

    platform = "temp"
    aliases: list[str] = []

    async def send(self, request: SendToAgentRequest) -> SendToAgentResult:
        options = request.options or {}
        prompt = request.prompt if isinstance(request.prompt, str) else str(request.prompt or "")
        # Optional: a Pydantic model (or JSON-schema-shaped dict) passed by the
        # caller. When present, the reply goes through the provider's own
        # constrained decoding: with_structured_output() where a tool call can
        # be forced, otherwise the schema as an optional strict tool
        # (_reply_through_optional_tool).
        response_schema = options.get("response_schema")
        try:
            llm = create_chat_model(
                temperature=float(options.get("temperature", 0.7)),
                max_tokens=int(options.get("max_tokens", 1024)),
                model=options.get("model"),
                api_key=options.get("api_key"),
                base_url=options.get("base_url"),
                provider=options.get("provider"),
            )
            raw_response = None
            if response_schema is not None and not forced_tool_choice_supported(llm):
                text = await _reply_through_optional_tool(llm, prompt, response_schema)
            elif response_schema is not None:
                structured = llm.with_structured_output(response_schema)
                parsed = await structured.ainvoke([HumanMessage(content=prompt)])
                data = parsed.model_dump() if hasattr(parsed, "model_dump") else parsed
                text = json.dumps(data, ensure_ascii=False)
            else:
                raw_response = await llm.ainvoke([HumanMessage(content=prompt)])
                text = extract_text(raw_response.content)
            return SendToAgentResult(
                ok=True,
                content=text,
                raw_response=raw_response,
                meta={
                    "connect_type": "http",
                    "platform": "temp",
                    "session": request.session,
                },
            )
        except Exception as e:
            return SendToAgentResult(
                ok=False,
                error=str(e),
                meta={
                    "connect_type": "http",
                    "platform": "temp",
                    "session": request.session,
                },
            )

    async def reset(self, request: ResetAgentRequest) -> ResetAgentResult:
        # stateless, no-op
        return ResetAgentResult(ok=True)


async def _reply_through_optional_tool(llm, prompt: str, response_schema) -> str:
    """A structured reply from a model that cannot be forced to call a tool.

    The schema is offered as the only tool, strict, with ``tool_choice="auto"``:
    a call is decoded inside the schema. A model that answers in text anyway
    was also given the schema in the prompt, and its text is returned for the
    caller to parse.
    """
    if isinstance(response_schema, dict):
        name = re.sub(r"[^A-Za-z0-9_-]", "_", str(response_schema.get("title") or "")) or "reply"
        schema, model_cls = response_schema, None
    else:
        schema, name, model_cls = response_schema.model_json_schema(), response_schema.__name__, response_schema
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
    args = drop_null_optionals(calls[0]["args"], schema)
    if model_cls is not None:
        with contextlib.suppress(ValidationError):  # the caller parses what is left
            args = model_cls.model_validate(args).model_dump()
    return json.dumps(args, ensure_ascii=False)


register(TempConnector())
