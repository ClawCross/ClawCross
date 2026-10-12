"""Guest invitation isolation, identity collisions, and the public browser proxy."""
import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'backend'))
sys.path.insert(0, str(ROOT / 'src'))
from groups.relay_store import RelayStore, RelayError, digest
from groups.server import create_app
from groups.client import ClientError, ClientStore, GroupClient
from fastapi.testclient import TestClient
from flask import Flask
from frontend.proxies.group_guests import register_guest_routes


class GuestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RelayStore(Path(self.temp.name) / 'group-relay.db')
        self.host = self.store.create(title='Friends', node_id='host', user_id='alice', display_name='Alice')
        self.invite = self.store.guest_invite(self.host['token'])['invite']

    def test_automatic_client_login_rejoins_locally_but_another_device_cannot_take_the_name(self):
        first_store=ClientStore(Path(self.temp.name)/'device-one.db')
        second_store=ClientStore(Path(self.temp.name)/'device-two.db')
        first=GroupClient(first_store,Mock(),Mock())
        second=GroupClient(second_store,Mock(),Mock())
        def enroll(url,path,headers,body):
            if path.endswith('/info'): return {'group_id':self.host['group']['group_id']}
            try:
                return self.store.join(self.invite,**body)
            except RelayError as exc:
                raise ClientError(str(exc),exc.status) from exc
        link='https://host.example/group-guest#'+self.invite
        with patch.object(GroupClient,'enroll',side_effect=enroll), patch('groups.client.own_front_ends',return_value=set()):
            card=first.join_link('bob',link=link,name='Bob')
            original=first_store.get('bob',card['group_id'])['connection_id']
            with self.assertRaises(ClientError) as denied:
                second.join_link('bob',link=link,name='Bob')
            self.assertEqual(denied.exception.status,409)
            self.assertIn('昵称',str(denied.exception))
            first_store.remove('bob',card['group_id'])
            again=first.join_link('bob',link=link,name='Bob')
            self.assertEqual(first_store.get('bob',again['group_id'])['connection_id'],original)
        self.assertEqual(len(self.store.detail(self.host['token'])['members']),2)

    def test_name_collisions_and_concurrent_join(self):
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, ' alice ', 'password123')
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, '\u200b', 'password123')
        def join():
            try: return self.store.guest_join(self.invite, 'Bob', 'password123')
            except RelayError as exc: return exc.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: join(), range(2)))
        self.assertTrue(all(isinstance(r, dict) for r in results))  # the second one signs in as the first
        self.assertEqual(len(self.store.detail(self.host['token'])['members']), 2)
        self.assertEqual(self.store.guest_identity(results[0]['token']), self.store.guest_identity(results[1]['token']))
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, 'ＢＯＢ', 'wrong-password')
        with self.assertRaises(RelayError): self.store.add_agent(self.host['token'], agent_id='a', name='bob', platform='webot')

    def test_guest_chats_and_brings_its_own_agent_but_cannot_manage_the_group(self):
        guest = self.store.guest_join(self.invite, 'Bob', 'password123')
        token = guest['token']
        posted = self.store.post(token, content='Hello', client_msg_id='one')
        self.assertFalse(self.store.post(token, content='Hello', client_msg_id='one')['created'])
        self.assertEqual(self.store.messages(self.host['token'])[-1]['content'], 'Hello')
        for action, fields in [('patch', {'title':'Hijack'}), ('delete', {}), ('primary', {})]:
            with self.assertRaises(RelayError): self.store.manage(token, action, fields)
        with self.assertRaises(RelayError): self.store.post(token, content='fake', agent_id='x')
        card = self.store.add_agent(token, agent_id='browser', name='Bob 的助手', platform='browser')
        card = self.store.add_agent(token, agent_id='browser', name='Bob 的助手', platform='browser')  # saving again keeps its name
        agent = next(m['principal'] for m in card['members'] if m['agent_id'] == 'browser')
        woke = self.store.post(self.host['token'], content='大家好')['message']
        state = self.store.guest_state(token)
        self.assertEqual(state['messages'][-1]['targets'], [agent])
        mine = {m['name']: m for m in state['members'] if m['own']}
        self.assertEqual(set(mine), {'Bob', 'Bob 的助手'})
        self.assertTrue(mine['Bob']['online'])
        # Members carry their device, so the page can draw the group by device.
        host = next(m for m in state['members'] if not m['own'])
        self.assertEqual((host['device'], host['device_kind']), (0, 'client'))
        self.assertEqual({(m['device'], m['device_kind']) for m in mine.values()}, {(1, 'browser')})
        self.assertEqual(mine['Bob 的助手']['platform'], 'browser')
        self.assertEqual(self.store.post(token, content='收到', agent_id='browser', client_msg_id='browser:%d' % woke['id'])['message']['sender'], agent)
        with self.assertRaises(RelayError): self.store.manage(token, 'remove_member', {'principal': 'p_missing'})
        self.store.manage(token, 'remove_member', {'principal': agent})
        self.assertFalse(any(m['is_agent'] for m in self.store.guest_state(token)['members']))
        with self.assertRaises(RelayError): self.store.guest_rename(token, 'Alice')
        self.store.guest_rename(token, 'Carol')
        state = self.store.guest_state(token)
        self.assertEqual(state['name'], 'Carol')
        self.assertEqual(state['messages'][0]['id'], posted['message']['id'])
        self.assertNotIn('user_id', str(state))
        self.store.post(self.host['token'], content='Welcome')
        self.assertEqual(self.store.guest_state(token, state['cursor'])['messages'][-1]['content'], 'Welcome')
        self.store.manage(self.host['token'], 'remove_member', {'principal': state['principal']})
        with self.assertRaises(RelayError): self.store.guest_state(token)

    def test_the_same_name_and_password_return_to_the_same_member_on_any_device(self):
        browser = self.store.guest_join(self.invite, 'Bob', 'correct-password')
        self.store.post(browser['token'], content='Before closing')
        with self.assertRaises(RelayError) as wrong: self.store.guest_join(self.invite, 'Bob', 'wrong-password')
        self.assertEqual(wrong.exception.status, 403)
        with self.assertRaises(RelayError) as wrong: self.store.join(self.invite, node_id='x', user_id='mallory', display_name='Bob', password='guessing')
        self.assertEqual(wrong.exception.status, 403)  # a ClawCross can't take the name either
        laptop = self.store.join(self.invite, node_id='laptop', user_id='bob', display_name='ＢＯＢ', password='correct-password')
        self.assertEqual(self.store.guest_identity(laptop['token']), self.store.guest_identity(browser['token']))
        self.assertEqual(len(self.store.detail(self.host['token'])['members']), 2)
        self.store.add_agent(laptop['token'], agent_id='helper', name='Helper', platform='codex')
        self.assertIn('Helper', [m['name'] for m in self.store.guest_state(browser['token'])['members'] if m['own']])
        self.assertEqual(self.store.guest_state(laptop['token'])['messages'][0]['content'], 'Before closing')
        self.store.set_password(laptop['token'], 'new-password')
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, 'Bob', 'correct-password')
        again = self.store.guest_join(self.invite, 'Bob', 'new-password')
        self.store.manage(self.host['token'], 'remove_member', {'principal': self.store.guest_identity(again['token'])['principal']})
        for token in (browser['token'], laptop['token'], again['token']):
            with self.assertRaises(RelayError): self.store.guest_state(token)
        self.assertTrue(self.store.guest_join(self.invite, 'Bob', 'fresh-password'))

    def test_a_member_without_a_password_keeps_its_name_until_it_sets_one(self):
        with self.assertRaises(RelayError) as taken:  # the host made the group without a password
            self.store.guest_join(self.invite, 'Alice', 'password123')
        self.assertEqual(taken.exception.status, 409)
        old = self.store.guest_join(self.invite, 'Old member', 'first-password')
        with self.store.db() as db:
            db.execute("UPDATE relay_connections SET guest_password_hash='',guest_salt='' WHERE token_hash=?", (digest(old['token']),))
        self.assertFalse(self.store.guest_state(old['token'])['password_set'])
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, 'Old member', 'first-password')
        self.store.set_password(old['token'], 'new-password')
        self.assertTrue(self.store.guest_state(old['token'])['password_set'])
        restored = self.store.join(self.invite, node_id='laptop', user_id='old', display_name='Old member', password='new-password')
        self.assertEqual(self.store.guest_identity(restored['token']), self.store.guest_identity(old['token']))

    def test_client_exposes_human_removal_only_to_group_owner(self):
        self.store.guest_join(self.invite, 'Bob', 'password123')
        cache = ClientStore(Path(self.temp.name) / 'client.db')
        alias = cache.save('alice', 'http://127.0.0.1:51203', self.host)
        group = self.store.detail(self.host['token'])
        cache.update('alice', alias, metadata=json.dumps(group))
        client = GroupClient(cache, Mock(), Mock())
        owner = client.card(cache.get('alice', alias))
        humans = {m['name']:m for m in owner['members'] if not m['is_agent']}
        self.assertFalse(humans['Alice']['can_remove'])
        self.assertTrue(humans['Bob']['can_remove'])
        nonowner = dict(cache.get('alice', alias));nonowner['connection_id'] = 'another-device'
        self.assertFalse(any(m['can_remove'] for m in client.card(nonowner)['members']))

    def test_invite_rotation_and_restart_do_not_eject_existing_guests(self):
        guest = self.store.guest_join(self.invite, 'Bob', 'password123')
        new = self.store.guest_invite(self.host['token'])['invite']
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, 'Old', 'password123')
        reopened = RelayStore(self.store.path)
        self.assertEqual(reopened.guest_identity(guest['token'])['name'], 'Bob')
        self.assertTrue(reopened.guest_join(new, 'New', 'password123'))
        reopened.guest_invite(self.host['token'], disable=True)
        with self.assertRaises(RelayError): reopened.guest_info(new)

    def test_the_invitation_link_also_joins_a_device_as_a_full_member(self):
        joined = self.store.join(self.invite, node_id='laptop', user_id='bob', display_name='Bob', password='password123')
        card = self.store.add_agent(joined['token'], agent_id='helper', name='Helper', platform='codex')
        self.assertTrue(any(m['is_agent'] and m['agent_id'] == 'helper' for m in card['members']))
        self.store.guest_invite(self.host['token'])  # a new link: the old one no longer joins
        with self.assertRaises(RelayError):
            self.store.join(self.invite, node_id='other', user_id='carol', display_name='Carol', password='password123')
        self.assertEqual(self.store.detail(joined['token'])['title'], 'Friends')  # joined members stay

    def test_poll_carries_the_websocket_stream_over_http(self):
        joined = self.store.join(self.invite, node_id='laptop', user_id='bob', display_name='Bob', password='password123')
        self.store.post(self.host['token'], content='hello')
        with TestClient(create_app(data_dir=self.temp.name, control_key='key', legacy=False)) as client:
            headers = {'Authorization': 'Bearer ' + joined['token']}
            first = client.post('/relay/poll', headers=headers, json={'cursor': 0}).json()
            self.assertEqual(first['events'][-1]['message']['content'], 'hello')
            cursor = first['events'][-1]['id']
            self.assertEqual(client.post('/relay/poll', headers=headers, json={'cursor': cursor}).json()['events'], [])
            self.assertEqual(client.post('/relay/poll', headers=headers, json={'cursor': -1}).status_code, 400)
            self.assertEqual(client.post('/relay/poll', json={'cursor': 0}).status_code, 401)

    def test_api_requires_guest_token_and_filters_private_metadata(self):
        card = self.store.add_agent(self.host['token'], agent_id='creative', name='创意专家', platform='webot')
        agent = next(m['principal'] for m in card['members'] if m['is_agent'])
        with TestClient(create_app(data_dir=self.temp.name, control_key='key', legacy=False)) as client:
            response = client.post('/relay/guest/join', json={'invite':self.invite,'name':'Bob','password':'password123'})
            self.assertEqual(response.status_code, 200)
            headers = {'Authorization':'Bearer ' + response.json()['token']}
            state = client.get('/relay/guest/state', headers=headers)
            self.assertEqual(state.status_code, 200)
            self.assertNotIn('user_id', state.text)
            self.assertEqual(client.post('/relay/guest/password', headers=headers, json={'password':'x'*8}).status_code, 200)
            self.assertEqual(client.post('/relay/agents', headers=headers, json={'agent_id':'browser','name':'Bob 的助手','platform':'browser'}).status_code, 200)
            self.assertEqual(client.post('/relay/manage/patch', headers=headers, json={'title':'X'}).status_code, 403)
            self.assertEqual(client.post('/relay/guest/messages', headers=headers, json={'content':'Hello'}).status_code, 200)
            self.assertEqual(client.post('/relay/guest/messages', headers=headers, json={'content':'你好', 'mentions':[agent]}).status_code, 200)
            self.assertEqual(self.store.events(self.host['token'], 0)['events'][-1]['targets'], [agent])
            self.assertEqual(client.post('/relay/guest/messages', headers=headers, json={'content':'你好', 'mentions':['foreign-member']}).status_code, 400)
            self.assertEqual(client.get('/relay/guest/state', headers={'Authorization':'Bearer '+self.host['token']}).status_code, 200)


    def test_an_app_server_runs_the_space_and_calls_one_agent_directly(self):
        import base64, threading
        card = self.store.add_agent(self.host['token'], agent_id='creative', name='创意专家', platform='webot')
        agent = next(m['principal'] for m in card['members'] if m['is_agent'])
        with TestClient(create_app(data_dir=self.temp.name, control_key='key', legacy=False)) as client:
            guest = client.post('/relay/guest/join', json={'invite':self.invite,'name':'Bob','password':'password123'}).json()
            host, member = {'Authorization':'Bearer ' + self.host['token']}, {'Authorization':'Bearer ' + guest['token']}
            self.assertEqual(client.post('/relay/app/key', headers=member).status_code, 403)
            self.assertEqual(client.post('/relay/guest/app_session', headers=member).status_code, 404)
            key = client.post('/relay/app/key', headers=host).json()['key']
            app = {'Authorization':'Bearer ' + key}
            self.assertEqual(client.post('/relay/app/poll', headers=member, json={}).status_code, 401)
            start = client.post('/relay/app/poll', headers=app, json={'name':'精灵对战'}).json()
            self.assertEqual(self.store.detail(self.host['token'])['app'], {'name':'精灵对战','online':True})
            members = client.get('/relay/app/members', headers=app).json()['members']
            self.assertEqual([m['name'] for m in members if m['owner']], ['Alice'])
            self.assertEqual({m['name'] for m in members if m['agent']}, {'创意专家'})
            # A request reaches only the device that runs the agent.
            request = client.post('/relay/app/ask', headers=app, json={'agent':agent,'content':'画一只精灵'}).json()['request']
            asked = [e for e in self.store.events(self.host['token'], 0)['events'] if e['kind'] == 'app_request']
            self.assertEqual([(e['id'], e['content'], e['agent_id']) for e in asked], [(request, '画一只精灵', 'creative')])
            self.assertFalse([e for e in self.store.events(guest['token'], 0)['events'] if e['kind'] == 'app_request'])
            self.assertEqual(client.get('/relay/guest/state', headers=member).json()['requests'], [])
            # The agent's send_to_group output goes to the app, not the chat; people cannot post here.
            self.store.post(self.host['token'], content='<svg/>', agent_id='creative')
            self.assertEqual(client.post('/relay/guest/messages', headers=member, json={'content':'hi'}).status_code, 409)
            output = client.post('/relay/app/poll', headers=app, json={'cursor':start['cursor']}).json()
            replies = [i for i in output['items'] if i['type'] == 'agent']
            self.assertEqual([(i['agent'], i['content'], i['request']) for i in replies], [(agent, '<svg/>', request)])
            self.assertFalse([e for e in self.store.events(guest['token'], 0)['events'] if e['kind'] == 'app_output'])
            # A member's page request is tunnelled to the app with the verified member.
            path = client.post('/relay/guest/app_session', headers=member).json()['path']
            result = {}
            page = threading.Thread(target=lambda: result.update(response=client.post('/' + path + 'move?x=1', content=b'{"a":1}',
                                    headers={'Content-Type':'application/json','Cookie':'session=secret'})))
            page.start()
            item = None
            for _ in range(20):
                item = next((i for i in client.post('/relay/app/poll', headers=app, json={'cursor':output['cursor']}).json()['items']
                             if i['type'] == 'http'), None)
                if item: break
            self.assertEqual((item['method'], item['path'], item['query'], item['member']['name']), ('POST', '/move', 'x=1', 'Bob'))
            self.assertEqual(base64.b64decode(item['body']), b'{"a":1}')
            self.assertNotIn('cookie', item['headers'])
            client.post('/relay/app/respond', headers=app, json={'id':item['id'],'status':201,'body':base64.b64encode(b'ok').decode(),
                                                                 'headers':{'Content-Type':'text/plain','Set-Cookie':'x=1'}})
            page.join(10)
            response = result['response']
            self.assertEqual((response.status_code, response.text), (201, 'ok'))
            self.assertIn('sandbox', response.headers['content-security-policy'])
            self.assertNotIn('set-cookie', response.headers)
            self.assertEqual(client.get('/relay/app/site/forged.token/').status_code, 404)
            # Disconnecting the app returns the space to plain chat.
            self.assertIsNone(client.post('/relay/manage/app_remove', headers=host, json={}).json()['app'])
            self.assertEqual(client.post('/relay/app/poll', headers=app, json={}).status_code, 401)
            self.assertEqual(client.post('/relay/guest/messages', headers=member, json={'content':'hi'}).status_code, 200)

class GuestProxyTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(ROOT / 'src/frontend/templates'))
        self.app.secret_key = 'test-secret'
        register_guest_routes(self.app, port_agent=1234, internal_token='internal', public_base=lambda:'https://chat.example')
        self.client = self.app.test_client()

    def test_app_calls_and_member_pages_pass_through_to_this_machines_group_server(self):
        reply = Mock(status_code=201, content=b'page', headers={'Content-Type':'text/html','Content-Security-Policy':'sandbox allow-scripts'})
        with patch('frontend.proxies.group_guests.requests.Session.request', return_value=reply) as forward:
            response = self.client.post('/relay/app/site/abc.def/move?x=1', data=b'{}',
                                        headers={'Content-Type':'application/json','Cookie':'session=secret','Authorization':'Bearer app_k'})
        self.assertEqual((response.status_code, response.data), (201, b'page'))
        self.assertEqual(response.headers['Content-Security-Policy'], 'sandbox allow-scripts')
        method, url = forward.call_args.args[:2]
        self.assertEqual(method, 'POST')
        self.assertTrue(url.endswith('/relay/app/site/abc.def/move'))
        self.assertEqual(dict(forward.call_args.kwargs['params']), {'x':'1'})
        self.assertNotIn('Cookie', forward.call_args.kwargs['headers'])
        self.assertEqual(forward.call_args.kwargs['headers']['Authorization'], 'Bearer app_k')

    def test_signed_invitation_and_no_main_login(self):
        self.assertEqual(self.client.post('/proxy_groups/rg_x/guest-link', json={}).status_code, 401)
        with self.client.session_transaction() as session: session['user_id'] = 'alice'
        reply = Mock(status_code=200); reply.json.return_value={'server_url':'http://127.0.0.1:51203','invite':'a'*43}
        with patch('frontend.proxies.group_guests.requests.post', return_value=reply):
            link = self.client.post('/proxy_groups/rg_x/guest-link', json={}).json['url']
        self.assertTrue(link.startswith('https://chat.example/group-guest#'))
        ticket = link.split('#')[1]
        with self.client.session_transaction() as session: session.clear()
        self.assertEqual(self.client.get('/group-guest').status_code, 200)
        self.assertEqual(self.client.post('/group-guest-api/join', headers={'X-Group-Invite':ticket+'bad'}, json={'name':'Bob'}).status_code, 403)
        self.assertEqual(self.client.post('/group-guest-api/manage', headers={'X-Group-Invite':ticket}, json={}).status_code, 405)
        transport = Mock(); transport.request.return_value = Mock(status_code=200)
        transport.request.return_value.json.return_value = {'token':'guest'}
        with patch('frontend.proxies.group_guests.requests.Session') as factory:
            factory.return_value.__enter__.return_value=transport
            result = self.client.post('/group-guest-api/join', headers={'X-Group-Invite':ticket}, json={'name':'Bob','password':'guest-password','agent_id':'forged'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(transport.request.call_args.kwargs['json'], {'invite':'a'*43,'name':'Bob','password':'guest-password'})
        self.assertEqual(self.client.post('/group-guest-api/password', headers={'X-Group-Invite':ticket}, json={}).status_code, 401)
        with self.client.session_transaction() as session: self.assertNotIn('user_id', session)

    def test_page_lets_the_agent_reach_its_model_and_the_worker_reaches_nothing(self):
        page = self.client.get('/group-guest').headers['Content-Security-Policy']
        self.assertIn("connect-src 'self' https:", page)
        self.assertIn("script-src 'self'", page)
        worker = self.client.get('/group-guest-worker.js')
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker.headers['Content-Security-Policy'], "default-src 'none'; script-src 'unsafe-eval'")
        self.assertIn('delete scope[name]', worker.get_data(as_text=True))

    def test_machine_proof_is_forwarded_only_for_a_host_request_to_its_own_relay(self):
        from itsdangerous import URLSafeTimedSerializer
        app = Flask('host-proof'); app.secret_key = 'host-proof-secret'
        origin = {'host':True}
        register_guest_routes(app,port_agent=1234,internal_token='internal',is_host_request=lambda:origin['host'])
        client = app.test_client()
        signer = URLSafeTimedSerializer(app.secret_key,salt='group-human-invite-v1')
        transport = Mock(); transport.request.return_value = Mock(status_code=200)
        transport.request.return_value.json.return_value = {'group_id':'g_1','title':'Friends'}
        with patch('frontend.proxies.group_guests.requests.Session') as factory, \
                patch('groups.config.service_key',return_value='machine-key'), \
                patch('groups.config.service_url',return_value='http://127.0.0.1:51203'):
            factory.return_value.__enter__.return_value = transport
            for host,url,expected in [(True,'http://127.0.0.1:51203',True),(False,'http://127.0.0.1:51203',False),(True,'https://remote.example',False)]:
                origin['host'] = host
                ticket = signer.dumps({'url':url,'invite':'a'*43})
                response = client.post('/group-guest-api/info',json={},headers={'X-Group-Invite':ticket,'X-Group-Service-Key':'machine-key'})
                self.assertEqual(response.status_code,200)
                self.assertEqual('X-Group-Service-Key' in transport.request.call_args.kwargs['headers'],expected)


    def _ticket(self):
        with self.client.session_transaction() as session: session['user_id'] = 'alice'
        reply = Mock(status_code=200); reply.json.return_value={'server_url':'http://127.0.0.1:51203','invite':'a'*43}
        with patch('frontend.proxies.group_guests.requests.post', return_value=reply):
            link = self.client.post('/proxy_groups/rg_x/guest-link', json={}).json['url']
        with self.client.session_transaction() as session: session.clear()
        return link.split('#')[1]

    def test_a_device_joins_through_the_link_and_keeps_its_own_credential(self):
        ticket = self._ticket()
        self.assertEqual(self.client.post('/relay/join', headers={'X-Group-Invite': ticket + 'bad'}, json={}).status_code, 403)
        self.assertEqual(self.client.post('/relay/create', headers={'X-Group-Invite': ticket}, json={}).status_code, 404)
        self.assertEqual(self.client.post('/relay/guest-invites', headers={'X-Group-Invite': ticket}, json={}).status_code, 404)
        self.assertEqual(self.client.get('/relay/group', headers={'X-Group-Invite': ticket}).status_code, 401)
        transport = Mock(); transport.request.return_value = Mock(status_code=200)
        transport.request.return_value.json.return_value = {'token': 'device'}
        with patch('frontend.proxies.group_guests.requests.Session') as factory:
            factory.return_value.__enter__.return_value = transport
            joined = self.client.post('/relay/join', headers={'X-Group-Invite': ticket},
                                      json={'node_id': 'laptop', 'user_id': 'bob', 'display_name': 'Bob', 'password': 'x'})
            self.assertEqual(joined.status_code, 200)
            self.assertEqual(transport.request.call_args.args[1], 'http://127.0.0.1:51203/relay/join')
            self.assertEqual(transport.request.call_args.kwargs['json'],
                             {'invite': 'a'*43, 'node_id': 'laptop', 'user_id': 'bob', 'display_name': 'Bob', 'password': 'x'})
            polled = self.client.post('/relay/poll', headers={'X-Group-Invite': ticket, 'Authorization': 'Bearer device'},
                                      json={'cursor': 3})
            self.assertEqual(polled.status_code, 200)
            self.assertEqual(transport.request.call_args.kwargs['headers'], {'Authorization': 'Bearer device'})
            self.assertEqual(transport.request.call_args.kwargs['json'], {'cursor': 3})


class JoinLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ClientStore(Path(self.temp.name) / 'client.db')
        self.client = GroupClient(self.store, Mock(), Mock())
        self.calls, self.bodies = [], []
        def enroll(url, path, headers, body):
            self.calls.append((url, path, headers))
            self.bodies.append(body)
            if path.endswith('/info'):
                return {'group_id': 'g_1', 'title': 'Friends'}
            return {'token': 't', 'connection_id': 'c', 'group': {'group_id': 'g_1', 'title': 'Friends', 'members': []}}
        patcher = patch.object(GroupClient, 'enroll', side_effect=enroll)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_another_machines_link_polls_through_its_front_end(self):
        with patch('groups.client.own_front_ends', return_value={'https://me.example'}):
            card = self.client.join_link('bob', link='https://host.example/group-guest#ticket', password='password123')
        row = self.store.get('bob', card['group_id'])
        self.assertEqual((row['url'], row['via']), ('https://host.example', 'ticket'))
        self.assertEqual(self.calls[-1], ('https://host.example', '/relay/join', {'X-Group-Invite': 'ticket'}))
        self.assertEqual(GroupClient.headers(row), {'Authorization': 'Bearer t', 'X-Group-Invite': 'ticket'})
        again = self.client.join_link('bob', link='https://host.example/group-guest#ticket', password='password123')
        self.assertEqual(again['group_id'], card['group_id'])
        self.assertEqual([path for _url, path, _h in self.calls].count('/relay/join'), 1)  # joined once

    def test_this_machines_own_link_becomes_a_local_member(self):
        with patch('groups.client.own_front_ends', return_value={'https://me.example'}), \
                patch('groups.client.frontend_url', return_value='http://127.0.0.1:51209'), \
                patch('groups.client.service_url', return_value='http://127.0.0.1:51203'), \
                patch('groups.client.service_key', return_value='machine-key'), \
                patch.object(self.client, 'request', return_value={'host_local':True}) as attest:
            card = self.client.join_link('bob', link='https://me.example/group-guest#ticket', password='password123')
            self.assertEqual(attest.call_args.args[1:], ('POST', '/host-local', {}))
        row = self.store.get('bob', card['group_id'])
        self.assertEqual((row['url'], row['via']), ('http://127.0.0.1:51203', ''))
        self.assertEqual(self.calls[-1][0], 'http://127.0.0.1:51209')

    def test_the_first_join_signs_in_with_a_name_and_password(self):
        with patch('groups.client.own_front_ends', return_value=set()):
            self.client.join_link('bob', link='https://host.example/group-guest#ticket', name=' Bob ', password='password123')
        self.assertEqual({k: self.bodies[-1][k] for k in ('user_id', 'display_name', 'password')},
                         {'user_id': 'bob', 'display_name': 'Bob', 'password': 'password123'})

    def test_device_credentials_are_persistent_scoped_and_not_in_cards(self):
        link='https://host.example/group-guest#ticket'
        with patch('groups.client.own_front_ends', return_value=set()):
            card=self.client.join_link('bob',link=link,name='Bob')
            secret=self.bodies[-1]['password']
            self.assertGreaterEqual(len(secret),40)
            self.assertNotIn(secret,json.dumps(card))
            self.store.remove('bob',card['group_id'])
            reopened=ClientStore(self.store.path)
            self.assertEqual(reopened.login_secret('bob','https://host.example','g_1'),secret)
            self.client.join_link('bob',link=link,name='Bob')
            self.assertEqual(self.bodies[-1]['password'],secret)
            self.assertNotEqual(reopened.login_secret('alice','https://host.example','g_1'),secret)
            self.assertNotEqual(reopened.login_secret('bob','https://other.example','g_1'),secret)
            self.assertNotEqual(reopened.login_secret('bob','https://host.example','g_2'),secret)
            other=ClientStore(self.store.path.with_name('another-device.db'))
            self.assertNotEqual(other.login_secret('bob','https://host.example','g_1'),secret)

    def test_legacy_password_is_remembered_after_successful_login(self):
        with patch('groups.client.own_front_ends', return_value=set()):
            card=self.client.join_link('bob',link='https://host.example/group-guest#ticket',password='password123')
            self.store.remove('bob',card['group_id'])
            self.client.join_link('bob',link='https://host.example/group-guest#ticket')
            self.assertEqual(self.bodies[-1]['password'],'password123')

    def test_only_an_invitation_link_is_accepted(self):
        for bad in ('', 'g_1', 'https://host.example/studio#x', 'ftp://host.example/group-guest#x', 'https://host.example/group-guest'):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                self.client.join_link('bob', link=bad, password='password123')

if __name__ == '__main__': unittest.main()
