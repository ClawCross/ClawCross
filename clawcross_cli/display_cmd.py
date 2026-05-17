"""Display commands for ClawCross: team, workflow, skill, cron.

Both the CLI (``clawcross team``) and the chatbot (``/cross team``) call into
these handlers. When ``interactive=True`` and a TTY is available, list views
offer a curses picker to drill into a specific item; otherwise plain text is
returned. None of the handlers ever call ``input()`` when ``interactive`` is
``False`` — chatbot transport has no stdin.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable

from clawcross_cli import api_client
from clawcross_cli.picker import curses_radiolist


# ── shared helpers ──────────────────────────────────────────────────────────

def _is_tty() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def _format_lines(lines: Iterable[str]) -> str:
    return "\n".join(line for line in lines if line is not None)


def _pick(title: str, labels: list[str]) -> int | None:
    if not labels or not _is_tty():
        return None
    try:
        idx = curses_radiolist(title, labels, selected=0, cancel_returns=-1)
    except Exception:
        return None
    if idx is None or idx < 0:
        return None
    return idx


# ── team ────────────────────────────────────────────────────────────────────

def _format_team_list(teams: list[Any]) -> str:
    if not teams:
        return "No teams found."
    lines = [f"Teams ({len(teams)}):"]
    for team in teams:
        if isinstance(team, dict):
            name = str(team.get("name") or team.get("team") or team.get("id") or "?")
            extras = []
            for key in ("member_count", "members", "size"):
                value = team.get(key)
                if isinstance(value, int):
                    extras.append(f"{value} members")
                    break
            suffix = f"  ({', '.join(extras)})" if extras else ""
        else:
            name = str(team)
            suffix = ""
        lines.append(f"  - {name}{suffix}")
    return _format_lines(lines)


def _team_label(team: Any) -> str:
    if isinstance(team, dict):
        return str(team.get("name") or team.get("team") or team.get("id") or "?")
    return str(team)


def _format_team_detail(name: str, members_body: dict | None, alarms: list[dict]) -> str:
    lines = [f"Team: {name}"]
    if not members_body:
        lines.append("  members: (unavailable)")
    else:
        members = members_body.get("members") or []
        internal = [m for m in members if isinstance(m, dict) and m.get("type") == "oasis"]
        external = [m for m in members if isinstance(m, dict) and m.get("type") != "oasis"]
        lines.append(f"  members: {len(members)}")
        if internal:
            lines.append(f"  internal agents ({len(internal)}):")
            for m in internal:
                tag = m.get("tag") or ""
                tag_part = f" [{tag}]" if tag else ""
                lines.append(f"    - {m.get('name', '?')}{tag_part}")
        if external:
            lines.append(f"  external agents ({len(external)}):")
            for m in external:
                tag = m.get("tag") or ""
                tag_part = f" [{tag}]" if tag else ""
                lines.append(f"    - {m.get('name', '?')}{tag_part}")
        if not members:
            lines.append("    (empty)")
    if alarms:
        lines.append(f"  alarms ({len(alarms)}):")
        for a in alarms[:10]:
            target = a.get("target_name") or "?"
            sched = a.get("cron") or a.get("run_at") or ""
            text = (a.get("text") or "").splitlines()[0][:60]
            lines.append(f"    - {target}  {sched}  {text}")
    else:
        lines.append("  alarms: 0")
    return _format_lines(lines)


_TEAM_HELP = (
    "\nTeam sub-commands:\n"
    "  /cross team <name>                          overview (members + alarm count)\n"
    "  /cross team <name> members                  internal + external agents\n"
    "  /cross team <name> personas                 persona/expert prompts\n"
    "  /cross team <name> workflows                team-scoped workflows\n"
    "  /cross team <name> skills                   team-scoped skills\n"
    "  /cross team <name> crons                    team-scoped cron alarms\n"
    "  /cross team new <name>                      create a new team folder"
)


def _format_team_workflows(team: str, user: str) -> str:
    """List YAML + Python workflows scoped to *team* only."""
    items = api_client.list_workflows(user, team=team)
    if not items:
        return f"Team {team!r}: no workflows."
    lines = [f"Team {team!r} workflows ({len(items)}):"]
    for it in items:
        kind = it.get("kind") or "?"
        lines.append(f"  - [{kind}] {it.get('file', '?')}")
        desc = (it.get("description") or "").strip()
        if desc:
            lines.append(f"      {desc}")
    return _format_lines(lines)


def _format_personas(name: str, personas: list[dict]) -> str:
    if not personas:
        return f"Team {name!r}: no personas."
    lines = [f"Team {name!r} personas ({len(personas)}):"]
    for p in personas:
        tag = p.get("tag") or ""
        title = p.get("name") or "?"
        cat = p.get("category") or ""
        desc = (p.get("description") or "").strip()
        tag_part = f" [{tag}]" if tag else ""
        cat_part = f" ({cat})" if cat else ""
        lines.append(f"  - {title}{tag_part}{cat_part}")
        if desc:
            lines.append(f"      {desc}")
    return _format_lines(lines)


def _format_members(name: str, members_body: dict | None) -> str:
    if not members_body:
        return f"Team {name!r}: members unavailable."
    members = members_body.get("members") or []
    internal = [m for m in members if isinstance(m, dict) and m.get("type") == "oasis"]
    external = [m for m in members if isinstance(m, dict) and m.get("type") != "oasis"]
    lines = [f"Team {name!r} members ({len(members)}):"]
    if internal:
        lines.append(f"  internal ({len(internal)}):")
        for m in internal:
            tag = m.get("tag") or ""
            tag_part = f" [{tag}]" if tag else ""
            lines.append(f"    - {m.get('name', '?')}{tag_part}")
    if external:
        lines.append(f"  external ({len(external)}):")
        for m in external:
            tag = m.get("tag") or ""
            tag_part = f" [{tag}]" if tag else ""
            platform = m.get("platform") or ""
            plat_part = f" ({platform})" if platform else ""
            lines.append(f"    - {m.get('name', '?')}{tag_part}{plat_part}")
    if not members:
        lines.append("  (empty)")
    return _format_lines(lines)


def handle_team_command(args: list[str], *, interactive: bool = False, user: str | None = None) -> str:
    args = list(args or [])
    user = (user or api_client.DEFAULT_USER or "").strip() or api_client.DEFAULT_USER

    if args and args[0].lower() in {"new", "create", "add"}:
        if len(args) >= 2:
            name = args[1].strip()
        elif interactive:
            from clawcross_cli.picker import prompt_text
            name = prompt_text("New team name: ").strip()
        else:
            return "Usage: /cross team new <name>"
        if not name:
            return "Team name is required."
        body, err = api_client.create_team(name, user=user)
        if err:
            return err
        return f"Team {name!r} created."

    if args and args[0].lower() == "help":
        return _TEAM_HELP.lstrip("\n")

    # /cross team <name> <component>
    if len(args) >= 2:
        name = args[0]
        component = args[1].lower()
        if component in {"members", "member"}:
            members, err = api_client.team_members(name, user=user)
            if err and members is None:
                return err
            return _format_members(name, members)
        if component in {"personas", "persona", "experts", "expert"}:
            personas, err = api_client.team_experts(name, user=user)
            if err:
                return err
            return _format_personas(name, personas)
        if component in {"workflows", "workflow"}:
            return handle_workflow_command([], interactive=interactive, user=user) \
                if False else _format_team_workflows(name, user)
        if component in {"skills", "skill"}:
            return handle_skill_command([name], interactive=False, user=user)
        if component in {"crons", "cron", "alarms", "alarm"}:
            return handle_cron_command([name], interactive=False, user=user)
        return f"Unknown component {component!r}.{_TEAM_HELP}"

    if args:
        name = args[0]
        members, err = api_client.team_members(name, user=user)
        if err and members is None:
            return err
        alarms, _ = api_client.list_crons(team=name, user=user)
        return _format_team_detail(name, members, alarms) + _TEAM_HELP

    teams, err = api_client.list_teams(user=user)
    if err:
        return err
    listing = _format_team_list(teams)
    if not interactive or not teams or not _is_tty():
        return listing
    labels = [_team_label(t) for t in teams]
    idx = _pick("Teams — pick one for details", labels)
    if idx is None:
        return listing
    chosen = labels[idx]
    members, err = api_client.team_members(chosen, user=user)
    if err and members is None:
        return f"{listing}\n\n{err}"
    alarms, _ = api_client.list_crons(team=chosen, user=user)
    return f"{listing}\n\n{_format_team_detail(chosen, members, alarms)}"


# ── workflow ────────────────────────────────────────────────────────────────

_WORKFLOW_HELP_FOOTER = (
    "\n"
    "How to use:\n"
    "  /cross workflow show <name>                    show the YAML/py content\n"
    "  /cross workflow show <name> team <T>           disambiguate by team\n"
    "  /cross workflow run <name> question <text...>  run a personal workflow\n"
    "  /cross workflow run <name> team <T> question <text...>\n"
    "                                                 run a team workflow\n"
    "  /cross workflow new <name> [team <T>] [from <file>]\n"
    "                                                 create a new YAML workflow\n"
    "                                                 (CLI opens $EDITOR; chatbot needs `from`)\n"
    "\n"
    "<name> is the file without extension (e.g. paper_review_council),\n"
    "or with extension to force kind (e.g. paper_survey_workflow.py).\n"
    "<text...> can be multiple words; everything after `question` is joined."
)


def _format_workflow_list(items: list[dict]) -> str:
    if not items:
        return "No workflows found." + _WORKFLOW_HELP_FOOTER

    by_team: dict[str, list[dict]] = {}
    personal: list[dict] = []
    for it in items:
        scope = (it.get("scope") or "").lower()
        team = (it.get("team") or "").strip()
        if scope == "team" and team:
            by_team.setdefault(team, []).append(it)
        else:
            personal.append(it)

    sections: list[str] = []
    if by_team:
        total_team = sum(len(v) for v in by_team.values())
        sections.append(f"Team workflows ({total_team} across {len(by_team)} teams):")
        for tname, batch in by_team.items():
            sections.append(f"  [{tname}]")
            for it in batch:
                kind = it.get("kind") or "?"
                sections.append(f"  - [{kind}] {it.get('file', '?')}")
                desc = (it.get("description") or "").strip()
                if desc:
                    sections.append(f"      {desc}")
    if personal:
        if sections:
            sections.append("")
        sections.append(f"Personal workflows ({len(personal)}):")
        for it in personal:
            kind = it.get("kind") or "?"
            sections.append(f"  - [{kind}] {it.get('file', '?')}")
            desc = (it.get("description") or "").strip()
            if desc:
                sections.append(f"      {desc}")

    return _format_lines(sections) + _WORKFLOW_HELP_FOOTER


def _workflow_label(item: dict) -> str:
    scope = item.get("scope") or ""
    team = item.get("team") or ""
    location = f"team:{team}" if scope == "team" else "personal"
    kind = item.get("kind") or "?"
    return f"[{kind}] {location} / {item.get('file', '?')}"


def _pick_workflow_to_run(user: str) -> dict | str:
    """Curses picker over runnable workflows (YAML + Python).

    Returns the chosen workflow item dict, or a string message when the user
    cancels / nothing is runnable.
    """
    items = api_client.list_workflows(user, team="")
    if not items:
        return (
            "No workflows found. "
            "Add one with `/workflow new <name>` or pass `from <file>`."
        )
    labels: list[str] = []
    for it in items:
        scope = it.get("scope") or "personal"
        team = it.get("team") or ""
        location = f"team:{team}" if scope == "team" else "personal"
        kind = it.get("kind") or "?"
        desc = (it.get("description") or "").strip()
        line = f"[{kind}] [{location}] {it.get('file', '?')}"
        if desc:
            line += f"  — {desc[:60]}"
        labels.append(line)
    labels.append("Cancel")
    idx = curses_radiolist(
        "Select a workflow to run:",
        labels,
        selected=0,
        cancel_returns=len(labels) - 1,
    )
    if idx == len(labels) - 1:
        return "Workflow run cancelled."
    return items[idx]


def _parse_run_args(rest: list[str]) -> tuple[dict | None, str | None]:
    """Parse ``<name> [team <T>] question <Q...>``. Returns (parsed, error)."""
    usage = (
        "Usage: /cross workflow run <name> [team <T>] question <text...>\n"
        "Example: /cross workflow run paper_review_council "
        "team 论文审读汇报团 question 分析这篇综述的核心论点"
    )
    if not rest:
        return None, usage
    parsed = {"name": rest[0], "team": "", "question": ""}
    i = 1
    current = None
    while i < len(rest):
        token = rest[i]
        lower = token.lower()
        if lower in {"team", "question"}:
            current = lower
            i += 1
            continue
        if current is None:
            return None, f"unexpected token: {token!r} (expected 'team' or 'question')\n{usage}"
        if parsed[current]:
            parsed[current] += " " + token
        else:
            parsed[current] = token
        i += 1
    if not parsed["question"]:
        return None, f"workflow run requires 'question <text>'\n{usage}"
    return parsed, None


_WORKFLOW_YAML_TEMPLATE = """\
# {name} — describe what this workflow does in one line.
# Fill in the schedule below. See OASIS docs for syntax.

