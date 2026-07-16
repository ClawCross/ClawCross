"""Shared Agent session inspection and identity-injection policy.

Callers describe identity; they do not decide whether this is the first send.
The transport-specific session registry remains the source of truth.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import time
from typing import Any

from integrations.base import SendToAgentRequest


@dataclass(frozen=True, slots=True)
class AgentSessionState:
    initialized: bool | None
    should_inject_identity: bool | None
    source: str
    session: str
    prompt_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "initialized": self.initialized,
            "should_inject_identity": self.should_inject_identity,
            "source": self.source,
            "session": self.session,
            "prompt_changed": self.prompt_changed,
        }


def _prepend_identity_to_messages(
    messages: list[Any],
    identity_prompt: str,
    *,
    mode: str,
) -> list[Any]:
    result = deepcopy(messages)
    if mode != "prepend_user":
        return [{"role": "system", "content": identity_prompt}, *result]

    for message in result:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = f"{identity_prompt}\n\n{content}".strip()
            return result
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    part["text"] = f"{identity_prompt}\n\n{part.get('text', '')}".strip()
                    return result
            content.insert(0, {"type": "text", "text": identity_prompt})
            return result
    result.insert(0, {"role": "user", "content": identity_prompt})
    return result


async def inspect_http_agent_session(
    *,
    group_db_path: str,
    session_key: str,
    identity_prompt: str = "",
) -> AgentSessionState:
    """Inspect the exact HTTP session used by sending and persona injection."""
    if not group_db_path or not session_key:
        return AgentSessionState(None, bool(identity_prompt), "no_session_registry", session_key)

    from api.group_repository import get_http_agent_session

    record = await get_http_agent_session(group_db_path, session_key)
    if record is None:
        return AgentSessionState(False, bool(identity_prompt), "http_session_registry", session_key)
    prompt_changed = bool(identity_prompt) and str(record.get("prompt_text") or "") != identity_prompt
    return AgentSessionState(
        True,
        prompt_changed,
        "http_session_registry",
        session_key,
        prompt_changed=prompt_changed,
    )


async def prepare_agent_session(request: SendToAgentRequest) -> tuple[SendToAgentRequest, AgentSessionState]:
    """Prepare one send using a shared identity/session policy.

    ACP injection remains atomic inside ``AcpxAdapter.ensure_session``. HTTP
    uses its persistent session registry and injects only for a new session or
    when the resolved identity prompt changed.
    """
    options = dict(request.options or {})
    identity_prompt = str(
        options.get("identity_prompt") or options.get("system_prompt") or ""
    ).strip()
    connect_type = str(request.connect_type or "").strip().lower()
    session_key = str(request.session or "").strip()

    if not identity_prompt:
        state = AgentSessionState(None, False, "no_identity_prompt", session_key)
        options["_agent_session_state"] = state.as_dict()
        return replace(request, options=options), state

    if connect_type == "acp":
        # ensure_session performs the existence check and session creation as
        # one transport operation; duplicating that CLI check here would race.
        options["system_prompt"] = identity_prompt
        state = AgentSessionState(None, None, "acpx_ensure_session", session_key)
        options["_agent_session_state"] = state.as_dict()
        return replace(request, options=options), state

    group_db_path = str(options.get("group_db_path") or "").strip()
    global_name = str(options.get("identity_global_name") or "").strip()
    try:
        state = await inspect_http_agent_session(
            group_db_path=group_db_path,
            session_key=session_key,
            identity_prompt=identity_prompt,
        )
    except Exception:
        # Session inspection must never turn an otherwise valid send into an
        # outage. Without a registry, use stable system-prompt semantics.
        state = AgentSessionState(
            None,
            True,
            "http_session_registry_unavailable",
            session_key,
        )
    should_inject = state.should_inject_identity
    if group_db_path and session_key and global_name and should_inject:
        from api.group_repository import upsert_http_agent_session

        try:
            persisted_should_inject = await upsert_http_agent_session(
                group_db_path,
                session_key=session_key,
                global_name=global_name,
                prompt_text=identity_prompt,
                transport="http",
                now_ts=time.time(),
            )
            if not persisted_should_inject:
                state = AgentSessionState(
                    True,
                    False,
                    "http_session_registry",
                    session_key,
                )
        except Exception:
            state = AgentSessionState(
                None,
                True,
                "http_session_registry_unavailable",
                session_key,
            )

    should_inject = state.should_inject_identity is True
    options.pop("system_prompt", None)
    prompt = request.prompt
    if should_inject:
        mode = str(options.get("identity_injection_mode") or "system").strip().lower()
        body = deepcopy(options.get("body") or {})
        body_messages = body.get("messages")
        if isinstance(body_messages, list):
            body["messages"] = _prepend_identity_to_messages(
                body_messages,
                identity_prompt,
                mode=mode,
            )
            options["body"] = body
            if isinstance(prompt, list):
                prompt = body["messages"]
        elif isinstance(prompt, list):
            prompt = _prepend_identity_to_messages(prompt, identity_prompt, mode=mode)
        else:
            options["system_prompt"] = identity_prompt

    options["_agent_session_state"] = state.as_dict()
    return replace(request, prompt=prompt, options=options), state
