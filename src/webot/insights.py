"""
Insights & Analytics Engine — session history analysis.

Ported from Hermes Agent's insights system:
- Token consumption & cost estimation
- Tool usage patterns (ranked by frequency)
- Activity trends (by day of week, hour)
- Model/platform breakdowns
- Session metrics (duration, message counts)
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from utils.runtime_paths import DATA_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class InsightsEngine:
    """Analyze session history from the LangGraph checkpoint DB."""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = str(db_path or (DATA_DIR / "agent_checkpoints"))
        self._trajectory_dir = DATA_DIR / "trajectories"

    def generate(self, days: int = 30, user_id: str = "") -> dict[str, Any]:
        """Generate comprehensive insights report.

        Args:
            days: Number of days to analyze
            user_id: Filter by user ID (empty = all)

        Returns:
            Dict with overview, tools, activity, models, top_sessions
        """
        # Use trajectory data as primary source (richer metadata)
        entries = self._load_trajectories(days, user_id)

        if not entries:
            return {
                "overview": {"total_sessions": 0, "period_days": days},
                "tools": [],
                "activity": {},
                "models": {},
                "top_sessions": {},
            }

        overview = self._compute_overview(entries, days)
        tools = self._compute_tool_breakdown(entries)
        activity = self._compute_activity_patterns(entries)
        models = self._compute_model_breakdown(entries)
        top_sessions = self._compute_top_sessions(entries)

        return {
            "overview": overview,
            "tools": tools,
            "activity": activity,
            "models": models,
            "top_sessions": top_sessions,
        }

    def _load_trajectories(self, days: int, user_id: str) -> list[dict[str, Any]]:
        """Load trajectory entries for analysis."""
        from webot.trajectory import list_trajectories
        entries = list_trajectories(limit=10000, user_id=user_id)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return [e for e in entries if e.get("timestamp", "") >= cutoff]

    def _compute_overview(self, entries: list[dict], days: int) -> dict[str, Any]:
        total = len(entries)
        completed = sum(1 for e in entries if e.get("completed"))
        total_msgs = sum(e.get("message_count", 0) for e in entries)
        total_tools = sum(e.get("tool_calls_count", 0) for e in entries)

        # Token and cost estimation
        total_input = 0
        total_output = 0
        for e in entries:
            usage = e.get("token_usage") or {}
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)

        # Rough cost estimation (using typical rates)
        estimated_cost = (total_input * 0.003 + total_output * 0.015) / 1000

        # Activity days
        active_dates = set()
        for e in entries:
            ts = e.get("timestamp", "")
            if ts:
                try:
                    active_dates.add(ts[:10])
                except Exception:
                    pass

        return {
            "total_sessions": total,
            "completed_sessions": completed,
            "success_rate": f"{completed / total * 100:.1f}%" if total else "N/A",
            "total_messages": total_msgs,
            "total_tool_calls": total_tools,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "estimated_cost_usd": round(estimated_cost, 2),
            "active_days": len(active_dates),
            "period_days": days,
        }

    def _compute_tool_breakdown(self, entries: list[dict]) -> list[dict[str, Any]]:
        """Rank tools by usage frequency."""
        tool_counter: Counter = Counter()
        for e in entries:
            convos = e.get("conversations") or []
            for msg in convos:
                text = msg.get("value", "")
                # Parse tool calls from formatted messages
                if "[Tool calls: " in text:
                    start = text.index("[Tool calls: ") + len("[Tool calls: ")
                    end = text.index("]", start)
                    tools_str = text[start:end]
                    for tool_name in tools_str.split(", "):
                        tool_name = tool_name.strip()
                        if tool_name:
                            tool_counter[tool_name] += 1

        total_calls = sum(tool_counter.values()) or 1
        return [
            {
                "tool": name,
                "count": count,
                "percentage": f"{count / total_calls * 100:.1f}%",
            }
            for name, count in tool_counter.most_common(20)
        ]

    def _compute_activity_patterns(self, entries: list[dict]) -> dict[str, Any]:
        """Analyze activity by day-of-week and hour-of-day."""
        day_counter: Counter = Counter()
        hour_counter: Counter = Counter()
        streak_dates: set[str] = set()

        for e in entries:
            ts = e.get("timestamp", "")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                day_counter[dt.strftime("%A")] += 1
                hour_counter[dt.hour] += 1
                streak_dates.add(dt.strftime("%Y-%m-%d"))
            except (ValueError, AttributeError):
                continue

        # Compute streak
        current_streak = 0
        if streak_dates:
            today = datetime.now(timezone.utc).date()
            while today.isoformat() in streak_dates or (today - timedelta(days=1)).isoformat() in streak_dates:
                if today.isoformat() in streak_dates:
                    current_streak += 1
                today -= timedelta(days=1)

        busiest_day = day_counter.most_common(1)[0] if day_counter else ("N/A", 0)
        busiest_hour = hour_counter.most_common(1)[0] if hour_counter else ("N/A", 0)

        return {
            "by_day_of_week": dict(day_counter.most_common()),
            "by_hour": {str(k): v for k, v in sorted(hour_counter.items())},
            "busiest_day": {"day": busiest_day[0], "sessions": busiest_day[1]},
            "busiest_hour": {"hour": busiest_hour[0], "sessions": busiest_hour[1]},
            "current_streak_days": current_streak,
        }

    def _compute_model_breakdown(self, entries: list[dict]) -> dict[str, Any]:
        """Per-model usage analysis."""
        model_data: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "sessions": 0, "completed": 0, "total_tools": 0,
            "total_messages": 0, "input_tokens": 0, "output_tokens": 0,
        })

        for e in entries:
            model = e.get("model", "unknown")
            data = model_data[model]
            data["sessions"] += 1
            if e.get("completed"):
                data["completed"] += 1
            data["total_tools"] += e.get("tool_calls_count", 0)
            data["total_messages"] += e.get("message_count", 0)
            usage = e.get("token_usage") or {}
            data["input_tokens"] += usage.get("input_tokens", 0)
            data["output_tokens"] += usage.get("output_tokens", 0)

        return dict(model_data)

    def _compute_top_sessions(self, entries: list[dict]) -> dict[str, Any]:
        """Find notable sessions."""
        if not entries:
            return {}

        most_messages = max(entries, key=lambda e: e.get("message_count", 0))
        most_tools = max(entries, key=lambda e: e.get("tool_calls_count", 0))

        def _summary(e: dict) -> dict:
            return {
                "session_id": e.get("session_id", "?"),
                "model": e.get("model", "?"),
                "messages": e.get("message_count", 0),
                "tool_calls": e.get("tool_calls_count", 0),
                "timestamp": e.get("timestamp", ""),
            }

        return {
            "most_messages": _summary(most_messages),
            "most_tool_calls": _summary(most_tools),
        }

    def format_terminal(self, insights: dict[str, Any]) -> str:
        """Format insights for terminal display."""
        ov = insights.get("overview", {})
        lines = [
            "=== ClawCross Insights ===",
            "",
            f"Period: last {ov.get('period_days', 30)} days",
            f"Sessions: {ov.get('total_sessions', 0)} ({ov.get('success_rate', 'N/A')} completed)",
            f"Messages: {ov.get('total_messages', 0)}",
            f"Tool calls: {ov.get('total_tool_calls', 0)}",
            f"Tokens: {ov.get('total_input_tokens', 0):,} in / {ov.get('total_output_tokens', 0):,} out",
            f"Est. cost: ${ov.get('estimated_cost_usd', 0):.2f}",
            f"Active days: {ov.get('active_days', 0)}",
        ]

        # Activity
        activity = insights.get("activity", {})
        if activity.get("current_streak_days"):
            lines.append(f"Current streak: {activity['current_streak_days']} days")
        if activity.get("busiest_day", {}).get("day") != "N/A":
            lines.append(f"Busiest day: {activity['busiest_day']['day']}")

        # Top tools
        tools = insights.get("tools", [])
        if tools:
            lines.append("")
            lines.append("Top tools:")
            for t in tools[:10]:
                lines.append(f"  {t['tool']: <30} {t['count']:>4} ({t['percentage']})")

        # Models
        models = insights.get("models", {})
        if models:
            lines.append("")
            lines.append("Models:")
            for model, data in models.items():
                lines.append(f"  {model}: {data['sessions']} sessions, {data['total_tools']} tool calls")

        return "\n".join(lines)
