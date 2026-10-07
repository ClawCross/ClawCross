"""Ordinary tool-table permissions, ownership, alarm scope and chat delivery."""

import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/backend'))

from agents import store as agent_store
from agents.messages import AgentMessage, AgentReply, DeliveryReceipt
from agents.store import AgentStore, WEBOT, ACPX
from teams.store import TeamStore
from webot import runtime_settings, runtime_store, workspace, skills
from webot.mcp import management, scheduler, session


@pytest.fixture
def registry(monkeypatch,tmp_path):
    store=AgentStore(tmp_path/'agents.db')
    users=tmp_path/'users'
    teams=TeamStore(store,users)
    monkeypatch.setattr(agent_store,'get_store',lambda:store)
    monkeypatch.setattr(management,'get_store',lambda:store)
    monkeypatch.setattr(management,'get_team_store',lambda *_:teams)
    monkeypatch.setattr(runtime_settings,'USER_FILES_DIR',users)
    monkeypatch.setattr(runtime_store,'AGENT_RUNTIME_DB_DIR',tmp_path/'runtime')
    for module in (workspace,skills):
        monkeypatch.setattr(module,'USER_FILES_DIR',users)
        monkeypatch.setattr(module,'WORKSPACE_DIR',tmp_path/'workspace')
    from oasis import experts
    monkeypatch.setattr(experts,'USER_FILES_DIR',users)
    store.create('alice',driver=WEBOT,agent_id='manager',config={'tools':['manage_team','manage_group','manage_agent_alarms']})
    store.create('alice',driver=WEBOT,agent_id='plain',config={'tools':['read_file']})
    store.create('alice',driver=ACPX,agent_id='native',name='Code researcher',config={'platform':'codex','api_key':'hidden-key','meta':{'acp':{'tools':['read_file']}}})
    store.create('bob',driver=WEBOT,agent_id='bobs-agent',config={})
    return store,teams,tmp_path


async def test_team_management_uses_the_ordinary_tool_table_and_not_team_membership(registry):
    store,teams,_=registry
    denied=json.loads(await management.manage_team('alice','create','Denied',source_session='missing'))
    assert not denied['ok'] and not teams.exists('alice','Denied')
    result=json.loads(await management.manage_team('alice','create','Project',source_session='manager'))
    assert result['ok'] and not store.require('alice','manager').teams
    result=json.loads(await management.manage_team('alice','add_member','Project',{'agent':'native','role':'Researcher'},'manager'))
    assert result['ok'] and store.require('alice','native').teams==['Project']
    result=json.loads(await management.manage_team('alice','add_member','Project',{'agent':'bobs-agent'},'manager'))
    assert not result['ok'] and not store.require('bob','bobs-agent').teams
    result=json.loads(await management.manage_team('alice','update_member','Project',{'agent':'native','is_lead':'false'},'manager'))
    assert not result['ok'] and not teams.member('alice','Project','native').is_lead
    result=json.loads(await management.manage_team('alice','create','__default__',source_session='manager'))
    assert not result['ok']
    result=json.loads(await management.manage_team('alice','save_persona','Project',{'tag':'research','name':'Research','persona':'Find primary evidence'},'manager'))
    assert result['ok']
    assert json.loads(await management.manage_team('alice','list_personas','Project',source_session='manager'))['data'][0]['tag']=='research'
    assert json.loads(await management.manage_team('alice','remove_member','Project',{'agent':'native'},'manager'))['ok']
    assert not store.require('alice','native').teams
    assert json.loads(await management.manage_team('alice','delete','Project',source_session='manager'))['ok']
    assert store.get('alice','native') is not None


async def test_all_tools_agent_can_use_management_without_special_opt_in(registry):
    store,teams,_=registry
    store.update('alice','plain',config={'tools':None})
    response=json.loads(await management.manage_team('alice','create','AllTools',source_session='plain'))
    assert response['ok'] and teams.exists('alice','AllTools')


