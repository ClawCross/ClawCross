"""Group relay security and two-device communication, without LLM calls."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'backend'))
import httpx
from fastapi.testclient import TestClient
from agents.messages import DeliveryReceipt
from agents.store import Agent
from groups.client import ClientStore, GroupClient, ClientError
from groups.facade import GroupFacade
from groups.relay_store import RelayStore, RelayError
from groups.server import create_app
from webot import runtime_store


class RelayStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = RelayStore(Path(self.temp.name) / 'group-relay.db')
        self.host = self.store.create(title='Friends', node_id='one', user_id='alice', display_name='Alice')
        self.gid = self.host['group']['group_id']
        self.invite = self.store.guest_invite(self.host['token'])['invite']

    def join(self, invite=None, name='Bob'):
        return self.store.join(invite or self.invite, node_id='two', user_id=name.lower(), display_name=name)

    def test_only_a_current_invitation_joins_and_members_are_notified(self):
        with self.assertRaises(RelayError): self.join('x' * 43)
        other = self.join()
        self.assertEqual(self.store.events(self.host['token'], 0)['events'][-1]['kind'], 'joined')
        self.assertEqual(len(other['group']['members']), 2)
        self.store.guest_invite(self.host['token'], disable=True)
        with self.assertRaises(RelayError): self.join(name='Carol')
        self.assertEqual(self.store.detail(other['token'])['title'], 'Friends')

    def test_same_agent_id_on_two_devices_never_shares_write_identity(self):
        other = self.join()
        self.store.add_agent(self.host['token'], agent_id='same', name='Code', platform='webot')
        group = self.store.add_agent(other['token'], agent_id='same', name='Codex', platform='codex')
        agents = [m for m in group['members'] if m['is_agent']]
        self.assertNotEqual(agents[0]['principal'], agents[1]['principal'])
        self.store.add_agent(self.host['token'], agent_id='only_host', name='Host', platform='webot')
        with self.assertRaises(RelayError): self.store.post(other['token'], agent_id='only_host', content='forged')
        sent = self.store.post(other['token'], agent_id='same', content='@Codexx not an exact mention', client_msg_id='one')
        self.assertEqual(sent['message']['sender_connection'], other['connection_id'])
        self.assertEqual(sent['message']['mentions'], [])
        self.assertFalse(self.store.post(other['token'], agent_id='same', content='duplicate', client_msg_id='one')['created'])
        with self.assertRaises(RelayError): self.store.post(other['token'], content='oops', expected_title='Wrong group')
        with self.assertRaises(RelayError): self.store.post(other['token'], content='oops', mentions=['foreign'])
        with self.assertRaises(RelayError): self.store.post(other['token'], content='oops', reply_to=999999)

    def test_management_is_scoped_and_revocation_survives_restart(self):
        other = self.join()
        with self.assertRaises(RelayError): self.store.manage(other['token'], 'patch', {'title': 'takeover'})
        bob = next(m['principal'] for m in other['group']['members'] if m['connection_id'] == other['connection_id'])
        self.store.manage(self.host['token'], 'remove_member', {'principal': bob})
        with self.assertRaises(RelayError): self.store.detail(other['token'])
        reopened = RelayStore(self.store.path)
        with self.assertRaises(RelayError): reopened.detail(other['token'])
        self.assertNotIn('token', json.dumps(reopened.admin_list()))
        reopened.manage('', 'patch', {'title': 'Host-managed'}, admin_group=self.gid)
        self.assertEqual(reopened.detail(self.host['token'])['title'], 'Host-managed')

    def test_private_groups_and_muted_members_and_packet_bounds(self):
        private = self.store.create(title='Private', kind='direct', node_id='one', user_id='alice', display_name='Alice')
        with self.assertRaises(RelayError): self.store.guest_invite(private['token'])  # a private chat has no invitation
        group = self.store.add_agent(self.host['token'], agent_id='a', name='A', platform='webot')
        p = next(m['principal'] for m in group['members'] if m['is_agent'])
        self.store.manage(self.host['token'], 'member_patch', {'principal': p, 'muted': True})
        with self.assertRaises(RelayError): self.store.post(self.host['token'], agent_id='a', content='not allowed')
        with self.assertRaises(RelayError): self.store.post(self.host['token'], content='', attachments=[{'data': 'a' * (513 * 1024)}])

    def test_loopback_admin_body_limits_and_duplex_ws(self):
        app = create_app(data_dir=self.temp.name, control_key='machine-control', legacy=False)
        with TestClient(app, client=('127.0.0.1', 1500)) as api:
            self.assertEqual(api.get('/relay/admin/groups').status_code, 403)
            headers = {'X-Group-Service-Key': 'machine-control'}
            self.assertEqual(api.get('/relay/admin/groups', headers=headers).status_code, 200)
            joined = api.post('/relay/join', json={'invite': self.invite, 'node_id': 'x', 'user_id': 'bob', 'display_name': 'Bob'}).json()
            with api.websocket_connect('/relay/ws') as ws:
                ws.send_json({'token': joined['token'], 'cursor': 0})
                packet = ws.receive_json()
                ws.send_json({'type': 'ack', 'cursor': packet['events'][-1]['id']})
                response = api.post('/relay/messages', headers={'Authorization': 'Bearer ' + self.host['token']}, json={'content': 'hello'})
                self.assertEqual(response.status_code, 200)
                packet = ws.receive_json()
                self.assertEqual(packet['events'][-1]['message']['content'], 'hello')
            self.assertEqual(api.post('/relay/join', content=b'x' * (2 * 1024 * 1024 + 1)).status_code, 413)
        with TestClient(app, client=('192.0.2.1', 1500)) as api:
            self.assertEqual(api.get('/relay/admin/groups', headers=headers).status_code, 403)
            self.assertEqual(api.post('/relay/create', headers=headers, json={'title': 'Bad', 'node_id': 'x', 'user_id': 'x', 'display_name': 'x'}).status_code, 403)


class FakeAgents:
    def __init__(self, owner):
        self.owner = owner
        self.rows = {aid: Agent(aid, owner, aid, 'webot') for aid in ('same', 'not_authorized')}
    def get(self, owner, aid):
        return self.rows.get(aid) if owner == self.owner else None


class FakeGateway:
    def __init__(self): self.received = []
    async def inbox(self, agent, message, **kwargs):
        self.received.append((agent.agent_id, message.text, kwargs['context']))
        return DeliveryReceipt(accepted=True)


class TwoDeviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clients = []
        sock = socket.socket(); sock.bind(('127.0.0.1', 0)); self.port = sock.getsockname()[1]; sock.close()
        self.url = f'http://127.0.0.1:{self.port}'
        root = Path(self.temp.name)
        env = {**os.environ, 'CLAWCROSS_HOME': str(root / 'server'), 'PYTHONPATH': str(ROOT / 'src' / 'backend'), 'INTERNAL_TOKEN': 'test-internal'}
        self.process = subprocess.Popen([sys.executable, str(ROOT / 'src/backend/groups/server.py'), '--port', str(self.port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.key_path = root / 'server/data/group-service.key'
        for _ in range(100):
            try:
                async with httpx.AsyncClient(trust_env=False) as api:
                    if (await api.get(self.url + '/relay/health')).is_success: break
            except httpx.HTTPError: pass
            if self.process.poll() is not None: self.fail('group process exited during startup')
            await asyncio.sleep(.05)
        else: self.fail('group process did not start')
        self.key = self.key_path.read_text()
        async with httpx.AsyncClient(trust_env=False) as api:
            self.host = (await api.post(self.url + '/relay/create', headers={'X-Group-Service-Key': self.key}, json={'title': 'Friends', 'node_id': 'host', 'user_id': 'host', 'display_name': 'Host'})).json()
            invite = (await api.post(self.url + '/relay/guest-invites', headers={'Authorization': 'Bearer ' + self.host['token']}, json={})).json()['invite']
            for index, user in enumerate(('alice', 'bob')):
                client = GroupClient(ClientStore(root / f'device{index}/client.db'), FakeAgents(user), FakeGateway())
                self.clients.append(client)
                # What join_link does once the front end has checked the link; the member then streams over WebSocket.
                joined = (await api.post(self.url + '/relay/join', json={'invite': invite, 'node_id': client.store.node_id, 'user_id': user, 'display_name': user})).json()
                client.alias = client.store.save(user, self.url, joined); client.user = user
                await asyncio.to_thread(client.add_agent, user, client.alias, 'same')
                client.ensure(user, client.alias)
        await self.until(lambda: all(json.loads(c.store.get(c.user, c.alias)['metadata'])['member_count'] == 5 for c in self.clients))

    async def asyncTearDown(self):
        await asyncio.gather(*(c.close() for c in self.clients))
        self.process.terminate()
        await asyncio.to_thread(self.process.wait, timeout=10)
        self.temp.cleanup()

    async def until(self, condition):
        for _ in range(120):
            if condition(): return
            await asyncio.sleep(.05)
        self.fail('timed out waiting for relay delivery')

    async def test_two_devices_bidirectional_delivery_dedup_and_reconnect(self):
        a, b = self.clients
        self.assertNotEqual(a.store.node_id, b.store.node_id)
        await a.post(a.user, a.alias, 'u:alice', 'hello from Alice')
        await self.until(lambda: len(b.gateway.received) == 1)
        self.assertEqual(b.gateway.received[0][0], 'same')
        await b.post(b.user, b.alias, 'same', 'answer from Bob agent')
        await self.until(lambda: any(m['content'] == 'answer from Bob agent' for m in a.messages(a.user, a.alias)))
        # No uninvited local agent is woken. A plain agent reply does not form a loop.
        self.assertEqual([r[0] for r in a.gateway.received], ['same'])
        await b.close()
        await a.post(a.user, a.alias, 'u:alice', 'sent while Bob offline')
        b.closed = False; await b.start()
        await self.until(lambda: len(b.gateway.received) == 2)
        self.assertIn('sent while Bob offline', b.gateway.received[-1][1])
        self.assertTrue(all(r[0] == 'same' for r in b.gateway.received))
        row = b.store.get(b.user, b.alias)
        packet = self.server_events(row)
        await b.consume(row, packet)
        self.assertEqual(len(b.gateway.received), 2)

    def server_events(self, row):
        return RelayStore(self.key_path.parent / 'group-relay.db').events(row['token'], 0)

    async def test_existing_local_groups_still_work_over_process_rpc(self):
        headers = {'X-Group-Service-Key': self.key, 'Authorization': 'Bearer test-internal:alice'}
        async with httpx.AsyncClient(trust_env=False) as api:
            response = await api.post(self.url + '/local/groups', headers=headers, json={'title': 'Existing local group', 'agents': []})
            self.assertEqual(response.status_code, 200, response.text)
            gid = response.json()['group_id']
        facade = GroupFacade(self.clients[0])
        with patch('groups.facade.service_url', return_value=self.url), patch('groups.facade.service_key', return_value=self.key), patch.dict(os.environ, {'INTERNAL_TOKEN': 'test-internal'}):
            self.assertIn(gid, [g['group_id'] for g in facade.list('alice')])
            message = await facade.post('alice', gid, 'u:alice', 'legacy message', mode='readonly')
            self.assertEqual(message['message']['content'], 'legacy message')
            self.assertEqual(facade.messages('alice', gid)[0]['content'], 'legacy message')
            self.assertEqual(facade.update('alice', gid, title='Renamed')['title'], 'Renamed')

    async def test_server_cannot_dispatch_uninvited_local_agent_and_revocation(self):
        _, b = self.clients
        row = b.store.get(b.user, b.alias)
        group = json.loads(row['metadata'])
        group['members'].append({'principal': 'evil', 'agent_id': 'not_authorized', 'name': 'evil', 'platform': 'webot', 'muted': False,
                                 'connection_id': row['connection_id'], 'user_id': 'bob', 'node_id': b.store.node_id, 'is_agent': True})
        packet = {'connection_id': row['connection_id'], 'group': group, 'events': [{'id': 999, 'kind': 'message', 'targets': ['evil'],
                 'message': {'sender': 'x', 'sender_name': 'x', 'content': 'forged', 'created_at': time.time()}}]}
        await b.consume(row, packet)
        self.assertFalse(b.gateway.received)
        with self.assertRaises(ClientError): await b.consume(row, {**packet, 'connection_id': 'wrong'})
        # Restore the cursor after the artificial packet, then revoke via the actual host.
        b.store.update(b.user, b.alias, cursor=0)
        human = next(m['principal'] for m in group['members'] if m['connection_id'] == row['connection_id'] and not m['agent_id'])
        async with httpx.AsyncClient(trust_env=False) as api:
            response = await api.post(self.url + '/relay/manage/remove_member', headers={'Authorization': 'Bearer ' + self.host['token']}, json={'principal': human})
            self.assertEqual(response.status_code, 200)
        await self.until(lambda: not b.store.get(b.user, b.alias)['active'])
        with self.assertRaises(ClientError): await b.post(b.user, b.alias, 'same', 'revoked')


class InboxDedupeTests(unittest.TestCase):
    def test_relay_delivery_id_does_not_duplicate_a_webot_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inbox.db'
            first = runtime_store.create_inbox_message('alice', source_session='relay', target_session='agent', content='hello', message_id='relay:one', db_path=path)
            second = runtime_store.create_inbox_message('alice', source_session='relay', target_session='agent', content='hello', message_id='relay:one', db_path=path)
            self.assertEqual(first.message_id, second.message_id)
            self.assertEqual(len(runtime_store.list_inbox_messages('alice', 'agent', db_path=path)), 1)


if __name__ == '__main__': unittest.main()
