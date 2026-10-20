"""What a Python workflow can reach: the team's agents and the persona library.

Agents are asked by their id over the agent layer's entrances, whatever runtime
they live in. A persona call is a temporary agent made for that one call.
"""

from __future__ import annotations

import asyncio
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
from oasis.agent_bindings import mapped_agent, merge_agent_maps, team_agent_map, validate_agent_map
from teams.store import get_team_store


class TemporaryAgent:
    """A workflow-owned Agent; reusable within its scope and closed on exit."""

    def __init__(self, client: AgentClient, fields: dict[str, Any], *, instructions: str = ""):
        self.agent_id = fields["agent_id"]
        self._client = client
        self._fields = fields
        self._instructions = instructions
        self._attempted = False
        self._made = False
        self._closed = False
        self._creation_lock = asyncio.Lock()
        self._turn_lock = asyncio.Lock()

    async def start(self):
        async with self._creation_lock:
            if self._closed:
                raise RuntimeError("Temporary Agent is already closed")
            if not self._made:
                self._attempted = True
                await self._client.create(**self._fields)
                self._made = True
        return self

    async def send(self, prompt: str, *, timeout: float | None = None) -> AgentReply:
        # One Agent remembers sequential turns; different Agents can run in parallel.
        async with self._turn_lock:
            await self.start()
            return await self._client.ask(
                self.agent_id, AgentMessage(text=prompt, instructions=self._instructions), timeout=timeout)

    async def close(self) -> None:
        async with self._creation_lock:
            if self._closed:
                return
            # A cancelled create request may already have registered its Agent.
            if self._attempted:
                await self._client.delete(self.agent_id)
            self._closed = True
            self._made = False

    async def __aenter__(self):
        try:
            return await self.start()
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *_exc):
        await self.close()


def _temporary_tools(tools: list[str] | str | None) -> list[str] | None:
    if tools is None or tools == "none":
        return []
    if tools == "all":
        return None
    if not isinstance(tools, list) or any(not isinstance(name, str) or not name.strip() for name in tools):
        raise ValueError("tools must be 'none', 'all', or a list of tool names")
    return list(dict.fromkeys(tools))


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
    def __init__(self, user_id: str, team: str = "", *, agent_map: dict[str, str] | None = None):
        self.user_id = user_id
        self.team = team
        self._personas = build_persona_catalog(user_id, team)
        self._client = AgentClient(user_id)
        self._temporary: set[TemporaryAgent] = set()
        self._agent_map: dict[str, str] = {}
        if agent_map is not None:
            self.bind_agents(agent_map)

    @property
    def agent_map(self) -> dict[str, str]:
        """Team role bindings plus this run's explicit overrides (a copy)."""
        return merge_agent_maps(team_agent_map(self.user_id, self.team), self._agent_map)

    def bind_agents(self, mapping: dict[str, str]) -> dict[str, str]:
        chosen = validate_agent_map(mapping)
        # Check every target before changing any binding. Never invent an Agent.
        for name in chosen:
            mapped_agent(self.user_id, name, chosen)
        self._agent_map = merge_agent_maps(self._agent_map, chosen)
        return self.agent_map

    def temporary_agent(self, name: str = "", *, persona_override: str = "",
                        persona_tag: str | None = None, tools: list[str] | str | None = None,
                        llm: dict[str, Any] | None = None, creation_template: str = "personal",
                        workspaces: dict[str, Any] | None = None) -> TemporaryAgent:
        """A temporary WeBot with its own memory, tool table and approval/sandbox policy.

        Use ``async with center.temporary_agent(...):`` for an explicit scope;
        the workflow also closes any remaining handles when it ends.
        """
        persona = self.get_persona(persona_tag) if persona_tag else {}
        fields = {
            "agent_id": f"tmp__py__{uuid.uuid4().hex[:12]}", "name": name or persona.get("name") or "临时 agent",
            "platform": "webot", "tools": _temporary_tools(tools),
            "persona": persona_override or persona.get("persona") or "",
            "llm": {**persona.get("llm", {}), **(llm or {})}, "creation_template": creation_template,
        }
        if workspaces is not None:
            fields["workspaces"] = deepcopy(workspaces)
        return self._track_temporary(fields)

    def _track_temporary(self, fields: dict[str, Any], instructions: str = "") -> TemporaryAgent:
        agent = TemporaryAgent(self._client, fields, instructions=instructions)
        self._temporary.add(agent)
        return agent

    async def close_temporary_agents(self) -> None:
        """Close only Agents this workflow created, including errored/cancelled turns."""
        agents = list(self._temporary)
        results = await asyncio.gather(*(agent.close() for agent in agents), return_exceptions=True)
        errors = []
        for agent, result in zip(agents, results):
            if isinstance(result, BaseException):
                errors.append(f"{agent.agent_id}: {result}")
            else:
                self._temporary.discard(agent)
        if errors:
            raise RuntimeError("Could not clean up temporary Agents: " + "; ".join(errors))

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
            return resolve_agent(self.user_id, self.team, key, self.agent_map)
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
                           llm: dict[str, Any] | None = None, tools: list[str] | str | None = None,
                           creation_template: str = "personal",
                           workspaces: dict[str, Any] | None = None) -> AgentReply:
        """One temporary persona task; tools opt into a WeBot, closed after its reply."""
        persona = self.get_persona(target)
        text = persona_override if persona_override is not None else persona["persona"]
        return await self._one_call(persona["name"], text, prompt, {**persona["llm"], **(llm or {})},
                                    tools=tools, creation_template=creation_template, workspaces=workspaces)

    async def send_agent_once(self, name: str = "", prompt: str | None = None, *,
                              persona_override: str | None = None, llm: dict[str, Any] | None = None,
                              tools: list[str] | str | None = None, creation_template: str = "personal",
                              workspaces: dict[str, Any] | None = None) -> AgentReply:
        """One call to an ad-hoc temporary agent: ``send_agent_once(prompt)`` or
        ``send_agent_once(name, prompt, persona_override=…)``."""
        if prompt is None:
            prompt, name = name, ""
        return await self._one_call(name or "临时 agent", persona_override or "", prompt, dict(llm or {}),
                                    tools=tools, creation_template=creation_template, workspaces=workspaces)

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

    async def _one_call(self, name: str, persona: str, prompt: str, llm: dict[str, Any], *,
                        tools: list[str] | str | None = None, creation_template: str = "personal",
                        workspaces: dict[str, Any] | None = None) -> AgentReply:
        chosen = _temporary_tools(tools)
        if chosen is None or chosen:
            agent = self.temporary_agent(name, persona_override=persona, tools=tools, llm=llm,
                                         creation_template=creation_template, workspaces=workspaces)
        else:
            fields = {"agent_id": f"tmp__py__{uuid.uuid4().hex[:12]}", "name": name,
                      "platform": "llm", "llm": llm}
            instructions = _build_identity_prompt(name, persona).strip() if persona else ""
            agent = self._track_temporary(fields, instructions)
        try:
            async with agent:
                return await agent.send(prompt)
        finally:
            if agent._closed:
                self._temporary.discard(agent)
