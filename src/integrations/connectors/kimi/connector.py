from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class KimiConnector(GenericAcpConnector):
    platform = "kimi"
    aliases: list[str] = []


register(KimiConnector())
