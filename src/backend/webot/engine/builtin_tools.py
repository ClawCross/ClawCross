"""Read bundled tool schemas locally; execute them through their existing MCP transport."""
from importlib import import_module
from pathlib import Path

from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool


async def load_builtin_tools(client):
    """The bundled servers share dependencies, so listing them needs no subprocesses.

    These are the application's fixed stdio connections, not user-supplied MCP
    servers. The converter keeps each original connection for actual tool calls,
    including MCP result/error conversion and the per-instance environment.
    """
    tools = []
    for name, connection in client.connections.items():
        module = import_module('webot.mcp.' + Path(connection['args'][0]).stem)
        for definition in await module.mcp.list_tools():
            tools.append(convert_mcp_tool_to_langchain_tool(
                None, definition, connection=connection, server_name=name,
                callbacks=client.callbacks, tool_interceptors=client.tool_interceptors,
                tool_name_prefix=client.tool_name_prefix, handle_tool_errors=client.handle_tool_errors,
            ))
    return tools
