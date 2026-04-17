from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx
from langchain_core.messages import HumanMessage

from integrations.acpx_adapter import AcpxError, get_acpx_adapter
from services.llm_factory import create_chat_model, extract_text


@dataclass(slots=True)
class SendToAgentRequest:
    prompt: Any
    connect_type: str
    platform: str
    session: str | None = None
    options: dict[str, Any] | None = None


@dataclass(slots=True)
class SendToAgentResult:
    ok: bool
    content: str | None = None
    raw_response: Any | None = None
    error: str | None = None
    meta: dict[str, Any] | None = None


SenderFunc = Callable[[SendToAgentRequest], Awaitable[SendToAgentResult]]
_SENDERS: dict[str, SenderFunc] = {}


def register_sender(connect_type: str, sender: SenderFunc) -> None:
    _SENDERS[(connect_type or "").strip().lower()] = sender


async def send_to_agent(request: SendToAgentRequest) -> SendToAgentResult:
    connect_type = (request.connect_type or "").strip().lower()
    sender = _SENDERS.get(connect_type)
    if sender is None:
        return SendToAgentResult(
            ok=False,
            error=f"unsupported connect_type: {request.connect_type}",
        )
    return await sender(request)


async def _send_via_acp(request: SendToAgentRequest) -> SendToAgentResult:
    options = request.options or {}
    cwd = options.get("cwd")
    timeout_sec = int(options.get("timeout_sec") or 180)
    prompt_text = request.prompt if isinstance(request.prompt, str) else str(request.prompt or "")
    attachments = options.get("attachments")
    try:
        adapter = get_acpx_adapter(cwd=cwd)
        reply = await adapter.prompt(
            tool=_canonical_platform(request.platform),
            session_key=request.session or "default",
            prompt_text=prompt_text,
            timeout_sec=timeout_sec,
            reset_session=bool(options.get("reset_session")),
            system_prompt=options.get("system_prompt"),
            attachments=attachments,
        )
        return SendToAgentResult(
            ok=True,
            content=reply,
            raw_response=reply,
            meta={
                "connect_type": "acp",
                "platform": _canonical_platform(request.platform),
                "session": request.session,
            },
        )
    except (AcpxError, RuntimeError) as e:
        return SendToAgentResult(
            ok=False,
            error=str(e),
            meta={
                "connect_type": "acp",
                "platform": _canonical_platform(request.platform),
                "session": request.session,
            },
        )


async def _send_via_http(request: SendToAgentRequest) -> SendToAgentResult:
    options = request.options or {}
    platform = _canonical_platform(request.platform)
    if platform == "temp":
        return await _send_via_temp_http(request)

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


async def _send_via_temp_http(request: SendToAgentRequest) -> SendToAgentResult:
    options = request.options or {}
    prompt = request.prompt if isinstance(request.prompt, str) else str(request.prompt or "")
    try:
        llm = create_chat_model(
            temperature=float(options.get("temperature", 0.7)),
            max_tokens=int(options.get("max_tokens", 1024)),
            model=options.get("model"),
            api_key=options.get("api_key"),
            base_url=options.get("base_url"),
            provider=options.get("provider"),
        )
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        text = extract_text(resp.content)
        return SendToAgentResult(
            ok=True,
            content=text,
            raw_response=resp,
            meta={
                "connect_type": "http",
                "platform": "temp",
                "session": request.session,
            },
        )
    except Exception as e:
        return SendToAgentResult(
            ok=False,
            error=str(e),
            meta={
                "connect_type": "http",
                "platform": "temp",
                "session": request.session,
            },
        )


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


register_sender("acp", _send_via_acp)
register_sender("http", _send_via_http)
