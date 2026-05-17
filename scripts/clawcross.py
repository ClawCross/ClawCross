#!/usr/bin/env python3
"""ClawCross Shell: a Codex-style multi-platform agent CLI."""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - Windows fallback uses regular input().
    termios = None
    tty = None


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src.utils.runtime_paths import ENV_FILE, STATE_DIR, ensure_runtime_dirs
from src.utils.env_settings import read_env_all, write_env_settings
ensure_runtime_dirs()
STATE_PATH = STATE_DIR / "state.json"
STATE_VERSION = 1
APP_NAME = "ClawCross Code"


ANSI_GREEN = "\033[38;5;36m"
ANSI_DIM = "\033[2m"
ANSI_RESET = "\033[0m"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass


def _load_env() -> None:
    env_path = ENV_FILE
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_configure_stdio()
_load_env()

PORT_AGENT = int(os.getenv("PORT_AGENT", "51200"))
PORT_FRONTEND = int(os.getenv("PORT_FRONTEND", "51209"))
AGENT_BASE = f"http://127.0.0.1:{PORT_AGENT}"
FRONT_BASE = f"http://127.0.0.1:{PORT_FRONTEND}"
INTERNAL_TOKEN = os.getenv("INTERNAL_TOKEN", "")
def _resolve_default_user() -> str:
    """Pick the canonical CLI user from env, users.json, or 'admin' fallback."""
    for var in ("CLAW_USER", "CLI_USER"):
        v = (os.getenv(var) or "").strip()
        if v:
            return v
    users_json = Path(os.getenv("CLAWCROSS_HOME", str(Path.home() / ".clawcross"))) / "config" / "users.json"
    if users_json.is_file():
        try:
            data = json.loads(users_json.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return next(iter(data))
        except Exception:
            pass
    return "admin"


DEFAULT_USER = _resolve_default_user()

KNOWN_PLATFORMS = {
    "internal": "ClawCross internal agent",
    "openclaw": "OpenClaw agent via acpx",
    "codex": "ACP Codex CLI via acpx",
    "claude": "ACP Claude Code via acpx",
    "gemini": "ACP Gemini CLI via acpx",
    "aider": "ACP Aider via acpx",
    "cursor": "ACP Cursor CLI via acpx",
    "copilot": "ACP Copilot CLI via acpx",
    "droid": "ACP Droid CLI via acpx",
    "iflow": "ACP iFlow CLI via acpx",
    "kilocode": "ACP Kilo Code CLI via acpx",
    "kimi": "ACP Kimi CLI via acpx",
    "kiro": "ACP Kiro CLI via acpx",
    "opencode": "ACP OpenCode CLI via acpx",
    "pi": "ACP Pi CLI via acpx",
    "qoder": "ACP Qoder CLI via acpx",
    "qwen": "ACP Qwen CLI via acpx",
    "trae": "ACP Trae CLI via acpx",
    "acp": "Generic ACP connector",
    "http": "Generic HTTP connector",
    "temp": "Temporary connector",
    "openclaw:main": "OpenClaw main agent (planned route)",
    "team:default": "ClawCross team route (planned route)",
}
ACP_PLATFORMS = {
    "openclaw",
    "codex",
    "claude",
    "gemini",
    "aider",
    "cursor",
    "copilot",
    "droid",
    "iflow",
    "kilocode",
    "kimi",
    "kiro",
    "opencode",
    "pi",
    "qoder",
    "qwen",
    "trae",
    "claude-code",
    "gemini-cli",
}
SLASH_COMMANDS = [
    ("/use <platform>", "switch platform"),
    ("/session", "pick a session (replays last 10 messages on resume)"),
    ("/session <id>", "switch session by id (no history replay)"),
    ("/new session", "create and switch to a new session"),
    ("/cwd [path]", "show or change workspace"),
    ("/mode <mode>", "set execute, plan, or review label"),
    ("/platforms", "list agent platforms"),
    ("/state", "show persisted state"),
    ("/cancel", "cancel internal-agent generation"),
    ("/help", "show commands"),
    ("/exit", "quit"),
]
SLASH_MENU = [
    ("/platforms", "list agent platforms", "/platforms", True),
    ("/state", "show persisted state", "/state", True),
    ("/help", "show commands", "/help", True),
    ("/cancel", "cancel internal-agent generation", "/cancel", True),
    ("/use", "choose agent platform", "/use", True),
    ("/session", "pick session — resumes & replays last 10 messages", "/session", True),
    ("/new session", "create a new session", "/new session", True),
    ("/cwd [path]", "show or change workspace", "/cwd ", False),
    ("/mode <mode>", "set execute, plan, or review label", "/mode ", False),
    ("/model", "pick LLM model (curses TUI)", "/model", True),
    ("/team [<name>]", "list teams or show one team", "/team", True),
    ("/workflow", "list / show / run workflows", "/workflow", True),
    ("/skill [<team>]", "list managed skills", "/skill", True),
    ("/cron [<team>]", "list cron alarms", "/cron", True),
    ("/channel", "list / setup chatbot channels", "/channel", True),
    ("/exit", "quit", "/exit", True),
]
CLI_COMMANDS = [
    ("clawcross", "enter interactive shell"),
    ("clawcross run [-p platform] <prompt>", "run one prompt"),
    ("clawcross use <platform>", "persist current platform"),
    ("clawcross config KEY VALUE", "set a config value in config/.env"),
    ("clawcross config get KEY", "print one config value"),
    ("clawcross config list", "list configured values"),
    ("clawcross model [name]", "select/set LLM model"),
    ("clawcross team [name]", "list teams or show one team's details"),
    ("clawcross workflow [show|run ...]", "list/show/run OASIS workflows"),
    ("clawcross skill [agent]", "list skills (optionally filtered by agent)"),
    ("clawcross cron [team]", "list cron alarms (optionally for one team)"),
    ("clawcross channel [list|setup ...]", "list / interactively set up chatbot channels"),
    ("clawcross platforms", "list available platforms"),
    ("clawcross state", "print state json"),
    ("clawcross cancel", "cancel internal generation"),
]

SENSITIVE_CONFIG_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASS|COOKIE|AUTH)", re.IGNORECASE)
CHAT_SLASH_COMMANDS = [
    ("/cross help", "show this command list"),
    ("/cross platforms", "list agent platforms"),
    ("/cross use <platform>", "switch platform"),
    ("/cross session", "list sessions for current platform"),
    ("/cross session <id>", "switch session by id"),
    ("/cross new session", "create and switch to a new session"),
    ("/cross cwd [path]", "show or change workspace"),
    ("/cross mode <mode>", "set execute/plan/review"),
    ("/cross model [name]", "select/set LLM model"),
    ("/cross team [name]", "list teams or show one team's details"),
    ("/cross workflow", "list workflows (or `show <name>` / `run <name> team <T> question <Q>`)"),
    ("/cross skill [agent]", "list skills (optionally filtered by agent)"),
    ("/cross cron [team]", "list cron alarms (optionally for one team)"),
    ("/cross channel", "list configured chatbot channels (setup requires CLI)"),
    ("/cross state", "show current shell state"),
    ("/cross cancel", "cancel internal generation"),
    ("/cross front", "get a public magic link"),
    ("/cross exit", "leave /cross mode"),
]


def _repo_session_name(cwd: str | None = None) -> str:
    root = Path(cwd or os.getcwd()).resolve()
    name = root.name or "default"
    return name.replace(" ", "-")


def _default_state() -> dict:
    cwd = str(Path.cwd())
    session = _repo_session_name(cwd)
    return {
        "version": STATE_VERSION,
        "current": {
            "platform": "internal",
            "session": session,
            "user": DEFAULT_USER,
            "mode": "execute",
            "cwd": cwd,
        },
        "platforms": {
            "internal": {"session": session},
        },
        "recent": [],
    }


def _load_state(path: Path | str | None = None) -> dict:
    state_path = Path(path) if path else STATE_PATH
    if not state_path.exists():
        state = _default_state()
        state["__state_path"] = str(state_path)
        return state
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        data = _default_state()
    if not isinstance(data, dict):
        data = _default_state()
    default = _default_state()
    data.setdefault("version", STATE_VERSION)
    data.setdefault("current", default["current"])
    data.setdefault("platforms", {})
    data.setdefault("recent", [])
    for key, value in default["current"].items():
        data["current"].setdefault(key, value)
    # Migrate legacy "admin" user to the canonical user from users.json
    # when admin is not a registered account. Avoids the empty-result
    # problem when state was created before users.json was provisioned.
    cur_user = (data["current"].get("user") or "").strip()
    canonical = _resolve_default_user()
    if cur_user and cur_user != canonical:
        users_json = Path(os.getenv("CLAWCROSS_HOME", str(Path.home() / ".clawcross"))) / "config" / "users.json"
        if users_json.is_file():
            try:
                registered = json.loads(users_json.read_text(encoding="utf-8"))
                if isinstance(registered, dict) and cur_user not in registered and canonical in registered:
                    data["current"]["user"] = canonical
            except Exception:
                pass
    data["__state_path"] = str(state_path)
    return data


