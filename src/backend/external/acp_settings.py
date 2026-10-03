"""Passive ACP capability discovery and per-Agent configuration."""
import json
from pathlib import Path
import re
from external.session import runtime_session


def native_options(agent):
    from external.acpx import _default_acpx_cwd
    # Reuse only an option catalog, never another session's selected values.
    candidates = []
    package = {'codex':'@agentclientprotocol/codex-acp', 'claude':'@agentclientprotocol/claude-agent-acp'}.get(agent.platform)
    # acpx local records are the authority; no adapter is launched by the UI.
    for path in (Path.home() / '.acpx' / 'sessions').glob('*.json'):
        try:
            if path.stat().st_size > 16 * 1024 * 1024:
                continue
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if record.get('closed'):
            continue
        if Path(record.get('cwd', '')).resolve() != Path(_default_acpx_cwd()).resolve():
            continue
        options = (record.get('acpx') or {}).get('config_options') or []
        if record.get('name') == runtime_session(agent) and options:
            return options
        if package and (record.get('name') or '').startswith(f'clawcross-{agent.owner}-') and                 re.search(re.escape(package) + r'(?:@|\s|$)', record.get('agent_command') or '') and options:
            candidates.append((path.stat().st_mtime, options))
    if not candidates:
        return []
    options = max(candidates, key=lambda item:item[0])[1]
    preferred = {'mode':'agent' if agent.platform == 'codex' else 'default',
                 'model':str(agent.config.get('model') or ('gpt-5.5' if agent.platform == 'codex' else 'sonnet')),
                 'reasoning_effort':'medium', 'effort':'medium', 'collaboration_mode':'default',
                 'fast-mode':'off', 'fast':'off'}
    result = []
    configured = ((agent.config.get('meta') or {}).get('acp') or {}).get('config_options') or {}
    for item in options:
        choices = [choice['value'] for choice in item.get('options', []) if 'value' in choice]
        value = configured.get(item['id'], preferred.get(item['id']))
        if value not in choices:
            value = next((choice for choice in choices if choice != 'default'), choices[0] if choices else '')
        result.append({**item, 'currentValue':value, 'provisional':True})
    return result


def initial_config_options(agent):
    """Use the same explicit initial values that the settings page displays."""
    return {item['id']: item['currentValue'] for item in native_options(agent)
            if item.get('provisional') and item.get('currentValue')}



def capability_card(agent):
    acp = (agent.config.get('meta') or {}).get('acp') or {}
    return {'platform': agent.platform, 'transport': 'acpx', 'streaming_tools': True,
            'clawcross_tools': bool(acp.get('clawcross_tools', True)),
            'config_options': native_options(agent), 'settings': {'clawcross_tools': True, **acp},
            'modes': ['chat', 'readonly', 'manual', 'auto', 'bypass'],
            'supports': {'native_config': True, 'tool_bridge': True,
                         'clawcross_compaction': False, 'native_tools': True}}


def validate_settings(agent, value):
    if not isinstance(value, dict):
        raise ValueError('ACP settings must be an object')
    allowed = {'clawcross_tools', 'config_options', 'timeout_sec', 'ttl_sec', 'tools'}
    if set(value) - allowed:
        raise ValueError('Unknown ACP setting')
    if 'clawcross_tools' in value and not isinstance(value['clawcross_tools'], bool):
        raise ValueError('clawcross_tools must be a boolean')
    tools = value.get('tools')
    if tools is not None and (not isinstance(tools, list) or len(tools) > 500 or any(
            not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', name) for name in tools)):
        raise ValueError('tools must be a list of tool names or null')
    for name, lower, upper in [('timeout_sec', 5, 3600), ('ttl_sec', 60, 86400)]:
        if name in value and (type(value[name]) is not int or not lower <= value[name] <= upper):
            raise ValueError(f'{name} must be between {lower} and {upper}')
    options = value.get('config_options', {})
    if not isinstance(options, dict) or len(options) > 24:
        raise ValueError('Invalid config_options')
    advertised = {item['id']: item for item in native_options(agent) if item.get('id')}
    for key, selected in options.items():
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', key) or not isinstance(selected, str) or len(selected) > 200:
            raise ValueError('Invalid native config selection')
        if advertised and key not in advertised:
            raise ValueError(f'{key} is not supported by this Agent')
        choices = {item['value'] for item in advertised.get(key, {}).get('options', []) if 'value' in item}
        if choices and selected not in choices:
            raise ValueError(f'{selected} is not supported for {key}')
    return value
