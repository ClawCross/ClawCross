"""Per-Agent ACP launch targets; selecting one never installs or starts a program."""
import re

LAUNCH_PRESETS = {
    'codebuddy': {'label': 'CodeBuddy', 'agent_command': 'codebuddy --acp'},
    'zcode': {'label': 'ZCode', 'agent_command': 'zcode-acp-server'},
    'acp': {'label': '自定义 ACP', 'agent_command': ''},
}


def launch_settings(config):
    options = ((config.get('meta') or {}).get('acp') or {})
    name = options.get('agent_name') or ''
    command = options.get('agent_command') or ''
    if not name and not command:
        command = LAUNCH_PRESETS.get(config.get('platform'), {}).get('agent_command', '')
    return {'agent_name': name, 'agent_command': command}


def validate_launch_settings(options):
    for key in ('agent_name', 'agent_command'):
        if key in options and (not isinstance(options[key], str) or len(options[key]) > 4096
                               or any(char in options[key] for char in ('\0', '\n', '\r'))):
            raise ValueError(f'Invalid {key}')
    name = str(options.get('agent_name') or '').strip()
    command = str(options.get('agent_command') or '').strip()
    if name and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', name):
        raise ValueError('ACP Agent 名称只能包含字母、数字、点、下划线和短横线')
    if name and command:
        raise ValueError('ACP Agent 名称与启动命令只能选择一种')
    return {**options, **{key: value for key, value in [('agent_name', name), ('agent_command', command)] if key in options}}
