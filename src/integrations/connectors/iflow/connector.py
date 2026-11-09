from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class IflowConnector(GenericAcpConnector):
    platform = "iflow"
    aliases: list[str] = []


register(IflowConnector())
