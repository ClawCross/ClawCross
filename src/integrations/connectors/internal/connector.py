from __future__ import annotations

import os

from integrations.base import (
    SendToAgentRequest,
    SendToAgentResult,
)
from integrations.connectors._generic_http import GenericHttpConnector
from integrations.registry import register


class InternalConnector(GenericHttpConnector):
    """Connector for internal (same-host) HTTP agent."""

    platform = "internal"
    aliases: list[str] = []

    async def send(self, request: SendToAgentRequest) -> SendToAgentResult:
        options = dict(request.options or {})
        if not options.get("api_url"):
            port = os.getenv("PORT_AGENT", "51200")
            options["api_url"] = f"http://127.0.0.1:{port}/v1/chat/completions"
        updated_request = SendToAgentRequest(
            prompt=request.prompt,
            connect_type=request.connect_type,
            platform=request.platform,
            session=request.session,
            options=options,
        )
        return await super().send(updated_request)


register(InternalConnector())
