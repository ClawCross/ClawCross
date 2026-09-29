"""What a Python workflow can reach: the team's agents and the persona library.

Agents are asked by their id over the agent layer's entrances, whatever runtime
they live in. A persona call is a temporary agent made for that one call.
"""

from __future__ import annotations

import os
import sys
import uuid
from copy import deepcopy
from typing import Any

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # src/backend: the import root
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from agents.client import AgentClient
from agents.messages import AgentMessage, AgentReply
from agents.routes import agent_card
from agents.store import Agent, get_store
from oasis.experts import _build_identity_prompt, get_all_experts
from teams.store import get_team_store


def build_persona_catalog(user_id: str, team: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for expert in get_all_experts(user_id, team=team):
        tag = str(expert.get("tag", "") or "").strip()
        if not tag:
            continue
        llm = {"temperature": float(expert.get("temperature", 0.7))}
        for key in ("model", "api_key", "base_url", "provider"):
            if expert.get(key):
                llm[key] = expert.get(key)
        items.append({
            "id": f"persona:{tag}",
            "name": str(expert.get("name", "") or tag),
            "tag": tag,
            "persona": str(expert.get("persona", "") or ""),
            "source": str(expert.get("source", "") or ""),
            "llm": llm,
        })
    return items


class AgentCenter:
    def __init__(self, user_id: str, team: str = ""):
        self.user_id = user_id
        self.team = team
        self._personas = build_persona_catalog(user_id, team)
        self._client = AgentClient(user_id)

    # ── agents ───────────────────────────────────────────────────────────

    def _members(self) -> list[tuple[Agent, str]]:
        teams = get_team_store()
        if self.team and teams.exists(self.user_id, self.team):
            return [(m.agent, m.role) for m in teams.members(self.user_id, self.team)]
        return [(a, a.name) for a in get_store().list(self.user_id)]

    def list_agents(self) -> list[dict[str, Any]]:
        """The team's agents (or all of the user's, outside a team), with their role names."""
        return [{**agent_card(agent), "role": role} for agent, role in self._members()]

    def _agent(self, target: str) -> tuple[Agent, str]:
        from oasis.engine import resolve_agent

        key = str(target or "").strip()
        if not key:
            raise ValueError("target 不能为空")
        try:
            return resolve_agent(self.user_id, self.team, key)
        except LookupError:
            raise ValueError(f"未找到 agent: {target}") from None

    def get_agent(self, target: str) -> dict[str, Any]:
        agent, role = self._agent(target)
        return {**agent_card(agent), "role": role}

    async def send_agent(self, target: str, prompt: str, *, persona_tag: str | None = None,
                         persona_override: str | None = None) -> AgentReply:
        """Ask one of the agents; a persona given here frames this one message."""
        agent, role = self._agent(target)
        persona = persona_override if persona_override is not None else (
            str(self.get_persona(persona_tag).get("persona") or "") if persona_tag else "")
        instructions = _build_identity_prompt(role, persona).strip() if persona else ""
        return await self._client.ask(agent.agent_id, AgentMessage(text=prompt, instructions=instructions))

    # ── personas ─────────────────────────────────────────────────────────

    def list_personas(self) -> list[dict[str, Any]]:
        return deepcopy(self._personas)

    def get_persona(self, target: str) -> dict[str, Any]:
        key = str(target or "").strip()
        if not key:
            raise ValueError("persona target 不能为空")
        matches = [p for p in self._personas if key in (p["id"], p["name"], p["tag"])]
        if not matches:
            raise ValueError(f"未找到 persona: {target}")
        if len(matches) > 1:
            raise ValueError(f"persona 标识不唯一: {target} -> {', '.join(p['id'] for p in matches)}")
        return deepcopy(matches[0])

    async def send_persona(self, target: str, prompt: str, *, persona_override: str | None = None,
                           llm: dict[str, Any] | None = None) -> AgentReply:
        """One call to a temporary agent with a persona from the library: no tools, no memory."""
        persona = self.get_persona(target)
        text = persona_override if persona_override is not None else persona["persona"]
        return await self._one_call(persona["name"], text, prompt, {**persona["llm"], **(llm or {})})

    async def send_agent_once(self, name: str = "", prompt: str | None = None, *,
                              persona_override: str | None = None, llm: dict[str, Any] | None = None) -> AgentReply:
        """One call to an ad-hoc temporary agent: ``send_agent_once(prompt)`` or
        ``send_agent_once(name, prompt, persona_override=…)``."""
        if prompt is None:
            prompt, name = name, ""
        return await self._one_call(name or "临时 agent", persona_override or "", prompt, dict(llm or {}))

    async def call_llm(self, prompt: str, *, temperature: float | None = None, model: str | None = None,
                       max_tokens: int | None = None) -> AgentReply:
        """A bare model call: no persona, no memory, no tools."""
        llm: dict[str, Any] = {}
        if temperature is not None:
            llm["temperature"] = temperature
        if model:
            llm["model"] = model
        if max_tokens is not None:
            llm["max_tokens"] = max_tokens
        return await self._one_call("llm", "", prompt, llm)

    async def _one_call(self, name: str, persona: str, prompt: str, llm: dict[str, Any]) -> AgentReply:
        agent_id = f"tmp__py__{uuid.uuid4().hex[:12]}"
        await self._client.create(agent_id=agent_id, name=name, platform="llm", llm=llm)
        instructions = _build_identity_prompt(name, persona).strip() if persona else ""
        try:
            return await self._client.ask(agent_id, AgentMessage(text=prompt, instructions=instructions))
        finally:
            await self._client.delete(agent_id)
