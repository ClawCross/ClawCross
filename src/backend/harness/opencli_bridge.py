"""OpenCLI bridge for the private ClawCross harness control plane.

This module intentionally shells out only to the `opencli` executable with an
argument vector. It does not run through a shell, so remote workers can ask the
local ClawCross host for logged-in browser / local CLI facts without receiving a
generic command execution primitive.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import shutil
import subprocess
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_OUTPUT_CHARS = 20000
MAX_TIMEOUT_SECONDS = 300
MAX_OUTPUT_CHARS = 200000


OPENCLI_EXTERNAL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "wx",
        "package": "wx-cli",
        "binary": "wx",
        "description": "WeChat local data CLI: sessions, messages, search, contacts, export",
        "homepage": "https://github.com/jackwener/wx-cli",
        "tags": ["wechat", "messaging", "search", "export", "ai-agent"],
        "example": ["wx", "search", "OpenCLI"],
    },
    {
        "name": "wecom-cli",
        "package": "企业微信",
        "binary": "wecom-cli",
        "description": "WeCom / enterprise WeChat CLI: contacts, messages, docs, calendar",
        "homepage": "https://github.com/WecomTeam/wecom-cli",
        "tags": ["wecom", "wechat-work", "collaboration", "ai-agent"],
        "example": ["wecom-cli", "msg", "list"],
    },
    {
        "name": "lark-cli",
        "binary": "lark-cli",
        "description": "Lark / Feishu CLI: messages, docs, calendar, tasks",
        "homepage": "https://github.com/larksuite/cli",
        "tags": ["lark", "feishu", "collaboration", "ai-agent"],
        "example": ["lark-cli", "calendar", "+agenda"],
    },
    {
        "name": "ntn",
        "package": "notion",
        "binary": "ntn",
        "description": "Notion CLI: pages, databases, blocks, search, comments",
        "homepage": "https://ntn.dev",
        "tags": ["notion", "notes", "knowledge", "productivity"],
        "example": ["ntn", "pages", "list"],
    },
    {
        "name": "tg",
        "package": "tg-cli",
        "binary": "tg",
        "description": "Telegram CLI: local-first sync, search, export",
        "homepage": "https://github.com/jackwener/tg-cli",
        "tags": ["telegram", "messaging", "search", "export", "ai-agent"],
        "example": ["tg", "search", "AI", "-f", "json"],
    },
    {
        "name": "discord",
        "package": "discord-cli",
        "binary": "discord",
        "description": "Discord CLI: local-first sync, search, export via SQLite",
        "homepage": "https://github.com/jackwener/discord-cli",
        "tags": ["discord", "messaging", "search", "export", "ai-agent"],
        "example": ["discord", "recent", "--channel", "general"],
    },
    {
        "name": "gh",
        "binary": "gh",
        "description": "GitHub CLI: repos, PRs, issues, releases, gists",
        "homepage": "https://cli.github.com",
        "tags": ["github", "git", "dev"],
        "example": ["gh", "pr", "list", "--limit", "5"],
    },
    {
        "name": "docker",
        "binary": "docker",
        "description": "Docker command-line interface",
        "homepage": "https://docs.docker.com/engine/reference/commandline/cli/",
        "tags": ["docker", "containers", "devops"],
        "example": ["docker", "ps"],
    },
    {
        "name": "vercel",
        "binary": "vercel",
        "description": "Vercel CLI: deploys, domains, env vars, logs",
        "homepage": "https://vercel.com/docs/cli",
        "tags": ["vercel", "deployment", "frontend", "devops"],
        "example": ["vercel", "ls"],
    },
    {
        "name": "obsidian",
        "binary": "obsidian",
        "description": "Obsidian vault management: notes, search, tags, tasks",
        "homepage": "https://obsidian.md/help/cli",
        "tags": ["notes", "knowledge", "markdown"],
        "example": ["obsidian", "search", "query=AI"],
    },
]

OPENCLI_BROWSER_CAPABILITIES: list[dict[str, Any]] = [
    {
        "name": "browser",
        "description": "Drive logged-in Chrome through OpenCLI Browser Bridge.",
        "commands": [
            "open",
            "state",
            "click",
            "type",
            "fill",
            "select",
            "keys",
            "wait",
            "get",
            "find",
            "extract",
            "frames",
            "screenshot",
            "scroll",
            "network",
            "tab list",
            "tab new",
            "tab select",
            "bind",
            "unbind",
            "verify",
            "close",
        ],
        "example": ["browser", "gmail", "bind"],
        "tags": ["browser", "chrome", "gmail", "outlook", "logged-in"],
    },
    {
        "name": "gmail-browser",
        "description": "Use browser primitives against an already logged-in Gmail tab/profile.",
        "commands": ["bind", "state", "click Search", "network", "extract", "unbind"],
        "example": ["browser", "gmail", "state"],
        "tags": ["gmail", "mail", "email", "browser"],
    },
    {
        "name": "outlook-browser",
        "description": "Use browser primitives against an already logged-in Outlook Web tab/profile.",
        "commands": ["bind", "state", "find", "extract", "network"],
        "example": ["browser", "outlook", "state"],
        "tags": ["outlook", "mail", "email", "browser"],
    },
]

_HIGH_RISK_ARGS: tuple[tuple[str, ...], ...] = (
    ("external", "register"),
    ("external", "install"),
    ("external", "uninstall"),
    ("plugin", "install"),
    ("plugin", "uninstall"),
    ("plugin", "update"),
    ("daemon", "stop"),
    ("daemon", "restart"),
)
_MUTATING_VERBS = {
    "post",
    "reply",
    "publish",
    "delete",
    "remove",
    "rm",
    "mv",
    "rename",
    "like",
    "unlike",
    "follow",
    "unfollow",
    "block",
    "unblock",
    "comment",
    "save",
    "send",
}


@dataclass(frozen=True)
class OpenCliRunResult:
    ok: bool
    returncode: int
    command: list[str]
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False
    parsed_json: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "returncode": self.returncode,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "truncated": self.truncated,
        }
        if self.parsed_json is not None:
            payload["json"] = self.parsed_json
        return payload


def _opencli_path() -> str:
    return os.getenv("OPENCLI_BIN", "").strip() or shutil.which("opencli") or ""


def _installed(binary: str) -> bool:
    return bool(shutil.which(binary))


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0:
        return "", bool(text)
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]", True


def _parse_json_maybe(text: str) -> Any | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        pass
    start_positions = [idx for idx in (raw.find("{"), raw.find("[")) if idx >= 0]
    if not start_positions:
        return None
    start = min(start_positions)
    try:
        return json.loads(raw[start:])
    except Exception:
        return None


def _matches_query(item: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    haystack = json.dumps(item, ensure_ascii=False).lower()
    return query.lower() in haystack


def _coerce_timeout(value: int | float | None) -> float:
    try:
        timeout = float(value if value is not None else DEFAULT_TIMEOUT_SECONDS)
    except Exception:
        timeout = DEFAULT_TIMEOUT_SECONDS
    return max(1.0, min(timeout, float(MAX_TIMEOUT_SECONDS)))


def _coerce_max_output(value: int | None) -> int:
    try:
        max_chars = int(value if value is not None else DEFAULT_MAX_OUTPUT_CHARS)
    except Exception:
        max_chars = DEFAULT_MAX_OUTPUT_CHARS
    return max(1000, min(max_chars, MAX_OUTPUT_CHARS))


def _is_high_risk(args: list[str]) -> bool:
    lowered = [item.lower() for item in args]
    for pattern in _HIGH_RISK_ARGS:
        if tuple(lowered[: len(pattern)]) == pattern:
            return True
    if len(lowered) >= 2 and lowered[0] not in {"browser", "list", "external", "doctor", "profile"}:
        return lowered[1] in _MUTATING_VERBS
    return False


def get_opencli_status(query: str = "") -> dict[str, Any]:
    """Return installed status plus a stable capability catalog for agents."""
    opencli = _opencli_path()
    external = []
    for item in OPENCLI_EXTERNAL_CATALOG:
        enriched = dict(item)
        enriched["installed"] = _installed(str(item.get("binary") or item.get("name") or ""))
        if _matches_query(enriched, query):
            external.append(enriched)

    browser = [dict(item) for item in OPENCLI_BROWSER_CAPABILITIES if _matches_query(item, query)]
    payload: dict[str, Any] = {
        "ok": True,
        "opencli_installed": bool(opencli),
        "opencli_path": opencli,
        "install_hint": "npm install -g @jackwener/opencli",
        "doctor_command": ["opencli", "doctor"],
        "capabilities": {
            "browser": browser,
            "external_clis": external,
        },
    }
    if not opencli:
        payload["warning"] = "OpenCLI is not installed on this ClawCross host."
        return payload

    try:
        result = subprocess.run(
            [opencli, "external", "list", "-f", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        payload["external_list"] = {
            "returncode": result.returncode,
            "json": _parse_json_maybe(result.stdout),
            "stderr": result.stderr.strip()[:1000],
        }
    except Exception as exc:
        payload["external_list_error"] = str(exc)
    return payload


def run_opencli_command(
    args: list[str],
    *,
    timeout_seconds: int | float | None = None,
    max_output_chars: int | None = None,
    profile: str = "",
    allow_mutating: bool = False,
) -> dict[str, Any]:
    opencli = _opencli_path()
    if not opencli:
        raise FileNotFoundError("opencli is not installed; run: npm install -g @jackwener/opencli")
    clean_args = [str(item) for item in (args or []) if str(item).strip()]
    if not clean_args:
        raise ValueError("args is required, e.g. ['wx', 'search', 'keyword']")
    if clean_args[0] == "opencli":
        clean_args = clean_args[1:]
    if not clean_args:
        raise ValueError("opencli subcommand is required")
    if not allow_mutating and _is_high_risk(clean_args):
        raise PermissionError("this OpenCLI command looks mutating; pass allow_mutating=true only for an explicit user-approved action")

    timeout = _coerce_timeout(timeout_seconds)
    max_chars = _coerce_max_output(max_output_chars)
    command = [opencli, *clean_args]
    env = os.environ.copy()
    if profile:
        env["OPENCLI_PROFILE"] = profile
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _truncate(exc.stdout or "", max_chars)
        stderr, stderr_truncated = _truncate(exc.stderr or "", max_chars)
        return OpenCliRunResult(
            ok=False,
            returncode=124,
            command=command,
            stdout=stdout,
            stderr=stderr or f"OpenCLI command timed out after {timeout:g}s",
            timed_out=True,
            truncated=stdout_truncated or stderr_truncated,
        ).to_dict()

    stdout, stdout_truncated = _truncate(completed.stdout or "", max_chars)
    stderr, stderr_truncated = _truncate(completed.stderr or "", max_chars)
    return OpenCliRunResult(
        ok=completed.returncode == 0,
        returncode=int(completed.returncode),
        command=command,
        stdout=stdout,
        stderr=stderr,
        truncated=stdout_truncated or stderr_truncated,
        parsed_json=_parse_json_maybe(stdout),
    ).to_dict()