async def test_disabled_management_is_blocked_by_standard_tool_node(registry):
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from webot.engine.agent import UserAwareToolNode
    store,_,_=registry
    execute=AsyncMock(return_value='must not execute')
    tool=StructuredTool(name='manage_team',description='Team operations',args_schema={'type':'object','properties':{}},coroutine=execute)
    node=UserAwareToolNode([tool],find_internal_session_meta_fn=lambda owner,aid:store.require(owner,aid).config)
    output=await node({'user_id':'alice','session_id':'plain','session_mode':'bypass',
        'messages':[AIMessage(content='',tool_calls=[{'id':'blocked','name':'manage_team','args':{}}])]}, {})
    execute.assert_not_awaited()
    assert any('enabled_tools' in str(message.content) for message in output['messages'])


async def test_team_workflow_lifecycle_uses_existing_scoped_directories(registry):
    _,teams,root=registry
    for owner,team in [('alice','Project'),('alice','Other'),('bob','Project')]:teams.create(owner,team)
    yaml='plan:\n  - id: review\n    manual: {content: Review the work}\n'
    python="await ctx.publish('Report')\n"
    for name,kind,content in [('review','yaml',yaml),('report.py','python',python)]:
        reply=json.loads(await management.manage_team('alice','save_workflow','Project',{'name':name,'kind':kind,'content':content},'manager'))
        assert reply['ok'] and Path(reply['data']['path']).is_relative_to(root/'users/alice/teams/Project/oasis')
        saved=json.loads(await management.manage_team('alice','get_workflow','Project',{'name':name,'kind':kind},'manager'))
        assert saved['data']['content']==content
    listing=json.loads(await management.manage_team('alice','list_workflows','Project',source_session='manager'))
    assert {row['name'] for row in listing['data']['workflows']}=={'review.yaml','report.py'}
    other=json.loads(await management.manage_team('alice','list_workflows','Other',source_session='manager'))
    assert other['data']['workflows']==[]
    denied=json.loads(await management.manage_team('alice','save_workflow','Project',{'name':'../../escape','content':yaml},'manager'))
    assert not denied['ok']
    assert not (root/'users/bob/teams/Project/oasis').exists()
    assert json.loads(await management.manage_team('alice','delete_workflow','Project',{'name':'review'},'manager'))['ok']
    assert not json.loads(await management.manage_team('alice','get_workflow','Project',{'name':'review'},'manager'))['ok']


async def test_team_alarms_stay_in_team_and_creation_requires_a_member(registry,monkeypatch):
    _,teams,_=registry
    teams.create('alice','Project');teams.add('alice','Project','native')
    tasks=[{'task_id':'mine','user_id':'alice','team':'Project','agent':'native','text':'Old','cron':'0 9 * * *','schedule_type':'cron','run_at':''},
           {'task_id':'other-team','user_id':'alice','team':'Other','agent':'native'},
           {'task_id':'other-user','user_id':'bob','team':'Project','agent':'native'}]
    request=AsyncMock(return_value=tasks)
    monkeypatch.setattr(management,'local_request',request)
    own=json.loads(await management.manage_team('alice','list_alarms','Project',source_session='manager'))
    assert [task['task_id'] for task in own['data']]==['mine']
    request.reset_mock()
    denied=json.loads(await management.manage_team('alice','create_alarm','Project',{'agent':'plain','text':'Reminder','cron':'0 9 * * *'},'manager'))
    assert not denied['ok'] and not request.called
    assert json.loads(await management.manage_team('alice','create_alarm','Project',{'agent':'native','text':'Reminder','cron':'0 9 * * *'},'manager'))['ok']
    assert request.await_args.args[3]['team']=='Project' and request.await_args.args[3]['user_id']=='alice'
    updated=json.loads(await management.manage_team('alice','update_alarm','Project',{'task_id':'mine','text':'New'},'manager'))
    assert updated['ok'] and request.await_args.args[1:3]==('PATCH','/tasks/mine')
    assert request.await_args.args[3]['text']=='New' and request.await_args.args[3]['team']=='Project'
    request.reset_mock()
    denied=json.loads(await management.manage_team('alice','delete_alarm','Project',{'task_id':'other-team'},'manager'))
    assert not denied['ok'] and request.await_count==1
    assert json.loads(await management.manage_team('alice','delete_alarm','Project',{'task_id':'mine'},'manager'))['ok']
    assert request.await_args.args==('alice','DELETE','/tasks/mine')


