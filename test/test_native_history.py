"""ACP replay is read-only, complete, paged and idempotently persisted."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agents import native_sessions
from agents.store import AgentStore
from external.acpx import AcpxAdapter
from external.history import ExternalAgentHistoryStore
from external.native_replay import normalize_updates
from external import session
from ops.components import binary_path


class ReplayTests(unittest.TestCase):
    def test_chunks_tool_updates_and_thoughts_keep_complete_content(self):
        messages = normalize_updates([
            {'sessionUpdate':'user_message_chunk','messageId':'u1','content':{'type':'text','text':'hello'}},
            {'sessionUpdate':'agent_thought_chunk','messageId':'t1','content':{'type':'text','text':'thinking'}},
            {'sessionUpdate':'tool_call','toolCallId':'c1','title':'read','rawInput':{'path':'x'},'status':'in_progress'},
            {'sessionUpdate':'tool_call_update','toolCallId':'c1','status':'completed','rawOutput':{'formatted_output':'full output'},
             'content':[{'type':'content','content':{'type':'text','text':'output'}}]},
            {'sessionUpdate':'agent_message_chunk','messageId':'a1','content':{'type':'text','text':'part1'}},
            {'sessionUpdate':'agent_message_chunk','messageId':'a1','content':{'type':'text','text':'part2'}},
            {'sessionUpdate':'agent_message_chunk','messageId':'a2','content':{'type':'text','text':'separate'}},
        ])
        self.assertEqual(len(messages), 5)
        self.assertEqual(messages[3]['content'],'part1part2')
        self.assertEqual(messages[2]['meta']['native_tool']['rawInput'],{'path':'x'})
        self.assertIn('full output', messages[2]['content'])
        self.assertEqual(messages[1]['direction'],'thought')

    def test_real_sdk_load_replays_without_prompt_and_denies_client_execution(self):
        acpx = os.getenv('CLAWCROSS_TEST_ACPX') or binary_path('acpx')
        if not acpx or not shutil.which('node'):
            self.skipTest('Installed acpx and Node are required for the protocol integration')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            fake=root/'fake.mjs'
            fake.write_text('''import readline from 'node:readline';
const send=x=>process.stdout.write(JSON.stringify(x)+'\\n');
for await(const line of readline.createInterface({input:process.stdin})) {
 const p=JSON.parse(line);
 if(p.method==='initialize') send({jsonrpc:'2.0',id:p.id,result:{protocolVersion:1,agentCapabilities:{loadSession:true}}});
 else if(p.method==='session/load') {
   if(p.params.mcpServers.length) process.exit(8);
   globalThis.load=p;
   send({jsonrpc:'2.0',method:'session/update',params:{sessionId:p.params.sessionId,update:{sessionUpdate:'user_message_chunk',content:{type:'text',text:'old user'}}}});
   send({jsonrpc:'2.0',method:'session/update',params:{sessionId:p.params.sessionId,update:{sessionUpdate:'agent_message_chunk',content:{type:'text',text:'old answer'}}}});
   send({jsonrpc:'2.0',id:900,method:'fs/write_text_file',params:{sessionId:p.params.sessionId,path:'/tmp/must-not-be-written',content:'no'}});
 } else if(p.id===900) {
   if(!p.error) process.exit(9);
   send({jsonrpc:'2.0',id:globalThis.load.id,result:{}});
 } else if(p.method==='session/prompt'||p.method==='session/new') process.exit(10);
}''')
            helper=Path(__file__).resolve().parents[1]/'src/backend/external/native_history.mjs'
            payload={'acpx':acpx,'platform':'codex','session_id':'old-session','cwd':tmp,'argv':['node',str(fake)],'timeout_ms':10000}
            result=subprocess.run(['node',str(helper)],input=json.dumps(payload),capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            packets=[json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(packets[-1]['status'],'loaded',packets)
            self.assertEqual(packets[-1]['events'],2)


class HistoryStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.history=ExternalAgentHistoryStore(self.root/'history')
        self.agents=AgentStore(self.root/'agents.db')
        self.agent=self.agents.create('alice',driver='acpx',name='Old',config={'platform':'gemini'})
        self.agents.set_runtime('alice',self.agent.agent_id,{'native_resume_id':'native','acp_cwd':str(self.root)})
        self.agent=self.agents.require('alice',self.agent.agent_id)

    async def test_full_snapshot_over_5000_rows_idempotent_and_cursor_stable(self):
        key=session.runtime_session(self.agent)
        messages=[{'role':'user' if i%2==0 else 'assistant','direction':'send' if i%2==0 else 'recv','content':str(i),'meta':{}} for i in range(5003)]
        n=await self.history.import_native_messages(platform='gemini',session_key=key,native_id='native',messages=messages,user_id='alice')
        self.assertEqual(n,5003)
        self.assertEqual(await self.history.import_native_messages(platform='gemini',session_key=key,native_id='native',messages=messages,user_id='alice'),5003)
        page=await self.history.message_page(platform='gemini',session_key=key,limit=3)
        self.assertEqual([r['content'] for r in page['rows']],['5000','5001','5002'])
        await self.history.record_send(platform='gemini',session_key=key,connect_type='acpx',prompt='new managed input',options={'_history_user_id':'alice'})
        next_page=await self.history.message_page(platform='gemini',session_key=key,limit=3,before=page['next_before'])
        self.assertEqual([r['content'] for r in next_page['rows']],['4997','4998','4999'])
        newest=await self.history.message_page(platform='gemini',session_key=key,limit=2)
        self.assertEqual(newest['rows'][-1]['content'],'new managed input')
        self.assertEqual(newest['rows'][-1]['rowid'],1)
        with patch('external.history.get_store',new=AsyncMock(return_value=self.history)):
            oldest=await session.log_page(self.agent,1000,before=-4000)
        self.assertTrue(oldest['messages'])
        self.assertEqual(oldest['messages'][0]['content'],'3')

    async def test_loaded_snapshot_is_not_fetched_twice_and_unsupported_is_visible(self):
        adapter=AsyncMock();adapter.load_native_history.return_value={'status':'loaded','messages':[{'role':'user','direction':'send','content':'old','meta':{}}]}
        state=await native_sessions.import_history(self.agent,self.agents,adapter=adapter,history_store=self.history)
        self.assertEqual(state['message_count'],1)
        await native_sessions.import_history(self.agent,self.agents,adapter=adapter,history_store=self.history)
        self.assertEqual(adapter.load_native_history.await_count,1)
        self.assertEqual(self.agents.require('alice',self.agent.agent_id).runtime['native_resume_id'],'native')
        self.assertFalse(adapter.prompt.called)

    async def test_existing_managed_prompt_is_not_duplicated(self):
        key=session.runtime_session(self.agent)
        await self.history.record_send(platform='gemini',session_key=key,connect_type='acpx',prompt='managed exact',options={})
        messages=[{'role':'user','direction':'send','content':'earlier'}, {'role':'user','direction':'send','content':'managed exact'}, {'role':'assistant','content':'answer'}]
        n=await self.history.import_native_messages(platform='gemini',session_key=key,native_id='native',messages=messages,user_id='alice')
        self.assertEqual(n,1)
        page=await self.history.message_page(platform='gemini',session_key=key)
        self.assertEqual([r['content'] for r in page['rows']],['earlier','managed exact'])

    async def test_busy_agent_history_load_returns_without_interrupting_the_turn(self):
        adapter=AsyncMock()
        async with session.turn(self.agents,self.agent):
            state=await native_sessions.import_history(self.agent,self.agents,adapter=adapter,history_store=self.history)
        self.assertEqual(state['status'],'busy')
        adapter.load_native_history.assert_not_awaited()

    async def test_adapter_failure_does_not_lose_the_registered_agent(self):
        with patch('external.acp.adapter',side_effect=RuntimeError('not installed')):
            state=await native_sessions.import_history(self.agent,self.agents,history_store=self.history)
        self.assertEqual(state['status'],'error')
        self.assertIsNotNone(self.agents.get('alice',self.agent.agent_id))
        self.assertEqual(self.agents.require('alice',self.agent.agent_id).runtime['native_history']['status'],'error')

    def test_create_agent_tools_apply_to_acp_as_well_as_webot(self):
        from agents.routes import AgentCreate, new_agent_config
        for platform in ('webot','codex','claude','gemini'):
            with self.subTest(platform=platform):
                driver,config=new_agent_config(AgentCreate(name='Scoped',platform=platform,tools=['read_file']))
                actual=config['tools'] if driver=='webot' else config['meta']['acp']['tools']
                self.assertEqual(actual,['read_file'])

    async def test_registration_and_history_http_keep_owner_and_cursor_boundaries(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from agents.routes import create_agents_router
        adapter=AsyncMock()
        adapter.list_native_sessions.return_value={'sessions':[{'session_id':'http-native','cwd':str(self.root),'title':'Old','updated_at':''}]}
        adapter.load_native_history.return_value={'status':'loaded','messages':[
            {'role':'user','direction':'send','content':'one','meta':{}},
            {'role':'assistant','direction':'recv','content':'two','meta':{}},
            {'role':'user','direction':'send','content':'three','meta':{}},
        ]}
        app=FastAPI();app.include_router(create_agents_router(internal_token='internal',verify_password=lambda u,p:p=='pass',store=self.agents,gateway=AsyncMock()))
        client=TestClient(app)
        with patch.object(native_sessions,'DB_PATH',self.root/'native.db'), \
             patch.object(native_sessions,'_foreign_native_ids',return_value=set()), \
             patch('external.acp.adapter',return_value=adapter), \
             patch('external.history.get_store',new=AsyncMock(return_value=self.history)), \
             patch.dict('os.environ',{'CLAWCROSS_NATIVE_SESSION_USERS':''}):
            rows=await native_sessions.catalog('alice','gemini',adapter=adapter,store=self.agents)
            body={'ticket':rows['sessions'][0]['ticket'],'name':'Imported'}
            response=client.post('/v1/agents/native-sessions',json=body,headers={'Authorization':'Bearer alice:pass'})
            self.assertEqual(response.status_code,200,response.text)
            agent=response.json();self.assertEqual(agent['native_history']['message_count'],3)
            endpoint='/v1/agents/'+agent['agent_id']+'/history'
            page=client.get(endpoint+'?limit=2',headers={'Authorization':'Bearer alice:pass'}).json()
            self.assertEqual([m['content'] for m in page['messages']],['two','three'])
            older=client.get(endpoint+'?limit=2&before='+str(page['next_before']),headers={'Authorization':'Bearer alice:pass'}).json()
            self.assertEqual([m['content'] for m in older['messages']],['one'])
            self.assertFalse(older['has_more'])
            self.assertEqual(client.get(endpoint,headers={'Authorization':'Bearer bob:pass'}).status_code,404)
            self.assertEqual(client.post('/v1/agents/native-sessions',json=body,headers={'Authorization':'Bearer bob:pass'}).status_code,400)
            again=client.post('/v1/agents/native-sessions',json=body,headers={'Authorization':'Bearer alice:pass'})
            self.assertEqual(again.status_code,200,again.text)
            self.assertEqual(again.json()['agent_id'],agent['agent_id'])
            self.assertEqual(adapter.load_native_history.await_count,1)

    async def test_native_browser_accepts_other_acp_platforms(self):
        adapter=AcpxAdapter.__new__(AcpxAdapter)
        adapter._run_json=AsyncMock(return_value=json.dumps({'sessions':[{'sessionId':'gemini-old','cwd':str(self.root),'title':'Gemini'}]}))
        result=await adapter.list_native_sessions(tool='gemini')
        self.assertEqual(result['sessions'][0]['session_id'],'gemini-old')
        self.assertEqual(adapter._run_json.call_args.args[0][0],'gemini')
        self.assertTrue(adapter._run_json.call_args.kwargs['offline'])
