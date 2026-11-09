from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class QwenConnector(GenericAcpConnector):
    platform = "qwen"
    aliases: list[str] = []


register(QwenConnector())