async def test_group_administration_checks_owner_and_validates_before_mutating(registry,monkeypatch):
    request=AsyncMock(return_value={'owner':'bob','group_id':'rg_x'})
    monkeypatch.setattr(management,'local_request',request)
    denied=json.loads(await management.manage_group('alice','rename','rg_x',{'title':'New'},'manager'))
    assert not denied['ok'] and request.await_count==1
    request.reset_mock();request.return_value={'owner':'alice','group_id':'rg_x'}
    invalid=json.loads(await management.manage_group('alice','external_access','rg_x',{'enabled':'false'},'manager'))
    assert not invalid['ok'] and request.await_count==1
    request.reset_mock()
    accepted=json.loads(await management.manage_group('alice','external_access','rg_x',{'enabled':False},'manager'))
    assert accepted['ok']
    assert request.await_args.args==('alice','POST','/groups/rg_x/external-access',{'enabled':False})
    request.reset_mock()
    denied=json.loads(await management.manage_group('alice','create',data={'title':'X','user_id':'bob'},source_session='manager'))
    assert not denied['ok'] and not request.called


async def test_session_lookup_includes_empty_and_native_agents_without_secrets(registry,monkeypatch):
    results=json.loads(await session.list_sessions('alice',query='research'))
    assert [row['agent_id'] for row in results['sessions']]==['native']
    assert len(json.loads(await session.list_sessions('alice'))['sessions'])==3
    details=json.loads(await session.get_session_details('alice','native'))
    assert details['ok'] and details['data']['platform']=='codex'
    assert 'hidden-key' not in json.dumps(details)
    assert details['data']['tools']==['read_file']
    assert not json.loads(await session.get_session_details('alice','bobs-agent'))['ok']
    request=AsyncMock(return_value={'messages':[{'content':'Hello'}],'api_key':'history-secret'})
    monkeypatch.setattr(management,'local_request',request)
    details=json.loads(await session.get_session_details('alice','native',history_limit=2))
    assert details['data']['history']['messages'][0]['content']=='Hello'
    assert 'history-secret' not in json.dumps(details)


async def test_high_privilege_alarms_cannot_target_other_users(registry,monkeypatch):
    request=AsyncMock(return_value=[{'task_id':'a','user_id':'alice','agent':'native'},
                                  {'task_id':'b','user_id':'alice','agent':'plain'},
                                  {'task_id':'c','user_id':'bob','agent':'native'}])
    monkeypatch.setattr(management,'local_request',request)
    assert [row['task_id'] for row in json.loads(await management.manage_agent_alarms('alice','list','native',source_session='manager'))['data']]==['a']
    assert not json.loads(await management.manage_agent_alarms('alice','delete','native',{'task_id':'b'},'manager'))['ok']
    request.reset_mock()
    assert not json.loads(await management.manage_agent_alarms('alice','create','bobs-agent',{'text':'run'},'manager'))['ok']
    assert not request.called
    assert json.loads(await management.manage_agent_alarms('alice','delete','native',{'task_id':'a'},'manager'))['ok']
    assert request.await_args.args==('alice','DELETE','/tasks/a')


async def test_own_alarms_cannot_read_or_delete_another_agents_alarm(registry,monkeypatch):
    calls=[]
    tasks=[{'task_id':'mine','user_id':'alice','agent':'plain','text':'Mine'},
           {'task_id':'other','user_id':'alice','agent':'native','text':'Other'}]
    def dispatch(request):
        calls.append(request)
        if request.method=='GET':return httpx.Response(200,json=tasks)
        if request.method=='POST':
            payload=json.loads(request.content)
            assert payload['agent']=='plain'
            return httpx.Response(200,json={'task_id':'new'})
        return httpx.Response(200,json={'ok':True})
    client_type=httpx.AsyncClient
    monkeypatch.setattr(scheduler.httpx,'AsyncClient',lambda *a,**kw:client_type(transport=httpx.MockTransport(dispatch)))
    assert 'Mine' in await scheduler.list_alarms('alice','plain')
    assert 'Other' not in await scheduler.list_alarms('alice','plain')
    assert '只能删除当前 Agent' in await scheduler.delete_alarm('alice','other','plain')
    assert not any(request.method=='DELETE' for request in calls)
    assert '已成功删除' in await scheduler.delete_alarm('alice','mine','plain')
    assert '已设置' in await scheduler.add_alarm('alice','0 1 * * *','Mine',session_id='plain')


