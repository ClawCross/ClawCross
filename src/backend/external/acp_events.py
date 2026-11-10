"""Normalize ACP updates and fold them into the items of a turn (text runs, tool calls)."""
import json


def text_content(blocks):
    parts = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        content = block.get('content', block)
        if isinstance(content, dict) and isinstance(content.get('text'), str):
            parts.append(content['text'])
    return '\n'.join(parts)[:32000]


def normalize_event(packet):
    if not isinstance(packet, dict) or packet.get('method') != 'session/update':
        return None
    update = (packet.get('params') or {}).get('update') or {}
    kind = update.get('sessionUpdate')
    if kind == 'agent_message_chunk':
        content = update.get('content') or {}
        return {'type': 'text', 'text': content.get('text', '')} if content.get('type') == 'text' else None
    if kind in ('tool_call', 'tool_call_update'):
        status = update.get('status')
        event = {'type': 'acpx_tool_end' if status in ('completed', 'failed') else
                 'acpx_tool_start' if kind == 'tool_call' else 'acpx_tool_update',
                 'tool_call_id': str(update.get('toolCallId') or ''), 'status': status,
                 'content_text': text_content(update.get('content'))}
        for source, target in [('title', 'title'), ('name', 'name'), ('kind', 'kind'), ('rawInput', 'input'), ('rawOutput', 'output')]:
            if source in update:
                event[target] = update[source]
        if 'output' in event and not event['content_text']:
            output = event['output']
            event['content_text'] = (output.get('formatted_output', '') if isinstance(output, dict) and 'formatted_output' in output else
                                     output if isinstance(output, str) else
                                     json.dumps(event['output'], ensure_ascii=False))[:32000]
        delta = ((update.get('_meta') or {}).get('terminal_output_delta') or {}).get('data')
        if isinstance(delta, str):
            event['content_delta'] = delta[:32000]
        if status is None:
            event.pop('status')
        return event if event['tool_call_id'] else None
    if kind == 'usage_update':
        return {'type': 'acpx_usage', 'used': update.get('used'), 'size': update.get('size')}
    if kind == 'config_option_update':
        return {'type': 'acpx_config', 'options': update.get('configOptions', [])}
    return None


def apply(items, event):
    """Fold a normalized event into a turn's ordered items; return the item it changed.

    The text between two tool calls is one ``text`` item; a tool call is one
    ``tool`` item holding its latest title, status, input and output tail.
    """
    kind = event.get('type')
    if kind == 'text':
        if not event.get('text'):
            return None
        if items and items[-1]['kind'] == 'text':
            items[-1]['text'] += event['text']
        else:
            items.append({'id': f'text-{len(items)}', 'kind': 'text', 'text': event['text']})
        return items[-1]
    if not str(kind).startswith('acpx_tool_'):
        return None
    tool = next((item for item in reversed(items) if item['kind'] == 'tool' and item['id'] == event['tool_call_id']), None)
    if tool is None:
        tool = {'id': event['tool_call_id'], 'kind': 'tool', 'title': '', 'status': 'pending', 'input': None, 'output': ''}
        items.append(tool)
    tool['title'] = str(event.get('title') or event.get('name') or event.get('kind') or tool['title'])
    if event.get('status'):
        tool['status'] = event['status']
    if 'input' in event:
        tool['input'] = event['input']
    if 'content_delta' in event:
        tool['output'] = (tool['output'] + event['content_delta'])[-32000:]
    elif event.get('content_text'):
        tool['output'] = event['content_text']
    return tool


def items_from_output(output):
    """The items of a finished turn, replayed from acpx's stdout."""
    items = []
    for line in output.splitlines():
        try:
            event = normalize_event(json.loads(line))
        except ValueError:
            continue
        if event:
            apply(items, event)
    return items
