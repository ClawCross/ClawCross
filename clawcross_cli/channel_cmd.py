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
import socket
import subprocess
import sys
from pathlib import Path

from clawcross_cli import channels as catalog
from clawcross_cli.channels import BotField, ChannelInfo
from clawcross_cli.picker import curses_radiolist, prompt_text
from src.utils.env_settings import read_env_all, write_env_settings
from src.utils.runtime_paths import PID_DIR


def _env_path() -> Path:
    home = Path(os.environ.get("CLAWCROSS_HOME", Path.home() / ".clawcross"))
    return home / "config" / ".env"


def _read_env() -> dict[str, str]:
    return read_env_all(str(_env_path()))


def _write_env(updates: dict[str, str]) -> None:
    path = _env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_env_settings(str(path), updates)


def _request_chatbot_restart() -> None:
    try:
        PID_DIR.mkdir(parents=True, exist_ok=True)
        (PID_DIR / "chatbot_restart_flag").write_text("restart", "utf-8")
    except Exception:
        pass


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


def _channel_env_keys(channel: ChannelInfo) -> list[str]:
    keys = []
    if channel.env_key:
        keys.append(channel.env_key)
    keys.extend(k for k in channel.env_aliases if k and k not in keys)
    return keys


def _read_channel_bots(channel: ChannelInfo, env: dict[str, str]) -> tuple[str, list[dict]]:
    for key in _channel_env_keys(channel):
        bots = _parse_bots(env.get(key, ""))
        if bots:
            return key, bots
    return channel.env_key, []


def _nonebot_adapter_name(channel: ChannelInfo) -> str:
    return channel.adapter or channel.id