async def test_pure_chat_delivers_text_via_framework_once_without_enabling_tools(registry,monkeypatch):
    from groups.client import ClientStore,GroupClient,service_url
    from groups.relay_store import RelayStore
    store,_,root=registry
    store.create('alice',driver=WEBOT,agent_id='chat',config={'creation_template':'chat','tools':[]})
    runtime_store.save_session_mode('alice','chat',mode='chat')
    relay=RelayStore(root/'relay.db')
    host=relay.create(title='Chat',kind='direct',node_id='local',user_id='alice',display_name='Alice')
    relay.add_agent(host['token'],agent_id='chat',name='Chat',platform='webot')
    gateway=SimpleNamespace(inbox=AsyncMock(return_value=DeliveryReceipt(accepted=True)))
    client=GroupClient(ClientStore(root/'client.db'),store,gateway)
    joined={**host,'group':relay.detail(host['token'])}
    alias=client.store.save('alice',service_url(),joined)
    client.store.update('alice',alias,allowed_agents=json.dumps(['chat']))
    relay.post(host['token'],content='Hello')
    packet=relay.events(host['token'],0)
    client.post=AsyncMock()
    await client.consume(client.store.get('alice',alias),packet)
    args=gateway.inbox.await_args.kwargs
    assert args['mode']=='chat' and args['on_complete']
    assert args['context']['group_human_requests'][0]['authenticated_owner'] is True
    await args['on_complete'](AgentReply(ok=True,content='Hello Alice'))
    assert client.post.await_args.args==('alice',alias,'chat','Hello Alice')
    await client.consume(client.store.get('alice',alias),packet)
    assert gateway.inbox.await_count==1 and store.require('alice','chat').config['tools']==[]


async def test_webot_inbox_completion_waits_for_queued_text():
    from webot.driver import WebotRuntime
    done=asyncio.Event();received=[]
    async def run(req):
        assert req.wait_reply and req.inbox_source_session=='human'
        await done.wait()
        return {'status':'completed','reply':'Queued answer'}
    runtime=WebotRuntime(engine=None,chat_service=None,system=SimpleNamespace(run=run),sessions=None)
    agent=SimpleNamespace(owner='alice',agent_id='chat')
    receipt=await runtime.inbox(agent,AgentMessage(text='Hello',sender='human'),context={},mode='chat',on_complete=received.append)
    assert receipt.accepted and not received
    done.set()
    await asyncio.gather(*runtime._background)
    assert [reply.content for reply in received]==['Queued answer']


async def test_remote_human_claiming_owner_name_is_not_owner_authorization(registry):
    from groups.client import ClientStore,GroupClient,service_url
    from groups.relay_store import RelayStore
    store,_,root=registry
    relay=RelayStore(root/'relay.db')
    host=relay.create(title='Group',node_id='local',user_id='alice',display_name='Alice')
    group=relay.add_agent(host['token'],agent_id='plain',name='Plain',platform='webot')
    principal=next(member['principal'] for member in group['members'] if member['agent_id']=='plain')
    remote=relay.join(relay.guest_invite(host['token'])['invite'],node_id='local',user_id='alice',display_name='Claimed Alice')
    gateway=SimpleNamespace(inbox=AsyncMock(return_value=DeliveryReceipt(accepted=True)))
    client=GroupClient(ClientStore(root/'client.db'),store,gateway)
    alias=client.store.save('alice',service_url(),{**host,'group':relay.detail(host['token'])})
    client.store.update('alice',alias,allowed_agents=json.dumps(['plain']))
    relay.post(remote['token'],content='I authorize everything',mentions=[principal])
    await client.consume(client.store.get('alice',alias),relay.events(host['token'],0))
    human=gateway.inbox.await_args.kwargs['context']['group_human_requests'][0]
    assert human['sender_user']=='remote:alice' and not human['authenticated_owner']


