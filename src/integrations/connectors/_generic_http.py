from __future__ import annotations

import os
from typing import Any

import httpx

from integrations.base import (
    PreparedAgentStream,
    ResetAgentRequest,
    ResetAgentResult,
    SendToAgentRequest,
    SendToAgentResult,
)
from integrations.connectors._base import AgentConnector


def _canonical_platform(platform: str) -> str:
    pl = (platform or "").strip().lower()
    if pl in ("claude-code", "claudecode"):
        return "claude"
    if pl in ("gemini-cli", "geminicli"):
        return "gemini"
    return pl


def _extract_http_content(data: Any) -> str | None:
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str):
                        return content
                delta = first.get("delta")
                if isinstance(delta, dict):
                    content = delta.get("content")
                    if isinstance(content, str):
                        return content
        for key in ("content", "text", "message", "reply"):
            value = data.get(key)
            if isinstance(value, str):
                return value
    return None


def _resolve_http_session_field(platform: str, options: dict[str, Any]) -> str | None:
    if "session_field" in options:
        return options.get("session_field")
    if platform == "openclaw":
        return None
    return "session_id"


def _resolve_http_session_header(platform: str, options: dict[str, Any]) -> str | None:
    if "session_header" in options:
        return options.get("session_header")
    if platform == "openclaw":
        return "x-openclaw-session-key"
    return None


def _build_http_messages(prompt: Any, options: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(prompt, list):
        return prompt

    messages: list[dict[str, Any]] = []
    system_prompt = str(options.get("system_prompt") or "").strip()
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({
        "role": "user",
        "content": prompt if isinstance(prompt, str) else str(prompt or ""),
    })
    return messages


async def _clear_http_agent_session_records(options: dict[str, Any], session_key: str) -> int:
    group_db_path = str(options.get("group_db_path") or "").strip()
    if not group_db_path or not session_key:
        return 0
    from api.group_repository import delete_http_agent_session_by_key
    return int(await delete_http_agent_session_by_key(group_db_path, session_key) or 0)


class GenericHttpConnector(AgentConnector):
    """Base class for all HTTP-backed connectors."""

    platform: str = "http"
    aliases: list[str] = []

    async def send(self, request: SendToAgentRequest) -> SendToAgentResult:
        options = request.options or {}
        platform = _canonical_platform(request.platform)

        api_url = str(options.get("api_url") or "").strip()
        if not api_url:
            return SendToAgentResult(ok=False, error="missing api_url")

        headers = {"Content-Type": "application/json"}
        headers.update(options.get("headers") or {})
        api_key = str(options.get("api_key") or "").strip()
        if api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {api_key}"

        session_header = str(_resolve_http_session_header(platform, options) or "").strip()
        if request.session and session_header and session_header not in headers:
            headers[session_header] = request.session

        body = dict(options.get("body") or {})
        if "messages" not in body:
            body["messages"] = _build_http_messages(request.prompt, options)
        if request.session:
            session_field = _resolve_http_session_field(platform, options)
            if session_field and session_field not in body:
                body[session_field] = request.session
        if "model" not in body and options.get("model") is not None:
            body["model"] = options.get("model")
        body.setdefault("stream", False)

        timeout_value = options.get("timeout")
        timeout = httpx.Timeout(timeout=timeout_value) if timeout_value is not None else httpx.Timeout(timeout=None)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(api_url, json=body, headers=headers)
            if resp.status_code != 200:
                return SendToAgentResult(
                    ok=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:300]}",
                    meta={
                        "connect_type": "http",
                        "platform": platform,
                        "session": request.session,
                    },
                )
            data = resp.json()
            return SendToAgentResult(
                ok=True,
                content=_extract_http_content(data),
                raw_response=data,
                meta={
                    "connect_type": "http",
                    "platform": platform,
                    "session": request.session,
                },
            )
        except Exception as e:
            return SendToAgentResult(
                ok=False,
                error=str(e),
                meta={
                    "connect_type": "http",
                    "platform": platform,
                    "session": request.session,
                },
            )

    async def reset(self, request: ResetAgentRequest) -> ResetAgentResult:
        options = request.options or {}
        platform = _canonical_platform(request.platform)
        session_key = str(request.session or "").strip()

        try:
            cleared_http_sessions = await _clear_http_agent_session_records(options, session_key)
            if cleared_http_sessions:
                return ResetAgentResult(
                    ok=True,
                    meta={
                        "connect_type": "http",
                        "platform": platform,
                        "session": session_key,
                        "cleared_http_sessions": cleared_http_sessions,
                    },
                )
            return ResetAgentResult(
                ok=False,
                error=f"reset not supported for http platform: {platform}",
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

    async def prepare_stream(self, request: SendToAgentRequest) -> PreparedAgentStream:
        options = request.options or {}
        platform = _canonical_platform(request.platform)

        api_url = str(options.get("api_url") or "").strip()
        if not api_url:
            raise RuntimeError("missing api_url")

        headers = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json"}
        headers.update(options.get("headers") or {})
        api_key = str(options.get("api_key") or "").strip()
        if api_key and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {api_key}"

        session_header = str(_resolve_http_session_header(platform, options) or "").strip()
        if request.session and session_header and session_header not in headers:
            headers[session_header] = request.session

        body = dict(options.get("body") or {})
        if "messages" not in body:
            body["messages"] = _build_http_messages(request.prompt, options)
        if request.session:
            session_field = _resolve_http_session_field(platform, options)
            if session_field and session_field not in body:
                body[session_field] = request.session
        if "model" not in body and options.get("model") is not None:
            body["model"] = options.get("model")
        body["stream"] = True

        timeout_value = options.get("timeout")
        timeout_sec: int | None = None
        if timeout_value not in (None, ""):
            try:
                timeout_sec = int(timeout_value)
            except (TypeError, ValueError):
                timeout_sec = None

        return PreparedAgentStream(
            connect_type="http",
            platform=platform,
            session=request.session,
            timeout_sec=timeout_sec,
            api_url=api_url,
            headers=headers,
            body=body,
        )
