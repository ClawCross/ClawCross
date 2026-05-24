import sys as _sys
import os as _os

_src_dir = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _src_dir not in _sys.path:
    _sys.path.insert(0, _src_dir)

import json
from mcp.server.fastmcp import FastMCP

from webot.lsp import format_diagnostics, probe_diagnostics

mcp = FastMCP("WorkspaceLSP")


@mcp.tool()
async def lsp(
    username: str,
    file: str,
    op: str = "diagnostics",
    line: int = 0,
    col: int = 0,
    new_name: str = "",
    session_id: str = "",
    timeout_seconds: int = 30,
    max_diagnostics: int = 50,
    output_format: str = "text",
) -> str:
    """
    Workspace LSP-style front door.

    Currently implements op=diagnostics for Python, TypeScript, JavaScript,
    and JSON. Other operations return a deterministic stub so agents can plan
    around the missing capability instead of guessing.
    """
    normalized_op = (op or "diagnostics").strip().lower()
    if normalized_op != "diagnostics":
        if normalized_op == "rename" and not (new_name or "").strip():
            return json.dumps(
                {
                    "ok": False,
                    "op": normalized_op,
                    "file": file,
                    "error": "lsp rename requires new_name",
                },
                ensure_ascii=False,
                indent=2,
            )
        suffix = f":{line}:{col}" if line or col else ""
        rename_note = f" new_name={new_name}" if normalized_op == "rename" and new_name else ""
        return f"[stub] [lsp {normalized_op} at {file}{suffix}{rename_note} is not implemented yet]"

    payload = probe_diagnostics(
        username=username,
        session_id=session_id,
        file=file,
        timeout_seconds=timeout_seconds,
        max_diagnostics=max_diagnostics,
    )
    if (output_format or "").strip().lower() == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return format_diagnostics(payload)


@mcp.tool()
async def workspace_diagnostics(
    username: str,
    file: str,
    session_id: str = "",
    timeout_seconds: int = 30,
    max_diagnostics: int = 50,
    output_format: str = "text",
) -> str:
    """
    Run best-effort diagnostics for one workspace file.
    """
    payload = probe_diagnostics(
        username=username,
        session_id=session_id,
        file=file,
        timeout_seconds=timeout_seconds,
        max_diagnostics=max_diagnostics,
    )
    if (output_format or "").strip().lower() == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return format_diagnostics(payload)


if __name__ == "__main__":
    mcp.run()
