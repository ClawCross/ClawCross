from __future__ import annotations

import os
import sys
from copy import deepcopy
from typing import Any

_SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
from utils.runtime_paths import DATA_DIR

from integrations.agent_sender import (
    ResetAgentRequest,
    ResetAgentResult,
    SendToAgentRequest,
    SendToAgentResult,
    reset_agent,
    send_to_agent,
)
from oasis.agent_catalog import build_agent_catalog, build_persona_catalog


class AgentCenter:
    def __init__(self, user_id: str, team: str = ""):
        self.user_id = user_id
        self.team = team
        self._catalog = build_agent_catalog(user_id, team)
        self._persona_catalog = build_persona_catalog(user_id, team)
        self._by_id = {str(item.get("id", "")): item for item in self._catalog}
        self._persona_by_id = {str(item.get("id", "")): item for item in self._persona_catalog}

    def list_agents(self) -> list[dict[str, Any]]:
        return deepcopy(self._catalog)

    def list_personas(self) -> list[dict[str, Any]]:
        return deepcopy(self._persona_catalog)

    def get_agent(self, target: str) -> dict[str, Any]:
        key = str(target or "").strip()
        if not key:
            raise ValueError("target 不能为空")
        if key in self._by_id:
            return deepcopy(self._by_id[key])
        candidates = [
            item for item in self._catalog
            if key in {
                str(item.get("id", "")),
                str(item.get("name", "")),
                str(item.get("tag", "")),
            }
        ]
        if not candidates:
            raise ValueError(f"未找到 agent: {target}")
        if len(candidates) > 1:
            ids = ", ".join(str(item.get("id", "")) for item in candidates)
            raise ValueError(f"agent 标识不唯一: {target} -> {ids}")
        return deepcopy(candidates[0])

    def get_persona(self, target: str) -> dict[str, Any]:
        key = str(target or "").strip()
        if not key:
            raise ValueError("persona target 不能为空")
        if key in self._persona_by_id:
            return deepcopy(self._persona_by_id[key])
        candidates = [
            item for item in self._persona_catalog
            if key in {
                str(item.get("id", "")),
                str(item.get("name", "")),
                str(item.get("tag", "")),
            }
        ]
        if not candidates:
            raise ValueError(f"未找到 persona: {target}")
        if len(candidates) > 1:
            ids = ", ".join(str(item.get("id", "")) for item in candidates)
            raise ValueError(f"persona 标识不唯一: {target} -> {ids}")
        return deepcopy(candidates[0])

    async def send_agent(
        self,
        target: str,
        prompt: str,
        *,
        persona_tag: str | None = None,
        persona_override: str | None = None,
        session: str | None = None,
        connect_type: str | None = None,
        platform: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> SendToAgentResult:
        agent = self.get_agent(target)
        merged_options = deepcopy(agent.get("options") or {})
        if options:
            merged_options.update(options)

        effective_persona = ""
        if persona_override is not None:
            effective_persona = persona_override
        elif persona_tag:
            effective_persona = str(self.get_persona(persona_tag).get("persona", "") or "")
        if effective_persona and not merged_options.get("system_prompt"):
            merged_options["system_prompt"] = effective_persona
        effective_connect_type = connect_type or str(agent.get("connect_type", "") or "")
        effective_platform = platform or str(agent.get("platform", "") or "")
        effective_session = session if session is not None else agent.get("session")

        def _can_fallback_to_persona() -> bool:
            return bool(agent.get("kind") == "external" and str(agent.get("tag", "") or "").strip())

        def _fallback_triggered(error_text: str) -> bool:
            low = str(error_text or "").strip().lower()
            if not low:
                return False
            return any(token in low for token in (
                "unsupported connect_type",
                "missing api_url",
                "unsupported platform",
                "platform not found",
                "tool not found",
                "unknown tool",
            ))

        request = SendToAgentRequest(
            prompt=prompt,
            connect_type=effective_connect_type,
            platform=effective_platform,
            session=effective_session,
            options=merged_options,
        )
        result = await send_to_agent(request)
        if result.ok or not _can_fallback_to_persona() or not _fallback_triggered(result.error or ""):
            return result

        try:
            fallback_result = await self.send_persona(
                str(agent.get("tag", "") or ""),
                prompt,
                persona_override=effective_persona if effective_persona else None,
            )
            fallback_meta = dict(fallback_result.meta or {})
            fallback_meta["fallback_from_platform"] = effective_platform
            fallback_meta["fallback_from_connect_type"] = effective_connect_type
            fallback_meta["fallback_agent_id"] = str(agent.get("id", "") or "")
            fallback_result.meta = fallback_meta
            return fallback_result
        except Exception:
            return result

    async def reset_agent(
        self,
        target: str,
        *,
        session: str | None = None,
        connect_type: str | None = None,
        platform: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> ResetAgentResult:
        agent = self.get_agent(target)
        merged_options = deepcopy(agent.get("options") or {})
        if options:
            merged_options.update(options)

        effective_connect_type = connect_type or str(agent.get("connect_type", "") or "")
        effective_platform = platform or str(agent.get("platform", "") or "")
        effective_session = session if session is not None else agent.get("session")

        merged_options.setdefault("cwd", _PROJECT_ROOT)
        merged_options.setdefault("group_db_path", os.path.join(str(DATA_DIR), "group_chat.db"))
        if effective_platform == "internal":
            merged_options.setdefault("user_id", self.user_id)
            merged_options.setdefault("internal_token", os.getenv("INTERNAL_TOKEN", ""))
            merged_options.setdefault(
                "delete_session_url",
                f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}/delete_session",
            )

        return await reset_agent(
            ResetAgentRequest(
                connect_type=effective_connect_type,
                platform=effective_platform,
                session=effective_session,
                options=merged_options,
            )
        )

    async def send_persona(
        self,
        target: str,
        prompt: str,
        *,
        persona_override: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> SendToAgentResult:
        persona = self.get_persona(target)
        merged_options = deepcopy(persona.get("options") or {})
        if options:
            merged_options.update(options)

        effective_persona = persona_override if persona_override is not None else str(persona.get("persona", "") or "")

        # temp is a pure, memoryless LLM call that ignores options["system_prompt"].
        # send_persona MUST carry an identity, so bake the persona into the prompt
        # body using the same framing as the YAML/swarm workflow
        # (oasis.experts._build_identity_prompt) — one source of truth for persona framing.
        effective_prompt = prompt
        if effective_persona:
            from oasis.experts import _build_identity_prompt
            persona_name = str(persona.get("name", "") or persona.get("tag", "") or target)
            identity = _build_identity_prompt(persona_name, effective_persona)
            if identity:
                effective_prompt = f"{identity}{prompt}"

        return await send_to_agent(
            SendToAgentRequest(
                prompt=effective_prompt,
                connect_type="http",
                platform="temp",
                session=f"persona:{persona.get('tag')}",
                options=merged_options,
            )
        )


def list_team_agents(user_id: str, team: str = "") -> list[dict[str, Any]]:
    return AgentCenter(user_id, team).list_agents()


def list_team_personas(user_id: str, team: str = "") -> list[dict[str, Any]]:
    return AgentCenter(user_id, team).list_personas()


async def send_team_agent(
    user_id: str,
    team: str,
    target: str,
    prompt: str,
    **kwargs: Any,
) -> SendToAgentResult:
    return await AgentCenter(user_id, team).send_agent(target, prompt, **kwargs)


async def reset_team_agent(
    user_id: str,
    team: str,
    target: str,
    **kwargs: Any,
) -> ResetAgentResult:
    return await AgentCenter(user_id, team).reset_agent(target, **kwargs)


async def send_team_persona(
    user_id: str,
    team: str,
    target: str,
    prompt: str,
    **kwargs: Any,
) -> SendToAgentResult:
    return await AgentCenter(user_id, team).send_persona(target, prompt, **kwargs)