version: 1
discussion:
  agents:
    - name: example
      role: "describe the role here"
  rules:
    - "say what each agent should do"

# Replace the example above with real agent definitions before saving.
"""


def _handle_workflow_new(rest: list[str], *, interactive: bool, user: str) -> str:
    """`/cross workflow new <name> [team <T>] [from <file>] [desc <text...>]`.

    Interactive (CLI tty): if no `from`, opens $EDITOR with a template.
    Chatbot: requires `from <path>` since there is no stdin/editor.
    """
    from clawcross_cli.picker import prompt_text

    if not rest:
        if not interactive:
            return (
                "Usage: /cross workflow new <name> [team <T>] [from <file>] [desc <text...>]\n"
                "  team <T>     team scope (default: personal)\n"
                "  from <file>  read YAML from a file instead of $EDITOR\n"
                "  desc <text>  free-form description"
            )
        name = prompt_text("Workflow name (no extension): ").strip()
    else:
        name = rest[0].strip()
    if not name:
        return "Workflow name is required."

    team = ""
    yaml_path = ""
    description = ""
    i = 1 if rest else 0
    current = None
    while i < len(rest):
        token = rest[i]
        lower = token.lower()
        if lower in {"team", "from", "desc", "description"}:
            current = lower
            i += 1
            continue
        if current == "team":
            team = token
            current = None
        elif current == "from":
            yaml_path = token
            current = None
        elif current in {"desc", "description"}:
            description = (description + " " + token).strip() if description else token
        i += 1

    yaml_content: str | None = None
    if yaml_path:
        try:
            with open(os.path.expanduser(yaml_path), "r", encoding="utf-8") as fh:
                yaml_content = fh.read()
        except OSError as e:
            return f"Failed to read {yaml_path}: {e}"
    elif interactive:
        template = _WORKFLOW_YAML_TEMPLATE.format(name=name)
        edited = _edit_in_editor(template, suffix=".yaml")
        if edited is None:
            return "Cancelled (editor unavailable or user aborted). Pass `from <file>` to upload an existing YAML."
        if edited.strip() == template.strip():
            return "No changes saved (template was not edited)."
        yaml_content = edited
    else:
        return "Workflow YAML required. Use `from <file>` in chatbot or run from a terminal to edit interactively."

    body, err = api_client.save_workflow(
        user=user, name=name, yaml_content=yaml_content, team=team, description=description,
    )
    if err:
        return err
    fname = (body or {}).get("file") or f"{name}.yaml"
    location = f"team {team!r}" if team else "personal"
    return f"Workflow saved: {fname} ({location})"


def _edit_in_editor(initial: str, *, suffix: str = ".yaml") -> str | None:
    """Open $EDITOR (or vi) on a temp file pre-filled with *initial*.

    Returns the edited content, or None if the user quit without saving or
    no editor is available.
    """
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    import shutil as _sh
    import subprocess as _sp
    import tempfile as _tf

    bin_name = editor.split()[0]
    if not _sh.which(bin_name):
        return None
    with _tf.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8") as fh:
        fh.write(initial)
        path = fh.name
    try:
        rc = _sp.run([*editor.split(), path]).returncode
        if rc != 0:
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


_WORKFLOW_ACTIONS = [
    ("list", "list all workflows"),
    ("show", "show source (picker)"),
    ("run", "run a workflow (picker)"),
    ("new", "create a new workflow"),
]


def _workflow_action_menu() -> str | None:
    """Curses picker over workflow actions. None on cancel / non-TTY."""
    if not _is_tty():
        return None
    labels = [f"{key:<6}  {desc}" for key, desc in _WORKFLOW_ACTIONS]
    labels.append("Cancel")
    idx = curses_radiolist(
        "Select workflow action:",
        labels,
        selected=0,
        cancel_returns=len(labels) - 1,
    )
    if idx == len(labels) - 1:
        return None
    return _WORKFLOW_ACTIONS[idx][0]


def handle_workflow_command(args: list[str], *, interactive: bool = False, user: str | None = None) -> str:
    args = list(args or [])
    user = (user or api_client.DEFAULT_USER or "").strip() or api_client.DEFAULT_USER

    if not args and interactive:
        chosen = _workflow_action_menu()
        if chosen is None:
            return ""
        args = [chosen]

    if args and args[0].lower() in {"new", "create", "add", "save"}:
        return _handle_workflow_new(args[1:], interactive=interactive, user=user)

    if args and args[0].lower() == "show":
        if len(args) < 2:
            if not (interactive and _is_tty()):
                return "usage: workflow show <name>"
            picked = _pick_workflow_to_run(user)
            if isinstance(picked, str):  # cancelled / empty
                return picked
            path = picked.get("path", "")
            if not path:
                return "workflow path missing"
            content, ferr = api_client.read_workflow_file(path)
            if ferr:
                return ferr
            return f"# {path}\n\n{content}"
        name = args[1]
        team = ""
        # optional "team <T>" suffix
        if len(args) >= 4 and args[2].lower() == "team":
            team = args[3]
        # Try YAML first, then python.
        path, err = api_client.resolve_yaml_workflow_path(user, name, team)
        if not path:
            ppath, perr = api_client.resolve_python_workflow_path(user, name, team)
            if not ppath:
                return err or perr or "workflow not found"
            path = ppath
        content, ferr = api_client.read_workflow_file(path)
        if ferr:
            return ferr
        return f"# {path}\n\n{content}"

    if args and args[0].lower() == "run":
        run_rest = args[1:]
        run_kind = "yaml"
        if not run_rest and interactive and _is_tty():
            picked = _pick_workflow_to_run(user)
            if isinstance(picked, str):  # error / cancel message
                return picked
            from clawcross_cli.picker import prompt_text
            question = prompt_text("Question: ").strip()
            if not question:
                return "Workflow run cancelled (empty question)."
            parsed = {
                "name": picked["name"],
                "team": picked["team"] if picked.get("scope") == "team" else "",
                "question": question,
            }
            run_kind = picked.get("kind") or "yaml"
        else:
            parsed, err = _parse_run_args(run_rest)
            if err:
                return err
        body, rerr = api_client.run_workflow(
            user=user,
            name=parsed["name"],
            team=parsed["team"],
            question=parsed["question"],
            kind=run_kind,
        )
        if rerr:
            return rerr
        out_lines = ["Workflow launched."]
        if isinstance(body, dict):
            for key, label in (
                ("topic_id", "topic_id"),
                ("run_id", "run_id"),
                ("log_file", "log_file"),
                ("message", "message"),
            ):
                val = body.get(key)
                if val:
                    out_lines.append(f"  {label}: {val}")
        return _format_lines(out_lines)

    items = api_client.list_workflows(user, team="")
    listing = _format_workflow_list(items)
    if not interactive or not items or not _is_tty():
        return listing
    labels = [_workflow_label(it) for it in items]
    idx = _pick("Workflows — pick one to view", labels)
    if idx is None:
        return listing
    chosen = items[idx]
    content, ferr = api_client.read_workflow_file(chosen["path"])
    if ferr:
        return f"{listing}\n\n{ferr}"
    return f"{listing}\n\n# {chosen['path']}\n\n{content}"


# ── skill ───────────────────────────────────────────────────────────────────

def _render_skill_row(sk: Any) -> str:
    """Format one skill entry from /skills or /teams/<t>/skills."""
    if not isinstance(sk, dict):
        return f"  - {sk}"
    name = sk.get("name") or "?"
    desc = (sk.get("description") or "").strip()
    cat = (sk.get("category") or "").strip()
    cat_part = f" [{cat}]" if cat else ""
    desc_part = f" — {desc}" if desc else ""
    return f"  - {name}{cat_part}{desc_part}"


def _format_skills_payload(body: Any, team_filter: str = "") -> str:
    """Render the response from /skills or /teams/<t>/skills.

    Expected shape: ``{"ok": True, "skills": {"personal": [...], "team": [...]?}}``.
    Each skill entry is a dict with ``name``, ``description``, ``category``,
    ``scope``, ``team``, ``modified``.
    """
    if body is None:
        return "No skills available."
    if not isinstance(body, dict):
        return str(body)

    skills_obj = body.get("skills")
    if isinstance(skills_obj, dict):
        team_list = skills_obj.get("team") or []
        personal_list = skills_obj.get("personal") or []
    elif isinstance(skills_obj, list):
        team_list, personal_list = [], skills_obj
    else:
        return "No skills available."

    sections: list[str] = []
    if team_filter and team_list:
        sections.append(f"Team skills — {team_filter} ({len(team_list)}):")
        sections.extend(_render_skill_row(s) for s in team_list)
    if personal_list:
        if sections:
            sections.append("")
        sections.append(f"Personal skills ({len(personal_list)}):")
        sections.extend(_render_skill_row(s) for s in personal_list)

    if not sections:
        scope = f" for team {team_filter}" if team_filter else ""
        return f"No skills{scope}."
    return _format_lines(sections)


_SKILL_TEMPLATE = """\
---
name: {name}
description: One-line summary of what this skill does.
---

