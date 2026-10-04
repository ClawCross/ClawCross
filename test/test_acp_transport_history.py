"""Reading acpx history stays local and keeps transport errors visible."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from agents.runtime import ControlError
from external.acp import AcpRuntime
from external.acpx import AcpxError


@pytest.mark.asyncio
async def test_captured_history_is_normalized_without_prompting_or_creating_session():
    acpx = SimpleNamespace(to_acpx_session_name=Mock(return_value='owner-agent'),
        read_session=AsyncMock(return_value={'entries':[
            {'role':'user','textPreview':'hello'}, {'role':'assistant','textPreview':'reply'},
            {'role':'system','textPreview':'not a conversation message'}]}),
        prompt=AsyncMock(), ensure_session=AsyncMock())
    agent = SimpleNamespace(platform='codex')
    with patch('external.acp.adapter', return_value=acpx), patch('external.acp.session.runtime_session', return_value='agent'):
        messages = await AcpRuntime().transport_history(agent, 200)
    assert messages == [{'role':'user','content':'hello'}, {'role':'assistant','content':'reply'}]
    acpx.read_session.assert_awaited_once_with(tool='codex', name='owner-agent', tail=200)
    acpx.prompt.assert_not_awaited()
    acpx.ensure_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_read_failure_does_not_silently_show_empty_history():
    acpx = SimpleNamespace(to_acpx_session_name=Mock(return_value='agent'), read_session=AsyncMock(side_effect=AcpxError('record not found')))
    with patch('external.acp.adapter', return_value=acpx), patch('external.acp.session.runtime_session', return_value='agent'):
        with pytest.raises(ControlError, match='record not found'):
            await AcpRuntime().transport_history(SimpleNamespace(platform='codex'), 20)
