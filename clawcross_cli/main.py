#!/usr/bin/env python3
"""
ClawCross CLI entrypoint — model and provider management.

Invoked by ``scripts/clawcross`` bash wrapper (or directly via
``python3 -m clawcross_cli.main``).

Subcommands:
  model [<model>]
  provider [<provider> [<base_url>]]
"""

from __future__ import annotations

import sys

from clawcross_cli.model_cmd import apply_model_interactive, apply_provider_interactive
from clawcross_cli.providers import list_providers, resolve_provider


def usage():
    print("Usage: clawcross <model|provider> [...]")
    print()
    print("  model                  interactively select a model")
    print("  model <model>          set LLM_MODEL directly")
    print("  provider               interactively select a provider + base URL")
    print("  provider <slug>        set LLM_PROVIDER with default base URL")
    print("  provider <slug> <url>  set LLM_PROVIDER and LLM_BASE_URL")
    sys.exit(2)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        usage()

    cmd = args[0].lower().strip()

    if cmd == "model":
        if len(args) > 1:
            apply_model_interactive(args[1])
        else:
            apply_model_interactive()

    elif cmd == "provider":
        if len(args) > 2:
            apply_provider_interactive(args[1], args[2])
        elif len(args) > 1:
            info = resolve_provider(args[1])
            if info is None:
                print(f"Unknown provider: {args[1]}")
                print("Available:", ", ".join(sorted(p.slug for p in list_providers())))
                sys.exit(1)
            apply_provider_interactive(args[1])
        else:
            apply_provider_interactive()

    elif cmd in ("list-providers", "providers"):
        for p in list_providers():
            print(f"{p.slug:<14} {p.label}  — {p.description}")

    elif cmd in ("list-models", "models"):
        for p in list_providers():
            if not p.models:
                continue
            print(f"\n[{p.slug}] {p.label}:")
            for m in p.models:
                print(f"  {m}")

    else:
        print(f"Unknown command: {cmd}")
        usage()


if __name__ == "__main__":
    main()
