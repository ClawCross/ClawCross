from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class GeminiConnector(GenericAcpConnector):
    platform = "gemini"
    aliases: list[str] = ["gemini-cli", "geminicli"]


register(GeminiConnector())
