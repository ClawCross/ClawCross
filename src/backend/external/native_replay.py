"""Normalize ACP history notifications without prompting or replaying them to a model."""
import json


def _text(content):
    if not isinstance(content, dict):
        return ''
    kind = content.get('type')
    if kind == 'text':
        return str(content.get('text') or '')
    if kind in ('image', 'audio'):
        return '[' + kind + ']'
    if kind == 'resource_link':
        return str(content.get('uri') or '')
    resource = content.get('resource') or {}
    return str(resource.get('text') or resource.get('uri') or '')


def normalize_updates(updates):
    rows = []
    tools = {}
    for event in updates:
        kind = event.get('sessionUpdate')
        if kind in ('user_message_chunk', 'agent_message_chunk', 'agent_thought_chunk'):
            role = 'user' if kind == 'user_message_chunk' else 'assistant'
            direction = 'thought' if kind == 'agent_thought_chunk' else ('send' if role == 'user' else 'recv')
            key = event.get('messageId')
            chunk = _text(event.get('content'))
            previous = rows[-1] if rows else None
            if (previous and previous['role'] == role and previous['direction'] == direction
                    and previous['meta'].get('message_id') == key):
                previous['content'] += chunk
                previous['meta']['content_blocks'].append(event.get('content'))
                previous['meta']['updates'].append(event)
            else:
                rows.append({'role':role, 'direction':direction, 'content':chunk,
                             'meta':{'message_id':key, 'content_blocks':[event.get('content')], 'updates':[event]}})
        elif kind in ('tool_call', 'tool_call_update'):
            call_id = event.get('toolCallId')
            if not call_id:
                continue
            if call_id not in tools:
                row = {'role':'tool', 'direction':'tool_result', 'content':'',
                       'meta':{'tool_call_id':call_id, 'native_tool':{}, 'tool_name':''}}
                rows.append(row); tools[call_id] = row
            row = tools[call_id]
            row['meta'].setdefault('updates', []).append(event)
            tool = row['meta']['native_tool']
            for key, value in event.items():
                if key == 'content':
                    tool.setdefault('content', []).extend(value or [])
                elif key != 'sessionUpdate':
                    tool[key] = value
            row['meta']['tool_name'] = tool.get('name') or tool.get('title') or tool.get('kind') or 'tool'
            row['meta']['status'] = tool.get('status')
            output = tool.get('rawOutput')
            parts = [_text(block.get('content')) for block in tool.get('content', []) if block.get('type') == 'content']
            row['content'] = '\n'.join(part for part in parts if part)
            if output is not None:
                row['content'] = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            if tool.get('rawInput') is not None:
                raw = json.dumps(tool['rawInput'], ensure_ascii=False)
                row['content'] = 'Input:\n' + raw + ('\n\nOutput:\n' + row['content'] if row['content'] else '')
            elif not row['content']:
                row['content'] = str(tool.get('title') or '')
    return rows
