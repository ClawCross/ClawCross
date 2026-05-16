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
import re
import shutil
import subprocess
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
            value = (env.get(_field_env_name(f)) or "").strip()
            if value and (not f.default or value != f.default):
                if f.password and value:
                    return True
                if not f.password:
                    return True
        # Fall back: any of the fields differs from default
        return any((env.get(_field_env_name(f)) or "").strip() not in {"", f.default}
                   for f in channel.bot_fields)
    return bool(_parse_bots(env.get(channel.env_key, "")))


def _field_env_name(field: BotField) -> str:
    return field.env_key or field.name


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
    else:
        for i, bot in enumerate(bots, 1):
            lines.append(f"  Bot {i}:")
            for k, v in bot.items():
                display = _mask(v) if any(p in k.lower() for p in ("token", "secret", "key")) else v
                lines.append(f"    {k}: {display}")
    env_fields = [f for f in ch.bot_fields if f.target == "env"]
    if env_fields:
        lines.append("  Env fields:")
        for f in env_fields:
            key = _field_env_name(f)
            value = env.get(key, "")
            display = _mask(value) if f.password or any(p in key.lower() for p in ("token", "secret", "key", "password")) else (value or "(unset)")
            lines.append(f"    {key}: {display}")
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
    updates = {}
    if ch.env_key in env and _parse_bots(env.get(ch.env_key, "")):
        updates[ch.env_key] = "[]"
    for f in ch.bot_fields:
        if f.target == "env" and env.get(_field_env_name(f)):
            updates[_field_env_name(f)] = ""
    if not updates:
        return f"Channel {ch.label} is already empty."
    _write_env(updates)
    return f"Cleared {ch.label}: {', '.join(updates)}."


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


def _validate_field_value(field: BotField, value: str) -> bool:
    if not value or not field.pattern:
        return True
    try:
        return re.fullmatch(field.pattern, value) is not None
    except re.error:
        return True


def _collect_bot(ch: ChannelInfo, *, interactive: bool) -> dict | None:
    bot: dict = {}
    for f in ch.bot_fields:
        if f.target == "env":
            continue
        value = _prompt_field(f, interactive=interactive)
        if not value and not f.default:
            if f.password:
                print(f"   {f.name} is required.")
                return None
        if not _validate_field_value(f, value):
            print(f"   {f.invalid_message or (f.name + ' has invalid format.')}")
            return None
        if value:
            if f.target == "bot_intents":
                bot.setdefault("intents", {})[f.name] = value.strip().lower() in {"1", "true", "yes", "on"}
            else:
                bot[f.name] = value
    return bot or None


def _collect_env_fields(ch: ChannelInfo, *, interactive: bool) -> dict[str, str]:
    updates: dict[str, str] = {}
    for f in ch.bot_fields:
        if f.target != "env":
            continue
        value = _prompt_field(f, interactive=interactive)
        if not _validate_field_value(f, value):
            print(f"   {f.invalid_message or (f.name + ' has invalid format.')}")
            return {}
        if value or f.default:
            updates[_field_env_name(f)] = value or f.default
    return updates


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
            if not _validate_field_value(f, value):
                return f.invalid_message or f"{f.name} has invalid format."
            if value or f.default:
                updates[_field_env_name(f)] = value or f.default
        if not updates:
            return "Setup cancelled (no values provided)."
        _write_env(updates)
        return f"Saved {len(updates)} env vars for {ch.label}: {', '.join(updates)}."

    env_updates = _collect_env_fields(ch, interactive=True)
    bot = _collect_bot(ch, interactive=True)
    if bot is None and not env_updates:
        return "Setup cancelled (no value provided)."

    env = _read_env()
    bots = _parse_bots(env.get(ch.env_key, ""))
    if bot is not None:
        bots.append(bot)
        env_updates[ch.env_key] = json.dumps(bots, ensure_ascii=False)
    _write_env(env_updates)
    if bot is None:
        return f"Saved env fields for {ch.label}: {', '.join(env_updates)}."
    return f"Saved 1 bot to {ch.env_key}.  Total bots: {len(bots)}."


# ── unified dispatcher ──────────────────────────────────────────────────────

# ── WeClaw native CLI passthrough ───────────────────────────────────────────
#
# `weclaw login` prints an ASCII QR on stdout and waits for the user to
# scan it with WeChat; on success it writes its account file and exits.
# Forwarding the subprocess's stdio straight to the terminal lets the
# user scan without the mobile UI in the loop.
# `weclaw stop` and `weclaw status` are similarly thin — we just exec them.

