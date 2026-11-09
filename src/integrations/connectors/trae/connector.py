from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class TraeConnector(GenericAcpConnector):
    platform = "trae"
    aliases: list[str] = []


register(TraeConnector())
