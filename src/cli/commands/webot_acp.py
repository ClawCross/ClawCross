"""ACP over stdio, backed by normal owner-scoped WeBot Agents and chat APIs.

No separate model client, hidden session store, tool executor or public listener.
The ACP client cannot grant workspaces/MCP tools or change permission modes.
"""
import asyncio
import json
import sys
from urllib.parse import quote

import httpx

from src.cli.commands.join import _configuration

MAX_LINE = 2 * 1024 * 1024


class RpcError(RuntimeError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class WeBotACP:
    def __init__(self, client, *, owner, agent_id=None, name='我的伙伴', template='group', emit=None):
        self.client, self.owner, self.bound = client, owner, agent_id
        self.name, self.template = name, template
        self.sessions, self.turns = set(), {}
        self.initialized = False
        self.emit = emit or self.stdout

    @staticmethod
    def stdout(value):
        sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(',', ':'))+'\n')
        sys.stdout.flush()

    async def request(self, method, path, body=None):
        response = await self.client.request(method, path, json=body)
        if response.status_code >= 400:
            # Never forward arbitrary server exception text containing paths/keys.
            raise RpcError(-32000, f'ClawCross request failed ({response.status_code})')
        return response.json()

    async def agent(self, aid):
        if not isinstance(aid, str) or not aid or (self.bound and aid != self.bound):
            raise RpcError(-32602, 'Session is not available to this ACP connection')
        card = await self.request('GET', '/v1/agents/'+quote(aid, safe=''))
        if card.get('platform') != 'webot':
            raise RpcError(-32602, 'The ACP server accepts WeBot Agents only')
        if card.get('archived') or card.get('settings', {}).get('archived'):
            raise RpcError(-32602, 'The Agent is archived')
        return card

    async def dispatch(self, method, params):
        if method == 'initialize':
            if params.get('protocolVersion') != 1:
                raise RpcError(-32602, 'ACP protocolVersion 1 is required')
            self.initialized = True
            return {'protocolVersion':1, 'agentInfo':{'name':'clawcross-webot','title':'ClawCross WeBot','version':'1.0.0'},
                    'agentCapabilities':{'loadSession':True,'sessionCapabilities':{'list':{}},
                                         'promptCapabilities':{'image':False,'audio':False,'embeddedContext':False}},
                    'authMethods':[]}
        if not self.initialized:
            raise RpcError(-32000, 'Initialize first')
        if method == 'session/new':
            if params.get('mcpServers'):
                raise RpcError(-32602, 'Configure tools in ClawCross; client MCP servers are not accepted')
            if self.bound:
                card = await self.agent(self.bound)
            else:
                card = await self.request('POST', '/v1/agents', {
                    'platform':'webot','name':self.name,'creation_template':self.template})
            aid = card['agent_id']; self.sessions.add(aid)
            return {'sessionId':aid}
        if method == 'session/list':
            cards = await self.request('GET', '/v1/agents')
            rows = cards.get('data', [])
            return {'sessions':[{'sessionId':row['agent_id'],'cwd':'',
                                 'title':row.get('name') or self.name}
                                for row in rows if row.get('platform') == 'webot' and
                                (not self.bound or row['agent_id'] == self.bound)]}
        aid = params.get('sessionId')
        if not isinstance(aid, str) or not aid:
            raise RpcError(-32602, 'A sessionId is required')
        if method in {'session/load','session/resume'}:
            if params.get('mcpServers'):
                raise RpcError(-32602, 'Configure tools in ClawCross; client MCP servers are not accepted')
            await self.agent(aid); self.sessions.add(aid)
            history = await self.request('GET', '/v1/agents/'+quote(aid, safe='')+'/history?limit=1000')
            for message in history.get('messages', []):
                role, content = message.get('role'), message.get('content')
                if role not in {'user','assistant'} or not isinstance(content, str) or not content:
                    continue
                self.emit({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':aid,
                    'update':{'sessionUpdate':'user_message_chunk' if role == 'user' else 'agent_message_chunk',
                              'content':{'type':'text','text':content}}}})
            return {}
        if aid not in self.sessions:
            raise RpcError(-32602, 'Load or create the session first')
        if method == 'session/cancel':
            await self.cancel(aid)
            return None
        if method == 'session/prompt':
            blocks = params.get('prompt')
            if not isinstance(blocks, list) or not blocks or any(
                    not isinstance(b, dict) or b.get('type') != 'text' or not isinstance(b.get('text'), str) for b in blocks):
                raise RpcError(-32602, 'Only text prompt blocks are supported')
            if aid in self.turns:
                raise RpcError(-32000, 'This session already has an active turn')
            self.turns[aid] = asyncio.current_task()
            try:
                await self.agent(aid)  # Recheck ownership/archive state for each turn.
                # Use the same OpenAI-compatible gateway as the frontend; it owns
                # history, the Agent's saved policy, tools, sandbox and approvals.
                async with self.client.stream('POST', '/v1/chat/completions', json={
                        'model':'webot', 'session_id':aid, 'stream':True,
                        'messages':[{'role':'user','content':'\n\n'.join(b['text'] for b in blocks)}]}) as response:
                    if response.status_code != 200:
                        raise RpcError(-32000, f'WeBot turn failed ({response.status_code})')
                    reason, finished = 'end_turn', False
                    async for line in response.aiter_lines():
                        if line == 'data: [DONE]':
                            finished = True
                            break
                        if not line.startswith('data: '):
                            continue
                        event = json.loads(line[6:])
                        for choice in event.get('choices', []):
                            delta = choice.get('delta') or {}
                            if delta.get('content'):
                                self.emit({'jsonrpc':'2.0','method':'session/update','params':{
                                    'sessionId':aid,'update':{'sessionUpdate':'agent_message_chunk',
                                    'content':{'type':'text','text':delta['content']}}}})
                            meta = delta.get('meta') or {}
                            if meta.get('type') == 'error' or choice.get('finish_reason') == 'error':
                                raise RpcError(-32000, 'WeBot turn failed; inspect the Agent in ClawCross')
                            if choice.get('finish_reason'):
                                finished = True
                                if choice['finish_reason'] == 'length':
                                    reason = 'max_tokens'
                    if not finished:
                        raise RpcError(-32000, 'WeBot stream ended before turn completion')
                    return {'stopReason':reason}
            except asyncio.CancelledError:
                return {'stopReason':'cancelled'}
            finally:
                self.turns.pop(aid, None)
        raise RpcError(-32601, 'Method not supported')

    async def cancel(self, aid):
        task = self.turns.get(aid)
        if not task:
            return
        await self.request('POST', '/v1/agents/'+quote(aid, safe='')+'/control', {'action':'cancel'})
        task.cancel()

    async def handle(self, packet):
        if not isinstance(packet, dict) or packet.get('jsonrpc') != '2.0' or not isinstance(packet.get('method'), str) or not isinstance(packet.get('params', {}), dict):
            self.emit({'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid request'}})
            return
        result = None
        try:
            result = await self.dispatch(packet['method'], packet.get('params', {}))
        except RpcError as exc:
            if 'id' in packet:
                self.emit({'jsonrpc':'2.0','id':packet['id'],'error':{'code':exc.code,'message':exc.message}})
            return
        except (OSError, ValueError, httpx.HTTPError):
            if 'id' in packet:
                self.emit({'jsonrpc':'2.0','id':packet['id'],'error':{'code':-32000,'message':'ClawCross backend unavailable'}})
            return
        if 'id' in packet:
            self.emit({'jsonrpc':'2.0','id':packet['id'],'result':result})


async def serve(*, owner, agent_id=None, name='我的伙伴', template='group'):
    base, token = _configuration()
    if not token:
        raise RuntimeError('Start ClawCross before using its ACP server')
    limits = httpx.Timeout(None, connect=5)
    async with httpx.AsyncClient(base_url=base, headers={'Authorization':f'Bearer {token}:{owner}'},
                                 timeout=limits, trust_env=False) as client:
        server = WeBotACP(client, owner=owner, agent_id=agent_id, name=name, template=template)
        if agent_id:
            await server.agent(agent_id)
        pending = set()
        try:
            while True:
                line = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    raise RuntimeError('ACP request exceeds 2 MiB')
                try:
                    packet = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    server.emit({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}})
                    continue
                # Keep lifecycle ordering while allowing cancel during a prompt.
                if isinstance(packet, dict) and packet.get('method') == 'session/prompt':
                    task = asyncio.create_task(server.handle(packet)); pending.add(task)
                    task.add_done_callback(pending.discard)
                    await asyncio.sleep(0)
                else:
                    await server.handle(packet)
        finally:
            for aid in list(server.turns):
                await server.cancel(aid)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
