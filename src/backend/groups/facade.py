"""Authenticated local API: legacy groups over RPC, new groups through the relay client."""
import asyncio
import json
import os
from urllib.parse import quote

import httpx

from groups.client import ClientError, GroupClient
from groups.config import service_key, service_url


class GroupFacade:
    supports_remote = True

    def __init__(self, client: GroupClient, names=None):
        self.client, self.names = client, names
        self.agents = client.agents

    @staticmethod
    def rpc_headers(user):
        return {'X-Group-Service-Key': service_key(), 'Authorization': f"Bearer {os.getenv('INTERNAL_TOKEN', '')}:{user}"}

    def rpc(self, user, method, path='', body=None, params=None):
        try:
            with httpx.Client(timeout=10, trust_env=False) as client:
                response = client.request(method, service_url() + '/local/groups' + path,
                                          headers=self.rpc_headers(user), json=body, params=params)
            if response.status_code >= 400:
                raise ClientError(str(response.json().get('detail', '群服务请求失败')), response.status_code)
            return response.json()
        except httpx.HTTPError as exc:
            raise ClientError('本地群服务不可用', 503) from exc

    def agent_id(self, user, ref):
        agent = self.agents.get(user, ref) or (self.names(user, ref) if self.names else None)
        if not agent:
            raise ClientError('此 agent 不属于当前用户或不存在', 404)
        return agent.agent_id

    def create(self, user, *, title, kind='group', agents=(), password='', local_join=True):
        ids = [self.agent_id(user, a) for a in agents]
        card = self.client.create(user, title=title or '私聊', kind=kind, agents=ids, password=password, local_join=local_join)
        self.client.ensure(user, card['group_id'])
        return card

    def list(self, user):
        legacy = self.rpc(user, 'GET').get('groups', [])
        return legacy + [self.client.card(r) for r in self.client.store.rows(user) if r['active']]

    def memberships(self, user, aid):
        try:
            legacy = self.rpc(user, 'GET', params={'agent_id': aid}).get('groups', []) if self.agents.get(user, aid) else []
        except ClientError as exc:
            if exc.status != 503:
                raise
            # Group service downtime must not prevent independent agent calls.
            legacy = []
        return legacy + self.client.memberships(user, aid)

    def detail(self, user, gid):
        if gid.startswith('rg_'):
            return self.client.card(self.client.require(user, gid))
        return self.rpc(user, 'GET', '/' + quote(gid, safe=''))

    def messages(self, user, gid, after_id=0):
        if gid.startswith('rg_'):
            return self.client.messages(user, gid, after_id)
        return self.rpc(user, 'GET', '/' + quote(gid, safe='') + '/messages', params={'after_id': after_id})['messages']

    def update(self, user, gid, *, title=None, dnd=None):
        fields = {k: v for k, v in {'title': title, 'dnd': dnd}.items() if v is not None}
        if gid.startswith('rg_'):
            return self.client.manage(user, gid, 'patch', fields)
        return self.rpc(user, 'PATCH', '/' + quote(gid, safe=''), fields)

    def delete(self, user, gid):
        if gid.startswith('rg_'):
            return self.client.manage(user, gid, 'delete', {})
        return self.rpc(user, 'DELETE', '/' + quote(gid, safe=''))

    def add_member(self, user, gid, ref):
        if gid.startswith('rg_'):
            return self.client.add_agent(user, gid, self.agent_id(user, ref))
        return self.rpc(user, 'POST', '/' + quote(gid, safe='') + '/members', {'agent': ref})

    def remove_member(self, user, gid, principal):
        if gid.startswith('rg_'):
            return self.client.manage(user, gid, 'remove_member', {'principal': principal})
        return self.rpc(user, 'DELETE', '/' + quote(gid, safe='') + '/members/' + quote(principal, safe=''))

    def update_member(self, user, gid, principal, *, muted=None, nickname=None):
        if gid.startswith('rg_'):
            return self.client.manage(user, gid, 'member_patch', {'principal': principal, 'muted': muted, 'name': nickname})
        return self.rpc(user, 'PATCH', '/' + quote(gid, safe='') + '/members/' + quote(principal, safe=''), {'muted': muted, 'nickname': nickname})

    def mute_agents(self, user, gid, muted):
        if gid.startswith('rg_'):
            for m in self.detail(user, gid)['members']:
                if m['is_agent']:
                    self.update_member(user, gid, m['principal'], muted=muted)
            return self.detail(user, gid)
        return self.rpc(user, 'POST', '/' + quote(gid, safe='') + '/mute_agents', {'muted': muted})

    def set_primary(self, user, gid, agent_ref):
        if gid.startswith('rg_'):
            return self.client.manage(user, gid, 'primary', {'principal': agent_ref})
        return self.rpc(user, 'PUT', '/' + quote(gid, safe='') + '/primary', {'agent': agent_ref})

    def typing(self, user, gid):
        if gid.startswith('rg_'):
            return {'typing': [], 'names': []}
        return self.rpc(user, 'GET', '/' + quote(gid, safe='') + '/typing')

    def agents_available(self, user, gid):
        if gid.startswith('rg_'):
            from agents.routes import agent_card
            inside = {m['principal'] for m in self.detail(user, gid)['members'] if not m['remote']}
            return [agent_card(a) for a in self.agents.list(user) if a.agent_id not in inside]
        return self.rpc(user, 'GET', '/' + quote(gid, safe='') + '/available_agents')['agents']

    async def post(self, user, gid, sender, content, **fields):
        if gid.startswith('rg_'):
            return await self.client.post(user, gid, sender, content, **fields)
        body = {'content': content, **fields}
        body['run_mode'] = body.pop('mode', None)
        if sender != 'u:' + user:
            body['agent'] = sender
        headers = {**self.rpc_headers(user), 'X-Internal-Token': os.getenv('INTERNAL_TOKEN', '')}
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.post(service_url() + '/local/groups/' + quote(gid, safe='') + '/messages', headers=headers, json=body)
        if response.status_code >= 400:
            raise ClientError(str(response.json().get('detail', '群服务请求失败')), response.status_code)
        return response.json()

    def forget(self, owner, aid):
        self.rpc(owner, 'POST', '/_forget', {'agent_id': aid})
        for row in self.client.store.rows(owner):
            if not row['active'] or aid not in json.loads(row['allowed_agents']):
                continue
            allowed = set(json.loads(row['allowed_agents']))
            allowed.discard(aid)
            self.client.store.update(owner, row['alias'], allowed_agents=json.dumps(sorted(allowed)))
            try:
                self.client.manage(owner, row['alias'], 'remove_member', {'principal': aid})
            except ClientError:
                # Locally revoke immediately; remote removal is retried by reconciliation.
                pass


