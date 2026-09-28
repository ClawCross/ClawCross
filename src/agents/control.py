"""Status and lifecycle of one agent: status, cancel, reset, history, and cleanup on delete.

Runs inside the Agent service, which owns the WeBot runtime; other processes go
through ``/v1/agents/{ref}/control``. Each driver answers for its own runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any

from agents.gateway import external_session_key
from agents.runtime_sessions import forget_agent, get_session
from agents.store import ACPX, OPENCLAW, WEBOT, Agent

logger = logging.getLogger(__name__)

ACTIONS = ("status", "cancel", "reset")


class ControlError(RuntimeError):
    pass


def supported_actions(agent: Agent) -> list[str]:
    if agent.driver in (WEBOT, ACPX, OPENCLAW):
        return ["status", "cancel", "reset"]
    return ["status", "reset"]


class AgentControl:
    def __init__(self, webot_runtime: Any, *, checkpoint_db_path: str = "", runtime_db_path: str = ""):
        self.webot = webot_runtime
        self.checkpoint_db_path = checkpoint_db_path
        self.runtime_db_path = runtime_db_path

    async def status(self, agent: Agent) -> dict[str, Any]:
        base = {"actions": supported_actions(agent)}
        try:
            if agent.driver == WEBOT:
                return {**base, **self._webot_status(agent)}
            if agent.driver == ACPX:
                return {**base, **await self._acpx_status(agent)}
            if agent.driver == OPENCLAW:
                return {**base, **await self._openclaw_status(agent)}
            return {**base, **await self._http_status(agent)}
        except Exception as exc:  # a status probe never fails the listing
            return {**base, "state": "unknown", "detail": str(exc)}

    def is_busy(self, agent: Agent) -> bool:
        """Whether the agent is still working. Only WeBot can tell; others count as busy."""
        if agent.driver != WEBOT:
            return True
        return self._webot_status(agent)["state"] == "running"

    async def run(self, agent: Agent, action: str) -> dict[str, Any]:
        if action == "status":
            return await self.status(agent)
        if action not in supported_actions(agent):
            raise ControlError(f"{agent.platform} agents do not support {action}")
        if agent.driver == WEBOT:
            thread = f"{agent.owner}#{agent.config.get('session', '')}"
            if action == "cancel":
                return {"cancelled": bool(await self.webot.cancel_task(thread))}
            await self._drop_webot_thread(thread)
            return {"reset": True}
        if agent.driver in (ACPX, OPENCLAW):
            await self._acpx_command(agent, action)
            if action == "reset":
                await forget_agent(self.runtime_db_path, str(agent.config.get("global_name") or ""))
            return {action: True}
        await forget_agent(self.runtime_db_path, str(agent.config.get("global_name") or ""))
        return {"reset": True}

    async def history(self, agent: Agent, limit: int = 200) -> list[dict[str, Any]]:
        """The agent's own conversation, oldest first: ``[{role, content, tool_calls?}]``."""
        if agent.driver == WEBOT:
            return (await self._webot_history(agent))[-limit:]
        from utils.external_agent_history import get_store as history_store

        store = await history_store()
        rows = await store.list_messages(platform=agent.platform, session_key=external_session_key(agent), limit=5000)
        return [
            {"role": row.get("role") or ("user" if row.get("direction") == "send" else "assistant"),
             "content": row.get("content") or ""}
            for row in rows[-limit:]
        ]

    async def cleanup(self, agent: Agent) -> None:
        """Release the runtime state of an agent that is being deleted."""
        try:
            if agent.driver == WEBOT:
                await self._drop_webot_thread(f"{agent.owner}#{agent.config.get('session', '')}")
            elif agent.driver == ACPX:
                await self._acpx_command(agent, "close")
            if agent.driver != WEBOT:
                await forget_agent(self.runtime_db_path, str(agent.config.get("global_name") or ""))
        except Exception:
            logger.exception("cleanup of %s failed", agent.address)

    # ── WeBot ────────────────────────────────────────────────────────────

    def _webot_status(self, agent: Agent) -> dict[str, Any]:
        thread = f"{agent.owner}#{agent.config.get('session', '')}"
        state = self.webot.get_all_thread_status(f"{agent.owner}#").get(thread, {})
        busy = bool(state.get("busy")) or thread in set(self.webot.list_active_task_keys(f"{agent.owner}#"))
        usage = getattr(self.webot, "get_thread_context_usage", None)
        return {
            "state": "running" if busy else "idle",
            "context": usage(thread) if callable(usage) else None,
            "pending": state.get("pending_system", 0),
        }

    async def _webot_history(self, agent: Agent) -> list[dict[str, Any]]:
        from services.llm_factory import extract_text

        thread = f"{agent.owner}#{agent.config.get('session', '')}"
        snapshot = await self.webot.agent_app.aget_state({"configurable": {"thread_id": thread}})
        out: list[dict[str, Any]] = []
        for msg in (snapshot.values.get("messages", []) if snapshot and snapshot.values else []):
            kind = type(msg).__name__
            if kind == "HumanMessage":
                out.append({"role": "user", "content": extract_text(msg.content)})
            elif kind in ("AIMessage", "AIMessageChunk"):
                calls = [{"name": c.get("name", ""), "args": c.get("args", {})} for c in (getattr(msg, "tool_calls", None) or [])]
                content = extract_text(msg.content)
                if content or calls:
                    out.append({"role": "assistant", "content": content, **({"tool_calls": calls} if calls else {})})
            elif kind == "ToolMessage":
                out.append({"role": "tool", "content": extract_text(msg.content), "tool_name": getattr(msg, "name", "")})
        return out

    async def _drop_webot_thread(self, thread: str) -> None:
        from utils.checkpoint_repository import delete_thread_records

        await self.webot.cancel_task(thread)
        close = getattr(self.webot, "close_thread_checkpoint", None)
        if callable(close):
            await close(thread)
        if self.checkpoint_db_path:
            await delete_thread_records(self.checkpoint_db_path, thread)

    # ── external runtimes ────────────────────────────────────────────────

    async def _http_status(self, agent: Agent) -> dict[str, Any]:
        record = await get_session(self.runtime_db_path, external_session_key(agent))
        return {"state": "online" if record else "idle"}

    @staticmethod
    def _adapter():
        from integrations.acpx_adapter import AcpxError, get_acpx_adapter
        from utils.runtime_paths import WORKSPACE_DIR

        if not shutil.which("acpx"):
            raise ControlError("acpx is not installed")
        try:
            return get_acpx_adapter(cwd=str(WORKSPACE_DIR / "acpx"))
        except AcpxError as exc:
            raise ControlError(str(exc)) from exc

    async def _acpx_status(self, agent: Agent) -> dict[str, Any]:
        sessions = await self._adapter().list_sessions(tool=agent.platform)
        key = external_session_key(agent)
        live = [s for s in sessions if s.get("name") == key and not s.get("closed")]
        return {"state": "online" if live else "idle", "sessions": live}

    async def _openclaw_status(self, agent: Agent) -> dict[str, Any]:
        binary = shutil.which("openclaw")
        if not binary:
            return {"state": "unavailable", "detail": "openclaw is not installed"}
        proc = await asyncio.create_subprocess_exec(
            binary, "sessions", "--json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
        raw = json.loads(stdout.decode("utf-8", errors="replace") or "[]")
        sessions = raw.get("sessions", []) if isinstance(raw, dict) else raw
        prefix = f"agent:{agent.config.get('global_name', '')}:"
        mine = [s for s in sessions if str(s.get("key", s.get("session_key", ""))).startswith(prefix)]
        return {"state": "online" if mine else "idle", "sessions": mine}

    async def _acpx_command(self, agent: Agent, action: str) -> None:
        from integrations.acpx_adapter import AcpxError, acpx_options_from_agent

        adapter = self._adapter()
        key = external_session_key(agent)
        policy = acpx_options_from_agent(agent.config, default_timeout_sec=180)
        common = {
            "timeout_sec": min(policy["timeout_sec"], 60) if action != "reset" else policy["timeout_sec"],
            "ttl_sec": policy["ttl_sec"],
            "approve_all": policy["approve_all"],
            "non_interactive_permissions": policy["non_interactive_permissions"],
        }
        try:
            if agent.driver == OPENCLAW:
                if action in ("cancel", "reset"):
                    await adapter.ops_openclaw_exec_slash(
                        session_key=key, slash="/stop" if action == "cancel" else "/new", **common,
                    )
                return
            tool = agent.platform
            if action == "cancel":
                await adapter.ops_non_openclaw_cancel(tool=tool, session_key=key, **common)
            elif action == "reset":
                await adapter.ops_non_openclaw_reset_session(tool=tool, session_key=key, **common)
            elif action == "close":
                await adapter.close_session(
                    tool=tool, session_key=key, acpx_session=adapter.to_acpx_session_name(tool=tool, session_key=key),
                    **common,
                )
        except AcpxError as exc:
            raise ControlError(str(exc)) from exc
