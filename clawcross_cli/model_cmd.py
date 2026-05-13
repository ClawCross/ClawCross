"""
Model and provider selection commands for ClawCross.

Exposes two high-level functions consumed by both the CLI and chatbot:
- select_model   -> interactive model picker across all providers
- select_provider -> interactive provider picker with optional base_url
- apply_model    -> write LLM_MODEL to config/.env
- apply_provider -> write LLM_PROVIDER + LLM_BASE_URL to config/.env

Also provides non-interactive direct-set helpers for scripted / one-shot use.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from clawcross_cli import models_store
from clawcross_cli.picker import curses_radiolist, prompt_text
from clawcross_cli.providers import (
    ENV_API_KEY,
    ENV_BASE_URL_KEY,
    ENV_MODEL_KEY,
    ENV_PROVIDER_KEY,
    PROVIDERS,
    ProviderInfo,
    list_providers,
    resolve_provider,
)


# ---------------------------------------------------------------------------
# .env I/O (mirrors scripts/clawcross.py helpers but self-contained)
# ---------------------------------------------------------------------------

def _find_env_file() -> Path:
    """Locate config/.env using standard ClawCross home resolution."""
    home = Path(os.environ.get("CLAWCROSS_HOME", Path.home() / ".clawcross"))
    return home / "config" / ".env"


def _read_env() -> dict[str, str]:
    path = _find_env_file()
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
    path = _find_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_env()
    existing.update(updates)
    lines = [f"{k}={_quote(v)}" for k, v in existing.items()]
    path.write_text("\n".join(lines) + "\n", "utf-8")


def _quote(v: str) -> str:
    v = str(v)
    if not v or any(c.isspace() for c in v) or any(c in v for c in "#'\""):
        import json

        return json.dumps(v, ensure_ascii=False)
    return v


# ---------------------------------------------------------------------------
# Non-interactive setters
# ---------------------------------------------------------------------------

def set_model(model: str) -> None:
    """Write LLM_MODEL to .env (no provider change)."""
    model = model.strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-.:/]+", model):
        raise ValueError(f"invalid model name: {model!r}")
    _write_env({ENV_MODEL_KEY: model})


def set_provider(provider_slug: str, base_url: str | None = None) -> None:
    """Write LLM_PROVIDER (and optionally LLM_BASE_URL) to .env."""
    info = resolve_provider(provider_slug)
    if info is None:
        valid = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown provider: {provider_slug!r}. Valid: {valid}")
    updates: dict[str, str] = {ENV_PROVIDER_KEY: info.slug}
    if base_url is not None:
        updates[ENV_BASE_URL_KEY] = base_url
    else:
        updates[ENV_BASE_URL_KEY] = info.default_base_url
    _write_env(updates)


# ---------------------------------------------------------------------------
# Current state queries
# ---------------------------------------------------------------------------

def current_model() -> str:
    """Return the currently configured model string."""
    return _read_env().get(ENV_MODEL_KEY, "")


def current_provider() -> str:
    """Return the currently configured provider slug."""
    return _read_env().get(ENV_PROVIDER_KEY, "openai")


def current_base_url() -> str:
    """Return the currently configured base URL."""
    return _read_env().get(ENV_BASE_URL_KEY, "")


# ---------------------------------------------------------------------------
# Interactive selection (console-based)
# ---------------------------------------------------------------------------

def select_provider(title: str = "Select provider:") -> ProviderInfo | None:
    """Interactive provider picker using a curses radio list.

    Appends two synthetic entries — "Custom endpoint" (prompts for a URL) and
    "Leave unchanged" (returns None).  Returns the chosen ProviderInfo, an
    ad-hoc ProviderInfo for custom URLs, or None when the user keeps current.
    """
    providers = list_providers()
    if not providers:
        print("No providers configured.", file=sys.stderr)
        return None

    labels = [f"{p.label}  — {p.description}" for p in providers]
    labels.append("Custom endpoint (enter URL manually)")
    labels.append("Leave unchanged")

    idx = curses_radiolist(title, labels, selected=0, cancel_returns=len(labels) - 1)

    # Leave unchanged
    if idx == len(labels) - 1:
        return None

    # Custom endpoint
    if idx == len(labels) - 2:
        url = prompt_text("Custom base URL: ")
        if not url:
            return None
        slug = prompt_text("Provider slug (any name): ", default="custom") or "custom"
        return ProviderInfo(
            slug=slug,
            label="Custom",
            default_base_url=url,
            models=[],
            description="user-defined",
            api_mode="chat",
        )

    return providers[idx]


def select_model(
    provider_slug: str | None = None,
    title: str = "Select model:",
) -> str | None:
    """Interactive model picker.

    If *provider_slug* is given, only that provider's models are shown.
    Otherwise the catalog is flattened across all providers (one row per
    model, suffixed with [provider_slug]).  Appends "Custom model name"
    (prompts for a free-form name) and "Leave unchanged" (returns None).
    """
    if provider_slug is not None:
        info = resolve_provider(provider_slug)
        if info is None:
            print(f"Unknown provider: {provider_slug}", file=sys.stderr)
            return None
        providers = [info]
    else:
        providers = list_providers()

    labels: list[str] = []
    model_map: list[str] = []  # parallel array of model names
    for provider in providers:
        for m in provider.models:
            if provider_slug is not None:
                labels.append(m)
            else:
                labels.append(f"{m}  [{provider.slug}]")
            model_map.append(m)

    labels.append("Custom model name (type)")
    labels.append("Leave unchanged")

    if len(labels) <= 2:
        # Only the two synthetic entries — provider has no curated catalog.
        # Fall straight to a free-form prompt.
        name = prompt_text("Model name: ")
        return name or None

    idx = curses_radiolist(title, labels, selected=0, cancel_returns=len(labels) - 1)

    # Leave unchanged
    if idx == len(labels) - 1:
        return None

    # Custom model name
    if idx == len(labels) - 2:
        name = prompt_text("Model name: ")
        return name or None

    return model_map[idx]


def apply_model_interactive(model: str | None = None) -> str:
    """Full flow: select and persist a model.

    If *model* is given, set it directly. Otherwise enter interactive picker.
    Returns the selected model name.
    """
    if model is not None:
        m = model.strip()
        set_model(m)
        print(f"LLM_MODEL={m}")
        return m

    chosen = select_model()
    if chosen is None:
        print("Model selection cancelled.", file=sys.stderr)
        return current_model()

    set_model(chosen)
    print(f"LLM_MODEL={chosen}")
    return chosen


def apply_provider_interactive(provider_slug: str | None = None, base_url: str | None = None) -> str:
    """Full flow: select provider, key, model — write all to .env at once.

    If *provider_slug* is given, set it directly. Otherwise enter interactive
    wizard: provider -> API key -> model -> write .env.
    Returns the selected provider slug.
    """
    if provider_slug is not None:
        set_provider(provider_slug, base_url)
        info = resolve_provider(provider_slug)
        if info is not None:
            print(f"LLM_PROVIDER={info.slug}")
            print(f"LLM_BASE_URL={base_url or info.default_base_url}")
        return provider_slug

    # ── Step 1: Pick provider ──
    chosen = select_provider()
    if chosen is None:
        print("Provider selection cancelled.", file=sys.stderr)
        return current_provider()

    print(f"\nDefault base URL: {chosen.default_base_url}")
    custom = prompt_text("Base URL (enter to use default): ", default="")
    url = custom if custom else chosen.default_base_url

    # ── Step 2: Enter API key ──
    import getpass
    try:
        key = getpass.getpass("API key (input hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        key = ""
    if not key:
        key = prompt_text("API key: ").strip()
    if not key:
        print("No API key entered — provider/model written, LLM_API_KEY left unchanged.", file=sys.stderr)
        key = ""  # keep existing from .env

    # ── Step 3: Pick model ──
    model = select_model(chosen.slug, f"Select model for {chosen.label}:")
    if model is None:
        model = prompt_text("Model name: ", default="").strip()
    if not model:
        print("No model selected — skipping LLM_MODEL.", file=sys.stderr)

    # ── Write all to .env ──
    updates: dict[str, str] = {
        ENV_PROVIDER_KEY: chosen.slug,
        ENV_BASE_URL_KEY: url,
    }
    if key:
        updates[ENV_API_KEY] = key
    if model:
        updates[ENV_MODEL_KEY] = model
    _write_env(updates)

    # ── Also save as a profile so /cross model use can switch later ──
    profile_name = f"{chosen.slug}-{model}".lower() if model else chosen.slug
    profile_name = re.sub(r"[^A-Za-z0-9_\-.]", "-", profile_name)[:48]
    profile = models_store.upsert_profile(
        name=profile_name,
        provider=chosen.slug,
        model=model,
        api_key=key,
        base_url=url,
        api_mode=chosen.api_mode,
        make_active=True,
    )

    masked_key = _mask_key(key) if key else "(unchanged)"
    print(f"\n  LLM_PROVIDER={chosen.slug}")
    print(f"  LLM_BASE_URL={url}")
    print(f"  LLM_API_KEY={masked_key}")
    if model:
        print(f"  LLM_MODEL={model}")
    print(f"  profile: {profile_name!r} (active)")
    print(f"  config_file: {_find_env_file()}")
    return chosen.slug


# ---------------------------------------------------------------------------
# Multi-profile subcommands (models.json)
# ---------------------------------------------------------------------------

_MODEL_SUBCMDS = {"list", "ls", "show", "use", "add", "new", "remove", "rm", "delete",
                  "edit", "migrate", "help"}


def _format_profile_row(p, *, active: bool) -> str:
    marker = "*" if active else " "
    masked = _mask_key(p.auth.api_key)
    return f" {marker} {p.name:<24} {p.provider:<10} {p.model:<32} {masked}"


def _mask_key(key: str) -> str:
    if not key:
        return "(no key)"
    if len(key) <= 10:
        return "***"
    return f"{key[:4]}…{key[-4:]}"


def cmd_list() -> str:
    store = models_store.load()
    profiles = list(store.profiles.values())
    if not profiles:
        return "No profiles configured. Use `/cross model add <name>` or `/cross model migrate`."
    lines = ["Profiles:"]
    for p in profiles:
        lines.append(_format_profile_row(p, active=(p.name == store.active)))
    return "\n".join(lines)


def cmd_catalog() -> str:
    """List the curated model catalog grouped by provider."""
    lines = ["Available models (use `/cross model <name>` to set):"]
    for provider in list_providers():
        if not provider.models:
            continue
        lines.append(f"\n[{provider.slug}] {provider.label}")
        for m in provider.models:
            lines.append(f"  {m}")
    return "\n".join(lines)


def cmd_show() -> str:
    p = models_store.get_active()
    if p is None:
        env_model = current_model() or "(unset)"
        env_provider = current_provider() or "(unset)"
        return (
            f"No active profile. Falling back to .env:\n"
            f"  LLM_MODEL={env_model}\n"
            f"  LLM_PROVIDER={env_provider}"
        )
    return (
        f"Active profile: {p.name}\n"
        f"  provider : {p.provider}\n"
        f"  model    : {p.model}\n"
        f"  base_url : {p.base_url or '(provider default)'}\n"
        f"  api_mode : {p.api_mode}\n"
        f"  api_key  : {_mask_key(p.auth.api_key)}"
    )


def cmd_use(name: str) -> str:
    try:
        p = models_store.set_active(name)
    except KeyError:
        existing = ", ".join(models_store.load().profiles) or "(none)"
        return f"Profile not found: {name!r}. Available: {existing}"
    return f"Active profile -> {p.name} ({p.provider}/{p.model})"


def cmd_remove(name: str) -> str:
    if models_store.remove_profile(name):
        new_active = models_store.load().active or "(none)"
        return f"Removed profile {name!r}. Active -> {new_active}"
    return f"Profile not found: {name!r}"


def cmd_migrate() -> str:
    """Import current config/.env into a new profile."""
    env = _read_env()
    api_key = env.get(ENV_API_KEY, "").strip()
    model = env.get(ENV_MODEL_KEY, "").strip()
    base_url = env.get(ENV_BASE_URL_KEY, "").strip()
    provider_slug = env.get(ENV_PROVIDER_KEY, "").strip().lower()

    if not (api_key or model):
        return "Nothing to migrate: .env has no LLM_API_KEY or LLM_MODEL."

    info = resolve_provider(provider_slug) if provider_slug else None
    api_mode = info.api_mode if info else "chat"
    name = f"{provider_slug or 'default'}-{model or 'unset'}".lower()
    name = re.sub(r"[^A-Za-z0-9_\-.]", "-", name)[:48]

    profile = models_store.upsert_profile(
        name=name,
        provider=provider_slug or (info.slug if info else ""),
        model=model,
        api_key=api_key,
        base_url=base_url or (info.default_base_url if info else ""),
        api_mode=api_mode,
        make_active=True,
    )
    return (
        f"Migrated .env into profile {profile.name!r} and marked active.\n"
        f"  provider={profile.provider} model={profile.model}"
    )


def cmd_add_interactive(name: str | None = None) -> str:
    """Interactive profile creation. Prompts for provider, model, api_key."""
    if not name:
        name = prompt_text("Profile name: ")
    if not name:
        return "Cancelled: empty profile name."
    if not re.fullmatch(r"[A-Za-z0-9_\-.]+", name):
        return f"Invalid profile name: {name!r} (alnum, _-. only)"

    provider_info = select_provider("Select provider:")
    if provider_info is None:
        return "Cancelled at provider selection."

    chosen_model = select_model(provider_info.slug, "Select model:")
    if chosen_model is None:
        chosen_model = prompt_text("Model name: ")
    if not chosen_model:
        return "Cancelled: empty model name."

    print(f"\nDefault base URL: {provider_info.default_base_url}")
    base_url = prompt_text("Base URL (enter for default): ", default="") or provider_info.default_base_url
    api_key = prompt_text("API key (paste): ")

    profile = models_store.upsert_profile(
        name=name,
        provider=provider_info.slug,
        model=chosen_model,
        api_key=api_key,
        base_url=base_url,
        api_mode=provider_info.api_mode,
        make_active=True,
    )
    return (
        f"Added profile {profile.name!r} and marked active.\n"
        f"  provider={profile.provider} model={profile.model}"
    )


def _model_help() -> str:
    return (
        "Usage: /cross model <subcommand>\n"
        "  list                list all profiles\n"
        "  show                show the active profile\n"
        "  use <name>          switch active profile\n"
        "  add [<name>]        add a new profile (interactive)\n"
        "  remove <name>       delete a profile\n"
        "  migrate             import current .env into a new profile\n"
        "  <name>              shorthand for `use <name>` if profile exists,\n"
        "                      else sets LLM_MODEL directly (legacy)"
    )


def handle_model_command(args: list[str], *, interactive: bool = False) -> str:
    """Unified dispatcher for /cross model and `clawcross model`.

    *interactive* must be True only when ``input()`` prompts are safe (true
    CLI invocation). The chatbot REPL passes False — there is no usable
    stdin for sub-prompts, so the dispatcher returns a usage hint instead.
    """
    if not args:
        store = models_store.load()
        sections = []
        if store.profiles:
            sections.append(cmd_list())
        sections.append(cmd_catalog())
        sections.append(_model_help())
        return "\n\n".join(sections)

    sub = args[0].lower()

    if sub in ("list", "ls"):
        return cmd_list()
    if sub == "show":
        return cmd_show()
    if sub in ("use",):
        if len(args) < 2:
            return "Usage: /cross model use <name>"
        return cmd_use(args[1])
    if sub in ("add", "new"):
        if not interactive:
            return "Interactive add not supported here — run `clawcross model add <name>` from a terminal."
        return cmd_add_interactive(args[1] if len(args) > 1 else None)
    if sub in ("remove", "rm", "delete"):
        if len(args) < 2:
            return "Usage: /cross model remove <name>"
        return cmd_remove(args[1])
    if sub == "migrate":
        return cmd_migrate()
    if sub == "help":
        return _model_help()

    name = args[0].strip()
    if models_store.get_profile(name) is not None:
        return cmd_use(name)

    active = models_store.get_active()
    if active is not None:
        models_store.upsert_profile(
            name=active.name,
            provider=active.provider,
            model=name,
            api_key=active.auth.api_key,
            base_url=active.base_url,
            api_mode=active.api_mode,
            make_active=True,
        )
        return f"Profile {active.name!r}: model -> {name}"

    set_model(name)
    return f"LLM_MODEL={name}"


def handle_provider_command(args: list[str], *, interactive: bool = False) -> str:
    """Unified dispatcher for /cross provider and `clawcross provider`.

    Interactive mode (CLI): runs wizard — provider -> API key -> model -> .env
    Non-interactive (chatbot): lists providers, or sets directly with slug.
    """
    active = models_store.get_active()

    if not args:
        if interactive:
            # Terminal CLI: run interactive wizard
            slug = apply_provider_interactive()
            return f"Configured: {slug}"
        # Chatbot: just list available providers
        lines = []
        if active is not None:
            lines.append(
                f"Active profile {active.name!r}: provider={active.provider}, "
                f"base_url={active.base_url or '(default)'}"
            )
        lines.append("\nAvailable providers (use `/cross provider <slug> [base_url]`):")
        for p in list_providers():
            lines.append(f"  {p.slug:<14} {p.label:<18} default_base_url={p.default_base_url}")
        return "\n".join(lines)

    slug = args[0].lower()
    url = args[1] if len(args) > 1 else None
    info = resolve_provider(slug)
    if info is None:
        valid = ", ".join(PROVIDERS)
        return f"Unknown provider: {slug!r}. Valid: {valid}"

    if active is None:
        set_provider(slug, url)
        return f"LLM_PROVIDER={slug}" + (f"\nLLM_BASE_URL={url}" if url else "")

    models_store.upsert_profile(
        name=active.name,
        provider=info.slug,
        model=active.model,
        api_key=active.auth.api_key,
        base_url=url or info.default_base_url,
        api_mode=info.api_mode,
        make_active=True,
    )
    return (
        f"Profile {active.name!r} updated: provider={info.slug}, "
        f"base_url={url or info.default_base_url}"
    )
