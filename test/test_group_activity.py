"""Shared activity: cross-device delivery, scoped identity, and expiring leases."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'backend'))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from groups.client import GroupClient
from groups.relay_api import relay_router
from groups.relay_store import ACTIVITY_SECONDS, RelayStore


class SharedActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RelayStore(Path(self.temp.name) / 'relay.db')
        self.host = self.store.create(title='Future room', node_id='one', user_id='alice', display_name='Alice')
        invite = self.store.guest_invite(self.host['token'])['invite']
        self.peer = self.store.join(invite, node_id='two', user_id='bob', display_name='Bob', password='password123')
        for connection in (self.host, self.peer):
            self.store.add_agent(connection['token'], agent_id='same', name='Companion', platform='acp')
        app = FastAPI()
        app.include_router(relay_router(self.store, 'test-host-key'))
        self.api = TestClient(app)
        self.addCleanup(self.api.close)

    def member(self, token, connection):
        return next(m for m in self.store.detail(token)['members']
                    if m['connection_id'] == connection['connection_id'] and m['is_agent'])

    def test_poll_shares_start_stop_and_expiry_without_chat_events(self):
        token = self.host['token']
        before = self.store.events(token, 0)['events']
        response = self.api.post('/relay/poll', headers={'Authorization': 'Bearer ' + token},
                                 json={'cursor': 0, 'agent_activity': ['same']})
        self.assertEqual(response.status_code, 200)
        running = self.member(self.peer['token'], self.host)['activity']
        self.assertEqual(running['state'], 'running')
        self.assertEqual(self.member(token, self.peer)['activity']['state'], 'idle')
        self.store.agent_activity(token, ['same'])
        self.assertEqual(self.member(self.peer['token'], self.host)['activity']['started_at'], running['started_at'])
        guest = self.store.guest_state(self.peer['token'])
        self.assertTrue(any(m['activity']['state'] == 'running' for m in guest['members']))
        with patch('groups.relay_store.time.time', return_value=time.time() + ACTIVITY_SECONDS + 1):
            self.assertEqual(self.member(self.peer['token'], self.host)['activity']['state'], 'idle')
        self.store.agent_activity(token, [])
        self.assertEqual(self.member(self.peer['token'], self.host)['activity']['state'], 'idle')
        self.assertEqual(self.store.events(token, 0)['events'], before)

    def test_activity_cannot_impersonate_other_members_and_validates_atomically(self):
        self.store.add_agent(self.peer['token'], agent_id='peer-only', name='Peer', platform='webot')
        self.store.agent_activity(self.host['token'], ['same'])
        for invalid, expected in [(['peer-only'], 403), (['unknown'], 403), ([{}], 400), ('same', 400)]:
            response = self.api.post('/relay/activity', headers={'Authorization': 'Bearer ' + self.host['token']},
                                     json={'running': invalid})
            self.assertEqual(response.status_code, expected)
            self.assertEqual(self.member(self.peer['token'], self.host)['activity']['state'], 'running')
        self.assertEqual(self.api.post('/relay/activity', headers={'Authorization': 'Bearer invalid'},
                                     json={'running': []}).status_code, 401)

    def test_websocket_broadcasts_activity_without_a_new_message(self):
        with self.api.websocket_connect('/relay/ws') as ws:
            ws.send_json({'token': self.peer['token'], 'cursor': 0})
            ws.receive_json()
            self.store.agent_activity(self.host['token'], ['same'])
            packet = ws.receive_json()
            member = next(m for m in packet['group']['members']
                          if m['connection_id'] == self.host['connection_id'] and m['is_agent'])
            self.assertEqual(member['activity']['state'], 'running')
            self.store.agent_activity(self.host['token'], [])
            packet = ws.receive_json()
            self.assertFalse(any(m['activity']['state'] == 'running' for m in packet['group']['members']))

    def test_device_client_keeps_remote_and_local_agent_ids_separate(self):
        self.store.agent_activity(self.host['token'], ['same'])
        row = {'owner': 'bob', 'alias': 'rg_peer', 'remote_id': self.peer['group']['group_id'],
               'connection_id': self.peer['connection_id'], 'url': 'https://group.example', 'error': '',
               'metadata': json.dumps(self.store.detail(self.peer['token'])), 'allowed_agents': '["same"]'}
        gateway = Mock()
        gateway.is_busy.return_value = False
        client = GroupClient(Mock(), Mock(), gateway)
        client.messages = Mock(return_value=[])
        card = client.card(row)
        agents = [m for m in card['members'] if m['is_agent']]
        remote = next(m for m in agents if m['remote'])
        local = next(m for m in agents if not m['remote'])
        self.assertEqual(remote['agent']['status']['state'], 'running')
        self.assertEqual(local['agent']['status']['state'], 'idle')
        self.assertNotEqual(remote['principal'], local['principal'])
        self.assertEqual(client.running_agents(row), [])
        gateway.is_busy.return_value = True
        self.assertEqual(client.running_agents(row), ['same'])


if __name__ == '__main__':
    unittest.main()
