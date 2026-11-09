from __future__ import annotations

from integrations.connectors._generic_acp import GenericAcpConnector
from integrations.registry import register


class ClaudeConnector(GenericAcpConnector):
    platform = "claude"
    aliases: list[str] = ["claude-code", "claudecode"]


register(ClaudeConnector())
