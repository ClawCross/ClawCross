from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class OpencodeConnector(GenericAcpConnector):
    platform = "opencode"
    aliases: list[str] = []


register(OpencodeConnector())
