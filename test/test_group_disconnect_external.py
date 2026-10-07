"""External networking can pause and resume without altering group membership."""

from pathlib import Path
import sys
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/backend'))

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from groups.relay_store import RelayError, RelayStore
from groups.server import create_app
from groups.client import ClientStore, GroupClient


@pytest.fixture
def group(tmp_path):
    store = RelayStore(tmp_path / 'group-relay.db')
    host = store.create(title='Friends', node_id='host', user_id='alice', display_name='Alice')
    invite = store.guest_invite(host['token'])['invite']
    return store, host, invite, tmp_path


def test_pause_preserves_members_credentials_invites_and_history_and_can_resume(group):
    store, host, invite, _ = group
    local = store.join(invite, node_id='host', user_id='cathy', display_name='Cathy')
    store.confirm_host_local(local['token'])
    # Self-reported host node IDs do not protect an unverified remote member.
    external = store.join(invite, node_id='host', user_id='bob', display_name='Bob')
    guest = store.guest_join(invite, 'Guest', 'password123')
    agent = store.add_agent(external['token'], agent_id='remote-agent', name='Remote', platform='webot')
    principal = next(member['principal'] for member in agent['members'] if member['agent_id'])
    store.manage(host['token'], 'primary', {'principal':principal})
    message = store.post(external['token'], content='History remains')['message']
    result = store.manage(host['token'], 'disconnect_external', {})
    assert result['external_access_enabled'] is False
    assert {member['name'] for member in result['members']} == {'Alice', 'Cathy', 'Bob', 'Guest', 'Remote'}
    assert result['primary_agent'] == principal
    assert store.messages(host['token'])[-1]['id'] == message['id']
    reopened = RelayStore(store.path)
    assert reopened.detail(local['token'])['title'] == 'Friends'
    for peer in (external, guest):
        with pytest.raises(RelayError) as paused: reopened.detail(peer['token'])
        assert paused.value.status == 503
        with reopened.db() as db:
            assert reopened.auth(db, peer['token'], check_network=False)['revoked'] == 0
    with pytest.raises(RelayError) as paused: store.join(invite, node_id='new', user_id='new', display_name='New')
    assert paused.value.status == 503
    with pytest.raises(RelayError) as paused: store.guest_join(invite, 'Guest', 'password123')
    assert paused.value.status == 503
    # Machine-confirmed local joins still work while external access is paused.
    assert store.join(invite,node_id='host',user_id='local',display_name='Local',host_local=True)['group']['title'] == 'Friends'
    store.manage(host['token'],'external_access',{'enabled':True})
    for peer in (external, guest):
        assert reopened.detail(peer['token'])['external_access_enabled'] is True
    assert store.join(invite, node_id='new', user_id='new', display_name='New')['group']['title'] == 'Friends'
    assert store.guest_state(guest['token'])['title'] == 'Friends'


def test_nonowner_cannot_close_group_and_other_groups_are_unchanged(group):
    store, host, invite, _ = group
    external = store.join(invite, node_id='remote', user_id='bob', display_name='Bob')
    with pytest.raises(RelayError) as error:
        store.manage(external['token'], 'disconnect_external', {})
    assert error.value.status == 403
    other = store.create(title='Other', node_id='host', user_id='alice', display_name='Alice')
    peer = store.join(store.guest_invite(other['token'])['invite'], node_id='remote', user_id='bob', display_name='Bob')
    store.manage(host['token'], 'disconnect_external', {})
    assert store.detail(peer['token'])['title'] == 'Other'


def test_host_confirmation_requires_local_machine_key_and_rejects_guests(group):
    store, host, invite, root = group
    external = store.join(invite, node_id='host', user_id='bob', display_name='Bob')
    guest = store.guest_join(invite, 'Guest', 'password123')
    app = create_app(data_dir=root, control_key='machine-key', legacy=False)
    headers = {'Authorization':'Bearer '+external['token']}
    with TestClient(app, client=('127.0.0.1',1234)) as api:
        assert api.post('/relay/host-local',headers=headers).status_code == 403
        headers['X-Group-Service-Key'] = 'machine-key'
        assert api.post('/relay/host-local',headers=headers).status_code == 200
        assert api.post('/relay/host-local',headers={**headers,'Authorization':'Bearer '+guest['token']}).status_code == 403
    with TestClient(app, client=('192.0.2.1',1234)) as api:
        assert api.post('/relay/host-local',headers=headers).status_code == 403


def test_existing_websocket_is_temporarily_closed_and_original_credential_resumes(group):
    store, host, invite, root = group
    external = store.join(invite, node_id='remote', user_id='bob', display_name='Bob')
    with TestClient(create_app(data_dir=root,control_key='machine-key',legacy=False)) as api:
        with api.websocket_connect('/relay/ws') as ws:
            ws.send_json({'token':external['token'],'cursor':0})
            ws.receive_json()
            response = api.post('/relay/manage/disconnect_external',json={},headers={'Authorization':'Bearer '+host['token']})
            assert response.status_code == 200
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 1013
        headers={'Authorization':'Bearer '+external['token']}
        assert api.post('/relay/poll',json={'cursor':0},headers=headers).status_code == 503
        assert api.post('/relay/manage/external_access',json={'enabled':True},headers={'Authorization':'Bearer '+host['token']}).status_code == 200
        assert api.post('/relay/poll',json={'cursor':0},headers=headers).status_code == 200
        with api.websocket_connect('/relay/ws') as ws:
            ws.send_json({'token':external['token'],'cursor':0})
            assert ws.receive_json()['connection_id'] == external['connection_id']


async def test_local_worker_confirms_host_identity_before_connecting(group):
    _store, host, _invite, root = group
    client = GroupClient(ClientStore(root / 'client.db'), Mock(), Mock())
    alias = client.store.save('alice', 'http://127.0.0.1:51203', host)
    client.request = Mock(return_value={'host_local':True})
    async def connected(*_):
        assert client.request.call_args.args[1:] == ('POST','/host-local',{})
        client.closed = True
    client._stream = AsyncMock(side_effect=connected)
    with patch('groups.client.service_url',return_value='http://127.0.0.1:51203'):
        await client.worker('alice',alias)
    client._stream.assert_awaited_once()
