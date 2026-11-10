"""Startup must preserve the wire tool contract without starting every MCP server."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from webot.engine.agent import TeamAgent


@pytest.mark.asyncio
async def test_local_catalogue_matches_mcp_and_still_executes_through_stdio(tmp_path):
    engine = TeamAgent(str(Path(__file__).parents[1] / 'src/backend'), str(tmp_path / 'checkpoints'))
    try:
        with patch('asyncio.create_subprocess_exec', side_effect=AssertionError('Startup spawned a tool server')):
            await engine.startup()
        wire = await engine._mcp_client.get_tools()
        def schema(tool):
            arguments = tool.args_schema
            if not isinstance(arguments, dict):
                arguments = arguments.model_json_schema()
            return (tool.name, tool.description, arguments)
        assert [schema(tool) for tool in engine.mcp_tools] == [schema(tool) for tool in wire]
        tool = next(tool for tool in engine.mcp_tools if tool.name == 'show_ui_panel')
        result = await tool.ainvoke({'title':'Startup verification', 'html':'<p>Ready</p>'})
        assert 'clawcross_ui_panel_v1' in json.dumps(result)
    finally:
        await engine.shutdown()
