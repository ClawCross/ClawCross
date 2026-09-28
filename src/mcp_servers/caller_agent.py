"""Which agent a WeBot session calling an MCP tool is.

A tool runs inside a WeBot session (``username`` / ``source_session`` are
injected). Anything that acts for that session elsewhere in ClawCross — posting
into a conversation, scheduling itself a task — acts as its agent.
"""

from __future__ import annotations

import os

import httpx


def _base() -> str:
    return f"http://127.0.0.1:{os.getenv('PORT_AGENT', '51200')}"


def internal_headers(username: str) -> dict[str, str]:
    token = os.getenv("INTERNAL_TOKEN", "")
    return {"Authorization": f"Bearer {token}:{username}", "X-Internal-Token": token}


async def caller_agent(username: str, session: str, *, register: bool = False) -> str:
    """The id of the agent this session is; with *register*, a session that is
    not an agent yet becomes one. "" when it is none."""
    session = (session or "").strip() or "default"
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{_base()}/v1/agents", params={"runtime": f"webot:{session}"}, headers=internal_headers(username),
        )
        response.raise_for_status()
        found = response.json().get("data") or []
        if found:
            return found[0]["agent_id"]
        if not register:
            return ""
        response = await client.post(
            f"{_base()}/v1/agents",
            json={"name": "主助手" if session == "default" else session, "platform": "webot", "session": session},
            headers=internal_headers(username),
        )
        response.raise_for_status()
        return response.json()["agent_id"]
