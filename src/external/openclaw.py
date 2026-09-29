"""An OpenClaw agent, over the OpenClaw gateway's OpenAI-compatible endpoint.

The agent's session is the key ``agent:<global_name>:clawcross-<owner>-<id>``;
cancel and reset are OpenClaw slash commands run through acpx.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any

from agents.runtime import ControlError
from agents.store import Agent
from external import acp, session
from external.http import HttpRuntime


class OpenclawRuntime(HttpRuntime):
    controls = ("cancel", "reset")

    def endpoint(self, agent: Agent) -> tuple[str, str, str, dict[str, str]]:
        api_url, api_key, model, headers = super().endpoint(agent)
        # The OpenClaw endpoint depends on the device: runtime env beats saved config.
        api_url = os.getenv("OPENCLAW_API_URL", "") or api_url
        api_key = os.getenv("OPENCLAW_GATEWAY_TOKEN", "") or api_key
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if not model.startswith("agent:"):
            model = f"agent:{agent.config.get('global_name') or 'main'}"
        headers["x-openclaw-session-key"] = session.runtime_session(agent)
        return api_url, api_key, model, headers

    async def status(self, agent: Agent) -> dict[str, Any]:
        binary = shutil.which("openclaw")
        if not binary:
            return {"state": "unavailable", "detail": "openclaw is not installed"}
        proc = await asyncio.create_subprocess_exec(
            binary, "sessions", "--json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
        raw = json.loads(stdout.decode("utf-8", errors="replace") or "[]")
        sessions = raw.get("sessions", []) if isinstance(raw, dict) else raw
        key = session.runtime_session(agent)
        mine = [s for s in sessions if str(s.get("key", s.get("session_key", ""))) == key]
        return {"state": "online" if mine else "idle", "sessions": mine}

    async def control(self, agent: Agent, action: str) -> dict[str, Any]:
        from integrations.acpx_adapter import AcpxError

        if action not in self.controls:
            return await super().control(agent, action)
        try:
            await acp.adapter().ops_openclaw_exec_slash(
                session_key=session.runtime_session(agent), slash="/stop" if action == "cancel" else "/new",
                **acp.command_options(agent, long=action == "reset"),
            )
        except AcpxError as exc:
            raise ControlError(str(exc)) from exc
        if action == "reset":
            session.forget(self._store, agent)
        return {action: True}
