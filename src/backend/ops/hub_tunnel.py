"""Outbound Hub tunnel for the complete loopback frontend, including SSE and uploads."""
import asyncio
import base64
from contextlib import suppress
import json
import os
import signal
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect

from common.hub_access import CREDENTIALS, STATUS, read_json, write_json

CHUNK=64*1024
WINDOW=8
HOP={'connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade'}
UNTRUSTED={'host','forwarded','x-user-id','x-internal-token','x-real-ip','true-client-ip','cf-connecting-ip','cf-ray'}


def hub_url(value):
    parsed=urlsplit(value)
    if parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Invalid ClawCross Hub URL')
    if parsed.scheme!='https' and parsed.hostname not in {'127.0.0.1','localhost','::1'}:
        raise ValueError('ClawCross Hub requires HTTPS')
    if parsed.path.rstrip('/'):
        raise ValueError('ClawCross Hub URL must be an origin without a path')
    return value.rstrip('/')


def forwarded_headers(headers,host,remote):
    connection={token.strip().lower() for key,value in headers if key.lower()=='connection' for token in value.split(',')}
    clean=[(key,value) for key,value in headers if key.lower() not in HOP|UNTRUSTED|connection
           and not key.lower().startswith('x-forwarded-') and '\r' not in key+value and '\n' not in key+value]
    # A public request must never acquire localhost's authentication exemption.
    return clean+[('Host',host),('X-Forwarded-Host',host),('X-Forwarded-Proto','https'),('X-Forwarded-For',remote)]


def status(phase,domain='',error=''):
    write_json(STATUS,{'provider':'hub','pid':os.getpid(),'phase':phase,'public_domain':domain,'error':error})


async def check(origin):
    origin=hub_url(origin)
    async with httpx.AsyncClient(trust_env=False,timeout=8) as api:
        response=await api.get(origin+'/_tunnel/health')
        if response.status_code!=200:
            raise RuntimeError('Hub 公网服务尚未启用；请部署 Hub 设备隧道服务。')
        card=response.json()
        if card.get('protocol')!=1:raise RuntimeError('Hub tunnel protocol is not supported')
        if card.get('dns_ready') is False:
            raise RuntimeError('Hub 设备子域名 DNS 尚未就绪，请管理员配置 *.'+str(card.get('domain','clawcross.net'))+' 指向 Hub 服务器。')
    return card


async def register(origin):
    saved=read_json(CREDENTIALS)
    fields={key:saved[key] for key in ('device_id','token') if saved.get('hub_url')==origin and key in saved}
    async with httpx.AsyncClient(trust_env=False,timeout=10) as api:
        response=await api.post(origin+'/_tunnel/register',json=fields)
        if response.status_code==403:raise RuntimeError('Hub 设备凭证已撤销；请重新登记设备。')
        if response.status_code!=200:raise RuntimeError(f'Hub device registration failed ({response.status_code})')
        card=response.json()
    if not all(isinstance(card.get(key),str) and card[key] for key in ('device_id','token','public_domain')):
        raise RuntimeError('Hub returned an invalid device registration')
    # The endpoint comes from the configured trusted Hub, never from a message.
    card.update(hub_url=origin)
    write_json(CREDENTIALS,card)
    return card


