"""Fail closed when an ACP server cannot enforce disabling its own tools.

ACP permission requests and --allowed-tools are advisory to the server. They
cannot prevent a server from executing built-ins without asking its client.
Never advertise those flags as a universal native-tool off switch.
"""
from agents.runtime import ControlError


def native_tools_enabled(config):
    return ((config.get('meta') or {}).get('acp') or {}).get('native_tools', True) is True


def require_native_execution(config):
    if not native_tools_enabled(config):
        raise ControlError('原生工具已关闭：此 ACP 程序没有可验证的全部工具禁用接口，已阻止启动，不会消耗模型额度。可使用 WeBot，或在设置中明确开启原生工具。')


def policy_card(config):
    enabled = native_tools_enabled(config)
    return {'enabled': enabled, 'disable_enforced': True,
            'text_only_supported': False, 'blocked': not enabled,
            'mechanism': 'block-native-process' if not enabled else 'adapter-permissions',
            'warning': 'ClawCross 沙盒不隔离原生 CLI 工具。关闭原生工具会阻止此适配器运行。'}
