"""
``clawcross channel`` — interactive channel setup for NoneBot adapters.

This module is CLI-only.  It writes one env var per channel (the
``env_key`` of each ChannelInfo) into ``~/.clawcross/config/.env``
using the JSON-array-of-bots format that the existing NoneBot bridge
already expects (e.g. ``TELEGRAM_BOTS=[{"token":"...","name":"bot1"}]``).
No backend code is touched.

Sub-commands:

  channel                   list channels with configured/not status
  channel status            same as `channel`
  channel show <id>         show the JSON entries currently in .env
  channel setup [<id>]      curses picker (or directly enter setup);
                            prints platform instructions, prompts each
                            BotField, appends to the existing JSON array
  channel clear <id>        remove the env_key for a channel
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

from clawcross_cli import channels as catalog
from clawcross_cli.channels import BotField, ChannelInfo
from clawcross_cli.picker import curses_radiolist, prompt_text


def _env_path() -> Path:
    home = Path(os.environ.get("CLAWCROSS_HOME", Path.home() / ".clawcross"))
    return home / "config" / ".env"


def _read_env() -> dict[str, str]:
    path = _env_path()
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        if k:
            values[k] = v
    return values


def _write_env(updates: dict[str, str]) -> None:
    path = _env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_env()
    existing.update(updates)
    lines = [f"{k}={_quote(v)}" for k, v in existing.items()]
    path.write_text("\n".join(lines) + "\n", "utf-8")


def _quote(value: str) -> str:
    v = str(value)
    if not v or any(c.isspace() for c in v) or any(c in v for c in "#'\""):
        return json.dumps(v, ensure_ascii=False)
    return v


def _parse_bots(raw: str) -> list[dict]:
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    return []


def _is_configured(channel: ChannelInfo, env: dict[str, str]) -> bool:
    if channel.kind == "env_vars":
        # Configured if any non-default required-looking field is set.
        for f in channel.bot_fields:
            value = (env.get(f.name) or "").strip()
            if value and (not f.default or value != f.default):
                if f.password and value:
                    return True
                if not f.password:
                    return True
        # Fall back: any of the fields differs from default
        return any((env.get(f.name) or "").strip() not in {"", f.default}
                   for f in channel.bot_fields)
    return bool(_parse_bots(env.get(channel.env_key, "")))


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return "***"
    return f"{value[:4]}…{value[-4:]}"


# ── command handlers ────────────────────────────────────────────────────────

def cmd_list() -> str:
    env = _read_env()
    lines = ["Channels:"]
    for ch in catalog.list_channels():
        emoji = (ch.emoji + " ") if ch.emoji else ""
        if ch.kind == "bots_json":
            bots = _parse_bots(env.get(ch.env_key, ""))
            marker = "✓" if bots else " "
            count = f" ({len(bots)} bot)" if bots else ""
            env_part = f"env={ch.env_key}"
            lines.append(f"  {marker} {emoji}{ch.label:<26} {env_part}{count}")
        else:
            configured = _is_configured(ch, env)
            marker = "✓" if configured else " "
            keys = ", ".join(f.name for f in ch.bot_fields[:3])
            if len(ch.bot_fields) > 3:
                keys += ", ..."
            lines.append(f"  {marker} {emoji}{ch.label:<26} env_vars: {keys}")
    lines.append("")
    lines.append("Run `clawcross channel setup <id>` to add a bot, or `clawcross channel setup` for the picker.")
    return "\n".join(lines)


def cmd_show(channel_id: str) -> str:
    ch = catalog.get_channel(channel_id)
    if ch is None:
        return f"Unknown channel: {channel_id!r}. Run `clawcross channel` to list."
    env = _read_env()
    if ch.kind == "env_vars":
        lines = [f"{ch.label} (env vars):"]
        any_set = False
        for f in ch.bot_fields:
            value = env.get(f.name, "")
            if value:
                any_set = True
                display = _mask(value) if f.password or any(p in f.name.lower()
                                                            for p in ("token", "secret", "key", "password")) else value
                lines.append(f"  {f.name}: {display}")
            else:
                lines.append(f"  {f.name}: (unset; default={f.default!r})")
        if not any_set:
            lines.append("  (nothing set yet)")
        return "\n".join(lines)
    bots = _parse_bots(env.get(ch.env_key, ""))
    lines = [f"{ch.label} ({ch.env_key}):"]
    if not bots:
        lines.append("  (no bots configured)")
        return "\n".join(lines)
    for i, bot in enumerate(bots, 1):
        lines.append(f"  Bot {i}:")
        for k, v in bot.items():
            display = _mask(v) if any(p in k.lower() for p in ("token", "secret", "key")) else v
            lines.append(f"    {k}: {display}")
    return "\n".join(lines)


def cmd_clear(channel_id: str) -> str:
    ch = catalog.get_channel(channel_id)
    if ch is None:
        return f"Unknown channel: {channel_id!r}."
    env = _read_env()
    if ch.kind == "env_vars":
        cleared = []
        updates: dict[str, str] = {}
        for f in ch.bot_fields:
            if env.get(f.name):
                updates[f.name] = ""
                cleared.append(f.name)
        if not cleared:
            return f"Channel {ch.label} is already empty."
        _write_env(updates)
        return f"Cleared {len(cleared)} env vars for {ch.label}: {', '.join(cleared)}."
    if ch.env_key not in env or not _parse_bots(env.get(ch.env_key, "")):
        return f"Channel {ch.label} is already empty."
    _write_env({ch.env_key: "[]"})
    return f"Cleared all bots for {ch.label} ({ch.env_key}=[])."


def _prompt_field(field: BotField, *, interactive: bool) -> str:
    if not interactive:
        return field.default
    label = field.prompt
    if field.default:
        label = f"{label} [{field.default}]"
    if field.help:
        print(f"   ↳ {field.help}")
    if field.password:
        try:
            value = getpass.getpass(label + ": ")
        except (KeyboardInterrupt, EOFError):
            return ""
    else:
        value = prompt_text(label + ": ")
    return (value or field.default).strip()


def _collect_bot(ch: ChannelInfo, *, interactive: bool) -> dict | None:
    bot: dict = {}
    for f in ch.bot_fields:
        value = _prompt_field(f, interactive=interactive)
        if not value and not f.default:
            if f.password:
                print(f"   {f.name} is required.")
                return None
        if value:
            bot[f.name] = value
    return bot or None


def cmd_setup(channel_id: str | None, *, interactive: bool) -> str:
    if not channel_id:
        if not interactive:
            return ("Usage: `clawcross channel setup <id>`.\n"
                    "Available: " + ", ".join(c.id for c in catalog.list_channels()))
        channels = catalog.list_channels()
        labels = [f"{(c.emoji + ' ') if c.emoji else ''}{c.label}  ({c.env_key})" for c in channels]
        labels.append("Cancel")
        idx = curses_radiolist("Configure which channel?", labels, selected=0,
                               cancel_returns=len(labels) - 1)
        if idx is None or idx >= len(channels):
            return "Cancelled."
        ch = channels[idx]
    else:
        ch = catalog.get_channel(channel_id)
        if ch is None:
            return f"Unknown channel: {channel_id!r}."

    if not interactive:
        return ("Non-interactive setup is not supported (each channel needs secrets "
                "typed by hand). Run `clawcross channel setup " + ch.id + "` from a terminal.")

    print()
    print(f"=== {ch.label} setup ===")
    if ch.setup_instructions:
        for step in ch.setup_instructions:
            print(f"  {step}")
        print()
    if ch.notes:
        print(f"Note: {ch.notes}")
        print()

    if not ch.bot_fields:
        return f"{ch.label} has no fields to prompt for — nothing to write."

    if ch.kind == "env_vars":
        updates: dict[str, str] = {}
        for f in ch.bot_fields:
            value = _prompt_field(f, interactive=True)
            if value or f.default:
                updates[f.name] = value or f.default
        if not updates:
            return "Setup cancelled (no values provided)."
        _write_env(updates)
        return f"Saved {len(updates)} env vars for {ch.label}: {', '.join(updates)}."

    bot = _collect_bot(ch, interactive=True)
    if bot is None:
        return "Setup cancelled (no value provided)."

    env = _read_env()
    bots = _parse_bots(env.get(ch.env_key, ""))
    bots.append(bot)
    _write_env({ch.env_key: json.dumps(bots, ensure_ascii=False)})
    return f"Saved 1 bot to {ch.env_key}.  Total bots: {len(bots)}."


# ── unified dispatcher ──────────────────────────────────────────────────────

_HELP = (
    "\nchannel sub-commands:\n"
    "  /cross channel                    list channels with configured/not status\n"
    "  /cross channel show <id>          inspect entries currently in .env\n"
    "  /cross channel setup [<id>]       guided setup (CLI only — needs a terminal)\n"
    "  /cross channel clear <id>         drop the env_key for a channel\n"
)


def handle_channel_command(args: list[str], *, interactive: bool = False) -> str:
    args = list(args or [])
    if not args:
        return cmd_list() + _HELP

    sub = args[0].lower()
    if sub in {"list", "ls", "status"}:
        return cmd_list()
    if sub in {"show", "info"}:
        if len(args) < 2:
            return "Usage: channel show <id>"
        return cmd_show(args[1])
    if sub in {"setup", "add", "new"}:
        return cmd_setup(args[1] if len(args) > 1 else None, interactive=interactive)
    if sub in {"clear", "remove", "rm", "delete"}:
        if len(args) < 2:
            return "Usage: channel clear <id>"
        return cmd_clear(args[1])
    if sub == "help":
        return _HELP.lstrip("\n")

    # No subcommand keyword — treat as channel id shortcut.
    ch = catalog.get_channel(sub)
    if ch is None:
        return f"Unknown command/channel: {sub!r}.{_HELP}"
    return cmd_show(sub)
