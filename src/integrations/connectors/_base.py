from __future__ import annotations

from abc import ABC, abstractmethod

from integrations.base import (
    PreparedAgentStream,
    SendToAgentRequest,
    SendToAgentResult,
)


class AgentConnector(ABC):
    platform: str
    aliases: list[str] = []

    @abstractmethod
    async def send(self, request: SendToAgentRequest) -> SendToAgentResult: ...

    async def prepare_stream(self, request: SendToAgentRequest) -> PreparedAgentStream:
        raise NotImplementedError(f"streaming not supported for platform: {self.platform}")
