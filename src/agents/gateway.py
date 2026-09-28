"""Talking to one agent, whatever runtime it lives in.

``ask`` sends a message and waits for the reply. ``deliver`` drops a message in
the agent's inbox and returns at once; the agent answers, if at all, through
the conversation it was told about. The transports are the connectors in
``integrations``; this module maps a message onto them per driver.

Temporary participants are agents too, just not stored: ``persona_agent`` is a
single model call with a persona, ``temp_session_agent`` a throwaway WeBot
session with tools, deleted with ``discard``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from pydantic import BaseModel

from agents.messages import (
    ACPX_OVERRIDES_BY_MODE,
    AgentMessage,
    AgentReply,
    DeliveryReceipt,
    build_openai_content,
    compose_text_prompt,
    normalize_run_mode,
)
from agents.store import ACPX, HTTP, LLM, OPENCLAW, TEMP_SESSION_PREFIX, WEBOT, Agent, AgentStore, get_store

logger = logging.getLogger(__name__)

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
_SESSION_SUFFIX = "clawcrosschat"

# Pass as ``timeout`` to wait for as long as the agent takes (long execution tasks).
NO_TIMEOUT = float("inf")


def persona_agent(owner: str, name: str, *, persona: str = "", team: str = "", llm: dict | None = None) -> Agent:
    """A persona for one call: no tools, no memory. *llm*: model, api_key, base_url, provider, temperature, max_tokens."""
    return Agent(agent_id="", owner=owner, handle=name, name=name, driver=LLM,
                 config={"persona": persona, "team": team, "llm": dict(llm or {})})


def temp_session_agent(owner: str, name: str, session: str, *, persona: str = "", team: str = "",
                       llm: dict | None = None) -> Agent:
    """A throwaway WeBot session (``tmp__…``) for a persona that needs tools; *llm* overrides its model."""
    if not session.startswith(TEMP_SESSION_PREFIX) or len(session) <= len(TEMP_SESSION_PREFIX):
        raise ValueError(f"not a temporary session: {session!r}")
    config = {"session": session, "persona": persona, "team": team}
    if llm:
        config["llm"] = dict(llm)
    return Agent(agent_id="", owner=owner, handle=name, name=name, driver=WEBOT, config=config)


def _reply_format_for(agent: Agent, response_format: Any) -> Any:
    """The requested reply shape as this runtime takes it: WeBot a ``json_schema``
    ``response_format``, a model call the Pydantic model itself, external runtimes nothing."""
    if response_format is None:
        return None
    is_model = isinstance(response_format, type) and issubclass(response_format, BaseModel)
    if agent.driver == LLM:
        return response_format
    if agent.driver != WEBOT:
        return None
    if not is_model:
        return response_format
    from core.tool_schema import to_strict_parameters

    return {"type": "json_schema", "json_schema": {
        "name": response_format.__name__,
        "schema": to_strict_parameters(response_format.model_json_schema()),
        "strict": True,
    }}


def external_session_key(agent: Agent) -> str:
    """``agent:<global_name>:<suffix>``: the one conversation ClawCross keeps with an external agent."""
    parts = str(agent.config.get("model") or "").split(":")
    suffix = parts[2].strip() if len(parts) >= 3 and parts[0] == "agent" and parts[2].strip() else _SESSION_SUFFIX
    return f"agent:{agent.config.get('global_name', '')}:{suffix}"


def reply_channel(agent: Agent, conversation_id: str) -> str:
    """How this agent posts into a ClawCross conversation: a tool for WeBot, the CLI otherwise."""
    import shlex

    if agent.driver == WEBOT:
        return (f'send_to_group(group_id="{conversation_id}", content="你的回复")'
                "（username 与 source_session 自动注入，不要手动填写）")
    return (f"cd {shlex.quote(_PROJECT_ROOT)} && uv run scripts/cli.py -u {shlex.quote(agent.owner)} "
            f"groups send --group-id {shlex.quote(conversation_id)} --agent {shlex.quote(agent.address)} "
            "--message '你的回复'")


class AgentGateway:
    def __init__(self, *, agent_base_url: str | None = None, internal_token: str | None = None,
                 store: AgentStore | None = None):
        self.agent_base_url = agent_base_url or f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}"
        self.internal_token = os.getenv("INTERNAL_TOKEN", "") if internal_token is None else internal_token
        self._store = store
        self._background: set[asyncio.Task] = set()
        self._external_system_prompt: str | None = None

    async def ask(
        self,
        agent: Agent,
        msg: AgentMessage,
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        tools: list[str] | None = None,
        response_format: dict | Any | None = None,
        timeout: float | None = None,
    ) -> AgentReply:
        """Send *msg* and wait for the reply. ``timeout`` in seconds; ``NO_TIMEOUT`` waits indefinitely.

        ``response_format`` is an OpenAI ``response_format`` dict or a Pydantic model;
        each runtime gets it in the form it can enforce, or not at all.
        """
        context = {"team": agent.config.get("team", ""), **(context or {})}
        mode = normalize_run_mode(mode)
        response_format = _reply_format_for(agent, response_format)
        try:
            if agent.driver == WEBOT:
                return await self._ask_webot(agent, msg, mode, tools, response_format, timeout)
            if agent.driver == ACPX:
                return await self._ask_acpx(agent, msg, context, mode, timeout)
            if agent.driver in (OPENCLAW, HTTP):
                return await self._ask_http(agent, msg, context, timeout)
            if agent.driver == LLM:
                return await self._ask_llm(agent, msg, response_format)
        except Exception as exc:
            logger.exception("ask %s failed", agent.address)
            return AgentReply(ok=False, error=f"{type(exc).__name__}: {exc}")
        return AgentReply(ok=False, error=f"unsupported driver: {agent.driver}")

    async def deliver(
        self,
        agent: Agent,
        msg: AgentMessage,
        *,
        context: dict[str, Any] | None = None,
        mode: str | None = None,
        coalesce_key: str | None = None,
        on_complete: Callable[[AgentReply], Any] | None = None,
    ) -> DeliveryReceipt:
        """Put *msg* in the agent's inbox without waiting for an answer.

        WeBot queues it in the agent's session. Other runtimes have no inbox: the
        message is sent in the background, its direct reply handed to
        *on_complete* (the agent speaks through the conversation's own channel).
        """
        if agent.driver == WEBOT:
            return await self._deliver_webot(agent, msg, normalize_run_mode(mode), coalesce_key)

        async def send() -> None:
            reply = AgentReply(ok=False, error="delivery did not complete")
            try:
                reply = await self.ask(agent, msg, context=context, mode=mode)
                if not reply.ok:
                    logger.warning("deliver to %s failed: %s", agent.address, reply.error)
            finally:
                if on_complete is not None:
                    try:
                        result = on_complete(reply)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        logger.exception("on_complete for %s failed", agent.address)

        task = asyncio.create_task(send())
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return DeliveryReceipt(accepted=True)

    async def discard(self, agent: Agent) -> bool:
        """Delete a temporary session agent and its history."""
        session = str(agent.config.get("session") or "")
        if not agent.temporary or not session.startswith(TEMP_SESSION_PREFIX):
            raise ValueError(f"{agent.name} is not temporary")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.agent_base_url}/delete_session",
                    headers={"X-Internal-Token": self.internal_token},
                    json={"user_id": agent.owner, "session_id": session},
                )
        except httpx.HTTPError as exc:
            logger.warning("discarding %s#%s failed: %s", agent.owner, session, exc)
            return False
        return response.status_code < 400

    # ── WeBot ────────────────────────────────────────────────────────────

    @staticmethod
    def _webot_fields(mode: str | None, tools: list[str] | None) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if tools is not None:
            fields["enabled_tools"] = list(tools)
        if mode:
            fields["session_mode"] = mode
            if mode == "chat":
                fields["enabled_tools"] = []  # chat: no tool calls at all
        return fields

    async def _ask_webot(self, agent, msg, mode, tools, response_format, timeout) -> AgentReply:
        from integrations.agent_sender import SendToAgentRequest, send_to_agent

        messages: list[dict] = []
        if msg.instructions:
            messages.append({"role": "system", "content": msg.instructions})
        messages.append({"role": "user", "content": build_openai_content(msg.text, msg.attachments)})
        body: dict[str, Any] = {"model": "webot", "messages": messages, "stream": False}
        body.update(self._webot_fields(mode, tools))
        if response_format:
            body["response_format"] = response_format
        if agent.config.get("llm"):
            body["llm_override"] = agent.config["llm"]
        result = await send_to_agent(SendToAgentRequest(
            prompt=messages,
            connect_type="http",
            platform="internal",
            session=str(agent.config.get("session") or ""),
            options={
                "api_url": f"{self.agent_base_url}/v1/chat/completions",
                "headers": {"Authorization": f"Bearer {self.internal_token}:{agent.owner}"},
                "body": body,
                "timeout": None if timeout == NO_TIMEOUT else (timeout if timeout is not None else 500),
                "_history_disabled": True,  # WeBot keeps its own history
            },
        ))
        return AgentReply(ok=result.ok, content=result.content or "", error=result.error or "", meta=result.meta or {})

    async def _deliver_webot(self, agent, msg, mode, coalesce_key) -> DeliveryReceipt:
        body: dict[str, Any] = {
            "user_id": agent.owner,
            "session_id": str(agent.config.get("session") or ""),
            "text": f"{msg.text}\n\n{msg.instructions}" if msg.instructions else msg.text,
        }
        if coalesce_key:
            body["coalesce_key"] = coalesce_key
        if msg.attachments:
            body["attachments"] = list(msg.attachments)
        body.update(self._webot_fields(mode, None))
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

    # ── one model call ───────────────────────────────────────────────────

    async def _ask_llm(self, agent, msg, response_format) -> AgentReply:
        from integrations.agent_sender import SendToAgentRequest, send_to_agent

        options = {**(agent.config.get("llm") or {}), "_history_disabled": True}  # nothing to look back on
        if response_format is not None:
            options["response_schema"] = response_format  # a Pydantic model or JSON schema
        prompt = f"{msg.instructions}\n\n{msg.text}" if msg.instructions else msg.text
        result = await send_to_agent(SendToAgentRequest(
            prompt=prompt, connect_type="http", platform="temp", session=agent.name, options=options,
        ))
        return AgentReply(ok=result.ok, content=result.content or "", error=result.error or "", meta=result.meta or {})

    # ── external runtimes ────────────────────────────────────────────────

    def _identity_prompt(self, agent: Agent, context: dict[str, Any], instructions: str) -> str:
        from integrations.acpx_adapter import load_external_agent_prompt_file, load_external_agent_system_prompt
        from integrations.external_persona import build_external_persona_prompt

        if self._external_system_prompt is None:
            # The same chat rules WeBot carries in its system prompt.
            self._external_system_prompt = "\n\n".join(p for p in (
                load_external_agent_system_prompt(_PROJECT_ROOT),
                load_external_agent_prompt_file(_PROJECT_ROOT, "conversation_rules.txt"),
            ) if p)
        parts = [
            self._external_system_prompt,
            build_external_persona_prompt(
                str(agent.config.get("persona") or ""), user_id=agent.owner, team=str(context.get("team") or ""),
            ),
            instructions,
        ]
        return "\n\n".join(p for p in parts if p).strip()

    def _remember(self, agent: Agent, **runtime: Any) -> None:
        """Record on the agent what its runtime now knows; temporary agents keep nothing."""
        if agent.temporary:
            return
        try:
            (self._store or get_store()).set_runtime(agent.agent_id, {**agent.runtime, **runtime, "last_used_at": time.time()})
        except Exception:
            logger.exception("could not record the runtime state of %s", agent.address)

    async def _ask_acpx(self, agent, msg, context, mode, timeout) -> AgentReply:
        from integrations.acpx_adapter import acpx_options_from_agent
        from integrations.agent_sender import SendToAgentRequest, send_to_agent
        from utils.external_agent_history import attach_history_context
        from utils.runtime_paths import WORKSPACE_DIR

        options: dict[str, Any] = {
            "cwd": str(WORKSPACE_DIR / "acpx"),
            **acpx_options_from_agent(
                agent.config,
                overrides=ACPX_OVERRIDES_BY_MODE.get(mode) if mode else None,
                default_timeout_sec=int(timeout) if timeout and timeout != NO_TIMEOUT else 180,
            ),
            "reset_session": False,
            "identity_prompt": self._identity_prompt(agent, context, msg.instructions),
            "attachments": [dict(a) for a in msg.attachments] or None,
            "return_trace": True,
        }
        if timeout == NO_TIMEOUT:
            options["timeout_sec"] = None
        options = attach_history_context(
            options, user_id=agent.owner, group_id=str(context.get("conversation_id") or ""),
            global_name=str(agent.config.get("global_name") or ""),
        )
        result = await send_to_agent(SendToAgentRequest(
            prompt=compose_text_prompt(msg.text, msg.attachments),
            connect_type="acp",
            platform=agent.platform,
            session=external_session_key(agent),
            options=options,
        ))
        if result.ok:
            self._remember(agent)  # acpx itself sends the identity to a new session
        return AgentReply(ok=result.ok, content=result.content or "", error=result.error or "", meta=result.meta or {})

    async def _ask_http(self, agent, msg, context, timeout) -> AgentReply:
        from integrations.agent_sender import SendToAgentRequest, send_to_agent
        from utils.external_agent_history import attach_history_context

        config = agent.config
        global_name = str(config.get("global_name") or "").strip()
        api_url = str(config.get("api_url") or "")
        api_key = str(config.get("api_key") or "")
        model = str(config.get("model") or "") or "gpt-3.5-turbo"
        if agent.driver == OPENCLAW:
            # The OpenClaw endpoint depends on the device: runtime env beats saved config.
            api_url = os.getenv("OPENCLAW_API_URL", "") or api_url
            api_key = os.getenv("OPENCLAW_GATEWAY_TOKEN", "") or api_key
            if global_name and not model.startswith("agent:"):
                model = f"agent:{global_name}"
        if not api_url:
            return AgentReply(ok=False, error=f"{agent.address} has no api_url")
        api_url = api_url.rstrip("/")
        if not api_url.endswith("/v1/chat/completions"):
            api_url = (api_url if api_url.endswith("/v1") else api_url + "/v1") + "/chat/completions"

        session_key = external_session_key(agent) if global_name else ""
        headers = {"Content-Type": "application/json", **dict(config.get("headers") or {})}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if agent.driver == OPENCLAW and session_key:
            headers["x-openclaw-session-key"] = session_key
        messages = [{"role": "user", "content": build_openai_content(msg.text, msg.attachments)}]
        identity = self._identity_prompt(agent, context, msg.instructions)
        # A runtime that keeps the conversation is told who it is once, and again when that changes.
        inject = bool(identity) and (not session_key or identity != agent.runtime.get("identity_prompt"))
        options: dict[str, Any] = {
            "api_url": api_url,
            "api_key": api_key,
            "headers": headers,
            "body": {"model": model, "messages": messages, "stream": False},
            "timeout": None if timeout == NO_TIMEOUT else (timeout if timeout is not None else 60),
            "identity_prompt": identity,
            "inject_identity": inject,
            "identity_injection_mode": "prepend_user",
        }
        options = attach_history_context(
            options, user_id=agent.owner, group_id=str(context.get("conversation_id") or ""), global_name=global_name,
        )
        result = await send_to_agent(SendToAgentRequest(
            prompt=messages,
            connect_type="http",
            platform=agent.platform,
            session=session_key or None,
            options=options,
        ))
        if result.ok and session_key:
            self._remember(agent, identity_prompt=identity)
        return AgentReply(ok=result.ok, content=result.content or "", error=result.error or "", meta=result.meta or {})


_GATEWAY: AgentGateway | None = None


def get_gateway() -> AgentGateway:
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = AgentGateway()
    return _GATEWAY
