from __future__ import annotations

import os

import httpx

from integrations.base import (
    ResetAgentRequest,
    ResetAgentResult,
    SendToAgentRequest,
    SendToAgentResult,
)
from integrations.connectors._generic_http import (
    GenericHttpConnector,
    _canonical_platform,
    _clear_http_agent_session_records,
)
from integrations.registry import register


class InternalConnector(GenericHttpConnector):
    """Connector for internal (same-host) HTTP agent."""

    platform = "internal"
    aliases: list[str] = []

    async def send(self, request: SendToAgentRequest) -> SendToAgentResult:
        options = dict(request.options or {})
        if not options.get("api_url"):
            port = os.getenv("PORT_AGENT", "51200")
            options["api_url"] = f"http://127.0.0.1:{port}/v1/chat/completions"
        updated_request = SendToAgentRequest(
            prompt=request.prompt,
            connect_type=request.connect_type,
            platform=request.platform,
            session=request.session,
            options=options,
        )
        return await super().send(updated_request)

    async def reset(self, request: ResetAgentRequest) -> ResetAgentResult:
        options = request.options or {}
        platform = _canonical_platform(request.platform)
        session_key = str(request.session or "").strip()

        try:
            if not session_key:
                return ResetAgentResult(ok=False, error="missing session")
            user_id = str(options.get("user_id") or "").strip()
            if not user_id:
                return ResetAgentResult(ok=False, error="missing user_id")
            delete_session_url = str(options.get("delete_session_url") or "").strip()
            if not delete_session_url:
                port = os.getenv("PORT_AGENT", "51200")
                delete_session_url = f"http://127.0.0.1:{port}/delete_session"

            headers = {"Content-Type": "application/json"}
            internal_token = str(options.get("internal_token") or "").strip()
            if internal_token:
                headers["X-Internal-Token"] = internal_token

            payload = {
                "user_id": user_id,
                "password": str(options.get("password") or ""),
                "session_id": session_key,
            }
            timeout_value = options.get("timeout")
            timeout = httpx.Timeout(timeout=timeout_value) if timeout_value is not None else httpx.Timeout(timeout=30)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(delete_session_url, json=payload, headers=headers)
            if resp.status_code != 200:
                return ResetAgentResult(
                    ok=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:300]}",
                    meta={
                        "connect_type": "http",
                        "platform": platform,
                        "session": session_key,
                    },
                )
            return ResetAgentResult(
                ok=True,
                meta={
                    "connect_type": "http",
                    "platform": platform,
                    "session": session_key,
                },
            )
        except Exception as e:
            return ResetAgentResult(
                ok=False,
                error=str(e),
                meta={
                    "connect_type": "http",
                    "platform": platform,
                    "session": session_key,
                },
            )


register(InternalConnector())