async def serve_socket(ws,api,target,registration):
    lock=asyncio.Lock();requests={}
    async def send(packet):
        async with lock:await ws.send(json.dumps(packet))
    await send({'device_id':registration['device_id'],'token':registration['token']})
    ready=json.loads(await asyncio.wait_for(ws.recv(),10))
    if ready.get('type')!='ready' or ready.get('public_domain')!=registration['public_domain']:
        raise RuntimeError('Hub did not authenticate this device')
    status('connected',registration['public_domain'])
    print('ClawCross public URL: '+registration['public_domain'],flush=True)

    async def request_body(identifier,state):
        while True:
            part=await state['body'].get()
            if part is None:break
            yield part
            await send({'type':'request_credit','id':identifier})

    async def execute(packet,state):
        identifier=packet['id']
        try:
            path=packet.get('path','')
            if not path.startswith('/') or path.startswith('//') or any(c in path for c in ('\r','\n','\\')):
                raise ValueError('Invalid request path')
            host=urlsplit(registration['public_domain']).netloc
            headers=forwarded_headers(packet.get('headers',[]),host,packet.get('remote') or '198.51.100.1')
            body=request_body(identifier,state) if packet.get('has_body') else None
            async with api.stream(packet['method'],target+path,headers=headers,content=body) as response:
                blocked=HOP|{token.strip().lower() for key,value in response.headers.multi_items() if key.lower()=='connection' for token in value.split(',')}
                headers=[[k,v] for k,v in response.headers.multi_items() if k.lower() not in blocked]
                await send({'type':'response_start','id':identifier,'status':response.status_code,'headers':headers})
                async for part in response.aiter_raw(chunk_size=CHUNK if response.headers.get('content-type','').startswith(('image/','video/','audio/','application/octet-stream')) else None):
                    for at in range(0,len(part),CHUNK):
                        await state['credit'].acquire()
                        await send({'type':'response_chunk','id':identifier,'data':base64.b64encode(part[at:at+CHUNK]).decode()})
                await send({'type':'response_end','id':identifier})
        except asyncio.CancelledError:raise
        except Exception:
            # Do not send local paths, credentials or tracebacks to public callers.
            with suppress(Exception):
                await send({'type':'response_start','id':identifier,'status':502,'headers':[['content-type','text/plain']]})
                await send({'type':'response_chunk','id':identifier,'data':base64.b64encode(b'The local frontend did not respond.').decode()})
                await send({'type':'response_end','id':identifier})
        finally:requests.pop(identifier,None)
    try:
        async for raw in ws:
            packet=json.loads(raw);identifier=packet.get('id');kind=packet.get('type')
            if kind=='request':
                if len(requests)>=64:raise RuntimeError('Too many active requests')
                state={'body':asyncio.Queue(WINDOW+2),'credit':asyncio.Semaphore(WINDOW)}
                requests[identifier]=state
                state['task']=asyncio.create_task(execute(packet,state))
                continue
            state=requests.get(identifier)
            if state is None:continue
            if kind=='request_chunk':state['body'].put_nowait(base64.b64decode(packet['data'],validate=True))
            elif kind=='request_end':state['body'].put_nowait(None)
            elif kind=='response_credit':state['credit'].release()
            elif kind=='cancel':state['task'].cancel()
    finally:
        tasks=[state['task'] for state in list(requests.values())]
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)


async def run(origin,port):
    origin=hub_url(origin)
    target=f'http://127.0.0.1:{int(port)}'
    if not 1<=int(port)<=65535:raise ValueError('Invalid frontend port')
    stopping=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):
        try:loop.add_signal_handler(sig,stopping.set)
        except (NotImplementedError,RuntimeError):signal.signal(sig,lambda *_:loop.call_soon_threadsafe(stopping.set))
    websocket=('wss' if origin.startswith('https:') else 'ws')+origin[origin.index(':'):]+ '/_tunnel/connect'
    delay=1
    async with httpx.AsyncClient(trust_env=False,timeout=httpx.Timeout(None,connect=5),follow_redirects=False,
                                 limits=httpx.Limits(max_connections=64,max_keepalive_connections=16)) as api:
        try:
            while not stopping.is_set():
                status('connecting')
                connection=None
                try:
                    registration=await register(origin)
                    async def connected():
                        async with connect(websocket,max_size=256*1024,ping_interval=20,ping_timeout=20,close_timeout=3) as ws:
                            await serve_socket(ws,api,target,registration)
                    connection=asyncio.create_task(connected());stop=asyncio.create_task(stopping.wait())
                    done,_=await asyncio.wait((connection,stop),return_when=asyncio.FIRST_COMPLETED)
                    if stop in done:
                        connection.cancel();await asyncio.gather(connection,return_exceptions=True);break
                    stop.cancel();await asyncio.gather(stop,return_exceptions=True)
                    await connection
                    delay=1
                except asyncio.CancelledError:raise
                except Exception as exc:
                    if connection and not connection.done():connection.cancel();await asyncio.gather(connection,return_exceptions=True)
                    status('reconnecting',error='Hub 连接中断，正在重连。')
                    print('Hub tunnel disconnected: '+type(exc).__name__,flush=True)
                try:await asyncio.wait_for(stopping.wait(),delay)
                except asyncio.TimeoutError:pass
                delay=min(15,delay*2)
        finally:status('stopped')
