"""Owned ACP session tracking and transport controls for the agent service."""
import asyncio
import os
from fastapi import HTTPException

async def list_all_sessions(user_id: str, acpx_bin: str | None) -> dict:
    """Only the caller's registered ACP agents, using local acpx records."""
    from agents.store import ACPX, canonical_platform, get_store
    from external.session import runtime_session
    from external.acpx import get_acpx_adapter

    owned = {(canonical_platform(agent.platform), runtime_session(agent)): agent
             for agent in get_store().list(user_id) if agent.driver == ACPX}
    if not owned or not acpx_bin:
        return {"status": "success", "acpx_sessions": []}
    adapter = get_acpx_adapter()
    rows = []
    for platform in sorted({key[0] for key in owned}):
        for row in await adapter.list_sessions(tool=platform):
            agent = owned.get((platform, row["name"]))
            if agent is None:
                continue
            rows.append({"platform": platform, "session_id": row.get("acpxRecordId"),
                         "name": row["name"], "agent_id": agent.agent_id,
                         "agent_name": agent.name, "cwd": row.get("cwd"),
                         "last_used_at": row.get("lastUsedAt"), "closed": row.get("closed", False)})
    return {"status": "success", "acpx_sessions": rows}


async def close_acp_session(platform: str, session_name: str, cwd: str = "", *, user_id: str, acpx_bin: str | None) -> dict:
    """Close an acpx session via 'acpx --cwd <session_cwd> <platform> sessions close <name>'.

    ``acpx`` binds every session to the cwd it was created in, and
    ``sessions close`` only acts on the current cwd (``acpx --help``:
    "Close session for current cwd"). The list rows carry each session's
    own cwd (column 3) — close must reuse *that* exact cwd, not a fixed
    store path. Closing from any other cwd prints
    ``No named session "<name>" for cwd <dir>`` and still exits 0, so the
    old fixed-``WORKSPACE_DIR/acpx`` path silently no-oped for every
    session created elsewhere while the UI reported success.
    """
    from agents.store import ACPX, canonical_platform, get_store
    from external.session import runtime_session
    from external.acpx import get_acpx_adapter
    owned = next((agent for agent in get_store().list(user_id)
                  if agent.driver == ACPX and canonical_platform(agent.platform) == canonical_platform(platform)
                  and runtime_session(agent) == session_name), None)
    if owned is None:
        raise HTTPException(404, "No owned ACP session")
    # The runtime owns cwd; never let a browser select another user's record.
    adapter = get_acpx_adapter(cwd=owned.runtime['acp_cwd']) if owned.runtime.get('acp_cwd') else get_acpx_adapter()
    cwd = adapter._cwd
    platform = canonical_platform(owned.platform)
    if not acpx_bin:
        return {"status": "error", "reason": "acpx not found"}
    acpx_cwd = (cwd or "").strip() or None
    if acpx_cwd is None:
        # No session cwd supplied: fall back to the canonical store so newly
        # created sessions (which use WORKSPACE_DIR/acpx) still close.
        try:
            from common.runtime_paths import WORKSPACE_DIR  # local import to avoid cycles
            acpx_cwd = os.path.join(str(WORKSPACE_DIR), "acpx")
            os.makedirs(acpx_cwd, exist_ok=True)
        except Exception:
            acpx_cwd = None
    cmd = [acpx_bin]
    if acpx_cwd:
        cmd.extend(["--cwd", acpx_cwd])
    cmd.extend([platform, "sessions", "close", session_name])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=acpx_cwd or None,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        err_text = (stderr.decode("utf-8", errors="replace") or "").strip()
        # acpx exits 0 even when it finds nothing to close ("No named session
        # ... for cwd ..."), so returncode alone can't confirm success. Treat
        # that message as a real failure instead of a false "closed".
        if "No named session" in err_text:
            return {"status": "error", "reason": err_text[:200]}
        # exit 0 = just closed, exit 1 = already closed (both fine for idempotency)
        if proc.returncode in (0, 1):
            return {"status": "success", "stderr": err_text} if err_text else {"status": "success"}
        return {"status": "error", "reason": err_text[:200] or f"exit={proc.returncode}"}
    except Exception as e:
        return {"status": "error", "reason": str(e)}
