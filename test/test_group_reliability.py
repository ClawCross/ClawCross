"""Regression coverage for overlapping sends, durable retries and receipt isolation."""
import asyncio
import json
from unittest.mock import Mock, patch

import httpx
import pytest

from groups.client import ClientError, ClientStore, GroupClient
from groups.relay_store import RelayError, RelayStore
from groups.server import create_app


def seed(tmp_path):
    store = RelayStore(tmp_path / 'group-relay.db')
    host = store.create(title='Friends', node_id='host', user_id='alice', display_name='Alice')
    other = store.join(store.guest_invite(host['token'])['invite'], node_id='remote', user_id='bob', display_name='Bob', password='password123')
    return store, host, other


def test_send_confirmation_never_returns_a_later_cached_message(tmp_path):
    server, host, other = seed(tmp_path)
    cache = ClientStore(tmp_path / 'client.db')
    alias = cache.save('alice', 'https://group.test', host)
    client = GroupClient(cache, Mock(), Mock())
    client.ensure_outbox = Mock()

    async def transport(request):
        result = server.post(host['token'], **json.loads(request.content))
        later = server.post(other['token'], content='Another person spoke while POST was in flight')
        cache.cache(cache.get('alice', alias), {'kind': 'message', 'id': later['message']['id'], 'message': later['message']})
        return httpx.Response(200, json=result)

    async def run():
        real = httpx.AsyncClient
        with patch('groups.client.httpx.AsyncClient', side_effect=lambda **kw: real(transport=httpx.MockTransport(transport), **kw)):
            sent = await client.post('alice', alias, 'u:alice', 'My message', client_msg_id='fixed-id')
            assert sent['message']['content'] == 'My message'
            assert sent['message']['sender'] == 'u:alice'
            assert sent['message']['id'] < cache.messages('alice', alias)[-1]['id']
            assert client.messages('alice', alias) == []  # POST cannot skip the unsynchronized prefix.
            await client.consume(cache.get('alice', alias), server.events(host['token'], 0))
            assert [row['content'] for row in client.messages('alice', alias)] == ['My message', 'Another person spoke while POST was in flight']
            assert not cache.pending_sends()
        await client.close()
    asyncio.run(run())


def test_ambiguous_timeout_retries_after_restart_without_duplicate(tmp_path):
    server, host, _ = seed(tmp_path)
    cache = ClientStore(tmp_path / 'client.db')
    alias = cache.save('alice', 'https://group.test', host)
    client = GroupClient(cache, Mock(), Mock())
    client.ensure_outbox = Mock()
    ids = []

    async def transport(request):
        body = json.loads(request.content)
        ids.append(body['client_msg_id'])
        result = server.post(host['token'], **body)
        if len(ids) == 1:
            raise httpx.ReadTimeout('response lost after commit', request=request)
        return httpx.Response(200, json=result)

    async def run():
        real = httpx.AsyncClient
        with patch('groups.client.httpx.AsyncClient', side_effect=lambda **kw: real(transport=httpx.MockTransport(transport), **kw)):
            with pytest.raises(ClientError, match='消息已保存待重试'):
                await client.post('alice', alias, 'u:alice', 'Exactly one stored message', client_msg_id='retry-id')
            await client.close()
            restored = GroupClient(ClientStore(cache.path), Mock(), Mock())
            result = await restored.send_pending(('alice', alias, '', 'retry-id'))
            assert result['created'] is False
            assert ids == ['retry-id', 'retry-id']
            assert len(server.messages(host['token'])) == 1
            assert restored.store.outbox(('alice', alias, '', 'retry-id')) is None
            await restored.close()
    asyncio.run(run())


def test_pending_send_cannot_change_identity_or_survive_local_removal(tmp_path):
    _, host, _ = seed(tmp_path)
    cache = ClientStore(tmp_path / 'client.db')
    alias = cache.save('alice', 'https://group.test', host)
    row = cache.get('alice', alias)
    body = {'agent_id': '', 'client_msg_id': 'one', 'content': 'first'}
    key = cache.enqueue(row, body)
    with pytest.raises(ValueError):
        cache.enqueue(row, {**body, 'content': 'different'})
    with cache.db() as db:
        db.execute("UPDATE group_client_connections SET connection_id='changed'")
    client = GroupClient(cache, Mock(), Mock())
    with pytest.raises(ClientError, match='身份已失效'):
        asyncio.run(client.send_pending(key))
    assert cache.outbox(key)['state'] == 'failed'
    cache.remove('alice', alias)
    assert cache.outbox(key) is None


def test_receipts_merge_devices_without_reading_or_waking_agents(tmp_path):
    store, host, other = seed(tmp_path)
    token = other['token']
    first = store.post(host['token'], content='first')['message']['id']
    last = store.post(host['token'], content='last')['message']['id']
    before = store.events(token, 0)
    store.receipt(token, 'phone', last)
    snapshot = store.events(host['token'], 0)
    bob = next(m['principal'] for m in other['group']['members'] if m['connection_id'] == other['connection_id'])
    receipt = next(r for r in snapshot['receipts'] if r['principal'] == bob)
    assert receipt == {'principal': bob, 'received_cursor': last, 'read_cursor': 0}
    store.receipt(token, 'laptop', first, first)
    store.receipt(token, 'phone', 0)
    reopened = RelayStore(store.path)
    merged = next(r for r in reopened.events(host['token'], 0)['receipts'] if r['principal'] == bob)
    assert merged == {'principal': bob, 'received_cursor': last, 'read_cursor': first}
    assert reopened.events(token, 0)['events'] == before['events']
    cache = ClientStore(tmp_path / 'client.db')
    alias = cache.save('alice', 'https://group.test', host)
    local = GroupClient(cache, Mock(), Mock())
    asyncio.run(local.consume(cache.get('alice', alias), reopened.events(host['token'], 0)))
    assert 'u:alice' in {r['principal'] for r in local.card(cache.get('alice', alias))['receipts']}
    foreign = store.create(title='Other', node_id='host', user_id='alice', display_name='Alice')
    foreign_id = store.post(foreign['token'], content='private')['message']['id']
    for device, received, read in [('phone', foreign_id, None), ('new', 0, last), ('phone', True, None)]:
        with pytest.raises(RelayError):
            store.receipt(token, device, received, read)


def test_poll_and_receipt_api_use_origin_capabilities_and_validate_identity(tmp_path):
    store, host, other = seed(tmp_path)
    message = store.post(host['token'], content='hello')['message']['id']
    async def run():
      async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(data_dir=tmp_path, control_key='test', legacy=False)), base_url='http://test') as api:
        headers = {'Authorization': 'Bearer ' + other['token']}
        assert (await api.get('/relay/capabilities')).json()['capabilities']['receipts'] == 'device-v1'
        packet = (await api.post('/relay/poll', headers=headers, json={'cursor': 0, 'device_id': 'phone'})).json()
        assert packet['capabilities']['message_idempotency'] is True
        assert (await api.post('/relay/receipts', headers=headers, json={'device_id': 'phone', 'received_cursor': message, 'read_cursor': message})).status_code == 200
        assert (await api.post('/relay/receipts', json={'device_id': 'phone'})).status_code == 401
        assert (await api.post('/relay/poll', headers=headers, json={'cursor': True})).status_code == 400
        store.manage(host['token'], 'remove_member', {'principal': next(m['principal'] for m in packet['group']['members'] if m['connection_id'] == other['connection_id'])})
        assert (await api.post('/relay/receipts', headers=headers, json={'device_id': 'phone'})).status_code == 401
    asyncio.run(run())
