from __future__ import annotations

import json

from langchain_core.messages import HumanMessage

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
        # caller. When present, the reply is forced through the provider's own
        # constrained-decoding / forced-tool-call path via LangChain's
        # with_structured_output(), so the result is guaranteed to conform —
        # no prose "please reply in JSON" hint or text-parsing fallback needed.
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
            if response_schema is not None:
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


register(TempConnector())
