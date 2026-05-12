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


def handle_team_command(args: list[str], *, interactive: bool = False, user: str | None = None) -> str:
    args = list(args or [])
    user = (user or api_client.DEFAULT_USER or "").strip() or api_client.DEFAULT_USER
    if args:
        name = args[0]
        members, err = api_client.team_members(name, user=user)
        if err and members is None:
            return err
        alarms, _ = api_client.list_crons(team=name, user=user)
        return _format_team_detail(name, members, alarms)

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


def handle_workflow_command(args: list[str], *, interactive: bool = False, user: str | None = None) -> str:
    args = list(args or [])
    user = (user or api_client.DEFAULT_USER or "").strip() or api_client.DEFAULT_USER

    if args and args[0].lower() == "show":
        if len(args) < 2:
            return "usage: workflow show <name>"
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
        parsed, err = _parse_run_args(args[1:])
        if err:
            return err
        body, rerr = api_client.run_workflow(
            user=user,
            name=parsed["name"],
            team=parsed["team"],
            question=parsed["question"],
            kind="yaml",
        )
        if rerr:
            return rerr
        topic_id = body.get("topic_id") if isinstance(body, dict) else None
        msg = body.get("message") if isinstance(body, dict) else ""
        out_lines = ["Workflow launched."]
        if topic_id:
            out_lines.append(f"  topic_id: {topic_id}")
        if msg:
            out_lines.append(f"  message: {msg}")
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


def handle_skill_command(args: list[str], *, interactive: bool = False, user: str | None = None) -> str:
    """``/cross skill [<team>]`` — list managed skills.

    Without args: aggregates the current user's personal skills *and* every
    team's team-scoped skills (one call per team).
    With *team*: shows just that team plus personal.
    """
    args = list(args or [])
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


def handle_cron_command(args: list[str], *, interactive: bool = False, user: str | None = None) -> str:
    args = list(args or [])
    team = args[0] if args else None
    user = (user or api_client.DEFAULT_USER or "").strip() or api_client.DEFAULT_USER
    alarms, err = api_client.list_crons(team=team, user=user)
    if err:
        return err
    return _format_cron_list(alarms, team=team)
