from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class DroidConnector(GenericAcpConnector):
    platform = "droid"
    aliases: list[str] = []


register(DroidConnector())
