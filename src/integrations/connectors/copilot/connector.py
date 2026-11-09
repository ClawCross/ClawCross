from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class CopilotConnector(GenericAcpConnector):
    platform = "copilot"
    aliases: list[str] = []


register(CopilotConnector())
