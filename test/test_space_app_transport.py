"""Real sockets: local port -> group membership proxy -> frontend, including SSE and WebSockets."""
import asyncio
from contextlib import suppress
import socket
import threading

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import Response, StreamingResponse
from flask import Flask
import uvicorn
from websockets.asyncio.client import connect
from werkzeug.serving import make_server

from frontend.proxies.group_guests import register_guest_routes
from groups.server import create_app


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_streams_and_sockets_keep_membership_without_polling_page_bodies(tmp_path, monkeypatch):
    async def scenario():
        group = create_app(data_dir=tmp_path, control_key='host-proof', legacy=False)
        store = group.state.relay_store
        host = store.create(title='Transport', node_id='host', user_id='alice', display_name='Alice')
        invite = store.guest_invite(host['token'])['invite']
        guest = store.guest_join(invite, 'Bob', 'bob-password')
        appkey = store.app_key(host['token'])['key']
        upstream = FastAPI()
        release = asyncio.Event()

        @upstream.get('/events')
        async def events(request: Request):
            assert request.headers['x-space-secret'] == 's' * 32
            assert request.headers['x-space-member-name'] == 'Bob'
            assert 'cookie' not in request.headers and 'authorization' not in request.headers

            async def chunks():
                yield b'data: first\n\n'
                await release.wait()
                yield b'data: last\n\n'
            return StreamingResponse(chunks(), media_type='text/event-stream', headers={'Set-Cookie':'forbidden=1'})

        @upstream.get('/large')
        async def large():
            return Response(b'a' * (2*1024*1024+17), media_type='application/octet-stream')

        @upstream.websocket('/socket')
        async def echo(ws: WebSocket):
            assert ws.headers['x-space-member-name'] == 'Bob'
            assert ws.headers['x-space-secret'] == 's'*32
            await ws.accept(subprotocol='chat')
            try:
                while True:
                    item = await ws.receive()
                    if item['type'] == 'websocket.disconnect':
                        return
                    if item.get('bytes') is not None:
                        await ws.send_bytes(item['bytes'])
                    else:
                        await ws.send_text(item['text'])
            except RuntimeError:
                pass

        ports = [free_port(), free_port()]
        jobs, servers = [], []
        for application, port in zip((group, upstream), ports):
            server = uvicorn.Server(uvicorn.Config(application, host='127.0.0.1', port=port,
                log_level='error', timeout_graceful_shutdown=1))
            servers.append(server)
            jobs.append(asyncio.create_task(server.serve()))
        front = Flask('app-transport')
        front.secret_key = 'fixture'
        monkeypatch.setattr('groups.config.service_url', lambda: f'http://127.0.0.1:{ports[0]}')
        register_guest_routes(front, port_agent=1, internal_token='fixture')
        flask = make_server('127.0.0.1', 0, front, threaded=True)
        thread = threading.Thread(target=flask.serve_forever, daemon=True)
        thread.start()
        try:
            while not all(s.started for s in servers):
                await asyncio.sleep(.01)
            async with httpx.AsyncClient(trust_env=False, timeout=5) as api:
                base = f'http://127.0.0.1:{ports[0]}'
                headers = {'Authorization':'Bearer '+appkey, 'X-Group-Service-Key':'host-proof'}
                fields = {'port':ports[1], 'secret':'s'*32}
                assert (await api.post(base+'/relay/app/serve', headers={
                    'Authorization':'Bearer '+appkey}, json=fields)).status_code == 403
                assert (await api.post(base+'/relay/app/serve', headers=headers, json=fields)).status_code == 200
                path = (await api.post(base+'/relay/app/session', headers={
                    'Authorization':'Bearer '+guest['token']})).json()['path']
                url = f'http://127.0.0.1:{flask.server_port}/'+path
                async with api.stream('GET', url+'events', headers={'Cookie':'session=private'}) as response:
                    assert response.status_code == 200
                    assert 'set-cookie' not in response.headers
                    assert 'sandbox' in response.headers['content-security-policy']
                    lines = response.aiter_lines()
                    # The last chunk cannot be produced until the first actually reaches this client.
                    assert await asyncio.wait_for(anext(lines), 3) == 'data: first'
                    release.set()
                    assert 'data: last' in [line async for line in lines]
                assert len((await api.get(url+'large')).content) == 2*1024*1024+17
                async with connect(url.replace('http:', 'ws:')+'socket', subprotocols=['chat'], proxy=None) as ws:
                    assert ws.subprotocol == 'chat'
                    for data in ('你好', b'\x00\xffbinary'):
                        await ws.send(data)
                        assert await asyncio.wait_for(ws.recv(), 3) == data
                store.manage(host['token'], 'remove_member', {'principal':store.guest_identity(guest['token'])['principal']})
                assert (await api.get(url+'events')).status_code == 403
        finally:
            release.set()
            await asyncio.to_thread(flask.shutdown)
            flask.server_close()
            thread.join(timeout=2)
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*jobs)
    asyncio.run(scenario())
