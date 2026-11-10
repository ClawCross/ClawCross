"""Minimal stdio MCP connector; it has no tool execution authority of its own."""
import sys
import os
# Executing this file must not let sibling external/http.py shadow stdlib http.
if sys.path and sys.path[0] == os.path.dirname(__file__):
    sys.path.pop(0)
import json
import httpx
from mcp.server.fastmcp import FastMCP

class AgentBridgeMCP(FastMCP):
    async def list_tools(self):
        tools = await super().list_tools()
        try:
            catalog = await request({'action':'catalog'})
        except (httpx.HTTPError, ValueError, KeyError):
            return tools
        description = catalog.get('description')
        if not isinstance(description,str) or not description:
            return tools
        return [tool.model_copy(update={'description':description}) if tool.name=='tool_search' else tool for tool in tools]


mcp = AgentBridgeMCP('ClawCross')


async def request(body):
    # The backend bounds approval waits and command execution. A separate
    # HTTP read deadline must not terminate a tool while its human is deciding.
    read_timeout = 10 if body.get('action')=='catalog' else None
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=read_timeout, write=30, pool=10), trust_env=False) as client:
        response = await client.post(os.environ['CLAWCROSS_BRIDGE_URL'], json=body,
            headers={'Authorization': 'Bearer ' + os.environ['CLAWCROSS_BRIDGE_TOKEN']})
        if response.status_code != 200:
            return {'ok': False, 'error': response.json().get('detail', 'Tool request rejected')}
        return response.json()


@mcp.tool()
async def tool_search(query: str) -> str:
    """Find ClawCross tools and their exact parameters. Search first when parameters are uncertain."""
    return json.dumps(await request({'action': 'search', 'query': query}), ensure_ascii=False)


@mcp.tool()
async def tool_call(tool_name: str, arguments_json: str) -> str:
    """Call an enabled ClawCross tool with the JSON object described by tool_search.

    Identity is supplied by the connector. Do not send username or source_session.
    The server enforces the Agent's tool list, command rules and approval policy.
    """
    if len(arguments_json) > 200_000:
        return json.dumps({'ok': False, 'error': 'Arguments too large'})
    try:
        arguments = json.loads(arguments_json)
    except ValueError:
        return json.dumps({'ok': False, 'error': 'arguments_json must be a JSON object'})
    if not isinstance(arguments, dict):
        return json.dumps({'ok': False, 'error': 'arguments_json must be a JSON object'})
    return json.dumps(await request({'action': 'call', 'name': tool_name, 'arguments': arguments}), ensure_ascii=False)


if __name__ == '__main__':
    mcp.run(transport='stdio')
