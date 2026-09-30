"""A bounded, isolated interactive panel for the Studio conversation."""

import json

from webot.mcp_tool_docs import DocumentedFastMCP as FastMCP


mcp = FastMCP("Conversation UI")
PANEL_KIND = "clawcross_ui_panel_v1"


def build_ui_panel(title: str, html: str, css: str = "", javascript: str = "") -> str:
    """Return a versioned payload; the browser runs it in a sandboxed iframe."""
    fields = {"title": title, "html": html, "css": css, "javascript": javascript}
    limits = {"title": 100, "html": 24000, "css": 12000, "javascript": 12000}
    for name, value in fields.items():
        if not isinstance(value, str) or len(value) > limits[name]:
            raise ValueError(f"{name} must be text of at most {limits[name]} characters")
    if not title.strip() or not html.strip():
        raise ValueError("title and html are required")
    return json.dumps({"kind": PANEL_KIND, **fields}, ensure_ascii=False)


@mcp.tool()
async def show_ui_panel(title: str, html: str, css: str = "", javascript: str = "") -> str:
    """Show an interactive panel within the current Studio chat turn.

    Use this when a small form, chart, or interactive preview helps answer the
    user's request. The panel has no access to the conversation, cookies, or
    network. Keep important results in your normal text response too.

    :param title: Short accessible panel heading.
    :param html: HTML body for the panel.
    :param css: Optional CSS scoped to this isolated panel.
    :param javascript: Optional JavaScript for local interaction in the panel.
    """
    try:
        return build_ui_panel(title, html, css, javascript)
    except ValueError as exc:
        return f"Panel was not shown: {exc}. Shorten the content and try again."


if __name__ == "__main__":
    mcp.run(transport="stdio")
