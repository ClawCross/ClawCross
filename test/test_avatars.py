"""Avatar isolation, bounded SVG, primary-model generation and cross-device sharing."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'backend'))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agents.gateway import AgentGateway
from agents.routes import create_agents_router
from agents.store import AgentStore
from common.avatars import AvatarStore, avatar_id, clean_svg, normalize_avatar
from groups.relay_api import relay_router
from groups.relay_store import RelayError, RelayStore

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><rect width="256" height="256" rx="60" fill="#e9e5ff"/><circle cx="128" cy="128" r="70" fill="#7971cc"/><ellipse cx="103" cy="120" rx="8" ry="10"><animate attributeName="ry" values="10;10;1;10" dur="5s" repeatCount="indefinite"/></ellipse><circle cx="153" cy="120" r="8"/></svg>'


class AvatarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.assets = AvatarStore(Path(self.temp.name) / 'assets')

    def test_svg_identity_stays_stable_between_devices_and_never_executes(self):
        first = normalize_avatar(SVG)
        self.assertEqual(normalize_avatar(first), first)
        no_namespace = '<svg><circle cx="20" cy="20" r="10"/></svg>'
        self.assertEqual(normalize_avatar(normalize_avatar(no_namespace)), normalize_avatar(no_namespace))
        self.assertIn('animate', clean_svg(SVG))
        for bad in [
            '<svg onload="alert(1)"></svg>', '<svg><script>alert(1)</script></svg>',
            '<svg><foreignObject/></svg>', '<svg><image href="https://example.com/a.png"/></svg>',
            '<svg><style>@import "https://example.com/x.css"</style></svg>',
            '<svg><animate attributeName="href" values="https://example.com"/></svg>',
            '<svg><rect style="fill:url(https://example.com)"/></svg>',
            '<!DOCTYPE svg [<!ENTITY x "hello">]><svg>&x;</svg>',
        ]:
            with self.subTest(svg=bad), self.assertRaises(ValueError):
                normalize_avatar(bad)
        with self.assertRaises(ValueError):
            normalize_avatar('<svg>' + ' ' * 13000 + '</svg>')

    def test_owned_storage_is_separate_from_portraits_and_other_users(self):
        saved = self.assets.save_user('alice', SVG)
        self.assertEqual(saved['avatar'], avatar_id(saved['url']))
        self.assertEqual(self.assets.get('bob', saved['avatar']), '')
        self.assertEqual(self.assets.get('alice', '../../secret'), '')
        self.assertEqual(AvatarStore(self.assets.root).user('alice'), saved)
        self.assertEqual(self.assets.save_user('alice', '')['avatar'], '')
        self.assertFalse(list(Path(self.temp.name).rglob('user_profile*')))

    def test_authenticated_api_returns_references_and_cannot_edit_other_agents(self):
        store = AgentStore(Path(self.temp.name) / 'agents.db')
        agent = store.create('alice', driver='webot', name='Companion')
        app = FastAPI()
        with patch('agents.routes.AvatarStore', return_value=self.assets):
            app.include_router(create_agents_router(internal_token='test', verify_password=lambda *_: False,
                store=store, gateway=AgentGateway(store=store)))
        client = TestClient(app)
        auth = {'Authorization':'Bearer test:alice'}
        self.assertEqual(client.put('/v1/agents/avatars/me',json={'avatar':SVG}).status_code,401)
        me = client.put('/v1/agents/avatars/me',headers=auth,json={'avatar':SVG})
        self.assertEqual(me.status_code,200,me.text)
        response = client.patch('/v1/agents/'+agent.agent_id,headers=auth,json={'settings':{'avatar':SVG}})
        self.assertEqual(response.status_code,200,response.text)
        ref = response.json()['settings']['avatar']
        self.assertLess(len(ref),100)
        self.assertNotIn('data:image',json.dumps(response.json()))
        self.assertEqual(client.get('/v1/agents/'+agent.agent_id+'/avatar',headers=auth).json()['url'],normalize_avatar(SVG))
        self.assertEqual(client.get('/v1/agents/'+agent.agent_id+'/avatar',headers={'Authorization':'Bearer test:bob'}).status_code,404)

    def test_primary_api_generates_a_preview_without_saving_or_creating_an_agent(self):
        from agents.avatar_generation import AvatarGenerator
        model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content='```svg\n'+SVG+'\n```')))
        with patch('common.llm_factory.create_chat_model',return_value=model) as factory:
            result = asyncio.run(AvatarGenerator().generate('alice','A blue cat','Companion'))
        self.assertEqual(result['url'],normalize_avatar(SVG))
        self.assertEqual(factory.call_args.kwargs['max_retries'],0)
        self.assertEqual(factory.call_args.kwargs['reasoning_level'],1)
        self.assertNotIn('api_key',factory.call_args.kwargs)
        self.assertEqual(self.assets.user('alice')['avatar'],'')

    def test_frontend_proxy_preserves_the_user_avatar_put_body(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'src'))
        from flask import Flask
        from frontend.proxies.agents import register_agent_routes
        app=Flask(__name__);app.secret_key='avatar-test'
        register_agent_routes(app,port_agent=51200,internal_token='test')
        client=app.test_client()
        with client.session_transaction() as session:
            session['user_id']='alice'
        upstream=SimpleNamespace(content=b'{"avatar":"saved"}',status_code=200,headers={'content-type':'application/json'})
        with patch('frontend.proxies.agents.requests.request',return_value=upstream) as relay:
            response=client.put('/v1/agents/avatars/me',json={'avatar':SVG})
        self.assertEqual(response.status_code,200)
        self.assertEqual(relay.call_args.args[0],'PUT')
        self.assertEqual(relay.call_args.kwargs['json'],{'avatar':SVG})
        self.assertEqual(relay.call_args.kwargs['headers']['Authorization'],'Bearer test:alice')

    def test_group_avatar_sync_publishes_a_change_once_and_then_stays_quiet(self):
        from groups.client import GroupClient
        saved=self.assets.save_user('alice',SVG)
        members=[{'principal':'human','connection_id':'one','is_agent':False,'avatar':''},
                 {'principal':'agent','connection_id':'one','is_agent':True,'agent_id':'companion','avatar':''}]
        row={'active':True,'connection_id':'one','metadata':json.dumps({'members':members})}
        writes=[];sleeps=[]
        def update(kind):
            writes.append(kind)
            members[0 if kind=='human' else 1]['avatar']=saved['avatar']
            row['metadata']=json.dumps({'members':members})
        agent=SimpleNamespace(agent_id='companion',config={'avatar':saved['avatar']})
        client=SimpleNamespace(closed=False,store=SimpleNamespace(get=lambda *_:row),
            agents=SimpleNamespace(get=lambda *_:agent),manage=lambda *_:update('human'),add_agent=lambda *_:update('agent'))
        async def sleep(delay):
            sleeps.append(delay)
            if len(sleeps)==3: raise asyncio.CancelledError()
        with patch('groups.client.AvatarStore',return_value=self.assets), patch('groups.client.asyncio.sleep',side_effect=sleep):
            asyncio.run(GroupClient._sync_avatars(client,'alice','group'))
        self.assertEqual(writes,['human','agent'])
        self.assertEqual(sleeps,[2,15,15])

    def test_group_members_resolve_an_avatar_but_another_group_cannot(self):
        store = RelayStore(Path(self.temp.name)/'relay.db')
        host = store.create(title='Room',node_id='one',user_id='alice',display_name='Alice')
        invite = store.guest_invite(host['token'])['invite']
        peer = store.join(invite,node_id='two',user_id='bob',display_name='Bob',password='password123')
        other = store.create(title='Other',node_id='three',user_id='eve',display_name='Eve')
        group = store.add_agent(host['token'],agent_id='companion',name='Companion',platform='webot',avatar=SVG)
        member = next(m for m in group['members'] if m['is_agent'])
        self.assertEqual(member['avatar'],self.assets.put('alice',SVG))
        self.assertEqual(store.member_avatar(peer['token'],member['principal'],member['avatar'])['url'],normalize_avatar(SVG))
        with self.assertRaises(RelayError):
            store.member_avatar(other['token'],member['principal'],member['avatar'])
        changed = store.manage(host['token'],'profile',{'avatar_image':SVG,'text':'Likes cats'})
        human = next(m for m in changed['members'] if not m['is_agent'] and m['connection_id']==host['connection_id'])
        self.assertEqual(human['avatar'],member['avatar'])
        self.assertNotIn('data:',json.dumps(human['profile']))
        app = FastAPI();app.include_router(relay_router(store,'test'))
        client = TestClient(app)
        response = client.get('/relay/guest/avatar',headers={'Authorization':'Bearer '+peer['token']},
            params={'principal':member['principal'],'image_id':member['avatar']})
        self.assertEqual(response.status_code,200,response.text)


if __name__ == '__main__':
    unittest.main()
