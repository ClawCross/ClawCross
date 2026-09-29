"""Split a tool's docstring into its description and per-argument documentation.

FastMCP hands a tool's raw docstring to the model as its description, argument
notes and all, while the input schema's properties carry no description at all.
That puts argument documentation in the wrong place twice over: the notes for
arguments the runtime injects (``username``, the session argument) still reach
the model after the schema hides those arguments, and a strict schema — the only
thing a decoder constrained to it will honour — says nothing about what any
argument means.

``DocumentedFastMCP`` registers tools exactly as ``FastMCP`` does, then moves
each ``:param x:`` / ``Args:`` entry onto the matching schema property and keeps
only the prose as the description. Docstrings stay the single place to write
tool documentation.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any

from mcp.server.fastmcp import FastMCP

_REST_PARAM = re.compile(r"^:param\s+(?:[^\s:]+\s+)?(?P<name>\w+)\s*:\s*(?P<text>.*)$")
_REST_OTHER = re.compile(r"^:(?:returns?|rtype|raises?\b[^:]*|type\s+\w+)\s*:\s*(?P<text>.*)$")
_GOOGLE_ARGS = re.compile(r"^(?:Args|Arguments|Parameters|Params|参数)\s*[:：]\s*$")
_GOOGLE_SECTION = re.compile(r"^(?:Returns?|Raises|Yields|Examples?|Notes?|返回|返回值)\s*[:：]\s*$")
_GOOGLE_ENTRY = re.compile(r"^(?P<name>\w+)\s*(?:\([^)]*\))?\s*[:：]\s*(?P<text>.*)$")


@dataclass
class ToolDoc:
    description: str
    params: dict[str, str] = field(default_factory=dict)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def parse_tool_docstring(doc: str | None) -> ToolDoc:
    """Split *doc* into prose and ``{argument: text}``.

    Recognises reST (``:param name: text``) and Google (``Args:`` followed by
    indented ``name: text``) entries, with continuation lines. ``:return:`` and
    ``Returns:`` sections are dropped: the description is paid for on every
    request, and what a tool returns is visible the first time it is called.
    Anything about the result the model needs up front belongs in the prose.
    """
    lines = inspect.cleandoc(doc or "").splitlines()
    kept: list[str] = []
    params: dict[str, str] = {}
    current: str | None = None  # argument whose text continuation lines extend
    in_args = False
    in_returns = False
    args_indent = 0
    entry_indent = 1 << 30  # indent of the current Args: block's entries, once one is seen

    for line in lines:
        stripped = line.strip()

        if in_returns:
            # Google "Returns:" block: skip its indented lines.
            if not stripped or _indent(line) > args_indent:
                continue
            in_returns = False

        if in_args:
            if not stripped:
                current = None
                continue
            if _indent(line) > args_indent:
                entry = _GOOGLE_ENTRY.match(stripped)
                if entry and (current is None or _indent(line) <= entry_indent):
                    current = entry["name"]
                    entry_indent = _indent(line)
                    params[current] = entry["text"].strip()
                elif current is not None:
                    params[current] = f"{params[current]} {stripped}".strip()
                continue
            in_args = False
            current = None

        if _GOOGLE_ARGS.match(stripped):
            in_args = True
            args_indent = _indent(line)
            entry_indent = 1 << 30
            current = None
            continue
        if re.match(r"^(?:Returns?|返回|返回值)\s*[:：]\s*$", stripped):
            in_returns = True
            args_indent = _indent(line)
            current = None
            continue

        rest = _REST_PARAM.match(stripped)
        if rest:
            current = rest["name"]
            params[current] = rest["text"].strip()
            continue
        if _REST_OTHER.match(stripped):
            current = None
            continue
        if current is not None and stripped and not _GOOGLE_SECTION.match(stripped):
            # Continuation of a reST :param: entry.
            params[current] = f"{params[current]} {stripped}".strip()
            continue
        current = None
        kept.append(line)

    description = "\n".join(kept).strip()
    description = re.sub(r"\n{3,}", "\n\n", description)
    return ToolDoc(description=description, params={k: v for k, v in params.items() if v})


def apply_param_docs(parameters: dict[str, Any], params: dict[str, str]) -> list[str]:
    """Write *params* onto the schema's properties; return names it had no property for."""
    properties = parameters.get("properties") or {}
    unknown = []
    for name, text in params.items():
        prop = properties.get(name)
        if prop is None:
            unknown.append(name)
            continue
        prop.setdefault("description", text)
    return unknown


class DocumentedFastMCP(FastMCP):
    """``FastMCP`` whose tools carry their argument docs in the schema, not the description."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # tool name -> documented arguments its signature does not have (stale docs).
        self.stale_param_docs: dict[str, list[str]] = {}

    def add_tool(self, fn, name=None, title=None, description=None, **kwargs) -> None:
        doc = parse_tool_docstring(fn.__doc__)
        super().add_tool(
            fn,
            name=name,
            title=title,
            description=description if description is not None else doc.description,
            **kwargs,
        )
        tool = self._tool_manager.get_tool(name or fn.__name__)
        if tool is not None:
            unknown = apply_param_docs(tool.parameters, doc.params)
            if unknown:
                self.stale_param_docs[tool.name] = sorted(unknown)
