from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class AcpConnector(GenericAcpConnector):
    """Wildcard connector for connect_type='acp' fallback."""
    platform = "acp"
    aliases: list[str] = []


register(AcpConnector())
