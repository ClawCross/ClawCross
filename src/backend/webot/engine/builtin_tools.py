"""Read bundled tool schemas locally; execute them through their existing MCP transport."""
from importlib import import_module
from inspect import signature
from pathlib import Path

from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool


async def load_builtin_tools(client):
    """The bundled servers share dependencies, so listing them needs no subprocesses.

    These are the application's fixed stdio connections, not user-supplied MCP
    servers. The converter keeps each original connection for actual tool calls,
    including MCP result/error conversion and the per-instance environment.
    """
    tools = []
    options = {
        'callbacks': client.callbacks,
        'tool_interceptors': client.tool_interceptors,
        'tool_name_prefix': client.tool_name_prefix,
    }
    # 0.2.x has neither this client attribute nor this converter parameter.
    # Keep its existing error semantics; newer releases retain the user's policy.
    if 'handle_tool_errors' in signature(convert_mcp_tool_to_langchain_tool).parameters:
        options['handle_tool_errors'] = getattr(client, 'handle_tool_errors', True)
    for name, connection in client.connections.items():
        module = import_module('webot.mcp.' + Path(connection['args'][0]).stem)
        for definition in await module.mcp.list_tools():
            tools.append(convert_mcp_tool_to_langchain_tool(
                None, definition, connection=connection, server_name=name,
                **options,
            ))
    return tools
