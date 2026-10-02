"""Guest invitation isolation, identity collisions, and the public browser proxy."""
import concurrent.futures
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'backend'))
sys.path.insert(0, str(ROOT / 'src'))
from groups.relay_store import RelayStore, RelayError
from groups.server import create_app
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
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, ' alice ')
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, '\u200b')
        def join():
            try: return self.store.guest_join(self.invite, 'Bob')
            except RelayError as exc: return exc.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: join(), range(2)))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertIn(409, results)
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, 'ＢＯＢ')
        with self.assertRaises(RelayError): self.store.add_agent(self.host['token'], agent_id='a', name='bob', platform='webot')

    def test_guest_can_chat_and_rename_but_cannot_manage_or_add_agents(self):
        guest = self.store.guest_join(self.invite, 'Bob')
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

    def test_invite_rotation_and_restart_do_not_eject_existing_guests(self):
        guest = self.store.guest_join(self.invite, 'Bob')
        new = self.store.guest_invite(self.host['token'])['invite']
        with self.assertRaises(RelayError): self.store.guest_join(self.invite, 'Old')
        reopened = RelayStore(self.store.path)
        self.assertEqual(reopened.guest_identity(guest['token'])['name'], 'Bob')
        self.assertTrue(reopened.guest_join(new, 'New'))
        reopened.guest_invite(self.host['token'], disable=True)
        with self.assertRaises(RelayError): reopened.guest_info(new)

    def test_api_requires_guest_token_and_filters_private_metadata(self):
        card = self.store.add_agent(self.host['token'], agent_id='creative', name='创意专家', platform='webot')
        agent = next(m['principal'] for m in card['members'] if m['is_agent'])
        with TestClient(create_app(data_dir=self.temp.name, control_key='key', legacy=False)) as client:
            response = client.post('/relay/guest/join', json={'invite':self.invite,'name':'Bob'})
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
            result = self.client.post('/group-guest-api/join', headers={'X-Group-Invite':ticket}, json={'name':'Bob','agent_id':'forged'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(transport.request.call_args.kwargs['json'], {'invite':'a'*43,'name':'Bob'})
        with self.client.session_transaction() as session: self.assertNotIn('user_id', session)


if __name__ == '__main__': unittest.main()
