from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Awaitable, Callable

import httpx
from langchain_core.messages import HumanMessage

from integrations.acpx_adapter import AcpxError, get_acpx_adapter, normalize_acpx_run_options
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

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


@dataclass(slots=True)
class ResetAgentRequest:
    connect_type: str
    platform: str
    session: str | None = None
    options: dict[str, Any] | None = None


@dataclass(slots=True)
class ResetAgentResult:
    ok: bool
    error: str | None = None
    meta: dict[str, Any] | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


SenderFunc = Callable[[SendToAgentRequest], Awaitable[SendToAgentResult]]
_SENDERS: dict[str, SenderFunc] = {}
ResetterFunc = Callable[[ResetAgentRequest], Awaitable[ResetAgentResult]]
_RESETTERS: dict[str, ResetterFunc] = {}


def register_sender(connect_type: str, sender: SenderFunc) -> None:
    _SENDERS[(connect_type or "").strip().lower()] = sender


def register_resetter(connect_type: str, resetter: ResetterFunc) -> None:
    _RESETTERS[(connect_type or "").strip().lower()] = resetter


async def send_to_agent(request: SendToAgentRequest) -> SendToAgentResult:
    connect_type = (request.connect_type or "").strip().lower()
    sender = _SENDERS.get(connect_type)
    if sender is None:
        return SendToAgentResult(
            ok=False,
            error=f"unsupported connect_type: {request.connect_type}",
        )
    return await sender(request)


async def reset_agent(request: ResetAgentRequest) -> ResetAgentResult:
    connect_type = (request.connect_type or "").strip().lower()
    resetter = _RESETTERS.get(connect_type)
    if resetter is None:
        return ResetAgentResult(
            ok=False,
            error=f"unsupported connect_type: {request.connect_type}",
        )
    return await resetter(request)


async def _send_via_acp(request: SendToAgentRequest) -> SendToAgentResult:
    options = request.options or {}
    cwd = options.get("cwd")
    run_options = normalize_acpx_run_options(options, default_timeout_sec=180)
    prompt_text = request.prompt if isinstance(request.prompt, str) else str(request.prompt or "")
    attachments = options.get("attachments")
    try:
        adapter = get_acpx_adapter(cwd=cwd)
        reply = await adapter.prompt(
            tool=_canonical_platform(request.platform),
            session_key=request.session or "default",
            prompt_text=prompt_text,
            timeout_sec=run_options["timeout_sec"],
            reset_session=bool(options.get("reset_session")),
            system_prompt=options.get("system_prompt"),
            attachments=attachments,
            ttl_sec=run_options["ttl_sec"],
            approve_all=run_options["approve_all"],
            non_interactive_permissions=run_options["non_interactive_permissions"],
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


async def _reset_via_acp(request: ResetAgentRequest) -> ResetAgentResult:
    options = request.options or {}
    run_options = normalize_acpx_run_options(options, default_timeout_sec=180)
    session_key = str(request.session or "").strip()
    if not session_key:
        return ResetAgentResult(ok=False, error="missing session")

    platform = _canonical_platform(request.platform)
    try:
        adapter = get_acpx_adapter(cwd=options.get("cwd"))
        if platform == "openclaw":
            await adapter.ops_openclaw_exec_slash(
                session_key=session_key,
                slash="/new",
                timeout_sec=run_options["timeout_sec"],
                ttl_sec=run_options["ttl_sec"],
                approve_all=run_options["approve_all"],
                non_interactive_permissions=run_options["non_interactive_permissions"],
            )
        else:
            await adapter.ops_non_openclaw_reset_session(
                tool=platform,
                session_key=session_key,
                timeout_sec=run_options["timeout_sec"],
                ttl_sec=run_options["ttl_sec"],
                approve_all=run_options["approve_all"],
                non_interactive_permissions=run_options["non_interactive_permissions"],
            )
        cleared_http_sessions = await _clear_http_agent_session_records(options, session_key)
        return ResetAgentResult(
            ok=True,
            meta={
                "connect_type": "acp",
                "platform": platform,
                "session": session_key,
                "cleared_http_sessions": cleared_http_sessions,
            },
        )
    except (AcpxError, RuntimeError, ValueError) as e:
        return ResetAgentResult(
            ok=False,
            error=str(e),
            meta={
                "connect_type": "acp",
                "platform": platform,
                "session": session_key,
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


async def _reset_via_http(request: ResetAgentRequest) -> ResetAgentResult:
    options = request.options or {}
    platform = _canonical_platform(request.platform)
    session_key = str(request.session or "").strip()

    try:
        if platform == "internal":
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

        if platform == "openclaw":
            return await _reset_via_acp(
                ResetAgentRequest(
                    connect_type="acp",
                    platform=platform,
                    session=session_key,
                    options=options,
                )
            )

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


async def _clear_http_agent_session_records(options: dict[str, Any], session_key: str) -> int:
    group_db_path = str(options.get("group_db_path") or "").strip()
    if not group_db_path or not session_key:
        return 0
    from api.group_repository import delete_http_agent_session_by_key

    return int(await delete_http_agent_session_by_key(group_db_path, session_key) or 0)


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
register_resetter("acp", _reset_via_acp)
register_resetter("http", _reset_via_http)
