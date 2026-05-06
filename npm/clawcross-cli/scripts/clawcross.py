#!/usr/bin/env python3
"""ClawCross Shell: a Codex-style multi-platform agent CLI."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - Windows fallback uses regular input().
    termios = None
    tty = None


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
STATE_DIR = Path(os.getenv("CLAWCROSS_STATE_DIR", Path.home() / ".clawcross"))
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
    env_path = PROJECT_ROOT / "config" / ".env"
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
DEFAULT_USER = os.getenv("CLAW_USER") or os.getenv("CLI_USER") or "admin"

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
    ("/session", "choose a session for current platform"),
    ("/session <id>", "switch session by id"),
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
    ("/session", "choose current-platform session", "/session", True),
    ("/new session", "create a new session", "/new session", True),
    ("/cwd [path]", "show or change workspace", "/cwd ", False),
    ("/mode <mode>", "set execute, plan, or review label", "/mode ", False),
    ("/exit", "quit", "/exit", True),
]
CLI_COMMANDS = [
    ("clawcross", "enter interactive shell"),
    ("clawcross run [-p platform] <prompt>", "run one prompt"),
    ("clawcross use <platform>", "persist current platform"),
    ("clawcross platforms", "list available platforms"),
    ("clawcross state", "print state json"),
    ("clawcross cancel", "cancel internal generation"),
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


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return _default_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return _default_state()
    if not isinstance(data, dict):
        return _default_state()
    default = _default_state()
    data.setdefault("version", STATE_VERSION)
    data.setdefault("current", default["current"])
    data.setdefault("platforms", {})
    data.setdefault("recent", [])
    for key, value in default["current"].items():
        data["current"].setdefault(key, value)
    return data


def _package_version() -> str:
    for path in (PROJECT_ROOT / "package.json", PROJECT_ROOT / "npm" / "clawcross-cli" / "package.json"):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        version = data.get("version")
        if isinstance(version, str) and version:
            return version
    return "dev"


def _style(text: str, color: str = ANSI_GREEN) -> str:
    if not sys.stdout.isatty() or os.getenv("NO_COLOR"):
        return text
    return f"{color}{text}{ANSI_RESET}"


def _dim(text: str) -> str:
    return _style(text, ANSI_DIM)


def _term_width() -> int:
    return max(76, min(120, shutil.get_terminal_size((100, 24)).columns))


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
        "       ████      ████",
        "    ████████████████        ○",
        "  █████  ██  ██  █████     ╱",
        "  █████    ▄     █████   □",
        "    ████████████████    ╱",
        "       ████      ████  ○",
        "",
        "         ○──□──○──□──○",
        "",
        "            ClawCross",
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
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(STATE_DIR),
        delete=False,
        prefix="state.",
        suffix=".tmp",
    ) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        tmp_name = handle.name
    os.replace(tmp_name, STATE_PATH)


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
    platform_state["session"] = current["session"]


def _set_session(state: dict, session: str) -> None:
    current = _current(state)
    platform = current.get("platform") or "internal"
    current["session"] = session or _repo_session_name(current.get("cwd"))
    state.setdefault("platforms", {}).setdefault(platform, {})["session"] = current["session"]


def _set_cwd(state: dict, cwd: str) -> None:
    path = Path(cwd).expanduser().resolve()
    current = _current(state)
    current["cwd"] = str(path)
    if not current.get("session"):
        current["session"] = _repo_session_name(str(path))


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
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    return json.loads(text) if text.strip() else {}


def _new_session_name(state: dict) -> str:
    cwd_name = _repo_session_name(_current(state).get("cwd"))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{cwd_name}-{stamp}"


def _switch_to_new_session(state: dict) -> str:
    session = _new_session_name(state)
    _set_session(state, session)
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
            query = urllib.parse.urlencode({"tool": tool})
            data = _request_json("GET", f"{FRONT_BASE}/proxy_acpx_sessions?{query}")
            raw_sessions = data.get("sessions", []) if isinstance(data, dict) else []
            sessions = []
            for row in raw_sessions:
                if not isinstance(row, dict) or row.get("closed"):
                    continue
                name = str(row.get("name") or row.get("session_id") or "").strip()
                if not name:
                    continue
                sessions.append({
                    "session": name,
                    "title": row.get("title") or row.get("cwd") or "",
                    "message_count": row.get("message_count"),
                })
            return sessions, None
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


def _print_sse_text(lines) -> bool:
    wrote = False
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
    if wrote:
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
    payload = {
        "tool": tool,
        "model": f"acp:{tool}",
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "session_id": session_id,
        "acp_session_name": session_id,
        "timeout_sec": 600,
    }
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
    print("Available platforms:")
    for name, description in KNOWN_PLATFORMS.items():
        marker = "*" if name == current.get("platform") else " "
        print(f" {marker} {name:<16} {description} [{_platform_status_line(name)}]")
    return 0


def cmd_state(_args, state: dict) -> int:
    print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"\nstate_file: {STATE_PATH}")
    return 0


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


def _prompt_label(state: dict) -> str:
    current = _current(state)
    platform = _fit(current.get("platform", "internal"), 14)
    session = _fit(current.get("session", "default"), 32)
    return f"clawcross[{platform}:{session}]> "


def _menu_lines(selected: int) -> list[str]:
    width = _term_width() - 1
    lines = [_dim("Commands")]
    for idx, (command, description, _insert, _execute) in enumerate(SLASH_MENU):
        marker = ">" if idx == selected else " "
        text = _fit(f"{marker} {_pad_display(command, 16)} {description}", width)
        lines.append(_style(text) if idx == selected else text)
    lines.append(_dim("Enter selects · ↑/↓ moves · Esc closes"))
    return lines


def _selection_menu_lines(title: str, rows: list[tuple[str, str]], selected: int) -> list[str]:
    lines = [_dim(title)]
    width = _term_width() - 1
    label_width = min(max((_display_width(label) for label, _ in rows), default=12), max(20, width // 2))
    for idx, (label, description) in enumerate(rows):
        marker = ">" if idx == selected else " "
        desc_width = max(8, width - label_width - 3)
        text = f"{marker} {_pad_display(label, label_width)} {_fit(description, desc_width)}"
        text = _fit(text, width)
        lines.append(_style(text) if idx == selected else text)
    lines.append(_dim("Enter selects · ↑/↓ moves · Esc cancels"))
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
    _set_session(state, session)
    _save_state(state)
    print(f"session: {session}")
    return True


def _choose_platform(state: dict) -> bool:
    current_platform = _current(state).get("platform", "internal")
    rows = []
    for name, description in KNOWN_PLATFORMS.items():
        status = _platform_status_line(name)
        detail = description if status in description.lower() else f"{description} ({status})"
        rows.append((name, detail))
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
    if not sys.stdin.isatty() or not sys.stdout.isatty() or termios is None or tty is None:
        return input(prompt)

    old_settings = termios.tcgetattr(sys.stdin.fileno())
    buffer = ""
    menu_open = False
    pending_escape = False
    pending_bracket = False
    selected = 0
    rendered_lines = 0

    def render() -> None:
        nonlocal rendered_lines
        if rendered_lines:
            sys.stdout.write("\r")
            if rendered_lines > 1:
                sys.stdout.write(f"\033[{rendered_lines - 1}A")
            sys.stdout.write("\033[J")
        lines = [prompt + buffer]
        if menu_open:
            lines.extend(_menu_lines(selected))
        sys.stdout.write("\n".join(lines))
        sys.stdout.flush()
        rendered_lines = len(lines)

    def finish_line() -> None:
        nonlocal rendered_lines
        if rendered_lines > 1:
            sys.stdout.write("\r")
            sys.stdout.write(f"\033[{rendered_lines - 1}A")
            sys.stdout.write("\033[J")
            sys.stdout.write(prompt + buffer)
        sys.stdout.write("\n")
        sys.stdout.flush()
        rendered_lines = 0

    try:
        tty.setcbreak(sys.stdin.fileno())
        render()
        while True:
            ch = sys.stdin.read(1)
            if pending_bracket:
                pending_bracket = False
                if menu_open and ch == "A":
                    selected = (selected - 1) % len(SLASH_MENU)
                    render()
                    continue
                if menu_open and ch == "B":
                    selected = (selected + 1) % len(SLASH_MENU)
                    render()
                    continue
            if pending_escape:
                pending_escape = False
                if ch == "[":
                    pending_bracket = True
                    continue
                if menu_open:
                    menu_open = False
                    buffer = ""
                    render()
            if ch in {"\r", "\n"}:
                if menu_open:
                    _display, _description, insert, execute_now = SLASH_MENU[selected]
                    buffer = insert
                    menu_open = False
                    render()
                    if execute_now:
                        finish_line()
                        return buffer
                    continue
                finish_line()
                return buffer
            if ch == "\x03":
                raise KeyboardInterrupt
            if ch == "\x04":
                if not buffer:
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
                    render()
                elif menu_open and seq == "[B":
                    selected = (selected + 1) % len(SLASH_MENU)
                    render()
                elif not seq:
                    pending_escape = True
                elif menu_open:
                    menu_open = False
                    buffer = ""
                    render()
                continue
            if ch in {"\x7f", "\b"}:
                if buffer:
                    buffer = buffer[:-1]
                    if not buffer:
                        menu_open = False
                    render()
                continue
            if ch == "/" and not buffer:
                buffer = "/"
                menu_open = True
                selected = 0
                render()
                continue
            if ch.isprintable():
                if menu_open:
                    menu_open = False
                buffer += ch
                render()
    finally:
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
    if name == "/help":
        print("Executable commands:")
        for command, description in SLASH_COMMANDS:
            print(f"  {command:<16} {description}")
        print("\nCLI commands:")
        for command, description in CLI_COMMANDS:
            print(f"  {command:<36} {description}")
        return True
    print(f"unknown command: {name}. Try /help.")
    return True


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
    parser.print_help()
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    raise SystemExit(main())
