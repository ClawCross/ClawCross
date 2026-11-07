"""Public group joins preserve user isolation and cannot start disabled native tools."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi import FastAPI

from agents.store import AgentStore, ACPX, WEBOT
from agents.gateway import AgentGateway
from agents.messages import AgentReply, AgentMessage, DeliveryReceipt
from agents.routes import create_agents_router
from agents.openai import create_openai_router, response, chunk, streaming
from agents.runtime import ControlError
from external.acp import adapter, AcpRuntime
from external.acp_launcher import default_agent_command
from external.acp_settings import validate_settings
from groups.client import ClientStore, GroupClient
from src.backend.common import local_accounts
from src.cli.commands.webot_acp import WeBotACP, RpcError
from webot.runtime_settings import ApprovalSettings


def connection(store):
    alias = store.save('alice', 'https://example.test', {'group':{'group_id':'group'},
                       'connection_id':'connection', 'token':'private'})
    return store.get('alice', alias)


def test_default_identity_is_first_account_and_fresh_install_has_default(tmp_path, monkeypatch):
    monkeypatch.setattr(local_accounts, 'USERS_FILE', tmp_path/'users.json')
    monkeypatch.setattr(local_accounts, 'USER_FILES_DIR', tmp_path/'user_files')
    monkeypatch.delenv('CLAW_USER', raising=False); monkeypatch.delenv('CLI_USER', raising=False)
    assert local_accounts.default_account(create=True) == 'default'
    assert local_accounts.accounts() == ['default']
    (tmp_path/'users.json').write_text(json.dumps({'first':'private-hash','second':'another-hash'}))
    assert local_accounts.default_account(create=True) == 'first'
    assert local_accounts.select_account('second', create=True) == 'second'
    assert 'private-hash' not in str(local_accounts.accounts())
    with pytest.raises(ValueError): local_accounts.select_account('../outside', create=True)


def test_native_off_blocks_execution_before_component_or_process_probe(tmp_path):
    store = AgentStore(tmp_path/'agents.db')
    agent = store.create('alice', driver=ACPX, config={'platform':'codex','meta':{'acp':{'native_tools':False}}})
    with patch('ops.components.binary_path') as binary:
        with pytest.raises(ControlError, match='已阻止启动'):
            adapter(agent)
        binary.assert_not_called()
    assert validate_settings(agent, {'native_tools':False}) == {'native_tools':False}
    with pytest.raises(ValueError): validate_settings(agent, {'native_tools':'false'})


def test_sandbox_is_required_and_deepseek_uses_official_acp_launcher():
    assert ApprovalSettings().command_sandbox == 'auto'
    assert default_agent_command('deepseek-harness', detect_local=False) == 'dsh --profile acp'


@pytest.mark.asyncio
async def test_disabled_agent_stays_a_member_without_waking_or_spending(tmp_path):
    agents = AgentStore(tmp_path/'agents.db')
    agent = agents.create('alice', driver=ACPX, config={'platform':'codex','meta':{'acp':{'native_tools':False}}})
    store = ClientStore(tmp_path/'client.db')
    gateway = SimpleNamespace(inbox=AsyncMock())
    client = GroupClient(store, agents, gateway)
    row = connection(store)
    await client._wake(row, {}, agent, {}, 1, AgentMessage(text='run a destructive tool'), [])
    gateway.inbox.assert_not_awaited()
    assert store.delivered(row, 1, agent.agent_id)
    assert (await AcpRuntime(agents).status(agent))['state'] == 'paused'


@pytest.mark.asyncio
async def test_toolless_group_reply_uses_normal_post_and_idempotency_key(tmp_path):
    agents = AgentStore(tmp_path/'agents.db')
    agent = agents.create('alice', driver=ACPX, config={'platform':'codex','meta':{'acp':{'clawcross_tools':False}}})
    gateway = SimpleNamespace(inbox=AsyncMock(return_value=DeliveryReceipt(accepted=True)))
    client = GroupClient(ClientStore(tmp_path/'client.db'), agents, gateway)
    client.post = AsyncMock()
    client.metadata = Mock(return_value={})
    row = connection(client.store)
    await client._wake(row, {}, agent, {}, 7, AgentMessage(text='hello'), [])
    call = gateway.inbox.call_args
    assert call.kwargs['context']['group_auto_reply'] is True
    await call.kwargs['on_complete'](AgentReply(ok=True,content='reply'))
    client.post.assert_awaited_once_with('alice',row['alias'],agent.agent_id,'reply',client_msg_id=f'chat-reply:7:{agent.agent_id}')


class TestWeBotACP:
    @pytest.fixture
    def api(self, tmp_path):
        store = AgentStore(tmp_path/'agents.db')
        alice = store.create('alice', driver=WEBOT, config={'tools':[]}, name='Alice')
        bob = store.create('bob', driver=WEBOT, config={'tools':[]}, name='Bob')
        runtime = SimpleNamespace(status=AsyncMock(return_value={'state':'idle'}), is_busy=lambda _:False)
        calls = []
        async def chat(agent, request):
            calls.append((agent,request))
            async def events():
                yield chunk(completion_id='1',content='hello',model='webot')
                yield chunk(completion_id='1',model='webot',finish_reason='stop')
                yield 'data: [DONE]\n\n'
            return streaming(events())
        gateway = SimpleNamespace(status=runtime.status, runtime=lambda _:runtime, chat=chat,
                                  history=AsyncMock(return_value=[{'role':'user','content':'saved question'},
                                                               {'role':'assistant','content':'saved answer'}]))
        app = FastAPI()
        common = dict(internal_token='test-token',verify_password=lambda *_:False,store=store,gateway=gateway)
        app.include_router(create_agents_router(**common))
        app.include_router(create_openai_router(**common))
        return app, alice, bob, calls

    @pytest.mark.asyncio
    async def test_acp_reuses_registered_agent_history_and_frontend_chat_gateway(self, api):
        app, alice, bob, calls = api
        emitted = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test',
                headers={'Authorization':'Bearer test-token:alice'}) as client:
            server = WeBotACP(client,owner='alice',agent_id=alice.agent_id,emit=emitted.append)
            await server.dispatch('initialize',{'protocolVersion':1})
            assert (await server.dispatch('session/new',{'cwd':'/private/host/files','mcpServers':[]}))['sessionId']==alice.agent_id
            await server.dispatch('session/load',{'sessionId':alice.agent_id})
            assert [p['params']['update']['content']['text'] for p in emitted] == ['saved question','saved answer']
            assert (await server.dispatch('session/prompt',{'sessionId':alice.agent_id,'prompt':[{'type':'text','text':'hello'}]}))['stopReason']=='end_turn'
            assert calls[0][0].agent_id==alice.agent_id
            assert not calls[0][1].cli_workspace and calls[0][1].session_mode is None
            assert emitted[-1]['params']['update']['content']['text']=='hello'
            assert [row['sessionId'] for row in (await server.dispatch('session/list',{}))['sessions']]==[alice.agent_id]
            with pytest.raises(RpcError): await server.dispatch('session/load',{'sessionId':bob.agent_id})
            with pytest.raises(RpcError): await server.dispatch('session/new',{'mcpServers':[{'name':'escape','command':'sh'}]})
