"""Network-facing relay protocol, with invitation-link joins and scoped duplex sessions."""
import asyncio
from collections import defaultdict, deque
import hmac
import time

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from groups.relay_store import RelayError, RelayStore


class Join(BaseModel):
    invite: str = Field(min_length=20, max_length=100)
    node_id: str = Field(max_length=100)
    user_id: str = Field(max_length=100)
    display_name: str = Field(max_length=160)


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


class GuestJoin(BaseModel):
    password: str = Field(min_length=6, max_length=128)
    invite: str = Field(min_length=20, max_length=100)
    name: str = Field(min_length=1, max_length=40)


class GuestPassword(BaseModel):
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


def local_control(request: Request, key: str) -> bool:
    return bool(request.client and request.client.host in {'127.0.0.1', '::1'} and
                hmac.compare_digest(request.headers.get('x-group-service-key', ''), key))


def relay_router(store: RelayStore, key: str) -> APIRouter:
    router = APIRouter(prefix='/relay')
    rate = defaultdict(deque)
    sockets = defaultdict(int)
    pending = 0

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
        if action not in {'patch', 'remove_member', 'member_patch', 'primary', 'delete'}:
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
        return await invoke(store.join, fields.pop('invite'), **fields)

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
    async def search(request: Request, query: str, before_id: int = 0, limit: int = 50, authorization: str | None = Header(None)):
        credential = token(authorization)
        await invoke(store.detail, credential)
        from groups.relay_store import digest
        limited('search:' + digest(credential), 60)
        guest = request.url.path.endswith('/guest/search')
        if guest:
            await invoke(store.guest_identity, credential)
        result = await invoke(store.search_messages, credential, query, max(0,before_id), limit)
        if guest:
            result['messages'] = [{k:m.get(k) for k in ('id','sender','sender_name','content','created_at','reply_to','reply')}
                                  for m in result['messages']]
        return result

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
        return await invoke(store.guest_info, str(body.get('invite', ''))[:100])

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
    async def guest_password(body: GuestPassword, authorization: str | None = Header(None)):
        credential = token(authorization)
        from groups.relay_store import digest
        limited('guest-password:' + digest(credential), 12)
        return await invoke(store.guest_set_password, credential, body.password)

    @router.post('/guest/rename')
    async def guest_rename(body: GuestName, authorization: str | None = Header(None)):
        return await invoke(store.guest_rename, token(authorization), body.name)

    @router.post('/manage/{action}')
    async def manage(action: str, body: dict, authorization: str | None = Header(None)):
        return await invoke(store.manage, token(authorization), action, body)

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
            cursor, version = max(0, int(hello.get('cursor', 0))), -1
            while True:
                packet = await asyncio.to_thread(store.events, credential, cursor)
                if packet['events'] or packet['group']['version'] != version:
                    await ws.send_json({'type': 'events', **packet})
                    if packet['events']:
                        cursor = packet['events'][-1]['id']
                    version = packet['group']['version']
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
        except (RelayError, ValueError, TypeError):
            await ws.close(1008)
        finally:
            if admitted:
                sockets[identity] -= 1
                if not sockets[identity]:
                    sockets.pop(identity, None)
            else:
                pending -= 1

    return router
