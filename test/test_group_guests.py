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
from groups.client import ClientStore, GroupClient
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

    def test_name_collisions_and_concurrent_join(self):
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, ' alice ', 'guest-password')
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, '\u200b', 'guest-password')
        def join():
            try: return self.store.guest_join(self.invite, 'Bob', 'guest-password')
            except RelayError as exc: return exc.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: join(), range(2)))
        self.assertTrue(all(isinstance(r, dict) for r in results))
        self.assertEqual(len(self.store.detail(self.host['token'])['members']), 2)
        self.assertEqual(self.store.guest_identity(results[0]['token'])['principal'], self.store.guest_identity(results[1]['token'])['principal'])
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, 'ＢＯＢ', 'wrong-password')
        with self.assertRaises(RelayError): self.store.add_agent(self.host['token'], agent_id='a', name='bob', platform='webot')

    def test_guest_can_chat_and_rename_but_cannot_manage_or_add_agents(self):
        guest = self.store.guest_join(self.invite, 'Bob', 'guest-password')
        token = guest['token']
        posted = self.store.post(token, content='Hello', client_msg_id='one')
        self.assertFalse(self.store.post(token, content='Hello', client_msg_id='one')['created'])
        self.assertEqual(self.store.messages(self.host['token'])[-1]['content'], 'Hello')
        for action, fields in [('patch', {'title':'Hijack'}), ('delete', {}), ('primary', {})]:
            with self.assertRaises(RelayError): self.store.manage(token, action, fields)
        with self.assertRaises(RelayError): self.store.add_agent(token, agent_id='x', name='X', platform='webot')
        with self.assertRaises(RelayError): self.store.post(token, content='fake', agent_id='x')
        with self.assertRaises(RelayError): self.store.guest_rename(token, 'Alice')
        self.store.guest_rename(token, 'Carol')
        state = self.store.guest_state(token)
        self.assertEqual(state['name'], 'Carol')
        self.assertEqual(state['messages'][-1]['id'], posted['message']['id'])
        self.assertNotIn('user_id', str(state))
        self.store.post(self.host['token'], content='Welcome')
        self.assertEqual(self.store.guest_state(token, state['cursor'])['messages'][-1]['content'], 'Welcome')
        self.store.manage(self.host['token'], 'remove_member', {'principal': state['principal']})
        with self.assertRaises(RelayError): self.store.guest_state(token)

    def test_password_login_reuses_member_and_preserves_browser_tokens(self):
        first = self.store.guest_join(self.invite, 'Bob', 'correct-password')
        self.store.post(first['token'], content='Before closing')
        with self.assertRaises(RelayError) as error:
            self.store.guest_join(self.invite, 'Bob', 'wrong-password')
        self.assertEqual(error.exception.status, 403)
        self.store.guest_set_password(first['token'], 'new-password')
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, 'Bob', 'correct-password')
        second = self.store.guest_join(self.invite, 'ＢＯＢ', 'new-password')
        self.assertNotEqual(first['token'], second['token'])
        state = self.store.guest_state(second['token'])
        self.assertEqual(state['principal'], self.store.guest_state(first['token'])['principal'])
        self.assertEqual(len(state['members']), 2)
        self.assertEqual(state['messages'][0]['content'], 'Before closing')
        self.store.guest_rename(second['token'], 'Carol')
        third = self.store.guest_join(self.invite, 'Carol', 'new-password')
        self.assertEqual(self.store.guest_state(third['token'])['principal'], state['principal'])
        self.store.manage(self.host['token'], 'remove_member', {'principal':state['principal']})
        for token in (first['token'], second['token'], third['token']):
            with self.assertRaises(RelayError): self.store.guest_state(token)
        self.assertTrue(self.store.guest_join(self.invite, 'Carol', 'fresh-password'))

    def test_legacy_guest_requires_existing_identity_to_set_password(self):
        guest = self.store.guest_join(self.invite, 'Old guest', 'first-password')
        with self.store.db() as db:
            db.execute("UPDATE relay_connections SET guest_password_hash='',guest_salt='' WHERE token_hash=?", (digest(guest['token']),))
        self.assertFalse(self.store.guest_state(guest['token'])['password_set'])
        with self.assertRaises(RelayError) as error: self.store.guest_join(self.invite, 'Old guest', 'new-password')
        self.assertEqual(error.exception.status, 409)
        self.store.guest_set_password(guest['token'], 'new-password')
        restored = self.store.guest_join(self.invite, 'Old guest', 'new-password')
        self.assertEqual(self.store.guest_identity(restored['token']), self.store.guest_identity(guest['token']))
        with self.assertRaises(RelayError): self.store.guest_set_password(self.host['token'], 'new-password')

    def test_client_exposes_human_removal_only_to_group_owner(self):
        self.store.guest_join(self.invite, 'Bob', 'guest-password')
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
        guest = self.store.guest_join(self.invite, 'Bob', 'guest-password')
        new = self.store.guest_invite(self.host['token'])['invite']
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, 'Old', 'guest-password')
        reopened = RelayStore(self.store.path)
        self.assertEqual(reopened.guest_identity(guest['token'])['name'], 'Bob')
        self.assertTrue(reopened.guest_join(new, 'New', 'guest-password'))
        reopened.guest_invite(self.host['token'], disable=True)
        with self.assertRaises(RelayError): reopened.guest_info(new)

    def test_the_invitation_link_also_joins_a_device_as_a_full_member(self):
        joined = self.store.join(self.invite, node_id='laptop', user_id='bob', display_name='Bob')
        card = self.store.add_agent(joined['token'], agent_id='helper', name='Helper', platform='codex')
        self.assertTrue(any(m['is_agent'] and m['agent_id'] == 'helper' for m in card['members']))
        self.store.guest_invite(self.host['token'])  # a new link: the old one no longer joins
        with self.assertRaises(RelayError):
            self.store.join(self.invite, node_id='other', user_id='carol', display_name='Carol')
        self.assertEqual(self.store.detail(joined['token'])['title'], 'Friends')  # joined members stay

    def test_poll_carries_the_websocket_stream_over_http(self):
        joined = self.store.join(self.invite, node_id='laptop', user_id='bob', display_name='Bob')
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
            response = client.post('/relay/guest/join', json={'invite':self.invite,'name':'Bob','password':'guest-password'})
            self.assertEqual(response.status_code, 200)
            headers = {'Authorization':'Bearer ' + response.json()['token']}
            state = client.get('/relay/guest/state', headers=headers)
            self.assertEqual(state.status_code, 200)
            self.assertNotIn('user_id', state.text)
            self.assertEqual(client.post('/relay/agents', headers=headers, json={'agent_id':'x','name':'X'}).status_code, 403)
            self.assertEqual(client.post('/relay/manage/patch', headers=headers, json={'title':'X'}).status_code, 403)
            self.assertEqual(client.post('/relay/guest/messages', headers=headers, json={'content':'Hello'}).status_code, 200)
            self.assertEqual(client.post('/relay/guest/messages', headers=headers, json={'content':'你好', 'mentions':[agent]}).status_code, 200)
            self.assertEqual(self.store.events(self.host['token'], 0)['events'][-1]['targets'], [agent])
            self.assertEqual(client.post('/relay/guest/messages', headers=headers, json={'content':'你好', 'mentions':['foreign-member']}).status_code, 400)
            self.assertEqual(client.get('/relay/guest/state', headers={'Authorization':'Bearer '+self.host['token']}).status_code, 403)


class GuestProxyTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__, template_folder=str(ROOT / 'src/frontend/templates'))
        self.app.secret_key = 'test-secret'
        register_guest_routes(self.app, port_agent=1234, internal_token='internal', public_base=lambda:'https://chat.example')
        self.client = self.app.test_client()

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
        with self.client.session_transaction() as session: self.assertNotIn('user_id', session)

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
                             {'invite': 'a'*43, 'node_id': 'laptop', 'user_id': 'bob', 'display_name': 'Bob'})
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
        self.calls = []
        def enroll(url, path, headers, body):
            self.calls.append((url, path, headers))
            if path.endswith('/info'):
                return {'group_id': 'g_1', 'title': 'Friends'}
            return {'token': 't', 'connection_id': 'c', 'group': {'group_id': 'g_1', 'title': 'Friends', 'members': []}}
        patcher = patch.object(GroupClient, 'enroll', side_effect=enroll)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_another_machines_link_polls_through_its_front_end(self):
        with patch('groups.client.own_front_ends', return_value={'https://me.example'}):
            card = self.client.join_link('bob', link='https://host.example/group-guest#ticket')
        row = self.store.get('bob', card['group_id'])
        self.assertEqual((row['url'], row['via']), ('https://host.example', 'ticket'))
        self.assertEqual(self.calls[-1], ('https://host.example', '/relay/join', {'X-Group-Invite': 'ticket'}))
        self.assertEqual(GroupClient.headers(row), {'Authorization': 'Bearer t', 'X-Group-Invite': 'ticket'})
        again = self.client.join_link('bob', link='https://host.example/group-guest#ticket')
        self.assertEqual(again['group_id'], card['group_id'])
        self.assertEqual([path for _url, path, _h in self.calls].count('/relay/join'), 1)  # joined once

    def test_this_machines_own_link_becomes_a_local_member(self):
        with patch('groups.client.own_front_ends', return_value={'https://me.example'}), \
                patch('groups.client.frontend_url', return_value='http://127.0.0.1:51209'), \
                patch('groups.client.service_url', return_value='http://127.0.0.1:51203'), \
                patch('groups.client.service_key', return_value='machine-key'), \
                patch.object(self.client, 'request', return_value={'host_local':True}) as attest:
            card = self.client.join_link('bob', link='https://me.example/group-guest#ticket')
            self.assertEqual(attest.call_args.args[1:], ('POST', '/host-local', {}))
        row = self.store.get('bob', card['group_id'])
        self.assertEqual((row['url'], row['via']), ('http://127.0.0.1:51203', ''))
        self.assertEqual(self.calls[-1][0], 'http://127.0.0.1:51209')

    def test_only_an_invitation_link_is_accepted(self):
        for bad in ('', 'g_1', 'https://host.example/studio#x', 'ftp://host.example/group-guest#x', 'https://host.example/group-guest'):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                self.client.join_link('bob', link=bad)

if __name__ == '__main__': unittest.main()
