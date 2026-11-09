from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class KilocodeConnector(GenericAcpConnector):
    platform = "kilocode"
    aliases: list[str] = []


register(KilocodeConnector())