def _chatbot_state_path(channel: str, user_id: str) -> Path:
    raw = f"{channel or 'chat'}-{user_id or 'anonymous'}"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._") or "chat-anonymous"
    return STATE_DIR / "chatbot" / f"{safe}.json"


def load_chatbot_state(channel: str, user_id: str, username: str | None = None) -> dict:
    state = _load_state(_chatbot_state_path(channel, user_id))
    current = _current(state)
    current["user"] = username or user_id or DEFAULT_USER
    safe_session = _chat_default_session(channel, user_id)
    current["session"] = current.get("session") or safe_session
    state["__chat_channel"] = channel
    state["__chat_user_id"] = user_id
    state["__chat_default_session"] = safe_session
    return state


def _chat_default_session(channel: str, user_id: str) -> str:
    raw = f"{channel or 'chat'}-{user_id or 'anonymous'}"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")
    return safe or "chat-anonymous"


def _package_version() -> str:
    path = PROJECT_ROOT / "package.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            version = data.get("version")
            if isinstance(version, str) and version:
                return version
        except Exception:
            pass
    return "dev"


def _style(text: str, color: str = ANSI_GREEN) -> str:
    if not sys.stdout.isatty() or os.getenv("NO_COLOR"):
        return text
    return f"{color}{text}{ANSI_RESET}"


def _dim(text: str) -> str:
    return _style(text, ANSI_DIM)


def _term_width() -> int:
    return max(76, min(120, shutil.get_terminal_size((100, 24)).columns))


def _term_height() -> int:
    return max(10, shutil.get_terminal_size((100, 24)).lines)


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", str(text))


def _cell_width(ch: str) -> int:
    if not ch:
        return 0
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in {"F", "W"}:
        return 2
    return 1


def _display_width(text: str) -> int:
    return sum(_cell_width(ch) for ch in _strip_ansi(text))


def _truncate_display(text: str, width: int) -> str:
    text = _strip_ansi(text)
    if width <= 0:
        return ""
    if _display_width(text) <= width:
        return text
    if width <= 1:
        return ""
    out = []
    used = 0
    ellipsis_width = 1
    for ch in text:
        ch_width = _cell_width(ch)
        if used + ch_width + ellipsis_width > width:
            break
        out.append(ch)
        used += ch_width
    return "".join(out) + "…"


def _pad_display(text: str, width: int) -> str:
    text = _truncate_display(text, width)
    return text + " " * max(0, width - _display_width(text))


def _fit(text: str, width: int) -> str:
    return _truncate_display(str(text), width)


def _claw_logo() -> list[str]:
    return [
        "     ████    ████",
        "   ██████████████",
        "  ████ ██ ██ ████",
        "  ████   ▄   ████",
        "   ██████████████",
        "     ████    ████",
        "",
        "      ○──□──○──□",
        "",
        "        ClawCross",
    ]


def _format_command_rows(rows: list[tuple[str, str]], width: int) -> list[str]:
    cmd_width = min(max(_display_width(command) for command, _ in rows) + 2, max(36, width - 18))
    lines = []
    for command, description in rows:
        left = _pad_display(command, cmd_width)
        right_width = max(10, width - cmd_width - 1)
        lines.append(f"{left} {_fit(description, right_width)}")
    return lines


def _platform_status_line(name: str) -> str:
    if name in {"openclaw:main", "team:default"}:
        return "planned"
    tool = _acpx_tool(name)
    if tool in ACP_PLATFORMS:
        return "acpx ok" if shutil.which("acpx") else "acpx missing"
    if name in {"internal"}:
        return "ready"
    if name in {"acp", "http", "temp"}:
        return "connector"
    return "available"


def _recent_lines(state: dict, width: int) -> list[str]:
    recent = state.get("recent") or []
    if not recent:
        return ["No recent activity yet."]
    lines = []
    for item in recent[:3]:
        platform = item.get("platform", "internal")
        session = item.get("session", "default")
        cwd = item.get("cwd", "")
        lines.append(_fit(f"{platform} / {session}", width))
        if cwd:
            lines.append(_fit(cwd, width))
    return lines


def _llm_status_hint() -> str:
    """Single-line hint shown in the welcome banner about LLM configuration."""
    try:
        from clawcross_cli import models_store
        active = models_store.get_active()
        if active is not None:
            return f"LLM: {active.provider}/{active.model} (profile {active.name!r})"
    except Exception:
        pass
    model = os.environ.get("LLM_MODEL", "").strip()
    if model:
        provider = os.environ.get("LLM_PROVIDER", "").strip() or "?"
        return f"LLM: {provider}/{model} (from .env)"
    return "LLM: not configured — type /model to choose one."


def _welcome_lines(state: dict) -> list[str]:
    current = _current(state)
    width = _term_width()
    platform = current.get("platform", "internal")
    right_width = min(max(52, width - 31), 76)
    right = [
        f"{APP_NAME} v{_package_version()}",
        _fit(f"Web UI: {FRONT_BASE}", right_width),
        _fit(
            f"Platform: {platform} ({_platform_status_line(platform)}) | "
            f"Session: {current.get('session', 'default')} | User: {current.get('user', DEFAULT_USER)}",
            right_width,
        ),
        _fit(f"CWD: {current.get('cwd', Path.cwd())}", right_width),
        "Type / as the first character to choose a command.",
        "Type /help for all commands.",
        _fit(_llm_status_hint(), right_width),
    ]
    logo = _claw_logo()
    left_width = max(_display_width(line) for line in logo)
    content_width = left_width + right_width + 5
    title = f" {APP_NAME} "
    lines = [_style("╭─" + title + "─" * max(0, content_width - len(title) - 1) + "╮")]
    for idx in range(max(len(logo), len(right))):
        left = logo[idx] if idx < len(logo) else ""
        text = right[idx] if idx < len(right) else ""
        lines.append(
            "│ "
            + _pad_display(left, left_width)
            + " "
            + _style("│")
            + " "
            + _pad_display(text, right_width)
            + " │"
        )
    lines.append(_style("╰" + "─" * content_width + "╯"))
    lines.append("")
    return lines


def print_welcome(state: dict) -> None:
    print("\n".join(_welcome_lines(state)))


