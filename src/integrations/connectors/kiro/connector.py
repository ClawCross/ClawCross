from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class KiroConnector(GenericAcpConnector):
    platform = "kiro"
    aliases: list[str] = []


register(KiroConnector())
