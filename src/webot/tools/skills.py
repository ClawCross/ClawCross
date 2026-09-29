"""
MCP Server: Self-Evolution Skill System

Exposes skill management tools via FastMCP for agent self-evolution:
- skill_evolution_report: Build an EvoSkill-style failure analysis report
- search_sessions: Search historical sessions
- usage_status: Get usage analytics and trajectory statistics
"""

import sys as _sys
import os as _os
_src_dir = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

import json
from utils.mcp_tool_docs import DocumentedFastMCP as FastMCP

mcp = FastMCP("SelfEvolution")


# ── Memory Improvement Report ──────────────────────────────────────

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
    :param name: Memory entry ID or name from list_files(storage="memory")
    :param team: Optional team scope. Team skill is preferred when both scopes contain the same name.
    :param session_id: Optional current session filter
    :param days: How many recent days to inspect
    :param limit: Max failure samples to analyze
    :param error_text: Optional fresh error text to include immediately
    :param command: Optional command associated with the fresh error
    :param strategy: Strategy preset (auto, balanced, innovate, harden, repair-only)
    """
    from webot.skill_evolution import analyze_skill_evolution

    from webot.skill_memory import memory_target, public_entry
    entry = memory_target(username, name, team, shared=True)
    from webot.skills import _parse_frontmatter
    content = entry["_path"].read_text(encoding="utf-8")
    _, body = _parse_frontmatter(content)
    result = analyze_skill_evolution(
        username,
        name=entry["_key"],
        team=entry["team"],
        session_id=session_id,
        days=days,
        limit=limit,
        error_text=error_text,
        command=command,
        strategy=strategy,
        skill_record={"content": content, "body": body, "path": str(entry["_path"]),
                      "scope": entry["scope"], "team": entry["team"]},
    )
    # Storage metadata is internal; the report proposes changes, never writes them.
    def redact(value):
        if isinstance(value, dict):
            return {k: redact(v) for k, v in value.items() if k not in {"path", "dir", "skill_path", "repo_root", "cwd"}}
        if isinstance(value, list):
            return [redact(v) for v in value]
        return value
    result = redact(result)
    result["skill_name"] = entry["name"]
    result["memory"] = public_entry(entry)
    result["next_step"] = "Read the entry, then apply the chosen improvement with write_file(storage='memory')."
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
async def usage_status(username: str, days: int = 30) -> str:
    """
    Get usage analytics for the current user: session stats, tool usage
    patterns, activity trends, model breakdown, cost estimation, and
    conversation trajectory stats (success/failure rates, tool calls per turn).

    :param username: User ID (auto-injected)
    :param days: Number of days to analyze (default 30)
    """
    from webot.insights import InsightsEngine
    from webot.trajectory import get_trajectory_stats
    engine = InsightsEngine()
    insights = engine.generate(days=days, user_id=username)
    formatted = engine.format_terminal(insights)
    stats = get_trajectory_stats(user_id=username, days=days)
    return f"{formatted}\n\nTrajectory stats:\n{json.dumps(stats, ensure_ascii=False)}"


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
