from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class CursorConnector(GenericAcpConnector):
    platform = "cursor"
    aliases: list[str] = []


register(CursorConnector())
