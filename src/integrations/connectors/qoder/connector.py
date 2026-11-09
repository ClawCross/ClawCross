from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class QoderConnector(GenericAcpConnector):
    platform = "qoder"
    aliases: list[str] = []


register(QoderConnector())
