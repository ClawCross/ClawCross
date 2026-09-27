"""One interface for talking to one agent, whatever runtime it lives in.

``ask`` sends a message and waits for the agent's reply. ``deliver`` drops a
message into the agent's inbox and returns at once: the agent answers, if at
all, through its own channels (for example a group chat's send tool). Control
actions (status, cancel, reset, …) go through the Agent service's
``/agent_control`` so every process shares one implementation.

The transports themselves are the existing connectors in ``integrations``;
this module decides, per driver, how a message and its options map onto them.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable

import httpx

from agents.messages import (
    ACPX_OVERRIDES_BY_MODE,
    VALID_RUN_MODES,
    AgentMessage,
    AgentReply,
    DeliveryReceipt,
    build_openai_content,
    compose_text_prompt,
    normalize_run_mode,
)
from agents.registry import (
    DRIVER_ACPX,
    DRIVER_HTTP,
    DRIVER_OPENCLAW,
    DRIVER_WEBOT,
    AgentRecord,
    AgentRegistry,
    get_registry,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
_DEFAULT_ACP_SESSION_SUFFIX = "clawcrosschat"

CAPABILITIES: dict[str, dict[str, Any]] = {
    DRIVER_WEBOT: {
        "ask": True, "deliver": True, "cancel": True, "reset": True,
        "attachments": True, "structured_output": True, "tool_selection": True,
        "modes": list(VALID_RUN_MODES),
    },
    DRIVER_ACPX: {
        "ask": True, "deliver": True, "cancel": True, "reset": True,
        "attachments": True, "structured_output": False, "tool_selection": False,
        "modes": list(ACPX_OVERRIDES_BY_MODE),
    },
    DRIVER_OPENCLAW: {
        "ask": True, "deliver": True, "cancel": True, "reset": True,
        "attachments": True, "structured_output": False, "tool_selection": False,
        "modes": [],
    },
    DRIVER_HTTP: {
        "ask": True, "deliver": True, "cancel": False, "reset": True,
        "attachments": True, "structured_output": False, "tool_selection": False,
        "modes": [],
    },
}


def agent_card(record: AgentRecord) -> dict[str, Any]:
    """What a caller may know about an agent: who it is and what it can do."""
    return {
        "agent_id": record.agent_id,
        "address": record.address,
        "owner": record.owner,
        "handle": record.handle,
        "display_name": record.display_name,
        "driver": record.driver,
        "platform": record.binding.get("platform") or record.driver,
        "persona_tag": record.persona_tag,
        "teams": [t for t in record.teams if t],
        "default_context": dict(record.default_context),
        "status": record.status,
        "capabilities": dict(CAPABILITIES.get(record.driver, {})),
    }


def _session_suffix(model: str) -> str:
    """``agent:<name>:<suffix>`` names an external session; the default is shared with group chat."""
    parts = (model or "").strip().split(":")
    if len(parts) >= 3 and parts[0] == "agent" and parts[2].strip():
        return parts[2].strip()
    return _DEFAULT_ACP_SESSION_SUFFIX


def _external_session_key(record: AgentRecord) -> str:
    global_name = str(record.binding.get("global_name") or "").strip()
    return f"agent:{global_name}:{_session_suffix(str(record.binding.get('model') or ''))}"


class AgentGateway:
    def __init__(
        self,
        registry: AgentRegistry | None = None,
        *,
        agent_base_url: str | None = None,
        internal_token: str | None = None,
    ):
        self.registry = registry or get_registry()
        self.agent_base_url = agent_base_url or f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}"
        self.internal_token = os.getenv("INTERNAL_TOKEN", "") if internal_token is None else internal_token
        self._background: set[asyncio.Task] = set()
        self._external_system_prompt: str | None = None

    # ── directory ────────────────────────────────────────────────────────

    def resolve(self, owner: str, ref: str, *, team: str | None = None) -> AgentRecord:
        return self.registry.resolve(owner, ref, team=team)

    def describe(self, owner: str, ref: str) -> dict[str, Any]:
        return agent_card(self.resolve(owner, ref))

    def list(self, owner: str) -> list[dict[str, Any]]:
        return [agent_card(record) for record in self.registry.list(owner)]

    # ── messaging ────────────────────────────────────────────────────────

    async def ask(
        self,
        owner: str,
        ref: str | AgentRecord,
        msg: AgentMessage,
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        tools: list[str] | None = None,
        response_format: dict | None = None,
        timeout: float | None = None,
    ) -> AgentReply:
        """Send *msg* and wait for the agent's reply."""
        record = ref if isinstance(ref, AgentRecord) else self.resolve(owner, ref)
        context = {**record.default_context, **(context or {})}
        mode = normalize_run_mode(mode)
        try:
            if record.driver == DRIVER_WEBOT:
                return await self._ask_webot(record, msg, context, mode, tools, response_format, timeout)
            if record.driver == DRIVER_ACPX:
                return await self._ask_acpx(record, msg, context, mode, timeout)
            if record.driver in (DRIVER_OPENCLAW, DRIVER_HTTP):
                return await self._ask_http(record, msg, context, timeout)
        except Exception as exc:
            logger.exception("ask %s failed", record.address)
            return AgentReply(ok=False, error=f"{type(exc).__name__}: {exc}")
        return AgentReply(ok=False, error=f"unsupported driver: {record.driver}")

    async def deliver(
        self,
        owner: str,
        ref: str | AgentRecord,
        msg: AgentMessage,
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        coalesce_key: str | None = None,
        on_complete: Callable[[AgentReply], Any] | None = None,
    ) -> DeliveryReceipt:
        """Put *msg* in the agent's inbox without waiting for an answer.

        External runtimes have no inbox: the message is sent in the background
        and its direct reply dropped (the agent speaks through its own channels).
        *on_complete* is called with that reply, or a failed one, when the send
        ends; WeBot delivery is queued in the agent's session and never calls it.
        """
        record = ref if isinstance(ref, AgentRecord) else self.resolve(owner, ref)
        if record.driver == DRIVER_WEBOT:
            return await self._deliver_webot(record, msg, normalize_run_mode(mode), coalesce_key)

        async def send() -> None:
            reply = AgentReply(ok=False, error="delivery did not complete")
            try:
                reply = await self.ask(owner, record, msg, context=context, mode=mode)
                if not reply.ok:
                    logger.warning("deliver to %s failed: %s", record.address, reply.error)
            finally:
                if on_complete is not None:
                    try:
                        result = on_complete(reply)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception("on_complete for %s failed", record.address)

        task = asyncio.create_task(send())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return DeliveryReceipt(accepted=True)

    async def control(self, owner: str, ref: str | AgentRecord, action: str) -> dict[str, Any]:
        """status / cancel / stop / reset / new / delete, via the Agent service."""
        record = ref if isinstance(ref, AgentRecord) else self.resolve(owner, ref)
        if record.driver == DRIVER_WEBOT:
            kind, identity = "internal", str(record.binding.get("session") or "")
        else:
            kind, identity = "external", str(record.binding.get("global_name") or "")
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{self.agent_base_url}/agent_control",
                headers={"X-Internal-Token": self.internal_token},
                json={"user_id": owner, "action": action, "kind": kind, "identity": identity},
            )
        try:
            payload = response.json()
        except ValueError:
            payload = {"error": response.text[:500]}
        payload.setdefault("status_code", response.status_code)
        return payload

    # ── WeBot ────────────────────────────────────────────────────────────

    @staticmethod
    def _webot_mode_fields(mode: str | None, tools: list[str] | None) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if tools is not None:
            fields["enabled_tools"] = list(tools)
        if mode:
            fields["session_mode"] = mode
            if mode == "chat":
                # Chat means no tool calls; an empty list is the explicit signal.
                fields["enabled_tools"] = []
        return fields

    async def _ask_webot(self, record, msg, context, mode, tools, response_format, timeout) -> AgentReply:
        from integrations.agent_sender import SendToAgentRequest, send_to_agent

        session = str(record.binding.get("session") or "")
        messages: list[dict] = []
        if msg.instructions:
            messages.append({"role": "system", "content": msg.instructions})
        messages.append({"role": "user", "content": build_openai_content(msg.text, msg.attachments)})
        body: dict[str, Any] = {"model": "webot", "messages": messages, "stream": False}
        body.update(self._webot_mode_fields(mode, tools))
        if response_format:
            body["response_format"] = response_format
        if record.settings.get("llm_override"):
            body["llm_override"] = record.settings["llm_override"]
        result = await send_to_agent(SendToAgentRequest(
            prompt=messages,
            connect_type="http",
            platform="internal",
            session=session,
            options={
                "api_url": f"{self.agent_base_url}/v1/chat/completions",
                "headers": {"Authorization": f"Bearer {self.internal_token}:{record.owner}"},
                "body": body,
                "timeout": timeout if timeout is not None else 500,
            },
        ))
        return AgentReply(ok=result.ok, content=result.content or "", error=result.error or "", meta=result.meta or {})

    async def _deliver_webot(self, record, msg, mode, coalesce_key) -> DeliveryReceipt:
        text = f"{msg.text}\n\n{msg.instructions}" if msg.instructions else msg.text
        body: dict[str, Any] = {
            "user_id": record.owner,
            "session_id": str(record.binding.get("session") or ""),
            "text": text,
        }
        if coalesce_key:
            body["coalesce_key"] = coalesce_key
        if msg.attachments:
            body["attachments"] = list(msg.attachments)
        body.update(self._webot_mode_fields(mode, None))
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.agent_base_url}/system_trigger",
                    headers={"X-Internal-Token": self.internal_token},
                    json=body,
                )
        except httpx.HTTPError as exc:
            return DeliveryReceipt(accepted=False, error=str(exc))
        if response.status_code >= 400:
            return DeliveryReceipt(accepted=False, error=f"HTTP {response.status_code}: {response.text[:300]}")
        return DeliveryReceipt(accepted=True)

    # ── external agents ──────────────────────────────────────────────────

    def _identity_prompt(self, record: AgentRecord, context: dict[str, Any], instructions: str) -> str:
        from integrations.acpx_adapter import load_external_agent_system_prompt
        from integrations.external_persona import build_external_persona_prompt

        if self._external_system_prompt is None:
            self._external_system_prompt = load_external_agent_system_prompt(_PROJECT_ROOT)
        parts = [
            self._external_system_prompt,
            build_external_persona_prompt(
                record.persona_tag,
                user_id=record.owner,
                team=str(context.get("team") or ""),
            ),
            instructions,
        ]
        return "\n\n".join(p for p in parts if p).strip()

    async def _ask_acpx(self, record, msg, context, mode, timeout) -> AgentReply:
        from integrations.acpx_adapter import acpx_options_from_agent
        from integrations.agent_sender import SendToAgentRequest, send_to_agent
        from utils.external_agent_history import attach_history_context
        from utils.runtime_paths import WORKSPACE_DIR

        global_name = str(record.binding.get("global_name") or "")
        platform = str(record.binding.get("platform") or "")
        options: dict[str, Any] = {
            "cwd": str(WORKSPACE_DIR / "acpx"),
            **acpx_options_from_agent(
                record.binding,
                overrides=ACPX_OVERRIDES_BY_MODE.get(mode) if mode else None,
                default_timeout_sec=int(timeout) if timeout else 180,
            ),
            "reset_session": False,
            "identity_prompt": self._identity_prompt(record, context, msg.instructions),
            "attachments": [dict(a) for a in msg.attachments] or None,
            "return_trace": True,
        }
        options = attach_history_context(
            options,
            user_id=record.owner,
            group_id=str(context.get("conversation_id") or ""),
            global_name=global_name,
        )
        result = await send_to_agent(SendToAgentRequest(
            prompt=compose_text_prompt(msg.text, msg.attachments),
            connect_type="acp",
            platform=platform,
            session=_external_session_key(record),
            options=options,
        ))
        return AgentReply(ok=result.ok, content=result.content or "", error=result.error or "", meta=result.meta or {})

    async def _ask_http(self, record, msg, context, timeout) -> AgentReply:
        from integrations.agent_sender import SendToAgentRequest, send_to_agent
        from utils.external_agent_history import attach_history_context

        binding = record.binding
        platform = str(binding.get("platform") or "")
        global_name = str(binding.get("global_name") or "").strip()
        api_url = str(binding.get("api_url") or "")
        api_key = str(binding.get("api_key") or "")
        model = str(binding.get("model") or "") or "gpt-3.5-turbo"
        if record.driver == DRIVER_OPENCLAW:
            # The OpenClaw endpoint depends on the device: runtime env beats saved config.
            api_url = os.getenv("OPENCLAW_API_URL", "") or api_url
            api_key = os.getenv("OPENCLAW_GATEWAY_TOKEN", "") or api_key
            if global_name and not model.startswith("agent:"):
                model = f"agent:{global_name}"
        if not api_url:
            return AgentReply(ok=False, error=f"{record.address} has no api_url")
        api_url = api_url.rstrip("/")
        if not api_url.endswith("/v1/chat/completions"):
            if not api_url.endswith("/v1"):
                api_url += "/v1"
            api_url += "/chat/completions"

        session_key = _external_session_key(record) if global_name else ""
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if record.driver == DRIVER_OPENCLAW and session_key:
            headers["x-openclaw-session-key"] = session_key
        messages = [{"role": "user", "content": build_openai_content(msg.text, msg.attachments)}]
        options: dict[str, Any] = {
            "api_url": api_url,
            "api_key": api_key,
            "headers": headers,
            "body": {"model": model, "messages": messages, "stream": False},
            "timeout": timeout if timeout is not None else 60,
            "identity_prompt": self._identity_prompt(record, context, msg.instructions),
            "identity_global_name": global_name,
            "group_db_path": self.registry.db_path,
            "identity_injection_mode": "prepend_user",
        }
        options = attach_history_context(
            options,
            user_id=record.owner,
            group_id=str(context.get("conversation_id") or ""),
            global_name=global_name,
        )
        result = await send_to_agent(SendToAgentRequest(
            prompt=messages,
            connect_type="http",
            platform=platform or record.driver,
            session=session_key or None,
            options=options,
        ))
        return AgentReply(ok=result.ok, content=result.content or "", error=result.error or "", meta=result.meta or {})


_GATEWAY: AgentGateway | None = None


def get_gateway() -> AgentGateway:
    """The process-wide gateway over the default registry."""
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = AgentGateway()
    return _GATEWAY
