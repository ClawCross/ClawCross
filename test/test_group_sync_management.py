"""Server history, paused networking and local host administration preserve identity."""
import json
import time
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from flask import Flask
import pytest

from groups.client import ClientError, ClientStore, GroupClient
from groups.relay_store import RelayError, RelayStore
from groups.server import create_app
from frontend.proxies.group_host import register_group_host_routes


def seed(tmp_path):
    store = RelayStore(tmp_path / 'group-relay.db')
    host = store.create(title='Local room', node_id='host', user_id='alice', display_name='Alice')
    invite = store.guest_invite(host['token'])['invite']
    remote = store.join(invite, node_id='remote', user_id='bob', display_name='Bob', password='password123')
    return store, host, remote, invite


def test_human_profile_is_self_owned_group_scoped_and_persistent(tmp_path):
    store, host, remote, invite = seed(tmp_path)
    store.add_agent(remote['token'], agent_id='helper', name='Bob helper', platform='webot')
    before = store.detail(host['token'])
    updated = store.manage(remote['token'], 'profile', {'name':'Bobby', 'avatar':'🌿', 'bio':'喜欢设计 <svg>', 'location':'上海'})
    me = next(m for m in updated['members'] if m['connection_id'] == remote['connection_id'] and not m['is_agent'])
    assert me['name'] == 'Bobby' and me['profile'] == {'text':'标记：🌿\n简介：喜欢设计 <svg>\n地区：上海','images':[]}
    assert updated['version'] > before['version']
    assert next(m for m in updated['members'] if m['agent_id'])['profile'] == {}
    restored = RelayStore(store.path).detail(remote['token'])
    assert next(m for m in restored['members'] if m['principal'] == me['principal'])['profile'] == me['profile']
    other = store.create(title='Elsewhere', node_id='remote', user_id='bob', display_name='Bobby')
    assert other['group']['members'][0]['profile'] == {'text':'','images':[]}
    with pytest.raises(RelayError):
        store.manage(host['token'], 'profile', {'principal':me['principal'],'bio':'overwrite another member'})
    for invalid in ({'name':'Alice'}, {'bio':'x'*281}, {'avatar':[]}, {'name':'   '}):
        with pytest.raises(RelayError):
            store.manage(remote['token'], 'profile', invalid)
    assert store.detail(remote['token'])['members'] == restored['members']


def test_guest_profile_card_exposes_public_fields_without_device_or_account_ids(tmp_path):
    store, host, _, invite = seed(tmp_path)
    guest = store.guest_join(invite, 'Carol', 'password123')
    store.manage(guest['token'], 'profile', {'bio':'Hello','location':'杭州'})
    state = store.guest_state(guest['token'], -1)
    me = next(m for m in state['members'] if m['principal'] == state['principal'])
    assert '简介：Hello' in me['profile']['text'] and me['joined_at'] > 0 and not me['is_owner']
    for member in state['members']:
        assert not {'node_id','connection_id','user_id'} & member.keys()


def test_existing_groups_receive_empty_profile_without_losing_history(tmp_path):
    store, host, _, _ = seed(tmp_path)
    store.post(host['token'], content='Preserve existing history')
    with store.db() as db:
        db.execute('ALTER TABLE relay_connections DROP COLUMN profile')
    restored = RelayStore(store.path)
    assert all(m['profile'] == {'text':'','images':[]} for m in restored.detail(host['token'])['members'])
    assert restored.messages(host['token'])[-1]['content'] == 'Preserve existing history'


def test_global_switch_preserves_per_group_policy_and_original_members(tmp_path):
    store, host, remote, invite = seed(tmp_path)
    other = store.create(title='Other', node_id='host', user_id='alice', display_name='Alice')
    store.manage(other['token'], 'external_access', {'enabled': False})
    store.post(host['token'], content='Keep history')
    before = store.detail(host['token'])
    store.set_network_enabled(False)
    assert store.detail(host['token'])['members'] == before['members']
    assert store.messages(host['token'])[-1]['content'] == 'Keep history'
    reopened = RelayStore(store.path)
    assert not reopened.network_status()['enabled']
    with pytest.raises(RelayError) as denied:
        reopened.detail(remote['token'])
    assert denied.value.status == 503
    with pytest.raises(RelayError):
        reopened.guest_join(invite, 'Friend', 'password123')
    added = reopened.create(title='New while paused', node_id='host', user_id='alice', display_name='Alice')
    assert not added['group']['external_access_enabled']
    reopened.set_network_enabled(True)
    assert reopened.detail(remote['token'])['external_access_enabled']
    assert not reopened.detail(other['token'])['external_access_enabled']


