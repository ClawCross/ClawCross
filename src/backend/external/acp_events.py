"""Normalize ACP updates for the shared desktop/mobile event UI."""
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