def client_router(facade, *, internal_token, verify_password):
    from fastapi import APIRouter, Header, HTTPException
    from pydantic import BaseModel, Field
    from agents.routes import authenticate

    class JoinBody(BaseModel):
        server_url: str = Field('', max_length=512)
        group_id: str = Field(min_length=1, max_length=100)
        password: str = Field('', max_length=256)
        agents: list[str] = Field(default_factory=list, max_length=32)

    class ShareBody(BaseModel):
        password: str = Field('', max_length=256)
        local_join: bool = True
        revoke_connections: bool = False

    router = APIRouter()

    def user_of(auth):
        return authenticate(auth, internal_token=internal_token, verify_password=verify_password)

    @router.post('/groups/join')
    async def join(body: JoinBody, authorization: str | None = Header(None)):
        user = user_of(authorization)
        try:
            fields = body.model_dump()
            fields['agents'] = [facade.agent_id(user, a) for a in fields['agents']]
            card = await asyncio.to_thread(facade.client.join, user, **fields)
            facade.client.ensure(user, card['group_id'])
            return card
        except ClientError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(503, '无法连接群服务器') from exc

    @router.post('/groups/{gid}/leave')
    async def leave(gid: str, authorization: str | None = Header(None)):
        try:
            return await asyncio.to_thread(facade.client.manage, user_of(authorization), gid, 'leave', {})
        except ClientError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.post('/groups/{gid}/sharing')
    async def sharing(gid: str, body: ShareBody, authorization: str | None = Header(None)):
        try:
            return await asyncio.to_thread(facade.client.manage, user_of(authorization), gid, 'patch', body.model_dump())
        except ClientError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.get('/groups/{gid}/invite')
    async def invite(gid: str, authorization: str | None = Header(None)):
        user = user_of(authorization)
        try:
            row = facade.client.require(user, gid)
            advertised = os.getenv('GROUP_PUBLIC_URL') if row['url'] == service_url() else None
            return {'server_url': advertised or row['url'], 'group_id': row['remote_id'],
                    'password_enabled': json.loads(row['metadata']).get('password_enabled', False)}
        except ClientError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    return router