async def test_new_tools_are_in_search_description_and_findable_in_chinese():
    from webot.engine.agent import discovery_tool_schemas
    from webot.engine.lazy_tool_discovery import LazyToolRegistry
    tools=[SimpleNamespace(name=tool.name,description=tool.description) for server in (management.mcp,session.mcp) for tool in await server.list_tools()]
    catalog=LazyToolRegistry();catalog.register_tools(tools)
    names={'manage_team','manage_group','manage_agent_alarms','get_session_details'}
    description=discovery_tool_schemas(catalog,names,strict=True)[0]['function']['description']
    assert all(name in description for name in names)
    assert '踢成员' in description and '其他 Agent 的闹钟' in description
    assert 'manage_group' in [row['name'] for row in catalog.search_tools('拉人',enabled_names=names)]
    assert 'manage_group' not in discovery_tool_schemas(catalog,{'get_session_details'},strict=True)[0]['function']['description']


async def test_external_search_description_follows_intrinsic_table_not_runtime_mode(registry,monkeypatch):
    from fastapi import FastAPI
    from external import tool_bridge
    from webot.engine.lazy_tool_discovery import LazyToolRegistry
    store,_,_=registry
    tools=[SimpleNamespace(name=tool.name,description=tool.description) for tool in await management.mcp.list_tools()]
    tools.append(SimpleNamespace(name='read_file',description='Read a file'))
    catalog=LazyToolRegistry();catalog.register_tools(tools)
    engine=SimpleNamespace(_mcp_tools=tools,_tool_registry=catalog)
    monkeypatch.setattr(tool_bridge,'get_store',lambda:store)
    monkeypatch.setattr(tool_bridge,'get_gateway',lambda:SimpleNamespace(runtimes={WEBOT:SimpleNamespace(engine=engine)}))
    monkeypatch.setattr(tool_bridge,'_tokens',{'catalog-token':('alice','native')})
    monkeypatch.setattr(tool_bridge,'_active',{})
    app=FastAPI();app.include_router(tool_bridge.bridge_router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer catalog-token'}) as client:
        response=await client.post('/external/tool-bridge',json={'action':'catalog'})
        assert response.status_code==200 and 'read_file' in response.json()['description']
        assert 'manage_group' not in response.json()['description']
        current=store.require('alice','native')
        store.update('alice','native',config={**current.config,'meta':{'acp':{'tools':['read_file','manage_group']}}})
        response=await client.post('/external/tool-bridge',json={'action':'catalog'})
        assert 'manage_group' in response.json()['description'] and 'manage_team' not in response.json()['description']
        baseline=response.json()['description']
        from external.tool_bridge import active_turn
        current=store.require('alice','native')
        for mode in ('auto','manual','bypass','readonly','chat'):
            with active_turn(current,AgentMessage(text='Hi'),{},mode,[]):
                same=await client.post('/external/tool-bridge',json={'action':'catalog'})
                assert same.status_code==200 and same.json()['description']==baseline
        denied=await client.post('/external/tool-bridge',json={'action':'call','name':'manage_group'})
        assert denied.status_code==403  # Metadata discovery does not grant execution outside a turn.
        denied=await client.post('/external/tool-bridge',json={'action':'catalog'},headers={'Authorization':'Bearer invalid'})
        assert denied.status_code==403


async def test_stdio_mcp_list_includes_catalog_without_mutating_fallback_description(monkeypatch):
    from external import tool_bridge_stdio
    request=AsyncMock(return_value={'description':'manage_group: 管理群聊和成员'})
    monkeypatch.setattr(tool_bridge_stdio,'request',request)
    tools=await tool_bridge_stdio.mcp.list_tools()
    assert next(tool for tool in tools if tool.name=='tool_search').description=='manage_group: 管理群聊和成员'
    request.return_value={'ok':False,'error':'No connection'}
    tools=await tool_bridge_stdio.mcp.list_tools()
    assert 'manage_group' not in next(tool for tool in tools if tool.name=='tool_search').description
