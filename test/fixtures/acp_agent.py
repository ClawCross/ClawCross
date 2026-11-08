"""Deterministic ACP subprocess: no model, network, shell or user credentials."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import uuid

pending = {}
prompts = {}


def send(packet):
    print(json.dumps({'jsonrpc':'2.0', **packet}), flush=True)


def log(event, **details):
    with Path('wire.jsonl').open('a') as stream:
        stream.write(json.dumps({'event':event, 'time':time.time(), 'pid':os.getpid(), **details})+'\n')


async def handle(packet):
    method, args, rid = packet.get('method'), packet.get('params') or {}, packet.get('id')
    result = {}
    if method == 'initialize':
        log('initialize', capabilities=args.get('clientCapabilities'))
        result = {'protocolVersion':1, 'agentCapabilities':{'loadSession':True,
                  'promptCapabilities':{'image':True, 'audio':True}}}
    elif method in {'session/new', 'session/load'}:
        sid = args.get('sessionId') or uuid.uuid4().hex
        log(method, sid=sid, servers=[s['name'] for s in args.get('mcpServers', [])])
        result = {'configOptions':[{'type':'select', 'id':'mode', 'name':'Mode', 'currentValue':'read-only',
                  'options':[{'value':'read-only','name':'Read only'}, {'value':'agent','name':'Agent'}]}]}
        if method == 'session/new': result['sessionId'] = sid
        else:
            send({'method':'session/update', 'params':{'sessionId':sid, 'update':{'sessionUpdate':'agent_message_chunk',
                  'content':{'type':'text','text':'OLD REPLAY MUST NOT APPEAR'}}}})
    elif method == 'session/set_config_option':
        log('config', **args)
        result = {'configOptions':[{'type':'select', 'id':args['configId'], 'name':args['configId'],
                  'currentValue':args['value'], 'options':[]}]}
    elif method == 'session/prompt':
        text, sid = args['prompt'][0]['text'], args['sessionId']
        log('begin', text=text, sid=sid, blocks=args['prompt'])
        prompts[sid] = asyncio.current_task()
        try:
            if text == 'crash': os._exit(7)
            if text == 'stderr': sys.stderr.write('x'*200000); sys.stderr.flush()
            if text == 'overflow':
                for _ in range(300):
                    send({'method':'session/update','params':{'sessionId':sid,'update':{
                        'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'x'*32768}}}})
            await asyncio.sleep(0.12 if text != 'slow' else 30)
            send({'method':'session/update', 'params':{'sessionId':sid, 'update':{'sessionUpdate':'agent_message_chunk',
                  'content':{'type':'text','text':text}}}})
            log('end', text=text, sid=sid)
            result = {'stopReason':'end_turn'}
        except asyncio.CancelledError:
            log('cancelled', sid=sid)
            result = {'stopReason':'cancelled'}
        finally:
            prompts.pop(sid, None)
    elif method == 'session/cancel':
        log('cancel', sid=args['sessionId'])
        task = prompts.get(args['sessionId'])
        if task: task.cancel()
    else:
        if rid is not None: send({'id':rid, 'error':{'code':-32601,'message':'unsupported'}})
        return
    if rid is not None: send({'id':rid, 'result':result})


async def main():
    loop = asyncio.get_running_loop()
    tasks = set()
    while line := await loop.run_in_executor(None, sys.stdin.readline):
        task = asyncio.create_task(handle(json.loads(line)))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


asyncio.run(main())
