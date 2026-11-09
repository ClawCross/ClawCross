"""
MCP Server: Self-Evolution Skill System

Exposes skill management tools via FastMCP for agent self-evolution:
- skill_manage: Create, edit, patch, delete skills
- skill_view: View full skill content
- skill_list: List all available skills
- skill_evolution_report: Build an EvoSkill-style failure analysis report
- skill_evolution_apply: Apply the top evolution candidate back into a skill
- skill_run: Apply a skill's instructions in context
- session_search: Search historical sessions
- get_insights: Get usage analytics
- get_trajectory_stats: Get trajectory statistics
"""

import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("SelfEvolution")


# ── Skill Management ────────────────────────────────────────────────

@mcp.tool()
async def skill_manage(
    username: str,
    action: str,
    team: str = "",
    name: str = "",
    content: str = "",
    category: str = "",
    file_path: str = "",
    file_content: str = "",
    old_string: str = "",
    new_string: str = "",
    replace_all: bool = False,
) -> str:
    """
    Manage agent skills (procedural memory). Skills are reusable procedures
    that persist across sessions.

    Actions:
    - create: Create a new skill. Requires name + content (YAML frontmatter + body).
    - edit: Full rewrite of a skill. Requires name + content.
    - patch: Targeted find-and-replace. Requires name + old_string + new_string.
    - delete: Remove a skill. Requires name.
    - write_file: Add supporting file. Requires name + file_path + file_content.
    - remove_file: Remove supporting file. Requires name + file_path.

    SKILL.md format:
    ---
    name: my-skill
    description: What this skill does
    ---
    Instructions and procedures...

    When to create skills:
    - After completing complex tasks (5+ tool calls)
    - After fixing tricky errors
    - After discovering non-trivial workflows
    When to update skills:
    - When a skill is outdated or wrong during use

    :param username: User ID (auto-injected)
    :param action: create, edit, patch, delete, write_file, remove_file
    :param team: Optional team scope. When provided, create/update the team-managed skill pool.
    :param name: Skill name (lowercase, hyphens, dots, underscores)
    :param content: SKILL.md content (for create/edit)
    :param category: Optional category folder
    :param file_path: Path for supporting files (e.g. references/notes.md)
    :param file_content: Content for supporting files
    :param old_string: Text to find (for patch)
    :param new_string: Text to replace with (for patch)
    :param replace_all: Replace all occurrences (for patch)
    """
    from webot.skills import (
        create_skill, edit_skill, patch_skill, delete_skill,
        write_skill_file, remove_skill_file,
    )

    action = (action or "").strip().lower()
    if not name and action != "list":
        return json.dumps({"success": False, "error": "Skill name is required"})

    if action == "create":
        result = create_skill(username, name=name, content=content, category=category, team=team)
    elif action == "edit":
        result = edit_skill(username, name=name, content=content, team=team)
    elif action == "patch":
        result = patch_skill(
            username, name=name,
            old_string=old_string, new_string=new_string,
            file_path=file_path, replace_all=replace_all, team=team,
        )
    elif action == "delete":
        result = delete_skill(username, name=name, team=team)
    elif action == "write_file":
        result = write_skill_file(username, name=name, file_path=file_path, file_content=file_content, team=team)
    elif action == "remove_file":
        result = remove_skill_file(username, name=name, file_path=file_path, team=team)
    else:
        result = {"success": False, "error": f"Unknown action: {action}. Use: create, edit, patch, delete, write_file, remove_file"}

    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def skill_view(username: str, name: str, team: str = "") -> str:
    """
    View full content of a skill (procedural memory).

    :param username: User ID (auto-injected)
    :param name: Skill name to view
    :param team: Optional team scope. When provided, return both team and shared skill variants by category.
    """
    from webot.skills import get_skill
    if team:
        team_skill = get_skill(username, name=name, team=team)
        personal_skill = get_skill(username, name=name)
        if not team_skill and not personal_skill:
            return json.dumps({"error": f"Skill '{name}' not found"})
        return json.dumps(
            {
                "name": name,
                "team": team,
                "sections": {
                    "team": team_skill,
                    "personal": personal_skill,
                },
            },
            ensure_ascii=False,
        )

    skill = get_skill(username, name=name)
    if not skill:
        return json.dumps({"error": f"Skill '{name}' not found"})
    return json.dumps(skill, ensure_ascii=False)


@mcp.tool()
async def skill_list(username: str, team: str = "") -> str:
    """
    List all available skills (procedural memory) for the current user.

    :param username: User ID (auto-injected)
    :param team: Optional team scope. When provided, show team and shared skills in separate sections.
    """
    from webot.skills import list_skills
    if team:
        team_skills = list_skills(username, team=team)
        personal_skills = list_skills(username)
        return json.dumps(
            {
                "count": len(team_skills) + len(personal_skills),
                "team": team,
                "sections": {
                    "team": team_skills,
                    "personal": personal_skills,
                },
            },
            ensure_ascii=False,
        )
    skills = list_skills(username)
    return json.dumps({"count": len(skills), "skills": skills}, ensure_ascii=False)


# ── Skill Evolution Loop ────────────────────────────────────────────