def _resolve_weclaw_bin() -> tuple[str, str | None]:
    env = _read_env()
    raw = (env.get("WECLAW_BIN") or os.environ.get("WECLAW_BIN") or "weclaw").strip() or "weclaw"
    resolved = shutil.which(raw) or raw
    if not (Path(resolved).is_file() or shutil.which(resolved)):
        return resolved, f"weclaw binary not found: {resolved}. Set WECLAW_BIN via `/channel setup weclaw`."
    return resolved, None


def _weclaw_exec(args: list[str], *, stream: bool, timeout: int | None = None) -> tuple[int, str]:
    """Run weclaw <args>. When *stream* is True, stdio is inherited so
    the user sees output live (used for `login`). Otherwise stdout is
    captured and returned."""
    bin_path, err = _resolve_weclaw_bin()
    if err:
        return 1, err
    cmd = [bin_path, *args]
    try:
        if stream:
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL)
            return proc.returncode, ""
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout,
        )
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return proc.returncode, out
    except FileNotFoundError:
        return 1, f"weclaw binary not on PATH (tried {bin_path})."
    except subprocess.TimeoutExpired:
        return 1, f"weclaw {' '.join(args)} timed out."


def cmd_login(channel_id: str, *, interactive: bool) -> str:
    ch = catalog.get_channel(channel_id)
    if ch is None or ch.id != "weclaw":
        return f"`channel login` is only supported for weclaw (got {channel_id!r})."
    if not interactive:
        return "Run `clawcross channel login weclaw` from a terminal — the QR has to render on your screen."
    print("Launching `weclaw login` — the QR will appear below.")
    print("Scan it with WeChat to authorize, or Ctrl-C to cancel.\n")
    rc, _ = _weclaw_exec(["login"], stream=True)
    if rc == 0:
        return "WeClaw login completed. Run `clawcross channel status weclaw` to verify."
    if rc == 130:  # Ctrl-C
        return "Login cancelled."
    return f"weclaw login exited with code {rc}. Re-run if the QR expired."


def cmd_logout(channel_id: str) -> str:
    ch = catalog.get_channel(channel_id)
    if ch is None or ch.id != "weclaw":
        return f"`channel logout` is only supported for weclaw (got {channel_id!r})."
    rc, out = _weclaw_exec(["stop"], stream=False, timeout=10)
    if rc == 0:
        return "WeClaw stopped." + (f"\n{out}" if out else "")
    return f"weclaw stop failed (exit={rc}).\n{out}" if out else f"weclaw stop failed (exit={rc})."


def cmd_native_status(channel_id: str) -> str:
    ch = catalog.get_channel(channel_id)
    if ch is None or ch.id != "weclaw":
        return f"Native status is only supported for weclaw (got {channel_id!r})."
    rc, out = _weclaw_exec(["status"], stream=False, timeout=5)
    body = out or f"exit={rc}"
    return f"weclaw status:\n{body}"


_HELP = (
    "\nchannel sub-commands:\n"
    "  /cross channel                    list channels with configured/not status\n"
    "  /cross channel show <id>          inspect entries currently in .env\n"
    "  /cross channel setup [<id>]       guided setup (CLI only — needs a terminal)\n"
    "  /cross channel clear <id>         drop the env_key for a channel\n"
    "  /cross channel login weclaw       run `weclaw login` — scan the QR in your terminal\n"
    "  /cross channel logout weclaw      run `weclaw stop`\n"
    "  /cross channel status weclaw      run `weclaw status`\n"
)


def handle_channel_command(args: list[str], *, interactive: bool = False) -> str:
    args = list(args or [])
    if not args:
        return cmd_list() + _HELP

    sub = args[0].lower()
    if sub in {"list", "ls"}:
        return cmd_list()
    if sub == "status":
        if len(args) >= 2:
            return cmd_native_status(args[1])
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
    if sub == "login":
        if len(args) < 2:
            return "Usage: channel login <id> (currently weclaw only)"
        return cmd_login(args[1], interactive=interactive)
    if sub in {"logout", "stop"}:
        if len(args) < 2:
            return "Usage: channel logout <id> (currently weclaw only)"
        return cmd_logout(args[1])
    if sub == "help":
        return _HELP.lstrip("\n")

    # No subcommand keyword — treat as channel id shortcut.
    ch = catalog.get_channel(sub)
    if ch is None:
        return f"Unknown command/channel: {sub!r}.{_HELP}"
    return cmd_show(sub)
