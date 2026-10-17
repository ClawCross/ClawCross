"""Authenticated, host-local mini app endpoints. HTTP bodies are never queued in app polls."""
import asyncio
from contextlib import suppress
import time
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from groups.relay_store import RelayError

REQUEST_HEADERS = {'accept', 'accept-language', 'content-type', 'if-none-match', 'if-modified-since', 'range', 'user-agent'}
REPLY_HEADERS = {'content-type', 'content-encoding', 'cache-control', 'etag', 'last-modified', 'location',
                 'content-disposition', 'content-range', 'accept-ranges'}
PAGE_HEADERS = {'Content-Security-Policy': 'sandbox allow-scripts allow-forms allow-popups allow-modals allow-downloads',
                'Access-Control-Allow-Origin': '*', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                'X-Accel-Buffering': 'no'}


class LocalApps:
    def __init__(self, store):
        self.store, self.endpoints = store, {}

    def register(self, gid, key, port, secret):
        if not port or not 1 <= port <= 65535 or len(secret) < 32:
            raise HTTPException(400, '需要本机监听端口和随机访问密钥')
        self.endpoints[gid] = {'port': port, 'secret': secret, 'key': key, 'seen': time.monotonic()}

    async def endpoint(self, gid):
        entry = self.endpoints.get(gid)
        if not entry or entry['seen'] < time.monotonic() - 60:
            raise HTTPException(503, '小程序暂时不在线，请稍后再打开')
        try:
            # Rotating/removing the app key also revokes the registered port.
            if await asyncio.to_thread(self.store.app_auth, entry['key']) != gid:
                raise HTTPException(503)
        except RelayError as exc:
            raise HTTPException(503, '小程序已断开') from exc
        return entry

    @staticmethod
    def headers(entry, member):
        return {'x-space-secret': entry['secret'], 'x-space-member': member['id'],
                'x-space-member-name': quote(member['name'], safe='')}

    async def http(self, request, gid, member, path):
        if request.method == 'OPTIONS':
            return Response(status_code=204, headers={**PAGE_HEADERS, 'Access-Control-Allow-Methods': '*',
                'Access-Control-Allow-Headers': '*', 'Access-Control-Max-Age': '600'})
        entry = await self.endpoint(gid)
        headers = {k: v for k, v in request.headers.items() if k.lower() in REQUEST_HEADERS}
        headers.update(self.headers(entry, member))
        client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(None, connect=5))
        url = f"http://127.0.0.1:{entry['port']}/" + quote(path, safe='/@:+,~!$&\'()*;=-._')
        if request.url.query:
            url += '?' + request.url.query
        try:
            # Membership cookies and caller-supplied identity headers never reach the app.
            upstream = await client.send(client.build_request(request.method, url, headers=headers,
                content=request.stream()), stream=True)
        except (httpx.HTTPError, OSError):
            await client.aclose()
            return Response('小程序没有响应', status_code=502, headers=PAGE_HEADERS)

        async def body():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()
        return StreamingResponse(body(), status_code=upstream.status_code, headers={
            **{k: v for k, v in upstream.headers.items() if k.lower() in REPLY_HEADERS}, **PAGE_HEADERS})

    async def websocket(self, ws, gid, member, path):
        entry = await self.endpoint(gid)
        await bridge_websocket(ws, entry, member, path)


async def bridge_websocket(ws, entry, member, path):
    """Same membership proof as HTTP, with native text/binary frames and subprotocols."""
    url = f"ws://127.0.0.1:{entry['port']}/" + quote(path, safe='/@:+,~!$&\'()*;=-._')
    if ws.scope.get('query_string'):
        url += '?' + ws.scope['query_string'].decode('ascii')
    try:
        async with connect(url, additional_headers=LocalApps.headers(entry, member),
                           subprotocols=ws.scope.get('subprotocols') or None, max_size=1024*1024,
                           proxy=None, open_timeout=5) as upstream:
            await ws.accept(subprotocol=upstream.subprotocol)

            async def upload():
                while True:
                    message = await ws.receive()
                    if message['type'] == 'websocket.disconnect':
                        await upstream.close(message.get('code', 1000))
                        return
                    await upstream.send(message.get('bytes') if message.get('bytes') is not None else message['text'])

            async def download():
                async for data in upstream:
                    await (ws.send_bytes(data) if isinstance(data, bytes) else ws.send_text(data))
                with suppress(RuntimeError):
                    await ws.close(upstream.close_code or 1000, upstream.close_reason or '')

            tasks = [asyncio.create_task(upload()), asyncio.create_task(download())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                with suppress(RuntimeError):
                    await ws.close(upstream.close_code if upstream.close_code in {1000, 1001, 1008, 1009, 1011} else 1000)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except (ConnectionClosed, InvalidHandshake, OSError, TimeoutError):
        with suppress(RuntimeError):
            await ws.close(1011)
