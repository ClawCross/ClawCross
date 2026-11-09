from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class PiConnector(GenericAcpConnector):
    platform = "pi"
    aliases: list[str] = []


register(PiConnector())