# {name}

Procedural steps the agent should follow when invoking this skill.

## Usage
- Describe inputs the agent expects.
- Describe outputs the agent should produce.

## Notes
- Add cautions or domain-specific tips.
"""


def _handle_skill_new(rest: list[str], *, interactive: bool, user: str) -> str:
    """`/cross skill new <name> [team <T>] [from <file>]`."""
    from clawcross_cli.picker import prompt_text

    if not rest:
        if not interactive:
            return "Usage: /cross skill new <name> [team <T>] [from <file>]"
        name = prompt_text("Skill name (no extension): ").strip()
    else:
        name = rest[0].strip()
    if not name:
        return "Skill name is required."

    team = ""
    md_path = ""
    i = 1 if rest else 0
    current = None
    while i < len(rest):
        token = rest[i]
        lower = token.lower()
        if lower in {"team", "from"}:
            current = lower
            i += 1
            continue
        if current == "team":
            team = token
            current = None
        elif current == "from":
            md_path = token
            current = None
        i += 1

    content: str | None = None
    if md_path:
        try:
            with open(os.path.expanduser(md_path), "r", encoding="utf-8") as fh:
                content = fh.read()
        except OSError as e:
            return f"Failed to read {md_path}: {e}"
    elif interactive:
        template = _SKILL_TEMPLATE.format(name=name)
        edited = _edit_in_editor(template, suffix=".md")
        if edited is None:
            return "Cancelled (editor unavailable or user aborted). Pass `from <file>` to upload an existing SKILL.md."
        if edited.strip() == template.strip():
            return "No changes saved (template was not edited)."
        content = edited
    else:
        return "SKILL.md content required. Use `from <file>` or run from a terminal."

    body, err = api_client.create_skill(name, content, team=team, user=user)
    if err:
        return err
    location = f"team {team!r}" if team else "personal"
    return f"Skill {name!r} created ({location})."


def handle_skill_command(args: list[str], *, interactive: bool = False, user: str | None = None) -> str:
    """``/cross skill [<team>]`` — list managed skills.

    Without args: aggregates the current user's personal skills *and* every
    team's team-scoped skills (one call per team).
    With *team*: shows just that team plus personal.
    """
    args = list(args or [])
    if args and args[0].lower() in {"new", "create", "add"}:
        return _handle_skill_new(args[1:], interactive=interactive,
                                  user=(user or api_client.DEFAULT_USER))
    team = args[0] if args else ""

    if team:
        body, err = api_client.list_skills(team=team, user=user)
        if err:
            return err
        return _format_skills_payload(body, team_filter=team)

    # No team: fan out across all teams + personal.
    personal_body, perr = api_client.list_skills(team="", user=user)
    personal_skills: list = []
    if isinstance(personal_body, dict):
        sk = personal_body.get("skills")
        if isinstance(sk, dict):
            personal_skills = sk.get("personal") or []
        elif isinstance(sk, list):
            personal_skills = sk

    teams_list, terr = api_client.list_teams(user=user)
    team_skills_map: dict[str, list] = {}
    team_errors: list[str] = []
    for entry in teams_list:
        if isinstance(entry, dict):
            tname = entry.get("name") or entry.get("team") or ""
        else:
            tname = str(entry or "")
        tname = tname.strip()
        if not tname:
            continue
        tbody, terr_one = api_client.list_skills(team=tname, user=user)
        if terr_one or not isinstance(tbody, dict):
            if terr_one:
                team_errors.append(f"{tname}: {terr_one}")
            continue
        sk_obj = tbody.get("skills") or {}
        if isinstance(sk_obj, dict):
            team_skills_map[tname] = sk_obj.get("team") or []

    sections: list[str] = []
    total_team = sum(len(v) for v in team_skills_map.values())
    if team_skills_map:
        sections.append(f"Team skills ({total_team} across {len(team_skills_map)} teams):")
        for tname, items in team_skills_map.items():
            if not items:
                continue
            sections.append(f"  [{tname}]")
            for sk in items:
                sections.append("  " + _render_skill_row(sk).lstrip())
    if personal_skills:
        if sections:
            sections.append("")
        sections.append(f"Personal skills ({len(personal_skills)}):")
        sections.extend(_render_skill_row(s) for s in personal_skills)

    if not sections:
        msg = "No skills."
        if perr:
            msg += f"\n  personal: {perr}"
        if terr:
            msg += f"\n  teams: {terr}"
        return msg
    if team_errors:
        sections.append("")
        sections.append("Some team scopes failed:")
        sections.extend(f"  {e}" for e in team_errors)
    return _format_lines(sections)


# ── cron ────────────────────────────────────────────────────────────────────

def _render_cron_row(a: dict) -> list[str]:
    target = a.get("target_name") or "?"
    ttype = a.get("target_type") or ""
    sched = a.get("cron") or a.get("run_at") or "?"
    text = (a.get("text") or "").splitlines()[0][:80]
    type_part = f" ({ttype})" if ttype else ""
    rows = [f"  - {target}{type_part}  {sched}"]
    if text:
        rows.append(f"      {text}")
    return rows


def _format_cron_list(alarms: list[dict], team: str | None = None) -> str:
    """Render crons grouped by team (like /skill).

    When *team* is given, treat all entries as belonging to that team.
    Otherwise read each entry's ``team`` field and group accordingly.
    """
    if not alarms:
        scope = f" for team {team}" if team else ""
        return f"No crons{scope}."

    by_team: dict[str, list[dict]] = {}
    personal: list[dict] = []
    for a in alarms:
        tname = team or (a.get("team") or "").strip()
        if tname and tname != "__public__":
            by_team.setdefault(tname, []).append(a)
        else:
            personal.append(a)

    sections: list[str] = []
    if by_team:
        total = sum(len(v) for v in by_team.values())
        sections.append(f"Team crons ({total} across {len(by_team)} teams):")
        for tname, batch in by_team.items():
            sections.append(f"  [{tname}]")
            for a in batch:
                sections.extend(_render_cron_row(a))
    if personal:
        if sections:
            sections.append("")
        sections.append(f"Personal/shared crons ({len(personal)}):")
        for a in personal:
            sections.extend(_render_cron_row(a))
    return _format_lines(sections)


def _handle_cron_new(rest: list[str], *, interactive: bool, user: str) -> str:
    """`/cross cron new team <T> target <name> [type internal|external] [cron <expr>|once <ISO>] text <message...>`."""
    from clawcross_cli.picker import prompt_text

    parsed = {
        "team": "",
        "target": "",
        "type": "internal",
        "cron": "",
        "once": "",
        "text": "",
    }
    i = 0
    current = None
    keywords = {"team", "target", "type", "cron", "once", "text"}
    while i < len(rest):
        token = rest[i]
        lower = token.lower()
        if lower in keywords:
            current = lower
            i += 1
            continue
        if current is None:
            i += 1
            continue
        if current == "text":
            parsed["text"] = (parsed["text"] + " " + token).strip() if parsed["text"] else token
        else:
            parsed[current] = token
        i += 1

    # Interactive fill-ins when running from a terminal.
    if interactive:
        if not parsed["team"]:
            parsed["team"] = prompt_text("Team: ").strip()
        if not parsed["target"]:
            parsed["target"] = prompt_text("Target name (internal session or external alias): ").strip()
        if not parsed["cron"] and not parsed["once"]:
            mode = prompt_text("Schedule type (cron|once) [cron]: ").strip().lower() or "cron"
            if mode == "once":
                parsed["once"] = prompt_text("Run at (ISO 8601, e.g. 2026-06-01T09:00:00): ").strip()
            else:
                parsed["cron"] = prompt_text("Cron expression (e.g. 0 9 * * 1): ").strip()
        if not parsed["text"]:
            parsed["text"] = prompt_text("Message to send: ").strip()

    if not parsed["team"]:
        return "Usage: /cross cron new team <T> target <name> [cron <expr>|once <ISO>] text <message...>"
    if not parsed["target"]:
        return "target is required (internal session name or external alias)"
    if not parsed["text"]:
        return "text is required"
    schedule_type = "once" if parsed["once"] else "cron"
    if schedule_type == "cron" and not parsed["cron"]:
        return "cron expression is required (or use `once <ISO>`)"

    body, err = api_client.create_cron(
        team=parsed["team"],
        target_name=parsed["target"],
        target_type=parsed["type"] or "internal",
        text=parsed["text"],
        schedule_type=schedule_type,
        cron_expr=parsed["cron"],
        run_at=parsed["once"],
        user=user,
    )
    if err:
        return err
    sched = parsed["once"] or parsed["cron"]
    return f"Cron created on team {parsed['team']!r}: target={parsed['target']} schedule={sched}"


def handle_cron_command(args: list[str], *, interactive: bool = False, user: str | None = None) -> str:
    args = list(args or [])
    user = (user or api_client.DEFAULT_USER or "").strip() or api_client.DEFAULT_USER
    if args and args[0].lower() in {"new", "create", "add"}:
        return _handle_cron_new(args[1:], interactive=interactive, user=user)
    team = args[0] if args else None
    alarms, err = api_client.list_crons(team=team, user=user)
    if err:
        return err
    return _format_cron_list(alarms, team=team)
