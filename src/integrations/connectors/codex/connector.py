from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class CodexConnector(GenericAcpConnector):
    platform = "codex"
    aliases: list[str] = []


register(CodexConnector())
