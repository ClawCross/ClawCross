from __future__ import annotations

from integrations.acpx_adapter import AcpxError, get_acpx_adapter, normalize_acpx_run_options
from integrations.base import (
    PreparedAgentStream,
    ResetAgentRequest,
    ResetAgentResult,
    SendToAgentRequest,
    SendToAgentResult,
)
from integrations.connectors._base import AgentConnector
from utils.oasis_acp_log import mark as _acp_mark


def _canonical_platform(platform: str) -> str:
    pl = (platform or "").strip().lower()
    if pl in ("claude-code", "claudecode"):
        return "claude"
    if pl in ("gemini-cli", "geminicli"):
        return "gemini"
    return pl


async def _clear_http_agent_session_records(options: dict, session_key: str) -> int:
    from typing import Any
    group_db_path = str(options.get("group_db_path") or "").strip()
    if not group_db_path or not session_key:
        return 0
    from api.group_repository import delete_http_agent_session_by_key
    return int(await delete_http_agent_session_by_key(group_db_path, session_key) or 0)


class GenericAcpConnector(AgentConnector):
    """Base class for all ACP-backed connectors."""

    platform: str = "acp"
    aliases: list[str] = []

    async def send(self, request: SendToAgentRequest) -> SendToAgentResult:
        options = request.options or {}
        cwd = options.get("cwd")
        run_options = normalize_acpx_run_options(options, default_timeout_sec=None)
        prompt_text = request.prompt if isinstance(request.prompt, str) else str(request.prompt or "")
        attachments = options.get("attachments")
        _acp_mark(
            "connector.send.enter",
            platform=request.platform,
            cwd=cwd or "<inherit>",
            timeout_sec=run_options["timeout_sec"],
            ttl_sec=run_options["ttl_sec"],
            return_trace=bool(options.get("return_trace")),
            prompt_chars=len(prompt_text),
        )
        try:
            adapter = get_acpx_adapter(cwd=cwd)
            platform = _canonical_platform(request.platform)
            _acp_mark("connector.adapter_ready", platform=platform)
            if options.get("return_trace"):
                trace = await adapter.prompt_with_trace(
                    tool=platform,
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
                _acp_mark("connector.prompt_with_trace.done", chars=len(trace.text or ""))
                return SendToAgentResult(
                    ok=True,
                    content=trace.text or "",
                    raw_response={
                        "message_chunks": trace.message_chunks,
                        "messages": trace.messages,
                        "tool_uses": trace.tool_uses,
                        "tool_results": trace.tool_results,
                    },
                    meta={
                        "connect_type": "acp",
                        "platform": platform,
                        "session": request.session,
                    },
                )
            reply = await adapter.prompt(
                tool=platform,
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
            _acp_mark("connector.prompt.done", chars=len(reply or ""))
            return SendToAgentResult(
                ok=True,
                content=reply,
                raw_response=reply,
                meta={
                    "connect_type": "acp",
                    "platform": platform,
                    "session": request.session,
                },
            )
        except (AcpxError, RuntimeError) as e:
            _acp_mark("connector.error", error=str(e)[:160])
            return SendToAgentResult(
                ok=False,
                error=str(e),
                meta={
                    "connect_type": "acp",
                    "platform": _canonical_platform(request.platform),
                    "session": request.session,
                },
            )

    async def reset(self, request: ResetAgentRequest) -> ResetAgentResult:
        options = request.options or {}
        run_options = normalize_acpx_run_options(options, default_timeout_sec=None)
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

    async def prepare_stream(self, request: SendToAgentRequest) -> PreparedAgentStream:
        options = request.options or {}
        cwd = options.get("cwd")
        run_options = normalize_acpx_run_options(options, default_timeout_sec=None)
        prompt_text = request.prompt if isinstance(request.prompt, str) else str(request.prompt or "")
        attachments = options.get("attachments")
        platform = _canonical_platform(request.platform)
        adapter = get_acpx_adapter(cwd=cwd)
        acpx_session = adapter.to_acpx_session_name(tool=platform, session_key=request.session or "default")
        await adapter.ensure_session(
            tool=platform,
            session_key=request.session or "default",
            acpx_session=acpx_session,
            system_prompt=options.get("system_prompt"),
            ttl_sec=run_options["ttl_sec"],
            approve_all=run_options["approve_all"],
            non_interactive_permissions=run_options["non_interactive_permissions"],
        )
        cmd, temp_path = adapter.prepare_prompt_command(
            tool=platform,
            session_key=request.session or "default",
            acpx_session=acpx_session,
            prompt_text=prompt_text,
            attachments=attachments,
            ttl_sec=run_options["ttl_sec"],
            approve_all=run_options["approve_all"],
            non_interactive_permissions=run_options["non_interactive_permissions"],
        )
        return PreparedAgentStream(
            connect_type="acp",
            platform=platform,
            session=request.session,
            timeout_sec=run_options["timeout_sec"],
            cmd=cmd,
            temp_path=temp_path,
            adapter=adapter,
        )
