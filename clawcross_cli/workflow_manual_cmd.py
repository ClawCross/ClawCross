"""``clawcross workflow-manual`` — print the canonical OASIS workflowpy authoring manual.

Thin CLI wrapper around ``oasis.workflow_rules.get_workflow_writing_rules``.
The same function is exposed via the MCP tool ``get_workflow_writing_rules``
in ``src/mcp_servers/oasis.py`` — both surfaces read the same single source
of truth so the manual never drifts between channels.
"""
from __future__ import annotations


def handle_workflow_manual_command(args: list[str] | None = None) -> str:
    del args  # no sub-args yet; reserved for future --section / --raw flags
    try:
        from oasis.workflow_rules import get_workflow_writing_rules
    except Exception as exc:
        return f"❌ failed to load workflow manual: {exc}"
    return get_workflow_writing_rules()