def _is_placeholder(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return (
        lowered.startswith("your_")
        or "your_" in lowered
        or lowered in {"changeme", "change_me", "placeholder", "null", "none"}
    )


def _sync_nonebot_adapter(env: dict[str, str], adapter: str, *, enabled: bool) -> str:
    adapters = [
        item.strip()
        for item in (env.get("NONEBOT_ADAPTERS") or "").split(",")
        if item.strip()
    ]
    if enabled:
        if adapter and adapter not in adapters:
            adapters.append(adapter)
    else:
        adapters = [item for item in adapters if item != adapter]
    return ",".join(adapters)


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
    if not channel.requires_config:
        adapter_enabled = _nonebot_adapter_name(channel) in [
            item.strip()
            for item in (env.get("NONEBOT_ADAPTERS") or "").split(",")
            if item.strip()
        ]
        if not adapter_enabled:
            return False
        return all(not _is_placeholder(env.get(key)) for key in channel.required_env)
    return bool(_read_channel_bots(channel, env)[1])


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
            _env_key, bots = _read_channel_bots(ch, env)
            configured = _is_configured(ch, env)
            marker = "✓" if configured else " "
            if ch.requires_config:
                count = f" ({len(bots)} bot)" if bots else ""
                env_part = f"env={ch.env_key}"
            else:
                count = " (adapter enabled)" if configured else ""
                env_part = f"adapter={_nonebot_adapter_name(ch)}"
            lines.append(f"  {marker} {emoji}{ch.label:<26} {env_part}{count}")
        else:
            configured = _is_configured(ch, env)
            marker = "✓" if configured else " "
            keys = ", ".join(f.name for f in ch.bot_fields[:3])
            if len(ch.bot_fields) > 3:
                keys += ", ..."
            extra = ""
            if ch.id == "weclaw":
                state = _weclaw_runtime_state()
                bits = [
                    f"logged_in={'yes' if state['logged_in'] else 'no'}",
                    f"running={'yes' if state['running'] else 'no'}",
                ]
                if state["accounts"]:
                    bits.append(f"accounts={len(state['accounts'])}")
                extra = " [" + ", ".join(bits) + "]"
            lines.append(f"  {marker} {emoji}{ch.label:<26} env_vars: {keys}{extra}")
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
        if ch.id == "weclaw":
            state = _weclaw_runtime_state()
            lines.append("")
            lines.append("Runtime:")
            lines.append(f"  logged_in: {'yes' if state['logged_in'] else 'no'}")
            lines.append(f"  running: {'yes' if state['running'] else 'no'}")
            lines.append(f"  accounts: {len(state['accounts'])}")
            lines.append(f"  config: {state['config_path']}")
            lines.append(f"  accounts_dir: {state['accounts_dir']}")
            lines.append(f"  proxy_port_open: {'yes' if state['proxy_port_open'] else 'no'} ({state['proxy_host']}:{state['proxy_port']})")
            if state["accounts"]:
                lines.append("  account_files:")
                lines.extend(f"    - {p.name}" for p in state["accounts"])
        return "\n".join(lines)
    active_env_key, bots = _read_channel_bots(ch, env)
    lines = [f"{ch.label} ({active_env_key or 'adapter-only'}):"]
    if not bots:
        if ch.requires_config:
            lines.append("  (no bots configured)")
        else:
            adapters = [item.strip() for item in (env.get("NONEBOT_ADAPTERS") or "").split(",") if item.strip()]
            status = "enabled" if _nonebot_adapter_name(ch) in adapters else "not enabled"
            lines.append(f"  adapter: {_nonebot_adapter_name(ch)} ({status})")
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
        _request_chatbot_restart()
        return f"Cleared {len(cleared)} env vars for {ch.label}: {', '.join(cleared)}."
    updates = {}
    for key in _channel_env_keys(ch):
        if key in env and _parse_bots(env.get(key, "")):
            updates[key] = "[]"
    for f in ch.bot_fields:
        if f.target == "env" and env.get(_field_env_name(f)):
            updates[_field_env_name(f)] = ""
    next_adapters = _sync_nonebot_adapter(env, _nonebot_adapter_name(ch), enabled=False)
    if next_adapters != (env.get("NONEBOT_ADAPTERS") or ""):
        updates["NONEBOT_ADAPTERS"] = next_adapters
    if not updates:
        return f"Channel {ch.label} is already empty."
    _write_env(updates)
    _request_chatbot_restart()
    return f"Cleared {ch.label}: {', '.join(updates)}. Chatbot restart requested."


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


def _coerce_field_value(field: BotField, value: str):
    if field.field_type == "boolean":
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if field.field_type == "number" and value.strip():
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value
    return value


def _set_nested_value(target: dict, path: str, value) -> None:
    parts = [p for p in (path or "").split(".") if p]
    if not parts:
        return
    current = target
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _collect_bot(ch: ChannelInfo, *, interactive: bool) -> dict | None:
    bot: dict = {}
    for f in ch.bot_fields:
        if f.target == "env":
            continue
        value = _prompt_field(f, interactive=interactive)
        if not value and not f.default and f.required:
            print(f"   {f.name} is required.")
            return None
        if not _validate_field_value(f, value):
            print(f"   {f.invalid_message or (f.name + ' has invalid format.')}")
            return None
        if value:
            coerced = _coerce_field_value(f, value)
            if f.target in {"bot_intents", "bot_intent"}:
                intent_key = "intents" if f.target == "bot_intents" else "intent"
                bot.setdefault(intent_key, {})[f.name] = bool(coerced)
            else:
                if f.path:
                    _set_nested_value(bot, f.path, coerced)
                else:
                    bot[f.name] = coerced
    return bot or None


def _collect_env_fields(ch: ChannelInfo, *, interactive: bool) -> dict[str, str]:
    updates: dict[str, str] = {}
    for f in ch.bot_fields:
        if f.target != "env":
            continue
        value = _prompt_field(f, interactive=interactive)
        if not value and not f.default and f.required:
            print(f"   {f.name} is required.")
            return {}
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
        ch = _choose_channel_interactive("Configure which channel?")
        if ch is None:
            return "Cancelled."
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
        _request_chatbot_restart()
        return f"Saved {len(updates)} env vars for {ch.label}: {', '.join(updates)}. Chatbot restart requested."

    env_updates = _collect_env_fields(ch, interactive=True)
    current_env = _read_env()
    missing_env = [
        _field_env_name(f)
        for f in ch.bot_fields
        if f.target == "env"
        and f.required
        and _is_placeholder(env_updates.get(_field_env_name(f)) or current_env.get(_field_env_name(f)))
    ]
    if missing_env:
        return f"Setup cancelled: missing required env values: {', '.join(missing_env)}."
    bot = _collect_bot(ch, interactive=True)
    if bot is None and not env_updates and ch.requires_config:
        return "Setup cancelled (no value provided)."

    env = current_env
    bots = _read_channel_bots(ch, env)[1]
    if bot is not None and ch.env_key:
        bots.append(bot)
        env_updates[ch.env_key] = json.dumps(bots, ensure_ascii=False)
    env_updates["NONEBOT_ADAPTERS"] = _sync_nonebot_adapter(
        env,
        _nonebot_adapter_name(ch),
        enabled=True,
    )
    _write_env(env_updates)
    _request_chatbot_restart()
    if bot is None:
        return f"Saved env fields for {ch.label}: {', '.join(env_updates)}. Chatbot restart requested."
    return f"Saved 1 bot to {ch.env_key}. Total bots: {len(bots)}. Chatbot restart requested."


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


def _weclaw_config_path() -> Path:
    env = _read_env()
    raw = (env.get("WECLAW_CONFIG") or os.environ.get("WECLAW_CONFIG") or "~/.weclaw/config.json").strip()
    return Path(os.path.expanduser(raw))


def _weclaw_account_files() -> list[Path]:
    accounts_dir = _weclaw_config_path().parent / "accounts"
    if not accounts_dir.is_dir():
        return []
    return sorted(
        p for p in accounts_dir.glob("*.json")
        if not p.name.endswith(".sync.json")
    )


def _is_tcp_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _weclaw_runtime_state() -> dict[str, object]:
    env = _read_env()
    config_path = _weclaw_config_path()
    accounts_dir = config_path.parent / "accounts"
    accounts = _weclaw_account_files()
    proxy_host = (env.get("WECLAW_PROXY_HOST") or os.environ.get("WECLAW_PROXY_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    proxy_port = int((env.get("WECLAW_PROXY_PORT") or os.environ.get("WECLAW_PROXY_PORT") or "51298").strip() or "51298")
    rc, out = _weclaw_exec(["status"], stream=False, timeout=5)
    body = out or f"exit={rc}"
    return {
        "config_path": config_path,
        "accounts_dir": accounts_dir,
        "accounts": accounts,
        "logged_in": bool(accounts),
        "proxy_host": proxy_host,
        "proxy_port": proxy_port,
        "proxy_port_open": _is_tcp_port_open(proxy_host, proxy_port),
        "native_rc": rc,
        "native_body": body,
        "running": "not running" not in body.lower(),
    }


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
    state = _weclaw_runtime_state()
    body = str(state["native_body"])
    status_lines = [
        "weclaw status:",
        f"  running: {'yes' if state['running'] else 'no'}",
        f"  logged_in: {'yes' if state['logged_in'] else 'no'}",
        f"  accounts: {len(state['accounts'])}",
        f"  config: {state['config_path']}",
        f"  accounts_dir: {state['accounts_dir']}",
        f"  proxy_port_open: {'yes' if state['proxy_port_open'] else 'no'} ({state['proxy_host']}:{state['proxy_port']})",
    ]
    if state["accounts"]:
        status_lines.append("  account_files:")
        status_lines.extend(f"    - {p.name}" for p in state["accounts"])
    status_lines.append("  native:")
    status_lines.extend(f"    {line}" for line in body.splitlines() if line.strip())
    if not any(line.strip() for line in body.splitlines()):
        status_lines.append(f"    exit={state['native_rc']}")
    return "\n".join(status_lines)


def _channel_picker_label(ch: ChannelInfo) -> str:
    emoji = (ch.emoji + " ") if ch.emoji else ""
    env = _read_env()
    if ch.kind == "bots_json":
        _env_key, bots = _read_channel_bots(ch, env)
        if ch.requires_config:
            suffix = f"configured ({len(bots)} bot)" if bots else "not configured"
        else:
            suffix = "configured" if _is_configured(ch, env) else "not configured"
        return f"{emoji}{ch.label}  [{suffix}]"
    configured = _is_configured(ch, env)
    suffix = "configured" if configured else "not configured"
    if ch.id == "weclaw":
        state = _weclaw_runtime_state()
        suffix += f", logged_in={'yes' if state['logged_in'] else 'no'}, running={'yes' if state['running'] else 'no'}"
    return f"{emoji}{ch.label}  [{suffix}]"


def _choose_channel_interactive(title: str = "Choose channel") -> ChannelInfo | None:
    channels = catalog.list_channels()
    labels = [_channel_picker_label(ch) for ch in channels]
    labels.append("Cancel")
    idx = curses_radiolist(title, labels, selected=0, cancel_returns=len(labels) - 1)
    if idx is None or idx >= len(channels):
        return None
    return channels[idx]


def _channel_actions(ch: ChannelInfo) -> list[tuple[str, str]]:
    actions = [
        ("show", "Show current config and runtime summary"),
        ("setup", "Guided setup / add config"),
        ("clear", "Clear this channel config"),
    ]
    if ch.id == "weclaw":
        actions.extend([
            ("login", "Run weclaw login and show QR in terminal"),
            ("logout", "Stop WeClaw"),
            ("status", "Show native WeClaw status"),
        ])
    return actions


def _run_channel_action(action: str, channel_id: str, *, interactive: bool) -> str:
    if action == "show":
        return cmd_show(channel_id)
    if action == "setup":
        return cmd_setup(channel_id, interactive=interactive)
    if action == "clear":
        return cmd_clear(channel_id)
    if action == "login":
        return cmd_login(channel_id, interactive=interactive)
    if action == "logout":
        return cmd_logout(channel_id)
    if action == "status":
        return cmd_native_status(channel_id)
    return f"Unknown action: {action}"


def _channel_action_menu(ch: ChannelInfo, *, interactive: bool) -> str:
    actions = _channel_actions(ch)
    labels = [f"{name:<8} {desc}" for name, desc in actions]
    labels.append("Back")
    idx = curses_radiolist(
        f"{ch.label} actions",
        labels,
        selected=0,
        cancel_returns=len(labels) - 1,
        description="Select an action for this channel.",
    )
    if idx is None or idx >= len(actions):
        return "Cancelled."
    return _run_channel_action(actions[idx][0], ch.id, interactive=interactive)


_HELP = (
    "\nchannel sub-commands:\n"
    "  /cross channel                    list channels; terminal CLI opens channel picker + action menu\n"
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
        if interactive:
            ch = _choose_channel_interactive()
            if ch is None:
                return "Cancelled."
            return _channel_action_menu(ch, interactive=interactive)
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
    if interactive:
        return _channel_action_menu(ch, interactive=interactive)
    return cmd_show(sub)
