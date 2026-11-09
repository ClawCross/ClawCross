from __future__ import annotations

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
        try:
            llm = create_chat_model(
                temperature=float(options.get("temperature", 0.7)),
                max_tokens=int(options.get("max_tokens", 1024)),
                model=options.get("model"),
                api_key=options.get("api_key"),
                base_url=options.get("base_url"),
                provider=options.get("provider"),
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            text = extract_text(resp.content)
            return SendToAgentResult(
                ok=True,
                content=text,
                raw_response=resp,
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
