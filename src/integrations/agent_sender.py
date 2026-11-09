from __future__ import annotations

# ---------------------------------------------------------------------------
# Public dataclasses – re-exported from base.py
# ---------------------------------------------------------------------------
from integrations.base import (  # noqa: F401
    PreparedAgentStream,
    ResetAgentRequest,
    ResetAgentResult,
    SendToAgentRequest,
    SendToAgentResult,
)

# ---------------------------------------------------------------------------
# Public functions – re-exported from registry.py
# ---------------------------------------------------------------------------
from integrations.registry import (  # noqa: F401
    prepare_send_to_agent_stream,
    reset_agent,
    send_to_agent,
)

# ---------------------------------------------------------------------------
# Trigger self-registration of all connectors
# ---------------------------------------------------------------------------
import integrations.connectors  # noqa: F401

# ---------------------------------------------------------------------------
# Backward-compat: register_sender / register_resetter
# ---------------------------------------------------------------------------
# These are kept so that any code calling register_sender("acp", fn) or
# register_sender("http", fn) continues to work.  The registered function
# is wrapped in a thin AgentConnector shim and inserted into the registry
# under that connect_type key (which serves as the wildcard fallback key).
from typing import Any, Awaitable, Callable

from integrations.base import (
    ResetAgentRequest as _ResetAgentRequest,
    ResetAgentResult as _ResetAgentResult,
    SendToAgentRequest as _SendToAgentRequest,
    SendToAgentResult as _SendToAgentResult,
)
from integrations.connectors._base import AgentConnector as _AgentConnector
import integrations.registry as _registry

SenderFunc = Callable[[_SendToAgentRequest], Awaitable[_SendToAgentResult]]
ResetterFunc = Callable[[_ResetAgentRequest], Awaitable[_ResetAgentResult]]


def register_sender(connect_type: str, sender: SenderFunc) -> None:
    key = (connect_type or "").strip().lower()

    class _WrappedSender(_AgentConnector):
        platform = key
        aliases: list[str] = []

        async def send(self, request: _SendToAgentRequest) -> _SendToAgentResult:
            return await sender(request)

    _registry.register(_WrappedSender())


def register_resetter(connect_type: str, resetter: ResetterFunc) -> None:
    key = (connect_type or "").strip().lower()
    existing = _registry._CONNECTORS.get(key)

    if existing is not None:
        # Monkey-patch the reset method onto the existing connector
        async def _reset(request: _ResetAgentRequest) -> _ResetAgentResult:
            return await resetter(request)
        existing.reset = _reset  # type: ignore[method-assign]
    else:
        class _WrappedResetter(_AgentConnector):
            platform = key
            aliases: list[str] = []

            async def send(self, request: _SendToAgentRequest) -> _SendToAgentResult:
                return _SendToAgentResult(ok=False, error=f"no sender for {key}")

            async def reset(self, request: _ResetAgentRequest) -> _ResetAgentResult:
                return await resetter(request)

        _registry.register(_WrappedResetter())


# Keep httpx importable from this module so that any remaining patches
# targeting `integrations.agent_sender.httpx` still find the symbol.
import httpx  # noqa: F401