def test_global_admin_requires_host_key_and_disconnects_existing_socket(tmp_path):
    store, host, remote, _ = seed(tmp_path)
    app = create_app(data_dir=tmp_path, control_key='host-key', legacy=False)
    with TestClient(app, client=('127.0.0.1', 1234)) as api:
        assert api.post('/relay/admin/network', json={'enabled': False}).status_code == 403
        with api.websocket_connect('/relay/ws') as ws:
            ws.send_json({'token': remote['token'], 'cursor': 0}); ws.receive_json()
            assert api.post('/relay/admin/network', json={'enabled': False}, headers={'X-Group-Service-Key': 'host-key'}).status_code == 200
            from starlette.websockets import WebSocketDisconnect
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 1013
        assert api.get('/relay/group', headers={'Authorization': 'Bearer ' + host['token']}).status_code == 200
    with TestClient(app, client=('192.0.2.10', 1234)) as api:
        assert api.get('/relay/admin/network', headers={'X-Group-Service-Key': 'host-key'}).status_code == 403


def test_catch_up_is_ordered_and_paged_history_never_delivers_to_agents(tmp_path):
    store, host, _, _ = seed(tmp_path)
    ids=[]
    with store.db() as db:
        for index in range(230):
            ids.append(store._event(db, host['group']['group_id'], 'message', {'message': {
                'sender': 'person', 'sender_name': 'Alice', 'content': str(index), 'mentions': [], 'created_at': time.time()}, 'targets': []}))
    client = GroupClient(ClientStore(tmp_path / 'client.db'), Mock(), Mock())
    alias = client.store.save('alice', 'http://example.test', host)
    row = client.store.get('alice', alias)
    for event in store.events(host['token'], 0, limit=100)['events']:
        client.store.cache(row, event)
    # Fill the remaining cache through the same event protocol, without delivering.
    cursor=0
    while True:
        packet=store.events(host['token'], cursor, limit=100)
        if not packet['events']: break
        for event in packet['events']: client.store.cache(row, event)
        cursor=packet['events'][-1]['id']
    assert [e['id'] for e in client.store.messages('alice', alias, ids[0])] == ids[1:101]
    assert [m['id'] for m in store.messages(host['token'], ids[0])] == ids[1:101]
    client.search_messages=Mock(side_effect=lambda owner, alias, q, before, limit, **kw: store.search_messages(host['token'], q, before, limit, **kw))
    client._wake=AsyncMock()
    before=0;found=[]
    while True:
        page=client.history('alice', alias, before, 50);found=page['messages']+found
        before=page['next_before_id']
        if not before: break
    assert [m['id'] for m in found] == ids
    assert client.store.get('alice', alias)['cursor'] == row['cursor']
    client._wake.assert_not_called()
    client.search_messages=Mock(side_effect=ClientError('Offline',503))
    assert client.history('alice', alias, ids[30], 10)['source']=='cache'
    assert len(client.history('alice', alias, ids[30], 10)['messages'])==10


def test_browser_admin_never_proxies_a_remote_or_cross_origin_request():
    app=Flask(__name__)
    register_group_host_routes(app,is_host_request=lambda:False)
    with app.test_client() as api, patch('frontend.proxies.group_host.requests.request') as upstream:
        assert api.get('/group-host').status_code==403
        assert api.get('/proxy_group_host').status_code==403
        assert api.post('/proxy_group_host/network',json={'enabled':False}).status_code==403
        upstream.assert_not_called()
    app=Flask('local')
    register_group_host_routes(app,is_host_request=lambda:True)
    with app.test_client() as api, patch('frontend.proxies.group_host.requests.request') as upstream:
        assert api.post('/proxy_group_host/network',json={'enabled':False},headers={'Origin':'https://evil.test'}).status_code==403
        assert api.get('/proxy_group_host',headers={'Host':'evil.test'}).status_code==403
        upstream.assert_not_called()
