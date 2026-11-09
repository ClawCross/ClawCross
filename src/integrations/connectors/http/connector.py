from __future__ import annotations

from integrations.connectors._generic_http import GenericHttpConnector
from integrations.registry import register


class HttpConnector(GenericHttpConnector):
    """Wildcard connector for connect_type='http' fallback."""
    platform = "http"
    aliases: list[str] = []


register(HttpConnector())