@mcp.tool()
async def skill_evolution_report(
    username: str,
    name: str,
    team: str = "",
    session_id: str = "",
    days: int = 30,
    limit: int = 8,
    error_text: str = "",
    command: str = "",
    strategy: str = "auto",
) -> str:
    """
    Build a lightweight EvoSkill-style report for a skill using recent failures.

    The report analyzes recent trajectory failures plus any explicit execution
    error text you pass in, then produces a small candidate frontier of
    possible skill mutations with heuristic scores.

    :param username: User ID (auto-injected)
    :param name: Skill name to analyze
    :param team: Optional team scope. Team skill is preferred when both scopes contain the same name.
    :param session_id: Optional current session filter
    :param days: How many recent days to inspect
    :param limit: Max failure samples to analyze
    :param error_text: Optional fresh error text to include immediately
    :param command: Optional command associated with the fresh error
    :param strategy: Strategy preset (auto, balanced, innovate, harden, repair-only)
    """
    from webot.skill_evolution import analyze_skill_evolution

    result = analyze_skill_evolution(
        username,
        name=name,
        team=team,
        session_id=session_id,
        days=days,
        limit=limit,
        error_text=error_text,
        command=command,
        strategy=strategy,
    )
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
async def skill_evolution_apply(
    username: str,
    name: str,
    team: str = "",
    session_id: str = "",
    days: int = 30,
    limit: int = 8,
    error_text: str = "",
    command: str = "",
    source: str = "runtime",
    strategy: str = "auto",
) -> str:
    """
    Apply the top self-evolution candidate back into a skill's managed block.

    This turns recent failure evidence into a durable SKILL.md update plus
    evolution reports and feedback-history artifacts under the skill directory.

    :param username: User ID (auto-injected)
    :param name: Skill name to update
    :param team: Optional team scope. Team skill is preferred when both scopes contain the same name.
    :param session_id: Optional current session filter
    :param days: How many recent days to inspect
    :param limit: Max failure samples to analyze
    :param error_text: Optional fresh error text to include immediately
    :param command: Optional command associated with the fresh error
    :param source: Source label for the feedback history
    :param strategy: Strategy preset (auto, balanced, innovate, harden, repair-only)
    """
    from webot.skill_evolution import apply_skill_evolution

    result = apply_skill_evolution(
        username,
        name=name,
        team=team,
        session_id=session_id,
        days=days,
        limit=limit,
        error_text=error_text,
        command=command,
        source=source,
        strategy=strategy,
    )
    return json.dumps(result, ensure_ascii=False)


# ── Session Search ──────────────────────────────────────────────────

@mcp.tool()
async def search_sessions(
    username: str,
    session_id: str = "",
    query: str = "",
    limit: int = 5,
) -> str:
    """
    Search across historical sessions for relevant context.
    Prevents you from asking the user to repeat information they've
    already provided in past conversations.

    :param username: User ID (auto-injected)
    :param session_id: Current session ID (auto-injected, excluded from results)
    :param query: Search keywords. Leave empty for recent sessions.
    :param limit: Max results (default 5)
    """
    from webot.session_search import session_search
    result = session_search(
        query=query,
        user_id=username,
        current_session_id=session_id,
        limit=limit,
    )
    return json.dumps(result, ensure_ascii=False, default=str)


# ── Insights & Analytics ────────────────────────────────────────────

@mcp.tool()
async def get_insights(username: str, days: int = 30) -> str:
    """
    Get usage analytics and insights for the current user.
    Shows: session stats, tool usage patterns, activity trends,
    model breakdown, cost estimation.

    :param username: User ID (auto-injected)
    :param days: Number of days to analyze (default 30)
    """
    from webot.insights import InsightsEngine
    engine = InsightsEngine()
    insights = engine.generate(days=days, user_id=username)
    formatted = engine.format_terminal(insights)
    return formatted


@mcp.tool()
async def get_trajectory_stats(username: str, days: int = 30) -> str:
    """
    Get conversation trajectory statistics.
    Shows: success/failure rates, tool call averages, model breakdown.

    :param username: User ID (auto-injected)
    :param days: Number of days to analyze (default 30)
    """
    from webot.trajectory import get_trajectory_stats
    stats = get_trajectory_stats(user_id=username, days=days)
    return json.dumps(stats, ensure_ascii=False)


# ── SOUL.md Personality ─────────────────────────────────────────────

@mcp.tool()
async def manage_personality(
    username: str,
    action: str = "get",
    content: str = "",
) -> str:
    """
    Manage agent personality via SOUL.md.

    Actions:
    - get: View current personality
    - set: Set new personality text
    - reset: Reset to default template
    - delete: Remove custom personality

    :param username: User ID (auto-injected)
    :param action: get, set, reset, delete
    :param content: Personality text (for 'set' action)
    """
    from webot.soul import get_soul, set_soul, reset_soul, delete_soul

    action = (action or "get").strip().lower()
    if action == "get":
        soul = get_soul(username)
        return json.dumps({"personality": soul or "(default — no custom personality set)"})
    elif action == "set":
        if not content.strip():
            return json.dumps({"success": False, "error": "Content is required for 'set' action"})
        result = set_soul(username, content)
        return json.dumps(result, ensure_ascii=False)
    elif action == "reset":
        result = reset_soul(username)
        return json.dumps(result, ensure_ascii=False)
    elif action == "delete":
        result = delete_soul(username)
        return json.dumps(result, ensure_ascii=False)
    else:
        return json.dumps({"success": False, "error": f"Unknown action: {action}"})


if __name__ == "__main__":
    mcp.run()