def _save_state(state: dict) -> None:
    state_path = Path(state.get("__state_path") or STATE_PATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {k: v for k, v in state.items() if not k.startswith("__")}
    payload = json.dumps(serializable, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(state_path.parent),
        delete=False,
        prefix="state.",
        suffix=".tmp",
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        tmp_name = handle.name
    os.replace(tmp_name, state_path)


def _current(state: dict) -> dict:
    return state.setdefault("current", _default_state()["current"])


def _set_platform(state: dict, platform: str) -> None:
    platform = (platform or "internal").strip()
    current = _current(state)
    old_platform = current.get("platform") or "internal"
    old_session = current.get("session") or _repo_session_name(current.get("cwd"))
    state.setdefault("platforms", {}).setdefault(old_platform, {})["session"] = old_session
    current["platform"] = platform
    platform_state = state.setdefault("platforms", {}).setdefault(platform, {})
    current["session"] = platform_state.get("session") or old_session
    current["session_resumed"] = False
    platform_state["session"] = current["session"]


def _set_chat_platform(state: dict, platform: str) -> None:
    platform = (platform or "internal").strip()
    current = _current(state)
    default_session = state.get("__chat_default_session") or _repo_session_name(current.get("cwd"))
    current["platform"] = platform
    current["session"] = default_session
    current["session_resumed"] = False
    state.setdefault("platforms", {}).setdefault(platform, {})["session"] = default_session
    _save_state(state)


def _set_session(state: dict, session: str, *, resumed: bool = False) -> None:
    current = _current(state)
    platform = current.get("platform") or "internal"
    current["session"] = session or _repo_session_name(current.get("cwd"))
    current["session_resumed"] = bool(resumed)
    state.setdefault("platforms", {}).setdefault(platform, {})["session"] = current["session"]


def _set_cwd(state: dict, cwd: str) -> None:
    path = Path(cwd).expanduser().resolve()
    current = _current(state)
    current["cwd"] = str(path)
    if not current.get("session"):
        current["session"] = _repo_session_name(str(path))
        current["session_resumed"] = False


def _remember_recent(state: dict) -> None:
    current = dict(_current(state))
    recent = state.setdefault("recent", [])
    item = {
        "platform": current.get("platform", "internal"),
        "session": current.get("session", "default"),
        "cwd": current.get("cwd", str(Path.cwd())),
    }
    recent[:] = [r for r in recent if not (
        r.get("platform") == item["platform"]
        and r.get("session") == item["session"]
        and r.get("cwd") == item["cwd"]
    )]
    recent.insert(0, item)
    del recent[20:]


def _headers_for_user(user: str) -> dict:
    if not INTERNAL_TOKEN:
        raise RuntimeError("INTERNAL_TOKEN is not configured. Start ClawCross or configure config/.env first.")
    return {"Authorization": f"Bearer {INTERNAL_TOKEN}:{user}"}


def _post_stream(url: str, headers: dict, data: dict, timeout: int = 600):
    body = json.dumps(data).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw_line in resp:
            yield raw_line.decode("utf-8", errors="replace")


def _request_json(method: str, url: str, headers: dict | None = None, data: dict | None = None, timeout: int = 20):
    body = json.dumps(data or {}).encode("utf-8") if data is not None else None
    hdrs = {"Accept": "application/json"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # Read the body so the caller sees the real backend error
        # (e.g. "acpx openclaw: sessions list failed: ...") instead of
        # just "HTTP Error 502: BAD GATEWAY".
        try:
            body_bytes = exc.read() or b""
        except Exception:
            body_bytes = b""
        body_text = body_bytes.decode("utf-8", errors="replace").strip()
        detail = ""
        if body_text:
            try:
                payload = json.loads(body_text)
                if isinstance(payload, dict):
                    detail = str(payload.get("error") or payload.get("message") or "").strip()
            except json.JSONDecodeError:
                detail = body_text[:200]
        if detail:
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        raise
    return json.loads(text) if text.strip() else {}


def _fetch_session_history(state: dict, session_id: str, *, limit: int = 10) -> tuple[list[dict], str | None]:
    """Fetch the tail of a session's messages for resume-replay.

    Mirrors the frontend's /proxy_session_history call. For ACP platforms,
    uses GET /proxy_acpx_session_history. Returns ([], error_str) on failure
    so callers can render silently when offline.
    """
    current = _current(state)
    platform = current.get("platform") or "internal"
    try:
        if platform == "internal":
            user = current.get("user") or DEFAULT_USER
            headers = {"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {}
            data = _request_json(
                "POST",
                f"{AGENT_BASE}/session_history",
                headers=headers,
                data={"user_id": user, "session_id": session_id},
            )
            messages = data.get("messages") if isinstance(data, dict) else None
            if not isinstance(messages, list):
                return [], None
            return messages[-limit:], None
        tool = _acpx_tool(platform)
        if ":" not in platform and tool in ACP_PLATFORMS:
            # Read directly from ~/.clawcross/data/external_agent_history/<tool>#<sid>.db
            # — bypasses acpx subprocess (which may be missing or fail) and gives
            # the full send/recv/tool stream, not acpx's short textPreview.
            from clawcross_cli.session_adapter import fetch_history_messages
            return fetch_history_messages(tool, session_id, limit=limit)
        return [], None
    except Exception as exc:
        return [], str(exc)


_HIST_COLOR_USER = "\033[38;5;39m"   # cyan-blue
_HIST_COLOR_AI = ANSI_GREEN
_HIST_COLOR_TOOL = "\033[38;5;179m"  # warm yellow


def _print_history_tail(messages: list[dict], *, max_chars: int = 400) -> None:
    """Render replayed history with turn numbers, colored labels, and
    blank-line separation. Each message stays on a single CLI row so a
    code block in an AI reply does not flood the terminal."""
    if not messages:
        return
    print(_dim("── history ──"))
    turn = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip().lower()
        content = msg.get("content") or ""
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text") or "")
            content = "".join(parts)
        text = re.sub(r"\s*\n\s*", " ⏎ ", str(content).strip())
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "…"
        tool_calls = msg.get("tool_calls") if role == "assistant" else None
        tool_call_names = [
            tc.get("name") for tc in tool_calls
            if isinstance(tc, dict) and tc.get("name")
        ] if isinstance(tool_calls, list) else []
        if not text and not tool_call_names:
            continue

        turn += 1
        if role == "user":
            label = _style("you", _HIST_COLOR_USER)
        elif role == "assistant":
            label = _style("ai", _HIST_COLOR_AI)
        elif role == "tool":
            label = _style(f"tool[{msg.get('tool_name', '')}]", _HIST_COLOR_TOOL)
        else:
            label = _dim(role or "?")
        prefix = _dim(f"[{turn}]")

        if text:
            print(f"  {prefix} {label}: {text}")
        for name in tool_call_names:
            arrow = _dim("→tool")
            print(f"  {prefix} {label}{arrow}: {name}")
        print()
    print(_dim("── end ──"))


def _new_session_name(state: dict) -> str:
    cwd_name = _repo_session_name(_current(state).get("cwd"))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{cwd_name}-{stamp}"


def _switch_to_new_session(state: dict) -> str:
    session = _new_session_name(state)
    _set_session(state, session, resumed=False)
    _save_state(state)
    return session


def _list_current_platform_sessions(state: dict) -> tuple[list[dict], str | None]:
    current = _current(state)
    platform = current.get("platform") or "internal"
    try:
        if platform == "internal":
            user = current.get("user") or DEFAULT_USER
            headers = {"X-Internal-Token": INTERNAL_TOKEN} if INTERNAL_TOKEN else {}
            data = _request_json("POST", f"{AGENT_BASE}/sessions", headers=headers, data={"user_id": user})
            raw_sessions = data.get("sessions", []) if isinstance(data, dict) else []
            sessions = []
            for row in raw_sessions:
                if not isinstance(row, dict):
                    continue
                sid = str(row.get("session_id") or row.get("id") or "").strip()
                if not sid:
                    continue
                sessions.append({
                    "session": sid,
                    "title": row.get("title") or row.get("last_message") or "",
                    "message_count": row.get("message_count"),
                })
            return sessions, None
        tool = _acpx_tool(platform)
        if ":" not in platform and tool in ACP_PLATFORMS:
            # Same source as fetch — list every session DB on disk for this tool.
            from clawcross_cli.session_adapter import list_history_sessions
            return list_history_sessions(tool)
        return [], f"Platform '{platform}' does not expose session listing yet."
    except Exception as exc:
        return [], str(exc)


def _print_session_rows(rows: list[dict], state: dict, error: str | None = None) -> None:
    current_session = _current(state).get("session", "")
    if error:
        print(f"session list unavailable: {error}")
    if not rows:
        print("No sessions found. Use /new session to create one.")
        return
    print("Sessions:")
    for row in rows:
        session = row.get("session", "")
        marker = "*" if session == current_session else " "
        title = row.get("title") or ""
        count = row.get("message_count")
        suffix = f" ({count} messages)" if isinstance(count, int) else ""
        print(f" {marker} {session:<28} {_fit(title, 44)}{suffix}")


_TOOL_COLOR = "\033[38;5;179m"   # warm yellow, matches history tool label


def _print_sse_text(lines) -> bool:
    wrote = False
    at_line_start = True
    seen_tool_ids: set[str] = set()
    for line in lines:
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        text = delta.get("content", "")
        if text:
            print(text, end="", flush=True)
            wrote = True
            at_line_start = text.endswith("\n")
            continue
        meta = delta.get("meta") if isinstance(delta, dict) else None
        if not isinstance(meta, dict):
            continue
        mtype = meta.get("type")
        # ACP route (proxy_acpx_chat): acpx_tool_start / acpx_tool_end (+title/kind/status)
        # Internal route (/v1/chat/completions): tool_start / tool_end (+name)
        is_start = mtype in ("acpx_tool_start", "tool_start")
        is_end = mtype in ("acpx_tool_end", "tool_end")
        if not (is_start or is_end):
            # ignore acpx_tool_update / acpx_trace / tools_start / tools_end / ai_start
            continue
        if not at_line_start:
            print()
            at_line_start = True
        tool_id = str(meta.get("tool_call_id") or "")
        title = (
            str(meta.get("title") or "").strip()
            or str(meta.get("name") or "").strip()
            or "tool"
        )
        if is_start:
            if tool_id and tool_id in seen_tool_ids:
                continue
            if tool_id:
                seen_tool_ids.add(tool_id)
            parts = [title]
            kind = str(meta.get("kind") or "").strip()
            status = str(meta.get("status") or "").strip()
            if kind:
                parts.append(kind)
            if status:
                parts.append(status)
            label = _style(f"→ tool[{' · '.join(parts)}]", _TOOL_COLOR)
            print(label, flush=True)
            wrote = True
        else:  # tool_end / acpx_tool_end
            print(_style(f"✓ {title}", _TOOL_COLOR), flush=True)
            wrote = True
    if wrote and not at_line_start:
        print()
    return wrote


def _run_internal(prompt: str, state: dict, *, model: str = "default") -> None:
    current = _current(state)
    user = current.get("user") or DEFAULT_USER
    session_id = current.get("session") or "default"
    payload = {
        "model": model or "default",
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "user": user,
        "session_id": session_id,
    }
    _print_sse_text(_post_stream(
        f"{AGENT_BASE}/v1/chat/completions",
        _headers_for_user(user),
        payload,
    ))


def _acpx_tool(platform: str) -> str:
    return platform.split(":", 1)[0].strip().lower()


def _run_acpx(prompt: str, state: dict, *, model: str = "default") -> None:
    current = _current(state)
    platform = current.get("platform") or "codex"
    tool = _acpx_tool(platform)
    if tool not in ACP_PLATFORMS:
        raise RuntimeError(f"Unsupported ACP platform: {platform}")
    session_id = current.get("session") or _repo_session_name(current.get("cwd"))
    # Pass the user's session name verbatim. The backend now trusts any
    # shell-safe name and forwards it to `acpx sessions ensure` (which is
    # idempotent — reuses an existing session or creates a new one).
    payload = {
        "tool": tool,
        "model": f"acp:{tool}",
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "session_id": session_id,
        "acp_session_name": session_id,
        "timeout_sec": 600,
    }
    # When the user picked an existing ACP session via /session, send the
    # strict-reuse hint so the backend errors if the session is gone instead
    # of silently creating a new one under the same name.
    if current.get("session_resumed"):
        payload["acp_session_pick"] = session_id
    _print_sse_text(_post_stream(
        f"{FRONT_BASE}/proxy_acpx_chat",
        {},
        payload,
        timeout=700,
    ))


def run_prompt(prompt: str, state: dict, *, model: str = "default") -> int:
    prompt = (prompt or "").strip()
    if not prompt:
        return 0
    current = _current(state)
    cwd = current.get("cwd") or str(Path.cwd())
    old_cwd = Path.cwd()
    platform = current.get("platform") or "internal"
    try:
        os.chdir(cwd)
        if platform == "internal":
            _run_internal(prompt, state, model=model)
        elif ":" not in platform and _acpx_tool(platform) in ACP_PLATFORMS:
            _run_acpx(prompt, state, model=model)
        else:
            print(f"Platform '{platform}' is selectable but not runnable in this MVP.")
            print("Use /use to pick a runnable platform.")
            return 2
        _remember_recent(state)
        _save_state(state)
        return 0
    except KeyboardInterrupt:
        print("\nInterrupted. Use /cancel to request server-side cancellation.", file=sys.stderr)
        return 130
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Connection failed: {exc.reason}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        os.chdir(old_cwd)


def cmd_platforms(_args, state: dict) -> int:
    current = _current(state)
    names = list(KNOWN_PLATFORMS)
    name_width = max(_display_width(name) for name in names)
    name_col_width = min(max(name_width, 12), 18)
    print("Available platforms")
    print("┌───┬" + "─" * (name_col_width + 2) + "┐")
    for name in KNOWN_PLATFORMS:
        marker = "•" if name == current.get("platform") else " "
        print("│ " + marker + " │ " + _pad_display(name, name_col_width) + " │")
    print("└───┴" + "─" * (name_col_width + 2) + "┘")
    return 0


def cmd_state(_args, state: dict) -> int:
    serializable = {k: v for k, v in state.items() if not k.startswith("__")}
    print(json.dumps(serializable, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"\nstate_file: {state.get('__state_path') or STATE_PATH}")
    return 0


def _mask_config_value(key: str, value: str) -> str:
    if not value:
        return ""
    if SENSITIVE_CONFIG_RE.search(key):
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}...{value[-4:]}"
    return value


def _set_config_value(key: str, value: str) -> None:
    key = key.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise ValueError(f"invalid config key: {key!r}")
    ensure_runtime_dirs()
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_env_settings(str(ENV_FILE), {key: value})


def cmd_config(args, _state: dict) -> int:
    action = args.config_action
    if action == "list":
        values = read_env_all(str(ENV_FILE))
        if not values:
            print(f"No config values found in {ENV_FILE}")
            return 0
        for key in sorted(values):
            print(f"{key}={_mask_config_value(key, values[key])}")
        print(f"\nconfig_file: {ENV_FILE}")
        return 0
    if action == "get":
        values = read_env_all(str(ENV_FILE))
        value = values.get(args.key)
        if value is None:
            print(f"{args.key} is not set")
            return 1
        print(f"{args.key}={_mask_config_value(args.key, value)}")
        return 0
    if action == "set":
        value = " ".join(args.value or [])
        try:
            _set_config_value(args.key, value)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        os.environ[args.key] = value
        print(f"{args.key}={_mask_config_value(args.key, value)}")
        print(f"config_file: {ENV_FILE}")
        return 0
    print("usage: clawcross config [list|get KEY|set KEY VALUE|KEY VALUE]")
    return 2


def cmd_use(args, state: dict) -> int:
    _set_platform(state, args.platform)
    _save_state(state)
    current = _current(state)
    print(f"platform: {current['platform']}")
    print(f"session: {current['session']}")
    return 0


def cmd_run(args, state: dict) -> int:
    if args.platform:
        _set_platform(state, args.platform)
    if args.session:
        _set_session(state, args.session)
    if args.user:
        _current(state)["user"] = args.user
    if args.cwd:
        _set_cwd(state, args.cwd)
    if args.mode:
        _current(state)["mode"] = args.mode
    prompt = " ".join(args.prompt or []).strip()
    return run_prompt(prompt, state, model=args.model or "default")


def cmd_cancel(args, state: dict) -> int:
    current = _current(state)
    user = args.user or current.get("user") or DEFAULT_USER
    session_id = args.session or current.get("session") or "default"
    payload = {"user_id": user, "session_id": session_id}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{AGENT_BASE}/cancel",
        data=body,
        headers={"Content-Type": "application/json", "X-Internal-Token": INTERNAL_TOKEN},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            print(resp.read().decode("utf-8", errors="replace"))
        return 0
    except Exception as exc:
        print(f"cancel failed: {exc}", file=sys.stderr)
        return 1


def cmd_update(args, _state: dict) -> int:
    target = "clawcross@latest" if not args.version else f"clawcross@{args.version}"
    npm_bin = "npm.cmd" if sys.platform == "win32" else "npm"
    cmd = [npm_bin, "install", "-g", target]
    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=False)
    except FileNotFoundError:
        print("npm not found in PATH. Install Node.js first: https://nodejs.org", file=sys.stderr)
        return 127
    if result.returncode != 0:
        print(
            "Update failed. If this is a permission error, retry with sudo or "
            "use a Node version manager (nvm/fnm) so global installs land in your home directory.",
            file=sys.stderr,
        )
        return result.returncode
    print(f"Updated to {target}. Re-run 'clawcross --version' to confirm.")
    return 0


def _prompt_label(state: dict) -> str:
    current = _current(state)
    platform = _fit(current.get("platform", "internal"), 14)
    session = _fit(current.get("session", "default"), 32)
    return f"clawcross[{platform}:{session}]> "


def _menu_lines(selected: int) -> list[str]:
    """Render the slash menu as a viewport — capped to fit inside the terminal.

    Budget: terminal_height - 4 rows (prompt + header + footer + breathing).
    The viewport scrolls so the selected row stays inside it.
    """
    width = _term_width() - 1
    total = len(SLASH_MENU)
    budget = max(4, _term_height() - 4)
    visible = min(total, budget)

    if total <= visible:
        first = 0
    else:
        first = max(0, min(total - visible, selected - visible // 2))

    lines = [_dim("Commands")]
    for idx in range(first, first + visible):
        command, description, _insert, _execute = SLASH_MENU[idx]
        marker = ">" if idx == selected else " "
        text = _fit(f"{marker} {_pad_display(command, 16)} {description}", width)
        lines.append(_style(text) if idx == selected else text)
    pos = f"{selected + 1}/{total}"
    if total > visible:
        scroll = "↕"
        if first == 0:
            scroll = "↓"
        elif first + visible >= total:
            scroll = "↑"
        lines.append(_dim(f"Enter selects · ↑/↓ moves · Esc closes  ·  {pos} {scroll}"))
    else:
        lines.append(_dim(f"Enter selects · ↑/↓ moves · Esc closes  ·  {pos}"))
    return lines


def _selection_menu_lines(title: str, rows: list[tuple[str, str]], selected: int) -> list[str]:
    """Render the menu with a scrolling viewport so it always fits the screen.

    Without a viewport, a 60+ row list would overflow the terminal, and the
    in-place redraw on each ↑/↓ press (\\033[nA + \\033[J) could not reach
    rows that had scrolled off the top — old and new frames would overlap.
    """
    width = _term_width() - 1
    total = len(rows)
    budget = max(4, _term_height() - 4)  # title + footer + breathing
    visible = min(total, budget)

    if total <= visible:
        first = 0
    else:
        first = max(0, min(total - visible, selected - visible // 2))
    last = first + visible

    def _one_line(s: str) -> str:
        # Collapse any embedded newlines so a single row occupies exactly
        # one terminal line. Otherwise the in-place redraw (\033[nA + \033[J)
        # counts logical lines while the terminal sees more, leaving an
        # un-erased ghost of the previous frame after every ↓ keypress.
        return re.sub(r"\s*\n\s*", " ⏎ ", str(s or ""))

    window_rows = [(_one_line(label), _one_line(desc)) for label, desc in rows[first:last]]
    label_width = min(
        max((_display_width(label) for label, _ in window_rows), default=12),
        max(20, width // 2),
    )

    lines = [_dim(title)]
    for offset, (label, description) in enumerate(window_rows):
        idx = first + offset
        marker = ">" if idx == selected else " "
        desc_width = max(8, width - label_width - 3)
        text = f"{marker} {_pad_display(label, label_width)} {_fit(description, desc_width)}"
        text = _fit(text, width)
        lines.append(_style(text) if idx == selected else text)

    pos = f"{selected + 1}/{total}"
    if total > visible:
        if first == 0:
            scroll = "↓"
        elif last >= total:
            scroll = "↑"
        else:
            scroll = "↕"
        lines.append(_dim(f"Enter selects · ↑/↓ moves · Esc cancels  ·  {pos} {scroll}"))
    else:
        lines.append(_dim(f"Enter selects · ↑/↓ moves · Esc cancels  ·  {pos}"))
    return lines


def _choose_from_menu(title: str, rows: list[tuple[str, str]]) -> int | None:
    if not rows:
        return None
    if not sys.stdin.isatty() or not sys.stdout.isatty() or termios is None or tty is None:
        return None

    old_settings = termios.tcgetattr(sys.stdin.fileno())
    selected = 0
    rendered_lines = 0
    pending_escape = False
    pending_bracket = False

    def render() -> None:
        nonlocal rendered_lines
        if rendered_lines:
            sys.stdout.write("\r")
            if rendered_lines > 1:
                sys.stdout.write(f"\033[{rendered_lines - 1}A")
            sys.stdout.write("\033[J")
        lines = _selection_menu_lines(title, rows, selected)
        sys.stdout.write("\n".join(lines))
        sys.stdout.flush()
        rendered_lines = len(lines)

    def clear() -> None:
        nonlocal rendered_lines
        if rendered_lines:
            sys.stdout.write("\r")
            if rendered_lines > 1:
                sys.stdout.write(f"\033[{rendered_lines - 1}A")
            sys.stdout.write("\033[J")
            sys.stdout.flush()
            rendered_lines = 0

    try:
        tty.setcbreak(sys.stdin.fileno())
        # Explicitly disable ECHO — tty.setcbreak does not turn it off on
        # older Python versions, so arrow-key escape sequences (\x1b[A/B)
        # get echoed mid-frame and the menu visually duplicates itself.
        attrs = termios.tcgetattr(sys.stdin.fileno())
        attrs[3] &= ~termios.ECHO  # lflag
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attrs)
        render()
        while True:
            ch = sys.stdin.read(1)
            if pending_bracket:
                pending_bracket = False
                if ch == "A":
                    selected = (selected - 1) % len(rows)
                    render()
                    continue
                if ch == "B":
                    selected = (selected + 1) % len(rows)
                    render()
                    continue
            if pending_escape:
                pending_escape = False
                if ch == "[":
                    pending_bracket = True
                    continue
                clear()
                return None
            if ch in {"\r", "\n"}:
                clear()
                return selected
            if ch == "\x03":
                clear()
                return None
            seq = ""
            if ch == "\x1b":
                for _ in range(2):
                    ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                    if not ready:
                        break
                    seq += sys.stdin.read(1)
                if not seq:
                    pending_escape = True
                    continue
            if seq == "[A":
                selected = (selected - 1) % len(rows)
                render()
            elif seq == "[B":
                selected = (selected + 1) % len(rows)
                render()
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)


def _choose_session(state: dict) -> bool:
    sessions, error = _list_current_platform_sessions(state)
    rows: list[tuple[str, str]] = [("<new session>", "create and switch to a new session")]
    rows.extend(
        (row.get("session", ""), str(row.get("title") or ""))
        for row in sessions
        if row.get("session")
    )
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        _print_session_rows(sessions, state, error)
        return True
    if error:
        print(f"session list unavailable: {error}")
    selected = _choose_from_menu("Sessions", rows)
    if selected is None:
        return True
    if selected == 0:
        session = _switch_to_new_session(state)
        print(f"session: {session}")
        return True
    session = rows[selected][0]
    _set_session(state, session, resumed=True)
    _save_state(state)
    print(f"session: {session} (resumed)")
    history, hist_err = _fetch_session_history(state, session, limit=10)
    if hist_err:
        print(f"history unavailable: {hist_err}")
    else:
        _print_history_tail(history)
    return True


def _choose_platform(state: dict) -> bool:
    current_platform = _current(state).get("platform", "internal")
    rows = []
    for name in KNOWN_PLATFORMS:
        rows.append((name, ""))
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        cmd_platforms(None, state)
        return True
    selected = _choose_from_menu("Platforms", rows)
    if selected is None:
        return True
    platform = rows[selected][0]
    _set_platform(state, platform)
    _save_state(state)
    current = _current(state)
    marker = " unchanged" if platform == current_platform else ""
    print(f"platform: {current['platform']}{marker}")
    print(f"session: {current['session']}")
    return True


def _read_interactive_line(prompt: str) -> str:
    """Read one line with a rounded-box prompt and optional slash menu.

    Layout on the main screen:

        ╭─── ClawCross ────────────────────╮
        │ clawcross[codex:ClawCross]> _    │
        ╰──────────────────────────────────╯

    The cursor lives inside the middle line. Typing redraws the middle
    line in place. Pressing `/` as the first char opens a slash-menu
    popup in the alternate screen buffer (no main-screen clear, so no
    blank-rows ghost effect after closing).
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty() or termios is None or tty is None:
        return input(prompt)

    old_settings = termios.tcgetattr(sys.stdin.fileno())
    buffer = ""
    menu_open = False
    pending_escape = False
    pending_bracket = False
    selected = 0
    box_width = max(40, min(_term_width(), 120))
    inner_width = box_width - 4  # "│ " ... " │"

    def _truncate(text: str, w: int) -> str:
        # Show the tail when the input exceeds the inner box width so the
        # cursor stays visible at the right edge.
        if _display_width(text) <= w:
            return text
        # Drop chars from the front until it fits.
        result = text
        while _display_width(result) > w and len(result) > 1:
            result = result[1:]
        return result

    def render_input() -> None:
        # Cursor is somewhere on the middle line. Clear it, redraw, and
        # leave the cursor right after the buffer text (inside the box).
        content = _truncate(prompt + buffer, inner_width)
        pad = inner_width - _display_width(content)
        sys.stdout.write("\r\033[K")
        sys.stdout.write("│ " + content + " " * pad + " │")
        # Position cursor right after content (column 2 + display_width).
        sys.stdout.write("\r")
        sys.stdout.write(f"\033[{2 + _display_width(content)}C")
        sys.stdout.flush()

    def draw_box() -> None:
        # Draw the three-line box and park the cursor on the middle line.
        horiz_top = "─" * (box_width - 2)
        horiz_bot = "─" * (box_width - 2)
        sys.stdout.write(f"╭{horiz_top}╮\n")
        sys.stdout.write(f"│{' ' * (box_width - 2)}│\n")
        sys.stdout.write(f"╰{horiz_bot}╯")
        # Move cursor up to middle line.
        sys.stdout.write("\033[1A\r")
        sys.stdout.flush()
        render_input()

    def render_menu() -> None:
        if not menu_open:
            return
        sys.stdout.write("\033[H\033[2J")
        sys.stdout.write(prompt + buffer + "\n\n")
        sys.stdout.write("\n".join(_menu_lines(selected)))
        sys.stdout.flush()

    def open_menu() -> None:
        nonlocal menu_open
        if menu_open:
            return
        menu_open = True
        sys.stdout.write("\033[?1049h\033[?25l")
        sys.stdout.flush()
        render_menu()

    def close_menu(*, restore_input: bool = True) -> None:
        nonlocal menu_open
        if not menu_open:
            return
        menu_open = False
        sys.stdout.write("\033[?1049l\033[?25h")
        sys.stdout.flush()
        if restore_input:
            render_input()

    def finish_line() -> None:
        if menu_open:
            close_menu()
        # Move cursor down past the bottom border before the trailing \n
        # so subsequent output appears below the box.
        sys.stdout.write("\033[1B\n")
        sys.stdout.flush()

    try:
        tty.setcbreak(sys.stdin.fileno())
        draw_box()

        while True:
            ch = sys.stdin.read(1)
            if pending_bracket:
                pending_bracket = False
                if menu_open and ch == "A":
                    selected = (selected - 1) % len(SLASH_MENU)
                    render_menu()
                    continue
                if menu_open and ch == "B":
                    selected = (selected + 1) % len(SLASH_MENU)
                    render_menu()
                    continue
            if pending_escape:
                pending_escape = False
                if ch == "[":
                    pending_bracket = True
                    continue
                if menu_open:
                    buffer = ""
                    close_menu()
            if ch in {"\r", "\n"}:
                if menu_open:
                    _display, _description, insert, execute_now = SLASH_MENU[selected]
                    buffer = insert
                    close_menu()
                    if execute_now:
                        finish_line()
                        return buffer
                    continue
                finish_line()
                return buffer
            if ch == "\x03":
                # bash-style: clear current line on Ctrl+C; exit only when
                # the buffer is already empty.
                if menu_open:
                    close_menu(restore_input=False)
                    buffer = ""
                    finish_line()
                    draw_box()
                    continue
                if buffer:
                    buffer = ""
                    finish_line()
                    sys.stdout.write("^C\n")
                    sys.stdout.flush()
                    draw_box()
                    continue
                finish_line()
                raise EOFError
            if ch == "\x04":
                if not buffer:
                    if menu_open:
                        close_menu(restore_input=False)
                    raise EOFError
                continue
            if ch == "\x1b":
                seq = ""
                for _ in range(2):
                    ready, _, _ = select.select([sys.stdin], [], [], 0.2)
                    if not ready:
                        break
                    seq += sys.stdin.read(1)
                if menu_open and seq == "[A":
                    selected = (selected - 1) % len(SLASH_MENU)
                    render_menu()
                elif menu_open and seq == "[B":
                    selected = (selected + 1) % len(SLASH_MENU)
                    render_menu()
                elif menu_open and seq in {"[5~"}:
                    selected = max(0, selected - 8)
                    render_menu()
                elif menu_open and seq in {"[6~"}:
                    selected = min(len(SLASH_MENU) - 1, selected + 8)
                    render_menu()
                elif not seq:
                    pending_escape = True
                elif menu_open:
                    buffer = ""
                    close_menu()
                continue
            if ch in {"\x7f", "\b"}:
                if buffer:
                    buffer = buffer[:-1]
                    if not buffer and menu_open:
                        close_menu()
                    elif not menu_open:
                        render_input()
                continue
            if ch == "/" and not buffer:
                buffer = "/"
                render_input()
                selected = 0
                open_menu()
                continue
            if ch.isprintable():
                if menu_open:
                    close_menu()
                buffer += ch
                render_input()
    finally:
        if menu_open:
            sys.stdout.write("\033[?1049l\033[?25h")
            sys.stdout.flush()
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)


def _handle_slash(command: str, state: dict) -> bool:
    parts = command.strip().split()
    if not parts:
        return True
    name = parts[0].lower()
    if name in {"/exit", "/quit", "/q"}:
        _save_state(state)
        return False
    if name == "/platforms":
        cmd_platforms(None, state)
        return True
    if name == "/state":
        cmd_state(None, state)
        return True
    if name == "/use":
        if len(parts) < 2:
            return _choose_platform(state)
        else:
            _set_platform(state, parts[1])
            _save_state(state)
            current = _current(state)
            print(f"platform: {current['platform']}")
            print(f"session: {current['session']}")
        return True
    if name == "/new" and len(parts) >= 2 and parts[1].lower() == "session":
        session = _switch_to_new_session(state)
        print(f"session: {session}")
        return True
    if name == "/session":
        if len(parts) == 1:
            return _choose_session(state)
        else:
            _set_session(state, parts[1])
            _save_state(state)
            print(f"session: {_current(state)['session']}")
        return True
    if name == "/cwd":
        if len(parts) == 1:
            print(_current(state).get("cwd", str(Path.cwd())))
        else:
            _set_cwd(state, " ".join(parts[1:]))
            _save_state(state)
            print(f"cwd: {_current(state)['cwd']}")
        return True
    if name == "/mode":
        if len(parts) == 1:
            print(_current(state).get("mode", "execute"))
        else:
            _current(state)["mode"] = parts[1]
            _save_state(state)
            print(f"mode: {_current(state)['mode']}")
        return True
    if name == "/cancel":
        class CancelArgs:
            user = ""
            session = ""
        cmd_cancel(CancelArgs(), state)
        return True
    if name == "/model":
        from clawcross_cli.model_cmd import handle_model_command
        out = handle_model_command(parts[1:], interactive=True)
        if out:
            print(out)
        return True
    current_user = (state.get("current", {}).get("user") or "").strip() or None
    if name == "/team":
        from clawcross_cli.display_cmd import handle_team_command
        out = handle_team_command(parts[1:], interactive=True, user=current_user)
        if out:
            print(out)
        return True
    if name == "/workflow":
        from clawcross_cli.display_cmd import handle_workflow_command
        out = handle_workflow_command(parts[1:], interactive=True, user=current_user)
        if out:
            print(out)
        return True
    if name == "/skill":
        from clawcross_cli.display_cmd import handle_skill_command
        out = handle_skill_command(parts[1:], interactive=True, user=current_user)
        if out:
            print(out)
        return True
    if name == "/cron":
        from clawcross_cli.display_cmd import handle_cron_command
        out = handle_cron_command(parts[1:], interactive=True, user=current_user)
        if out:
            print(out)
        return True
    if name == "/channel":
        from clawcross_cli.channel_cmd import handle_channel_command
        out = handle_channel_command(parts[1:], interactive=True)
        if out:
            print(out)
        return True
    if name == "/help":
        print(_rich_help_text())
        return True
    print(f"unknown command: {name}. Try /help.")
    return True


def welcome_text(state: dict) -> str:
    return _strip_ansi("\n".join(_welcome_lines(state))).strip()


def _chat_state_lines(state: dict) -> list[str]:
    current = _current(state)
    return [
        f"Agent: {current.get('platform', 'internal')}",
        f"User: {current.get('user', DEFAULT_USER)}",
        f"Mode: {current.get('mode', 'execute')}",
    ]


_HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Quick start", [
        ("clawcross", "start the interactive shell"),
        ("/model", "pick an LLM (curses TUI: ↑↓ / PgUp / PgDn / ENTER)"),
        ("/use codex", "switch to the Codex CLI platform (any /platforms entry works)"),
        ("type a message", "send to the active agent"),
    ]),
    ("LLM configuration", [
        ("/model", "interactive picker — choose model + provider in one go"),
        ("/model gpt-4o", "set directly (writes .env or updates the active profile)"),
        ("/model list", "list saved profiles in ~/.clawcross/config/models.json"),
        ("/model show", "show the active profile (provider/model/base_url/api_key)"),
        ("/model use <profile>", "switch which profile is active"),
        ("/model add <profile>", "create a new profile (CLI: prompts; chatbot: rejected)"),
        ("/model migrate", "import current .env into a new profile"),
    ]),
    ("Platform & session", [
        ("/platforms", "list all agent platforms (internal + acpx tools)"),
        ("/use <platform>", "switch active platform (internal / codex / claude / gemini / ...)"),
        ("/session", "interactive picker (resumes & replays last 10 messages)"),
        ("/session <name>", "switch to / create session by name (no replay)"),
        ("/new session", "create timestamped session (e.g. ClawCross-20260512-031544)"),
        ("/cwd [path]", "show or change the workspace directory"),
        ("/mode <mode>", "label the run as execute / plan / review"),
        ("/cancel", "cancel an in-flight internal generation"),
    ]),
    ("Team resources", [
        ("/team", "list teams (and a usage footer)"),
        ("/team <name>", "team overview (members + alarm count) + sub-command hints"),
        ("/team <name> members", "list internal + external agents"),
        ("/team <name> personas", "list persona / expert prompts (oasis_experts.json)"),
        ("/team <name> workflows", "list team-scoped workflows"),
        ("/team <name> skills", "list team SKILL.md files"),
        ("/team <name> crons", "list team-scoped cron alarms"),
        ("/team new <name>", "create a new team folder"),
    ]),
    ("Workflows", [
        ("/workflow", "list all workflows (personal + every team, grouped)"),
        ("/workflow show <name>", "print the YAML or Python source"),
        ("/workflow show <name> team <T>", "disambiguate when the name exists in several teams"),
        ("/workflow run <name> question <text...>", "run a personal workflow"),
        ("/workflow run <name> team <T> question <text...>", "run a team workflow"),
        ("/workflow new <name> [team <T>] [from <file>]",
         "create a YAML workflow. CLI: opens $EDITOR with a template. Chatbot: needs `from <file>`."),
    ]),
    ("Skills", [
        ("/skill", "list all skills aggregated across personal + every team"),
        ("/skill <team>", "show skills scoped to one team + personal"),
        ("/skill new <name> [team <T>] [from <file>]", "create a SKILL.md (CLI: $EDITOR)"),
    ]),
    ("Cron / Alarms", [
        ("/cron", "list all cron entries (personal + all teams)"),
        ("/cron <team>", "list one team's cron entries"),
        ("/cron new team <T> target <X> [cron <expr>|once <ISO>] text <msg...>",
         "create an alarm (cron expr or one-shot ISO time)"),
    ]),
    ("Chatbot channels", [
        ("/channel", "list 17 channels with configured/not status"),
        ("/channel setup [<id>]", "guided setup (curses picker; CLI only)"),
        ("/channel show <id>", "show JSON entries / env vars currently in .env"),
        ("/channel clear <id>", "drop the env_key (bots_json) or unset env vars"),
        ("/channel login weclaw", "run `weclaw login` — QR appears in your terminal"),
        ("/channel logout weclaw", "stop the WeClaw daemon"),
        ("/channel status weclaw", "ask weclaw for live status"),
    ]),
    ("Shell", [
        ("/state", "dump persisted state.json"),
        ("/front", "get a public magic link (when frontend is reachable)"),
        ("/exit", "leave the shell"),
    ]),
]


_HELP_TIPS = [
    "Press / on an empty line to open the command picker (alt-screen, ↑↓ ENTER, Esc cancels).",
    "All `/<cmd>` commands also work as `clawcross <cmd>` and `/cross <cmd>` (chatbot).",
    "`clawcross start` boots the full backend (web UI / API on PORT_FRONTEND).",
    "Reset LLM profiles: rm ~/.clawcross/config/models.json (.env still works as fallback).",
    "Reset shell state:  rm ~/.clawcross/state.json",
]

# ── Chatbot /cross help (no interactive-only commands, no terminal tips) ──

_CHAT_HELP_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Quick start", [
        ("/cross help", "show these commands"),
        ("/cross use codex", "switch to the Codex CLI platform"),
        ("/cross use internal", "use the built-in internal agent"),
        ("Send a message", "text without /cross runs as a prompt on the active agent"),
    ]),
    ("Platform & session", [
        ("/cross platforms", "list all agent platforms"),
        ("/cross use <platform>", "switch platform (internal / codex / claude / gemini / ...)"),
        ("/cross session", "show sessions for the current platform"),
        ("/cross session <id>", "switch to / create session by id"),
        ("/cross new session", "create timestamped session"),
        ("/cross cwd [path]", "show or change workspace directory"),
        ("/cross mode <mode>", "label the run as execute / plan / review"),
        ("/cross cancel", "cancel an in-flight internal generation"),
    ]),
    ("Model & LLM", [
        ("/cross model", "list saved model profiles"),
        ("/cross model show", "show the active profile"),
        ("/cross model use <name>", "switch active profile"),
        ("/cross model <model>", "set LLM model directly"),
    ]),
    ("Team resources", [
        ("/cross team", "list teams"),
        ("/cross team <name>", "team overview (members + alarm count)"),
        ("/cross team <name> members", "list internal + external agents"),
        ("/cross team <name> personas", "list persona / expert prompts"),
        ("/cross team <name> workflows", "list team-scoped workflows"),
        ("/cross team <name> skills", "list team SKILL.md files"),
        ("/cross team <name> crons", "list team-scoped cron alarms"),
    ]),
    ("Workflows", [
        ("/cross workflow", "list all workflows"),
        ("/cross workflow show <name>", "print the YAML or Python source"),
        ("/cross workflow show <name> team <T>", "disambiguate across teams"),
        ("/cross workflow run <name> question <text...>", "run a personal workflow"),
        ("/cross workflow run <name> team <T> question <text...>", "run a team workflow"),
    ]),
    ("Skills", [
        ("/cross skill", "list all skills"),
        ("/cross skill <team>", "show skills scoped to one team"),
    ]),
    ("Cron / Alarms", [
        ("/cross cron", "list all cron entries"),
        ("/cross cron <team>", "list one team's cron entries"),
    ]),
    ("Chatbot channels", [
        ("/cross channel", "list channels with configured/not status"),
        ("/cross channel show <id>", "show current channel config"),
        ("/cross channel clear <id>", "drop the channel config"),
        ("/cross channel login weclaw", "run `weclaw login` (QR code)"),
        ("/cross channel logout weclaw", "stop the WeClaw daemon"),
        ("/cross channel status weclaw", "ask weclaw for live status"),
    ]),
    ("Shell", [
        ("/cross state", "show current platform and session"),
        ("/cross front", "get a public magic link"),
        ("/cross exit", "leave cross shell (return to normal chat)"),
    ]),
]

_CHAT_HELP_TIPS = [
    "All commands use the /cross prefix in chatbot (e.g. /cross use codex).",
    "Send any message without /cross to run it as a prompt on the active agent.",
    "Send /cross front for a public magic link (web UI login).",
    "Send /cross exit (or /cross off / /exit / /quit) to leave cross shell.",
    "Some commands (model add, channel setup) need terminal — use `clawcross` CLI.",
]


def _rich_help_text() -> str:
    """Categorised /help output with one example per command + tips."""
    out: list[str] = []
    for section_title, rows in _HELP_SECTIONS:
        out.append(_style(section_title))
        col = max(len(label) for label, _ in rows)
        col = min(max(col, 18), 56)
        for label, desc in rows:
            pad = " " * max(2, col - len(label) + 2)
            out.append(f"  {label}{pad}{desc}")
        out.append("")
    out.append(_style("Tips"))
    for tip in _HELP_TIPS:
        out.append(f"  • {tip}")
    return "\n".join(out)


def chat_help_text() -> str:
    """Chatbot-flavoured help: /cross-prefixed commands, no interactive-only features."""
    out: list[str] = ["Commands:", ""]
    for section_title, rows in _CHAT_HELP_SECTIONS:
        out.append(section_title)
        col = max(len(label) for label, _ in rows)
        col = min(max(col, 18), 56)
        for label, desc in rows:
            pad = " " * max(2, col - len(label) + 2)
            out.append(f"  {label}{pad}{desc}")
        out.append("")
    out.append("Tips")
    for tip in _CHAT_HELP_TIPS:
        out.append(f"  \u2022 {tip}")
    return "\n".join(out)


def chat_welcome_text(state: dict, magic_link: str | None = None) -> str:
    lines = [
        *_claw_logo(),
        "",
        f"{APP_NAME} v{_package_version()}",
        "Cross shell is on.",
        "",
        *_chat_state_lines(state),
        "",
        "Switch agents with /cross use codex.",
        "Try /cross use claude or /cross use gemini.",
        "Send a message to run it.",
        "Send /cross help for commands.",
        "Send /cross front for a public magic link.",
        "Send /cross exit to leave.",
    ]
    if magic_link:
        lines.extend([
            "",
            "Magic link:",
            magic_link,
        ])
    return "\n".join(lines)


def handle_chatbot_input(text: str, state: dict) -> tuple[bool, str]:
    """Handle one ClawCross shell input line for non-terminal chat channels.

    Returns (active, reply). active becomes False when /exit or /quit is used.
    """
    line = (text or "").strip()
    if not line:
        return True, ""
    lower = line.lower()
    if lower.startswith("/cross "):
        line = "/" + line.split(maxsplit=1)[1].strip()
        lower = line.lower()
    elif lower.startswith("/cli "):
        line = "/" + line.split(maxsplit=1)[1].strip()
        lower = line.lower()
    out = io.StringIO()
    active = True
    if lower in {"help", "/help"}:
        return True, chat_help_text()
    if line.startswith("/") and line.split(maxsplit=1)[0].lower() == "/use":
        parts = line.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                cmd_platforms(None, state)
            table = _strip_ansi(out.getvalue()).strip()
            return True, f"```\n{table}\n```"
        platform = parts[1].strip().split()[0]
        _set_chat_platform(state, platform)
        current = _current(state)
        return True, (
            f"Agent switched to {current.get('platform', platform)}.\n"
            "Send a message to continue on this agent."
        )
    if line.startswith("/") and line.split(maxsplit=1)[0].lower() == "/session":
        parts = line.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            rows, error = _list_current_platform_sessions(state)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                _print_session_rows(rows, state, error)
            table = _strip_ansi(out.getvalue()).strip()
            return True, f"```\n{table}\n```"
        session = parts[1].strip().split()[0]
        _set_session(state, session)
        _save_state(state)
        return True, f"session: {_current(state)['session']}"
    if line.startswith("/") and line.split(maxsplit=1)[0].lower() == "/model":
        from clawcross_cli.model_cmd import handle_model_command
        rest = line.split(maxsplit=1)
        args = rest[1].strip().split() if len(rest) > 1 else []
        return True, handle_model_command(args) or ""
    current_user = (state.get("current", {}).get("user") or "").strip() or None
    if line.startswith("/") and line.split(maxsplit=1)[0].lower() == "/team":
        from clawcross_cli.display_cmd import handle_team_command
        rest = line.split(maxsplit=1)
        args = rest[1].strip().split() if len(rest) > 1 else []
        return True, handle_team_command(args, user=current_user) or ""
    if line.startswith("/") and line.split(maxsplit=1)[0].lower() == "/workflow":
        from clawcross_cli.display_cmd import handle_workflow_command
        rest = line.split(maxsplit=1)
        args = rest[1].strip().split() if len(rest) > 1 else []
        return True, handle_workflow_command(args, user=current_user) or ""
    if line.startswith("/") and line.split(maxsplit=1)[0].lower() == "/skill":
        from clawcross_cli.display_cmd import handle_skill_command
        rest = line.split(maxsplit=1)
        args = rest[1].strip().split() if len(rest) > 1 else []
        return True, handle_skill_command(args, user=current_user) or ""
    if line.startswith("/") and line.split(maxsplit=1)[0].lower() == "/cron":
        from clawcross_cli.display_cmd import handle_cron_command
        rest = line.split(maxsplit=1)
        args = rest[1].strip().split() if len(rest) > 1 else []
        return True, handle_cron_command(args, user=current_user) or ""
    if line.startswith("/") and line.split(maxsplit=1)[0].lower() == "/channel":
        from clawcross_cli.channel_cmd import handle_channel_command
        rest = line.split(maxsplit=1)
        args = rest[1].strip().split() if len(rest) > 1 else []
        return True, handle_channel_command(args) or ""
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        if line.startswith("/"):
            active = _handle_slash(line, state)
        else:
            run_prompt(line, state)
    reply = _strip_ansi(out.getvalue()).strip()
    return active, reply


def repl(state: dict) -> int:
    print_welcome(state)
    while True:
        try:
            line = _read_interactive_line(_prompt_label(state))
        except EOFError:
            print()
            _save_state(state)
            return 0
        except KeyboardInterrupt:
            print()
            continue
        if not line.strip():
            continue
        if line.lstrip().startswith("/"):
            if not _handle_slash(line, state):
                return 0
            continue
        run_prompt(line, state)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clawcross",
        description="ClawCross Shell: Codex-style multi-platform agent CLI",
    )
    parser.add_argument(
        "-V", "--version", action="version",
        version=f"%(prog)s {_package_version()}",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run one prompt on the current or selected platform")
    run.add_argument("prompt", nargs="*", help="Prompt text")
    run.add_argument("-p", "--platform", help="Platform, e.g. internal, codex, claude")
    run.add_argument("-s", "--session", help="Session id")
    run.add_argument("-u", "--user", help="User id")
    run.add_argument("--cwd", help="Workspace directory")
    run.add_argument("--mode", choices=["execute", "plan", "review"], help="Runtime mode label")
    run.add_argument("-m", "--model", help="Model name for internal route")

    use = sub.add_parser("use", help="Persist the current platform")
    use.add_argument("platform", help="Platform name")

    sub.add_parser("platforms", help="List known platforms")
    sub.add_parser("state", help="Show persisted shell state")
    sub.add_parser("chat", help="Enter interactive shell")

    cancel = sub.add_parser("cancel", help="Cancel current internal-agent generation")
    cancel.add_argument("-s", "--session", help="Session id")
    cancel.add_argument("-u", "--user", help="User id")

    update = sub.add_parser(
        "update",
        help="Upgrade the global clawcross npm package; does not restart running services",
    )
    update.add_argument(
        "version", nargs="?", default=None,
        help="Specific version (e.g. 0.0.2). Defaults to latest.",
    )

    config = sub.add_parser("config", help="Read or write config/.env values")
    config.add_argument("items", nargs="*", help="list | get KEY | set KEY VALUE | KEY VALUE")

    model = sub.add_parser("model", help="Manage LLM model profiles (list/show/use/add/remove/migrate)")
    model.add_argument("args", nargs="*", help="subcommand and arguments")

    team = sub.add_parser("team", help="List teams (or show one team's members and alarms)")
    team.add_argument("args", nargs="*", help="<team-name>")

    workflow = sub.add_parser("workflow", help="List/show/run OASIS workflows")
    workflow.add_argument("args", nargs="*", help="[show <name> | run <name> team <T> question <Q>]")

    skill = sub.add_parser("skill", help="List skills exposed by OpenClaw agents")
    skill.add_argument("args", nargs="*", help="[<agent>]")

    cron = sub.add_parser("cron", help="List cron alarms (optionally filtered by team)")
    cron.add_argument("args", nargs="*", help="[<team>]")

    channel = sub.add_parser("channel", help="List / setup chatbot channels (Telegram, Discord, ...)")
    channel.add_argument("args", nargs="*", help="[list|status|show <id>|setup [<id>]|clear <id>]")

    return parser


def main() -> int:
    state = _load_state()
    parser = build_parser()
    if len(sys.argv) == 1:
        return repl(state)
    args = parser.parse_args()
    if args.command == "run":
        return cmd_run(args, state)
    if args.command == "use":
        return cmd_use(args, state)
    if args.command == "platforms":
        return cmd_platforms(args, state)
    if args.command == "state":
        return cmd_state(args, state)
    if args.command == "chat":
        return repl(state)
    if args.command == "cancel":
        return cmd_cancel(args, state)
    if args.command == "update":
        return cmd_update(args, state)
    if args.command == "config":
        items = list(args.items or [])
        if not items or items[0] == "list":
            args.config_action = "list"
            args.key = ""
            args.value = []
        elif items[0] == "get" and len(items) == 2:
            args.config_action = "get"
            args.key = items[1]
            args.value = []
        elif items[0] == "set" and len(items) >= 3:
            args.config_action = "set"
            args.key = items[1]
            args.value = items[2:]
        elif len(items) >= 2:
            args.config_action = "set"
            args.key = items[0]
            args.value = items[1:]
        else:
            args.config_action = "usage"
            args.key = ""
            args.value = []
        return cmd_config(args, state)
    if args.command == "model":
        from clawcross_cli.model_cmd import handle_model_command
        out = handle_model_command(list(args.args or []), interactive=True)
        if out:
            print(out)
        return 0
    if args.command == "team":
        from clawcross_cli.display_cmd import handle_team_command
        out = handle_team_command(list(args.args or []), interactive=True)
        if out:
            print(out)
        return 0
    if args.command == "workflow":
        from clawcross_cli.display_cmd import handle_workflow_command
        out = handle_workflow_command(list(args.args or []), interactive=True)
        if out:
            print(out)
        return 0
    if args.command == "skill":
        from clawcross_cli.display_cmd import handle_skill_command
        out = handle_skill_command(list(args.args or []), interactive=True)
        if out:
            print(out)
        return 0
    if args.command == "cron":
        from clawcross_cli.display_cmd import handle_cron_command
        out = handle_cron_command(list(args.args or []), interactive=True)
        if out:
            print(out)
        return 0
    if args.command == "channel":
        from clawcross_cli.channel_cmd import handle_channel_command
        out = handle_channel_command(list(args.args or []), interactive=True)
        if out:
            print(out)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    raise SystemExit(main())
