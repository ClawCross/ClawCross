from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from integrations.base import (
    PreparedAgentStream,
    ResetAgentRequest,
    ResetAgentResult,
    SendToAgentRequest,
    SendToAgentResult,
)
from utils.external_agent_history import get_store, history_options_disabled

if TYPE_CHECKING:
    from integrations.connectors._base import AgentConnector

logger = logging.getLogger(__name__)

_CONNECTORS: dict[str, "AgentConnector"] = {}


def register(connector: "AgentConnector") -> None:
    _CONNECTORS[connector.platform] = connector
    for alias in connector.aliases:
        _CONNECTORS[alias] = connector


def _resolve(request: SendToAgentRequest) -> "AgentConnector | None":
    key = (request.platform or "").strip().lower()
    conn = _CONNECTORS.get(key)
    if conn:
        return conn
    return _CONNECTORS.get((request.connect_type or "").strip().lower())


async def send_to_agent(request: SendToAgentRequest) -> SendToAgentResult:
    key = (request.platform or "").strip().lower()
    conn = _resolve(request)
    if conn is None:
        return SendToAgentResult(ok=False, error=f"unsupported platform: {key}")

    record_history = not history_options_disabled(request.options)
    request_id: str | None = None
    if record_history:
        try:
            store = await get_store()
            request_id = await store.record_send(
                platform=request.platform,
                session_key=request.session,
                connect_type=request.connect_type,
                prompt=request.prompt,
                options=request.options,
            )
        except Exception as e:
            logger.warning("history record_send failed: %s", e)
            request_id = None

    result = await conn.send(request)

    if record_history and request_id:
        try:
            store = await get_store()
            await store.record_recv(
                platform=request.platform,
                session_key=request.session,
                connect_type=request.connect_type,
                request_id=request_id,
                ok=bool(result.ok),
                content=result.content,
                raw_response=result.raw_response,
                error=result.error,
                options=request.options,
            )
        except Exception as e:
            logger.warning("history record_recv failed: %s", e)
        meta = dict(result.meta or {})
        meta.setdefault("history_request_id", request_id)
        result.meta = meta

    return result


async def reset_agent(request: ResetAgentRequest) -> ResetAgentResult:
    key = (request.platform or "").strip().lower()
    conn = _CONNECTORS.get(key)
    if conn:
        return await conn.reset(request)
    fallback = _CONNECTORS.get((request.connect_type or "").strip().lower())
    if fallback:
        return await fallback.reset(request)
    return ResetAgentResult(ok=False, error=f"unsupported platform: {key}")


async def prepare_send_to_agent_stream(request: SendToAgentRequest) -> PreparedAgentStream:
    key = (request.platform or "").strip().lower()
    conn = _resolve(request)
    if conn is None:
        raise RuntimeError(f"streaming not supported for connect_type: {request.connect_type}")

    prepared = await conn.prepare_stream(request)

    if not history_options_disabled(request.options):
        try:
            store = await get_store()
            request_id = await store.record_send(
                platform=request.platform,
                session_key=request.session,
                connect_type=request.connect_type,
                prompt=request.prompt,
                options=request.options,
            )
            prepared.history_request_id = request_id
            prepared.history_options = dict(request.options or {})
        except Exception as e:
            logger.warning("history record_send (stream) failed: %s", e)

    return prepared
