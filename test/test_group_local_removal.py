"""Offline local removal stops reconnecting without modifying the remote group."""

import asyncio
from pathlib import Path
import sys
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/backend'))

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from groups.client import ClientError, ClientStore, GroupClient
from groups.facade import GroupFacade, client_router


@pytest.fixture
def setup_client(tmp_path):
    store = ClientStore(tmp_path / 'client.db')
    client = GroupClient(store, Mock(), Mock())
    client.request = Mock(side_effect=AssertionError('Local removal must not contact the server'))
    result = {'connection_id': 'alice-connection', 'token': 'alice-token', 'group': {
        'group_id': 'g_remote', 'title': 'Offline friends', 'kind': 'group',
        'owner': 'remote-owner', 'members': [], 'primary_agent': None}}
    alias = store.save('alice', 'https://remote.example', result)
    return client, alias, result


async def test_offline_removal_cancels_worker_purges_credentials_and_prevents_late_cache(setup_client):
    client, alias, _ = setup_client
    row = client.store.get('alice', alias)
    event = {'id': 1, 'kind': 'message', 'message': {'content': 'cached'}}
    client.store.cache(row, event)
    client.store.mark_delivered(row, 1, 'agent')
    client.store.update('alice', alias, error='连接中断，正在重连')
    worker = asyncio.create_task(asyncio.sleep(60))
    client.tasks[('alice', alias)] = worker

    assert await client.remove_local('alice', alias) == {'removed': alias, 'local_only': True}
    assert worker.cancelled()
    assert not client.tasks
    assert client.store.get('alice', alias) is None
    assert not client.store.messages('alice', alias)
    assert not client.store.delivered(row, 1, 'agent')
    client.store.cache(row, event)
    client.store.mark_delivered(row, 1, 'agent')
    assert not client.store.messages('alice', alias)
    assert not client.store.delivered(row, 1, 'agent')
    await client.start()
    assert not client.tasks
    client.request.assert_not_called()


async def test_revoked_connection_can_still_be_removed(setup_client):
    client, alias, _ = setup_client
    client.store.update('alice', alias, active=0)
    await client.remove_local('alice', alias)
    assert client.store.get('alice', alias) is None


async def test_removal_is_scoped_to_the_authenticated_user(setup_client):
    client, alias, result = setup_client
    other = {**result, 'connection_id': 'bob-connection', 'token': 'bob-token'}
    assert client.store.save('bob', 'https://remote.example', other) == alias
    await client.remove_local('alice', alias)
    assert client.store.get('bob', alias)['token'] == 'bob-token'
    with pytest.raises(ClientError) as error:
        await client.remove_local('alice', alias)
    assert error.value.status == 404


async def test_old_response_cannot_be_cached_after_rejoining(setup_client):
    client, alias, result = setup_client
    old = client.store.get('alice', alias)
    await client.remove_local('alice', alias)
    client.store.save('alice', 'https://remote.example', {**result, 'connection_id': 'new-connection'})
    client.store.cache(old, {'id': 1, 'kind': 'message'})
    assert not client.store.messages('alice', alias)
    client.store.cache(client.store.get('alice', alias), {'id': 2, 'kind': 'message'})
    assert [event['id'] for event in client.store.messages('alice', alias)] == [2]


def test_local_removal_route_requires_auth_and_cannot_remove_another_user(setup_client):
    client, alias, _ = setup_client
    app = FastAPI()
    app.include_router(client_router(GroupFacade(client), internal_token='internal', verify_password=lambda *_: False))
    with TestClient(app) as api:
        assert api.delete('/groups/' + alias + '/local').status_code == 401
        assert api.delete('/groups/' + alias + '/local', headers={'Authorization': 'Bearer internal:bob'}).status_code == 404
        assert client.store.get('alice', alias)
        response = api.delete('/groups/' + alias + '/local', headers={'Authorization': 'Bearer internal:alice'})
        assert response.status_code == 200
        assert response.json()['local_only'] is True
        assert client.store.get('alice', alias) is None
    client.request.assert_not_called()
