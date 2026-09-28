"""Sending to an agent runtime over a registered connector (the agent layer's transport)."""

from __future__ import annotations

from integrations.base import (  # noqa: F401
    PreparedAgentStream,
    SendToAgentRequest,
    SendToAgentResult,
)
from integrations.registry import (  # noqa: F401
    prepare_send_to_agent_stream,
    send_to_agent,
)

# Importing the connectors registers them.
import integrations.connectors  # noqa: F401
