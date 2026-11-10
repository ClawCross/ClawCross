"""
Local Claude Code integration helpers.

This module intentionally avoids installing LaunchAgents, changing wake
schedules, or touching power settings. It exposes the safe parts ClawCross can
own directly: local availability checks, ACP probes, one-shot kickoff prompts,
and claude-monitor reset-time parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
import shutil
import subprocess
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
RESET_PATTERNS = [
    re.compile(r"Limit\s+resets\s+at\s*[:\-]?\s*(\d{1,2}:\d{2})\s*(a\.m\.|p\.m\.|AM|PM)", re.I),
    re.compile(r"Limit\s+resets\s+at\s*[:\-]?\s*(\d{2}):(\d{2})", re.I),
    re.compile(r"Time\s*to\s*Reset\s*[:\-]?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", re.I),
    re.compile(r"Time\s*to\s*Reset\s*[:\-]?\s*(\d+)\s*h\s*(\d+)?\s*m", re.I),
]

_STATUS_CACHE: tuple[float, dict] | None = None


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def _safe_zoneinfo(timezone_name: str | None) -> ZoneInfo:
    name = (timezone_name or "").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def parse_reset_time(
    text: str,
    *,
    timezone_name: str = "UTC",
    now: datetime | None = None,
) -> datetime | None:
    """Parse claude-monitor reset output into an aware datetime."""
    output = strip_ansi(text)
    tz = _safe_zoneinfo(timezone_name)
    current = now.astimezone(tz) if now is not None else datetime.now(tz)

    match = RESET_PATTERNS[0].search(output)
    if match:
        clock_text = match.group(1)
        am_pm = match.group(2).replace("a.m.", "AM").replace("p.m.", "PM").upper()
        reset_time = datetime.strptime(f"{clock_text} {am_pm}", "%I:%M %p").time()
        target = current.replace(
            hour=reset_time.hour,
            minute=reset_time.minute,
            second=0,
            microsecond=0,
        )
        if target <= current:
            target += timedelta(days=1)
        return target

    match = RESET_PATTERNS[1].search(output)
    if match:
        target = current.replace(
            hour=int(match.group(1)),
            minute=int(match.group(2)),
            second=0,
            microsecond=0,
        )
        if target <= current:
            target += timedelta(days=1)
        return target

    match = RESET_PATTERNS[2].search(output)
    if match:
        return current + timedelta(
            hours=int(match.group(1)),
            minutes=int(match.group(2)),
            seconds=int(match.group(3) or 0),
        )

    match = RESET_PATTERNS[3].search(output)
    if match:
        return current + timedelta(
            hours=int(match.group(1)),
            minutes=int(match.group(2) or 0),
        )
    return None


def run_command(cmd: list[str], *, timeout: int = 30) -> CommandResult:
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(1, timeout),
            check=False,
        )
        return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")
    except Exception as exc:
        return CommandResult(1, "", str(exc))


def detect_claude_code() -> dict:
    claude_path = shutil.which("claude") or ""
    errors: list[str] = []

    claude_version = ""
    if claude_path:
        result = run_command([claude_path, "--version"], timeout=8)
        if result.returncode == 0:
            claude_version = strip_ansi((result.stdout or result.stderr).strip())
        else:
            errors.append((result.stderr or result.stdout or "claude --version failed").strip())
    else:
        errors.append("claude executable not found on PATH")

    from external.acp_profiles import command
    try:
        command({'platform':'claude'})
        adapter_available = True
    except RuntimeError:
        adapter_available = False
        errors.append('Run clawcross install-component acp-claude to prepare the official ACP adapter')
    available = bool(claude_path and claude_version and adapter_available)
    return {
        "available": available,
        "status": "available" if available else "unavailable",
        "claude_path": claude_path,
        "claude_version": claude_version,
        "transport": "direct",
        "adapter_available": adapter_available,
        "requires_acpx": False,
        "errors": errors,
        "checked_at": _utc_now(),
    }


def detect_claude_code_cached(*, ttl_seconds: int = 60) -> dict:
    global _STATUS_CACHE
    now = time.monotonic()
    if _STATUS_CACHE is not None and now - _STATUS_CACHE[0] < max(1, ttl_seconds):
        return dict(_STATUS_CACHE[1])
    status = detect_claude_code()
    _STATUS_CACHE = (now, dict(status))
    return status


async def probe_claude_acp(
    *, owner: str, prompt: str = "Reply only CLAUDE_ACP_OK",
    session_name: str = "", timeout: int = 90,
) -> dict:
    """Use a registered, owned Agent; never create a hidden probe session."""
    from agents.store import get_store
    from agents.gateway import get_gateway
    from agents.messages import AgentMessage
    agent = get_store().get(owner, session_name)
    if agent is None or agent.platform != 'claude':
        return {'ok':False,'status':'unavailable','error':'Select an owned Claude Agent ID',
                'session_name':session_name,'probe_at':_utc_now()}
    reply = await get_gateway().ask(agent, AgentMessage(text=prompt, sender='u:'+owner),
                                    timeout=max(10,min(int(timeout or 90),600)))
    return {'ok':reply.ok,'status':'success' if reply.ok else 'failed',
            'session_name':agent.agent_id,'stdout_tail':reply.content[-4000:],
            'stderr_tail':'','error':reply.error,'probe_at':_utc_now()}


def run_claude_cli_prompt(
    *,
    prompt: str,
    model: str = "",
    timeout: int = 90,
) -> dict:
    status = detect_claude_code_cached(ttl_seconds=10)
    claude_path = str(status.get("claude_path") or "")
    if not claude_path:
        return {
            "ok": False,
            "status": "unavailable",
            "error": "claude executable not found",
            "ran_at": _utc_now(),
            "claude_code": status,
        }
    cmd = [claude_path, "-p", (prompt or "ping").strip() or "ping", "--output-format", "json"]
    if model and model.strip().lower() != "default":
        cmd.extend(["--model", model.strip()])
    result = run_command(cmd, timeout=max(10, min(int(timeout or 90), 600)))
    return {
        "ok": result.returncode == 0,
        "status": "success" if result.returncode == 0 else "failed",
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-2000:],
        "returncode": result.returncode,
        "ran_at": _utc_now(),
        "claude_code": status,
    }
