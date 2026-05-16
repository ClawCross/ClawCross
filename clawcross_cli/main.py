#!/usr/bin/env python3
"""
ClawCross CLI entrypoint — model, team, workflow, skill, cron.

Invoked by ``scripts/clawcross`` bash wrapper (or directly via
``python3 -m clawcross_cli.main``).

Subcommands:
  model [list|show|use|add|remove|migrate|<name>]
  team [<name>]
  workflow [show <name> | run <name> team <T> question <Q>]
  skill [<agent>]
  cron [<team>]
"""

from __future__ import annotations

import sys

from clawcross_cli.model_cmd import handle_model_command


def usage() -> None:
    print("Usage: clawcross <model|team|workflow|skill|cron> [...]")
    print()
    print("  model                       interactive picker / list")
    print("  model list                  list configured profiles")
    print("  model show                  show active profile")
    print("  model use <name>            switch active profile")
    print("  model add [<name>]          add a profile (interactive)")
    print("  model remove <name>         delete a profile")
    print("  model migrate               import current .env into a profile")
    print("  team [<name>]               list teams or show one team's members + alarms")
    print("  workflow                    list workflows")
    print("  workflow show <name>        show workflow YAML/py content")
    print("  workflow run <name> team <T> question <Q>")
    print("                              launch a YAML workflow")
    print("  skill [<team>]              list managed skills (personal, or team+personal)")
    print("  cron [<team>]               list cron alarms (optionally for one team)")
    print("  channel                     list chatbot channels (Telegram, Discord, ...)")
    print("  channel setup [<id>]        guided channel setup (writes <ID>_BOTS in .env)")
    print("  channel show <id>           show channel JSON entries currently in .env")
    print("  channel clear <id>          drop the env_key for a channel")
    sys.exit(2)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        usage()

    cmd = args[0].lower().strip()
    rest = args[1:]

    if cmd == "model":
        out = handle_model_command(rest, interactive=True)
        if out:
            print(out)
    elif cmd == "team":
        from clawcross_cli.display_cmd import handle_team_command
        out = handle_team_command(rest, interactive=True)
        if out:
            print(out)
    elif cmd == "workflow":
        from clawcross_cli.display_cmd import handle_workflow_command
        out = handle_workflow_command(rest, interactive=True)
        if out:
            print(out)
    elif cmd == "skill":
        from clawcross_cli.display_cmd import handle_skill_command
        out = handle_skill_command(rest, interactive=True)
        if out:
            print(out)
    elif cmd == "cron":
        from clawcross_cli.display_cmd import handle_cron_command
        out = handle_cron_command(rest, interactive=True)
        if out:
            print(out)
    elif cmd == "channel":
        from clawcross_cli.channel_cmd import handle_channel_command
        out = handle_channel_command(rest, interactive=True)
        if out:
            print(out)
    else:
        print(f"Unknown command: {cmd}")
        usage()


if __name__ == "__main__":
    main()
