"""Fail closed when an ACP server cannot enforce disabling its own tools.

ACP permission requests and --allowed-tools are advisory to the server. They
cannot prevent a server from executing built-ins without asking its client.
Never advertise those flags as a universal native-tool off switch.
"""
def native_tools_enabled(config):
    return ((config.get('meta') or {}).get('acp') or {}).get('native_tools', True) is True


def supports_text_only(config):
    import sys
    options = (config.get('meta') or {}).get('acp') or {}
    return (sys.platform == 'linux' and config.get('platform') == 'codex' and options.get('text_only') is True
            and not config.get('connection_id') and not options.get('agent_name') and not options.get('agent_command'))


def runtime_allowed(config):
    return native_tools_enabled(config) or supports_text_only(config)


def clean_transport_warning(text):
    """Codex's expected HTTP fallback is transport status, not an Agent reply."""
    if text.startswith('Warning: Falling back from WebSockets to HTTPS transport.') and '\n\n' in text:
        return text.partition('\n\n')[2]
    return text


def require_native_execution(config):
    if not runtime_allowed(config):
        from agents.runtime import ControlError
        raise ControlError('原生工具已关闭：此 ACP 程序没有可验证的全部工具禁用接口，已阻止启动，不会消耗模型额度。可使用 WeBot，或在设置中明确开启原生工具。')


def policy_card(config):
    enabled = native_tools_enabled(config)
    text_only = supports_text_only(config) and not enabled
    return {'enabled': enabled, 'disable_enforced': True,
            'text_only_supported': supports_text_only(config), 'blocked': not runtime_allowed(config),
            'mechanism': 'model-tool-gate' if text_only else 'block-native-process' if not enabled else 'adapter-permissions',
            'warning': ('原生工具已关闭；Codex 通过受限模型传输继续聊天。仅 ClawCross 授权的工具可用。'
                        if text_only else 'ClawCross 沙盒不隔离原生 CLI 工具。无受限聊天路径时，关闭原生工具会阻止此适配器运行。')}
