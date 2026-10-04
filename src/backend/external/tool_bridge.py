"""Agent-scoped MCP bridge; calls use the same tool node and review as WeBot."""
from contextlib import contextmanager
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from agents.store import get_store, WEBOT
from agents.gateway import get_gateway
from common.runtime_paths import STATE_DIR

_tokens = {}
_active = {}


def connector_file(agent):
    key = hashlib.sha256(f'{agent.owner}\0{agent.agent_id}'.encode()).hexdigest()
    directory = STATE_DIR / 'acp-connectors'
    path = directory / (key + '.json')
    if not ((agent.config.get('meta') or {}).get('acp') or {}).get('clawcross_tools', True) and not path.exists():
        return None  # Preserve native connector defaults when explicitly disabled.
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_path = directory / (key + '.token')
    if not token_path.exists():
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_urlsafe(32))
    token = token_path.read_text().strip()
    for granted, principal in list(_tokens.items()):
        if principal == (agent.owner, agent.agent_id) and granted != token:
            _tokens.pop(granted, None)
    _tokens[token] = (agent.owner, agent.agent_id)
    value = {'mcpServers': [{'name': 'ClawCross', 'command': sys.executable,
             'args': ['-P', str(Path(__file__).with_name('tool_bridge_stdio.py'))],
             'env': [{'name': 'CLAWCROSS_BRIDGE_TOKEN', 'value': token},
                     {'name': 'CLAWCROSS_BRIDGE_URL', 'value':
                      f'http://127.0.0.1:{os.getenv("PORT_AGENT", "51200")}/external/tool-bridge'}]}]}
    encoded = json.dumps(value, ensure_ascii=False)
    if not ((agent.config.get('meta') or {}).get('acp') or {}).get('clawcross_tools', True):
        # An explicit empty config also removes a previous session connector.
        encoded = json.dumps({'mcpServers': []})
    if not path.exists() or path.read_text() != encoded:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as handle:
            handle.write(encoded)
    return str(path)


@contextmanager
def active_turn(agent, msg, context, mode, enabled_tools, *, prepared=None, response_format=None):
    key = (agent.owner, agent.agent_id)
    from langchain_core.messages import HumanMessage
    from webot.approval_review import review_context, resolve_conversation_reply
    group_requests = context.get('group_human_requests') or []
    human = HumanMessage(content=msg.text, additional_kwargs={
        'input_origin': 'user' if not group_requests and msg.sender == f'u:{agent.owner}' else 'system',
        'framework_group_requests': group_requests})
    resolve_conversation_reply(*key, review_context([human]))
    _active[key] = {'agent': agent, 'message': msg, 'context': context, 'mode': mode,
                    'enabled_tools': enabled_tools, 'lock': asyncio.Lock(),
                    'review_counters': {}, 'dynamic_context': prepared.dynamic_context if prepared else None,
                    'response_format': response_format, 'tasks': set()}
    try:
        yield
    finally:
        turn = _active.pop(key, None)
        if turn:
            for task in turn['tasks']:
                task.cancel()


def attach_runtime_context(payload, current, turn):
    """Only our MCP results carry changed runtime facts; native tool output stays native."""
    baseline = turn.get('dynamic_context')
    if baseline is None:
        return payload
    from external.session import build_dynamic_context
    from common.agent_prompt import render_section_updates
    latest = build_dynamic_context(current, turn['message'], context=turn['context'],
        mode=turn['mode'], enabled_tools=turn['enabled_tools'], response_format=turn['response_format'])
    delta = render_section_updates(baseline, latest)
    if delta:
        payload['runtime_context'] = delta
        baseline.clear()
        baseline.update(latest)
    return payload


class ToolRequest(BaseModel):
    action: str
    query: str = Field(default='', max_length=200)
    name: str = Field(default='', max_length=160)
    arguments: dict = Field(default_factory=dict)


