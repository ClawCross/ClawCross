"""Network-facing relay protocol, with invitation-link joins and scoped duplex sessions.

A space's app runs on the group host. Its page requests stream to its local port;
app polls carry only agent output and membership changes.
"""
import asyncio
import base64
from collections import defaultdict, deque
import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from groups.space_proxy import LocalApps

from groups.relay_store import RelayError, RelayStore


class Join(BaseModel):
    invite: str = Field(min_length=20, max_length=100)
    node_id: str = Field(max_length=100)
    user_id: str = Field(max_length=100)
    display_name: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=6, max_length=128)


class Create(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    node_id: str = Field(max_length=100)
    user_id: str = Field(max_length=100)
    display_name: str = Field(max_length=160)
    kind: str = 'group'


class AgentJoin(BaseModel):
    agent_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=160)
    platform: str = Field('agent', max_length=50)
    convert_to_group: bool = False


class GuestJoin(BaseModel):
    invite: str = Field(min_length=20, max_length=100)
    name: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=6, max_length=128)


class Password(BaseModel):
    password: str = Field(min_length=6, max_length=128)


class GuestName(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class GuestPost(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    client_msg_id: str = Field('', max_length=160)
    mentions: list[str] = Field(default_factory=list, max_length=128)
    reply_to: int | None = Field(None, ge=1)


class Post(BaseModel):
    content: str = Field(max_length=65536)
    agent_id: str = Field('', max_length=100)
    mentions: list[str] = Field(default_factory=list, max_length=128)
    attachments: list[dict] = Field(default_factory=list, max_length=8)
    client_msg_id: str = Field('', max_length=160)
    expected_title: str | None = Field(None, max_length=160)
    reply_to: int | None = None


class AppEndpoint(BaseModel):
    port: int | None = Field(None, ge=1, le=65535)
    secret: str = Field('', max_length=128)


class AppPoll(AppEndpoint):
    cursor: int | None = None  # None starts from now
    name: str = Field('', max_length=80)


class AppAsk(BaseModel):
    agent: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=65536)


def local_control(request: Request, key: str) -> bool:
    return bool(request.client and request.client.host in {'127.0.0.1', '::1'} and
                hmac.compare_digest(request.headers.get('x-group-service-key', ''), key))


def relay_router(store: RelayStore, key: str) -> APIRouter:
    router = APIRouter(prefix='/relay')
    rate = defaultdict(deque)
    sockets = defaultdict(int)
    pending = 0
    apps = LocalApps(store)
    signing = (key or secrets.token_hex(32)).encode()

    def limited(identity, maximum):
        now = time.monotonic()
        queue = rate[identity]
        while queue and queue[0] < now - 60:
            queue.popleft()
        if len(queue) >= maximum:
            raise HTTPException(429, '请求过于频繁，请稍后重试')
        queue.append(now)
        if len(rate) > 4096:
            for name in list(rate):
                if not rate[name] or rate[name][-1] < now - 60:
                    rate.pop(name, None)
            while len(rate) > 4096:
                rate.pop(next(iter(rate)))

    def token(authorization):
        if not authorization or not authorization.startswith('Bearer '):
            raise HTTPException(401, '需要群连接凭证')
        return authorization[7:]

    async def invoke(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except RelayError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    def app_key(authorization):
        credential = token(authorization)
        if not credential.startswith('app_'):
            raise HTTPException(401, '需要小程序密钥')
        return credential

    def sign(value: str) -> str:
        return hmac.new(signing, value.encode(), hashlib.sha256).hexdigest()[:32]

    def session_of(gid: str, member: str) -> str:
        """A member's entrance to the space's site, valid for a day; it is the page's only credential."""
        payload = base64.urlsafe_b64encode(json.dumps([gid, member, int(time.time()) + 86400]).encode()).decode().rstrip('=')
        return payload + '.' + sign(payload)

    def viewer_of(session: str) -> tuple[str, str]:
        payload, _, mark = session.partition('.')
        if not payload or not hmac.compare_digest(mark, sign(payload)):
            raise HTTPException(404, '小程序入口无效')
        gid, member, expires = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        if expires < time.time():
            raise HTTPException(404, '小程序入口已过期，请重新打开空间')
        return gid, member

    @router.get('/health')
    async def health():
        return {'status': 'ok', 'protocol': 1}

    @router.get('/admin/groups')
    async def admin_groups(request: Request):
        if not local_control(request, key):
            raise HTTPException(403, '服务器管理只允许本机机器凭证')
        return {'groups': await invoke(store.admin_list)}

    @router.post('/admin/groups/{gid}/{action}')
    async def admin_manage(gid: str, action: str, body: dict, request: Request):
        if not local_control(request, key):
            raise HTTPException(403, '服务器管理只允许本机机器凭证')
        if action not in {'patch', 'remove_member', 'member_patch', 'primary', 'delete', 'disconnect_external', 'external_access'}:
            raise HTTPException(400, '不支持的服务器管理操作')
        return await invoke(store.manage, '', action, body, admin_group=gid)

    @router.post('/create')
    async def create(body: Create, request: Request):
        if not local_control(request, key):
            raise HTTPException(403, '只有服务器本机管理接口可以建群')
        return await invoke(store.create, **body.model_dump())

    @router.post('/join')
    async def join(body: Join, request: Request):
        limited('join:global', 120)
        limited('join:' + (request.client.host if request.client else '?'), 12)
        fields = body.model_dump()
        return await invoke(store.join, fields.pop('invite'), **fields, host_local=local_control(request, key))

    @router.post('/host-local')
    async def confirm_host_local(request: Request, authorization: str | None = Header(None)):
        if not local_control(request, key):
            raise HTTPException(403, '主机连接需要本机机器凭证')
        return await invoke(store.confirm_host_local, token(authorization))

    @router.post('/poll')
    async def poll(body: dict, authorization: str | None = Header(None)):
        """The WebSocket stream over plain HTTP, for members reached through a web front
        end: acknowledge ``cursor`` and receive the events after it."""
        credential = token(authorization)
        cursor = body.get('cursor', 0)
        if not isinstance(cursor, int) or cursor < 0:
            raise HTTPException(400, '无效的游标')
        await invoke(store.acknowledge, credential, cursor)
        return {'type': 'events', **await invoke(store.events, credential, cursor)}

    @router.get('/group')
    async def group(authorization: str | None = Header(None)):
        return await invoke(store.detail, token(authorization))

    @router.get('/messages')
    async def messages(after_id: int = 0, authorization: str | None = Header(None)):
        return {'messages': await invoke(store.messages, token(authorization), max(0, after_id))}

    @router.get('/search')
    @router.get('/guest/search')
    async def search(request: Request, query: str = '', before_id: int = 0, limit: int = 50, sender: str = Query('',max_length=160), from_ts: int = Query(0,ge=0,le=253402300799), to_ts: int = Query(0,ge=0,le=253402300799), authorization: str | None = Header(None)):
        credential = token(authorization)
        await invoke(store.detail, credential)
        from groups.relay_store import digest
        limited('search:' + digest(credential), 60)
        guest = request.url.path.endswith('/guest/search')
        if guest:
            await invoke(store.guest_identity, credential)
        result = await invoke(store.search_messages, credential, query, max(0,before_id), limit, sender=sender,from_ts=from_ts,to_ts=to_ts)
        if guest:
            result['messages'] = [{k:m.get(k) for k in ('id','sender','sender_name','content','created_at','reply_to','reply')}
                                  for m in result['messages']]
        return result

    @router.get('/messages/{message_id}/context')
    async def message_context(message_id: int, authorization: str | None = Header(None)):
        return await invoke(store.message_context, token(authorization), message_id)

    @router.post('/messages')
    async def post(body: Post, authorization: str | None = Header(None)):
        credential = token(authorization)
        await invoke(store.detail, credential)
        from groups.relay_store import digest
        limited('post:' + digest(credential), 120)
        return await invoke(store.post, credential, **body.model_dump())

    @router.post('/agents')
    async def add_agent(body: AgentJoin, authorization: str | None = Header(None)):
        return await invoke(store.add_agent, token(authorization), **body.model_dump())

    @router.post('/guest-invites')
    async def guest_invite(body: dict, authorization: str | None = Header(None)):
        return await invoke(store.guest_invite, token(authorization), disable=body.get('disable') is True)

    @router.post('/guest/info')
    async def guest_info(body: dict, request: Request):
        limited('guest-info:' + (request.client.host if request.client else '?'), 120)
        return await invoke(store.guest_info, str(body.get('invite', ''))[:100], host_local=local_control(request, key))

    @router.post('/guest/join')
    async def guest_join(body: GuestJoin, request: Request):
        limited('guest-join:global', 120)
        limited('guest-join:' + (request.client.host if request.client else '?'), 12)
        return await invoke(store.guest_join, body.invite, body.name, body.password)

    @router.get('/guest/state')
    async def guest_state(after_id: int = -1, authorization: str | None = Header(None)):
        credential = token(authorization)
        return await invoke(store.guest_state, credential, after_id)

    @router.post('/guest/messages')
    async def guest_post(body: GuestPost, authorization: str | None = Header(None)):
        credential = token(authorization)
        await invoke(store.guest_identity, credential)
        from groups.relay_store import digest
        limited('post:' + digest(credential), 30)
        if not body.content.strip():
            raise HTTPException(400, '消息不能为空')
        result = await invoke(store.post, credential, **body.model_dump())
        return {'id': result['message']['id']}

    @router.post('/guest/password')
    async def guest_password(body: Password, authorization: str | None = Header(None)):
        credential = token(authorization)
        from groups.relay_store import digest
        limited('guest-password:' + digest(credential), 12)
        return await invoke(store.set_password, credential, body.password)

    @router.post('/guest/rename')
    async def guest_rename(body: GuestName, authorization: str | None = Header(None)):
        return await invoke(store.guest_rename, token(authorization), body.name)

    @router.post('/manage/{action}')
    async def manage(action: str, body: dict, authorization: str | None = Header(None)):
        return await invoke(store.manage, token(authorization), action, body)

    @router.post('/app/key')
    async def make_app_key(authorization: str | None = Header(None)):
        return await invoke(store.app_key, token(authorization))

    @router.post('/app/session')
    @router.post('/guest/app_session')
    async def app_session(authorization: str | None = Header(None)):
        viewer = await invoke(store.app_viewer, token(authorization))
        return {'path': f"relay/app/site/{session_of(viewer['group_id'], viewer['member'])}/"}

    @router.api_route('/app/site/{session}', methods=['GET', 'HEAD'])
    async def app_site_root(session: str):
        return Response(status_code=307, headers={'Location': f'./{session}/'})

    @router.api_route('/app/site/{session}/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    async def app_site(session: str, path: str, request: Request):
        gid, principal = viewer_of(session)
        member = await invoke(store.app_member, gid, principal)
        return await apps.http(request, gid, member, path)

    @router.websocket('/app/site/{session}/{path:path}')
    async def app_socket(ws: WebSocket, session: str, path: str):
        try:
            gid, principal = viewer_of(session)
            member = await invoke(store.app_member, gid, principal)
            await apps.websocket(ws, gid, member, path)
        except HTTPException:
            await ws.close(1008)

    async def host_app(request, authorization, name=None):
        if not local_control(request, key):
            raise HTTPException(403, '小程序须在群聊主机运行')
        credential = app_key(authorization)
        return credential, await invoke(store.app_auth, credential, name)

    @router.post('/app/serve')
    async def app_serve(body: AppEndpoint, request: Request, authorization: str | None = Header(None)):
        credential, gid = await host_app(request, authorization)
        apps.register(gid, credential, body.port, body.secret)
        return {'ok': True}

    @router.post('/app/poll')
    async def app_poll(body: AppPoll, request: Request, authorization: str | None = Header(None)):
        credential, gid = await host_app(request, authorization, body.name or '小程序')
        if body.port:
            apps.register(gid, credential, body.port, body.secret)
        deadline = time.monotonic() + 15
        while True:
            packet = await invoke(store.app_events, gid, body.cursor)
            if packet['items'] or body.cursor is None or time.monotonic() >= deadline:
                return packet
            await asyncio.sleep(0.5)

    @router.get('/app/members')
    async def app_members(request: Request, authorization: str | None = Header(None)):
        _, gid = await host_app(request, authorization)
        return await invoke(store.app_members, gid)

    @router.post('/app/ask')
    async def app_ask(body: AppAsk, request: Request, authorization: str | None = Header(None)):
        _, gid = await host_app(request, authorization)
        limited('app-ask:' + gid, 120)
        return await invoke(store.app_ask, gid, body.agent, body.content)

    @router.websocket('/ws')
    async def stream(ws: WebSocket):
        nonlocal pending
        if pending + sum(sockets.values()) >= 256:
            await ws.close(1013)
            return
        pending += 1
        await ws.accept()
        identity = None
        admitted = False
        try:
            hello = await asyncio.wait_for(ws.receive_json(), 10)
            credential = str(hello.get('token', ''))
            from groups.relay_store import digest
            identity = digest(credential)
            if sum(sockets.values()) >= 256 or sockets.get(identity, 0) >= 2:
                await ws.close(1013)
                return
            await asyncio.to_thread(store.acknowledge, credential, max(0, int(hello.get('cursor', 0))))
            sockets[identity] += 1
            pending -= 1
            admitted = True
            cursor, seen = max(0, int(hello.get('cursor', 0))), None
            while True:
                packet = await asyncio.to_thread(store.events, credential, cursor)
                # The card is sent again when it changes, or when a device goes online or offline.
                card = (packet['group']['version'], [m['online'] for m in packet['group']['members']])
                if packet['events'] or card != seen:
                    await ws.send_json({'type': 'events', **packet})
                    if packet['events']:
                        cursor = packet['events'][-1]['id']
                    seen = card
                try:
                    reply = await asyncio.wait_for(ws.receive_json(), 1)
                    if reply.get('type') == 'ack':
                        ack = int(reply.get('cursor', 0))
                        if ack > cursor:
                            raise RelayError('确认了未发送的事件')
                        await asyncio.to_thread(store.acknowledge, credential, ack)
                except asyncio.TimeoutError:
                    pass
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        except RelayError as exc:
            await ws.close(1013 if exc.status == 503 else 1008,
                           reason='群主已暂停外部联网' if exc.status == 503 else '')
        except (ValueError, TypeError):
            await ws.close(1008)
        finally:
            if admitted:
                sockets[identity] -= 1
                if not sockets[identity]:
                    sockets.pop(identity, None)
            else:
                pending -= 1

    return router
