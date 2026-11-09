"""
Lazy Tool Discovery – Claude Code style on-demand tool loading.

Features:
- Maintains a lightweight tool registry with just names + descriptions
- Full tool schemas loaded on-demand when the LLM requests them
- Tool search capability (find tools by keyword/description)
- Reduces initial prompt size by deferring full schema injection
- Supports tool categories and tags for better discovery

Claude Code's approach: instead of always binding all ~50 tools,
start with a compact list and let the agent "discover" tools as needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolRegistryEntry:
    """Lightweight tool metadata for discovery."""
    name: str
    description: str
    category: str = "general"
    tags: tuple[str, ...] = ()
    schema_loaded: bool = False
    _full_schema: dict[str, Any] | None = None

    @property
    def summary(self) -> str:
        """One-line summary for compact listing."""
        desc = self.description[:80] if self.description else "No description"
        return f"{self.name}: {desc}"


class LazyToolRegistry:
    """
    Registry that holds all tool metadata but defers full schema loading.

    Usage flow:
    1. At startup: register_tools() with full tool objects
    2. In system prompt: inject compact_tool_list() instead of full schemas
    3. When LLM needs a tool: get_full_schema(name) loads the complete spec
    4. LLM can search: search_tools(query) to discover relevant tools
    """

    def __init__(self):
        self._entries: dict[str, ToolRegistryEntry] = {}
        self._full_tools: dict[str, Any] = {}  # name -> full langchain tool object
        self._always_loaded: set[str] = set()  # Tools always included in full schema

    def register_tools(self, tools: list[Any]) -> None:
        """Register tools from LangChain tool objects."""
        for tool in tools:
            name = getattr(tool, "name", str(tool))
            description = getattr(tool, "description", "") or ""
            category = self._infer_category(name, description)
            tags = self._infer_tags(name, description)

            self._entries[name] = ToolRegistryEntry(
                name=name,
                description=description,
                category=category,
                tags=tags,
            )
            self._full_tools[name] = tool

    def set_always_loaded(self, tool_names: set[str]) -> None:
        """Mark certain tools to always have their full schema loaded."""
        self._always_loaded = set(tool_names)

    def _infer_category(self, name: str, description: str) -> str:
        """Infer tool category from name/description."""
        name_lower = name.lower()
        desc_lower = (description or "").lower()

        if any(k in name_lower for k in ("file", "read", "write", "append", "delete", "list_files")):
            return "filesystem"
        if any(k in name_lower for k in ("command", "python", "run")):
            return "execution"
        if any(k in name_lower for k in ("search", "grep", "find")):
            return "search"
        if any(k in name_lower for k in ("oasis", "workflow", "expert")):
            return "oasis"
        if any(k in name_lower for k in ("telegram", "send", "message")):
            return "communication"
        if any(k in name_lower for k in ("session", "subagent", "spawn")):
            return "agent"
        if any(k in name_lower for k in ("alarm", "schedule", "cron")):
            return "scheduling"
        if "git" in desc_lower:
            return "version_control"
        return "general"

    def _infer_tags(self, name: str, description: str) -> tuple[str, ...]:
        """Infer tags from name/description for search."""
        tags = set()
        combined = f"{name} {description}".lower()

        tag_keywords = {
            "file": "filesystem",
            "read": "read",
            "write": "write",
            "delete": "destructive",
            "search": "search",
            "command": "execution",
            "python": "code",
            "oasis": "oasis",
            "telegram": "messaging",
            "session": "session",
            "subagent": "agent",
            "alarm": "scheduling",
            "tool": "meta",
        }

        for keyword, tag in tag_keywords.items():
            if keyword in combined:
                tags.add(tag)

        return tuple(sorted(tags))

    def compact_tool_list(self, enabled_names: set[str] | None = None) -> str:
        """
        Generate a compact tool listing for system prompt injection.

        Only includes names and short descriptions, not full schemas.
        This reduces prompt size significantly.
        """
        lines = ["Available tools (use search_tools to discover more details):"]

        # Group by category
        by_category: dict[str, list[ToolRegistryEntry]] = {}
        for entry in self._entries.values():
            if enabled_names is not None and entry.name not in enabled_names:
                continue
            category = entry.category
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(entry)

        for category in sorted(by_category):
            lines.append(f"\n[{category}]")
            for entry in sorted(by_category[category], key=lambda e: e.name):
                lines.append(f"  - {entry.summary}")

        return "\n".join(lines)

    def get_full_schema(self, tool_name: str) -> dict[str, Any] | None:
        """Get the full schema for a specific tool (on-demand loading)."""
        tool = self._full_tools.get(tool_name)
        if tool is None:
            return None

        entry = self._entries.get(tool_name)
        if entry:
            entry.schema_loaded = True

        # Return LangChain tool as bindable schema
        return {
            "name": getattr(tool, "name", tool_name),
            "description": getattr(tool, "description", ""),
            "args_schema": getattr(tool, "args_schema", None),
        }

    def get_full_tools(self, tool_names: list[str]) -> list[Any]:
        """Get full LangChain tool objects for binding."""
        result = []
        for name in tool_names:
            tool = self._full_tools.get(name)
            if tool is not None:
                result.append(tool)
                entry = self._entries.get(name)
                if entry:
                    entry.schema_loaded = True
        return result

    def get_always_loaded_tools(self) -> list[Any]:
        """Get tools that should always have full schemas loaded."""
        return [
            self._full_tools[name]
            for name in self._always_loaded
            if name in self._full_tools
        ]

    def search_tools(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Search for tools by keyword.

        Returns matching tools with name, description, category, and tags.
        """
        if not query:
            return [
                {
                    "name": e.name,
                    "description": e.description[:200],
                    "category": e.category,
                    "tags": list(e.tags),
                }
                for e in sorted(self._entries.values(), key=lambda e: e.name)[:limit]
            ]

        query_tokens = set(re.findall(r"[a-z0-9_]+", query.lower()))
        scored: list[tuple[int, ToolRegistryEntry]] = []

        for entry in self._entries.values():
            haystack = f"{entry.name} {entry.description} {entry.category} {' '.join(entry.tags)}".lower()
            score = sum(1 for token in query_tokens if token in haystack)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "name": e.name,
                "description": e.description[:200],
                "category": e.category,
                "tags": list(e.tags),
                "relevance_score": score,
            }
            for score, e in scored[:limit]
        ]

    @property
    def tool_count(self) -> int:
        return len(self._entries)

    @property
    def loaded_count(self) -> int:
        return sum(1 for e in self._entries.values() if e.schema_loaded)

    def get_stats(self) -> dict[str, Any]:
        """Get registry statistics."""
        categories = {}
        for e in self._entries.values():
            categories[e.category] = categories.get(e.category, 0) + 1
        return {
            "total_tools": self.tool_count,
            "schemas_loaded": self.loaded_count,
            "categories": categories,
        }
