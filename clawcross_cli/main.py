#!/usr/bin/env python3
"""
ClawCross CLI entrypoint — model, team, workflow, skill, cron.

Invoked by ``scripts/clawcross`` bash wrapper (or directly via
``python3 -m clawcross_cli.main``).

Subcommands:
  model [list|show|use|add|remove|migrate|<name>]
  team [list | <name> | new <name> | rename <old> <new> | delete <name> | member ...]
  workflow [show <name> | run ... | new <name> | delete <name>]
  skill [<team> | show <name> | new <name> | delete <name>]
  expert [<team> | show <team> <tag> | add ... | edit ... | delete <team> <tag>]
  cron [list [<team>] | add | delete <task_id>]
"""

from __future__ import annotations

import sys

from clawcross_cli.model_cmd import handle_model_command


def usage() -> None:
    print("Usage: clawcross <model|team|workflow|skill|expert|cron|channel> [...]")
    print()
    print("  model                       interactive picker / list")
    print("  model list                  list configured profiles")
    print("  model show                  show active profile")
    print("  model use <name>            switch active profile")
    print("  model add [<name>]          add a profile (interactive)")
    print("  model remove <name>         delete a profile")
    print("  model migrate               import current .env into a profile")
    print("  team [<name>]               list teams or show one team's members + alarms")
    print("  team new <name>             create a new team folder")
    print("  team rename <old> <new>     rename a team folder")
    print("  team delete <name>          delete a team and its internal agents")
    print("  team member add <team> ...  add an external agent member (name/global/platform)")
    print("  team member edit|remove <team> <global_name> [...]")
    print("                              update / remove an external member")
    print("  workflow                    list workflows")
    print("  workflow show <name>        show workflow YAML/py content")
    print("  workflow run <name> team <T> question <Q>")
    print("                              launch a YAML workflow")
    print("  workflow new <name>         create a new workflow (opens $EDITOR)")
    print("  workflow delete <name>      delete a workflow file ([team <T>] to scope)")
    print("  workflow-manual             print the workflowpy authoring manual")
    print("  skill [<team>]              list managed skills (personal, or team+personal)")
    print("  skill show <name>           print a skill's SKILL.md ([team <T>] to scope)")
    print("  skill new <name>            create a new SKILL.md ([team <T>] to scope)")
    print("  skill delete <name>         delete a managed skill ([team <T>] to scope)")
    print("  expert <team>               list a team's personas/experts")
    print("  expert show <team> <tag>    show one expert's full persona")
    print("  expert add <team> tag <t> name <n> persona <text...>")
    print("                              add a new team expert")
    print("  expert edit <team> <tag> [name <n>] [persona <text...>] [temp <f>]")
    print("                              update an existing expert")
    print("  expert delete <team> <tag>  remove an expert by tag")
    print("  cron                        list all cron alarms")
    print("  cron list [<team>]          list cron alarms (optionally for one team)")
    print("  cron add                    create a cron (interactive)")
    print("  cron delete <task_id>       delete a cron by task_id")
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
    elif cmd == "workflow-manual":
        from clawcross_cli.workflow_manual_cmd import handle_workflow_manual_command
        out = handle_workflow_manual_command(rest)
        if out:
            print(out)
    elif cmd == "skill":
        from clawcross_cli.display_cmd import handle_skill_command
        out = handle_skill_command(rest, interactive=True)
        if out:
            print(out)
    elif cmd == "expert":
        from clawcross_cli.display_cmd import handle_expert_command
        out = handle_expert_command(rest, interactive=True)
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
