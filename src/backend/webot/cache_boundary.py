"""
System Prompt Cache Boundary Manager – Claude Code style.

Features:
- Splits system prompt into stable (cacheable) and dynamic sections
- Inserts cache control markers at optimal boundaries
- Tracks which system prompt sections change between turns
- Supports Anthropic-style cache_control breakpoints

The key insight from Claude Code: place cache boundaries between
the static system identity (rarely changes) and the dynamic runtime
context (changes every turn). This maximizes cache hits and reduces
cost by up to 90% on system prompt tokens.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import SystemMessage


@dataclass
class CacheSection:
    """A section of the system prompt with cacheability metadata."""
    key: str
    content: str
    cacheable: bool = True
    priority: int = 0  # Higher = more static/cacheable

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()[:12]


@dataclass
class CacheBoundaryResult:
    """Result of cache boundary computation."""
    sections: list[CacheSection]
    cache_breakpoint_index: int  # Index where dynamic content starts
    static_hash: str  # Hash of all static content for cache key
    total_chars: int
    static_chars: int
    dynamic_chars: int


class SystemPromptCacheManager:
    """
    Manages system prompt construction with cache boundary optimization.

    Sections are ordered by stability:
    1. Identity (model name, capabilities) – almost never changes
    2. Tools (available tools list) – changes rarely (when tools enable/disable)
    3. User profile – changes per session but stable within session
    4. Policies and rules – stable within session
    5. Runtime context – changes every turn (plan, todos, inbox, etc.)
    6. Budget notices – changes every turn

    Cache breakpoint is placed between 4 and 5.
    """

    def __init__(self):
        self._sections: dict[str, CacheSection] = {}
        self._section_order: list[str] = [
            "identity",
            "tools",
            "user_profile",
            "user_skills",
            "policies",
            "session_persona",
            "chat_rules",
            # --- CACHE BOUNDARY ---
            "session_mode",
            "runtime_context",
            "budget_notice",
            "tool_status_notice",
        ]
        # Sections above this index are considered static/cacheable
        self._boundary_key = "session_mode"

    def set_section(self, key: str, content: str, cacheable: bool | None = None) -> None:
        """Set or update a section of the system prompt."""
        if cacheable is None:
            # Auto-determine based on section order
            try:
                idx = self._section_order.index(key)
                boundary_idx = self._section_order.index(self._boundary_key)
                cacheable = idx < boundary_idx
            except ValueError:
                cacheable = False

        priority = 0
        try:
            priority = len(self._section_order) - self._section_order.index(key)
        except ValueError:
            pass

        self._sections[key] = CacheSection(
            key=key,
            content=content.strip(),
            cacheable=cacheable,
            priority=priority,
        )

    def remove_section(self, key: str) -> None:
        """Remove a section."""
        self._sections.pop(key, None)

    def compute_boundary(self) -> CacheBoundaryResult:
        """
        Compute the optimal cache boundary and return structured result.
        """
        ordered_sections: list[CacheSection] = []
        for key in self._section_order:
            if key in self._sections and self._sections[key].content:
                ordered_sections.append(self._sections[key])

        # Also add any sections not in the predefined order (at the end)
        known_keys = set(self._section_order)
        for key, section in self._sections.items():
            if key not in known_keys and section.content:
                ordered_sections.append(section)

        # Find boundary index
        boundary_idx = len(ordered_sections)  # Default: everything is static
        for i, section in enumerate(ordered_sections):
            if not section.cacheable:
                boundary_idx = i
                break

        static_sections = ordered_sections[:boundary_idx]
        dynamic_sections = ordered_sections[boundary_idx:]

        static_content = "\n\n".join(s.content for s in static_sections)
        dynamic_content = "\n\n".join(s.content for s in dynamic_sections)
        static_hash = hashlib.sha256(static_content.encode("utf-8")).hexdigest()[:16]

        return CacheBoundaryResult(
            sections=ordered_sections,
            cache_breakpoint_index=boundary_idx,
            static_hash=static_hash,
            total_chars=len(static_content) + len(dynamic_content),
            static_chars=len(static_content),
            dynamic_chars=len(dynamic_content),
        )

    def build_system_messages(self) -> list[SystemMessage]:
        """
        Build system messages with cache boundary markers.

        Returns TWO system messages for Anthropic-style cache control:
        1. Static system message (cacheable)
        2. Dynamic system message (not cacheable)

        For OpenAI/others, returns a single combined message.
        """
        boundary = self.compute_boundary()
        static_parts = []
        dynamic_parts = []

        for i, section in enumerate(boundary.sections):
            if i < boundary.cache_breakpoint_index:
                static_parts.append(section.content)
            else:
                dynamic_parts.append(section.content)

        messages = []
        if static_parts:
            static_content = "\n\n".join(static_parts)
            msg = SystemMessage(content=static_content)
            # Add cache control metadata (Anthropic cache_control)
            msg.additional_kwargs = {"cache_control": {"type": "ephemeral"}}
            messages.append(msg)

        if dynamic_parts:
            dynamic_content = "\n\n".join(dynamic_parts)
            messages.append(SystemMessage(content=dynamic_content))

        if not messages:
            messages.append(SystemMessage(content=""))

        return messages

    def build_single_prompt(self) -> str:
        """Build a single combined system prompt string."""
        boundary = self.compute_boundary()
        parts = [s.content for s in boundary.sections if s.content]
        return "\n\n".join(parts)

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache boundary statistics."""
        boundary = self.compute_boundary()
        return {
            "total_sections": len(boundary.sections),
            "cache_breakpoint": boundary.cache_breakpoint_index,
            "static_hash": boundary.static_hash,
            "total_chars": boundary.total_chars,
            "static_chars": boundary.static_chars,
            "dynamic_chars": boundary.dynamic_chars,
            "cache_ratio": (
                round(boundary.static_chars / boundary.total_chars, 3)
                if boundary.total_chars > 0
                else 0.0
            ),
        }