def bridge_router():
    router = APIRouter()

    @router.post('/external/tool-bridge')
    async def invoke(body: ToolRequest, authorization: str | None = Header(None)):
        token = (authorization or '').removeprefix('Bearer ')
        key = _tokens.get(token)
        turn = _active.get(key)
        if not turn:
            raise HTTPException(403, 'No active authorized Agent turn')
        current = get_store().require(*key)
        if not ((current.config.get('meta') or {}).get('acp') or {}).get('clawcross_tools', True):
            raise HTTPException(403, 'ClawCross connector disabled')
        if turn['mode'] == 'chat':
            raise HTTPException(403, 'Tools disabled in chat mode')
        if len(json.dumps(body.arguments)) > 200_000:
            raise HTTPException(413, 'Tool arguments too large')
        from langchain_core.messages import AIMessage, HumanMessage
        from webot.engine.agent import UserAwareToolNode, available_internal_tool_names, _visible_tool_parameters
        engine = get_gateway().runtimes[WEBOT].engine
        tools = engine._mcp_tools
        def meta(owner, aid):
            target = get_store().require(owner, aid)
            return {'tools': ((target.config.get('meta') or {}).get('acp') or {}).get('tools'),
                    'teams': target.teams}
        state = {'user_id': key[0], 'session_id': key[1], 'session_mode': turn['mode'],
                 'enabled_tools': turn['enabled_tools'],
                 '_approval_review_counters': turn['review_counters'],
                 'trigger_source': 'system' if turn['context'].get('conversation_id') else 'user'}
        names = available_internal_tool_names(tools, user_id=key[0], session_id=key[1],
                                             state=state, find_session_meta=meta)
        if body.action == 'search':
            results = engine._tool_registry.search_tools(body.query, limit=8, enabled_names=names)
            by_name = {tool.name: tool for tool in tools}
            for item in results:
                item['parameters'] = _visible_tool_parameters(by_name[item['name']])
            return attach_runtime_context({'tools': results}, current, turn)
        if body.action != 'call' or body.name not in names:
            raise HTTPException(403, 'Tool is not enabled for this Agent turn')
        from jsonschema import validate, ValidationError
        tool = next(tool for tool in tools if tool.name == body.name)
        try:
            validate(body.arguments, {**_visible_tool_parameters(tool), 'additionalProperties': False})
        except ValidationError as exc:
            raise HTTPException(400, 'Invalid tool arguments: ' + exc.message[:200]) from exc
        request = turn['message']
        group_requests = turn['context'].get('group_human_requests') or []
        human = HumanMessage(content=request.text, additional_kwargs={
            'framework_groups': turn['context'].get('groups') or [],
            'input_origin': 'user' if not group_requests and request.sender == f'u:{key[0]}' else 'system',
            'framework_group_requests': group_requests})
        from webot.approval_review import review_context, resolve_conversation_reply
        resolve_conversation_reply(*key, review_context([human]))
        call = {'name': body.name, 'args': body.arguments, 'id': 'bridge-' + secrets.token_hex(8)}
        node = UserAwareToolNode(tools, find_internal_session_meta_fn=meta,
                                 tool_registry=engine._tool_registry)
        async with turn['lock']:
            if _active.get(key) is not turn:
                raise HTTPException(403, 'Agent turn finished')
            if turn['review_counters'].get('consecutive_denials', 0) >= 3:
                raise HTTPException(403, 'Too many denied tool requests in this turn')
            task = asyncio.create_task(node({**state, 'messages': [human, AIMessage(content='', tool_calls=[call])]},
                                {'configurable': {'thread_id': key[0] + '#' + key[1]}}))
            turn['tasks'].add(task)
            try:
                result = await task
            finally:
                turn['tasks'].discard(task)
        current = get_store().require(*key)
        return attach_runtime_context({'results': [{'content': message.content, 'status': message.status}
                            for message in result['messages']]}, current, turn)

    return router
