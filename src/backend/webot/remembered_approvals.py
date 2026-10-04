"""Inspect and revoke exact KEEP Y tool actions belonging to one Agent."""
import hashlib
import json
import re

from webot.policy import get_tool_policy, save_tool_policy_config, serialize_tool_policy


def _actions(policy, session_id):
    for name, rule in policy.get('tools', {}).items():
        for encoded in rule.get('approved_args', []):
            try:
                args = json.loads(encoded)
            except (ValueError, TypeError):
                continue
            if isinstance(args, dict) and args.get('_approval_session') == session_id:
                yield name, encoded, args


def remembered_tool_actions(user_id, session_id):
    policy = serialize_tool_policy(get_tool_policy(user_id))
    return [{'tool': name, 'key': hashlib.sha256(encoded.encode()).hexdigest(),
             'summary': ', '.join(sorted(field for field in args if not field.startswith('_'))),
             'arguments': _public_arguments(args)}
            for name, encoded, args in _actions(policy, session_id)]


def revoke_tool_action(user_id, session_id, tool_name, key):
    policy = serialize_tool_policy(get_tool_policy(user_id))
    for name, encoded, _ in _actions(policy, session_id):
        if name == tool_name and hashlib.sha256(encoded.encode()).hexdigest() == key:
            policy['tools'][name]['approved_args'].remove(encoded)
            save_tool_policy_config(user_id, policy)
            return True
    return False


def _public_arguments(value):
    if isinstance(value, dict):
        return {key: ('••••' if re.search(r'password|secret|token|api.?key|authorization|credential', key, re.I)
                      else _public_arguments(item))
                for key, item in value.items() if not key.startswith('_')}
    if isinstance(value, list):
        return [_public_arguments(item) for item in value]
    return value


def remembered_permissions(user_id, session_id):
    from webot.runtime_settings import get_runtime_settings, stored_sandbox_grants
    grants = stored_sandbox_grants(user_id, session_id)
    return {'actions': remembered_tool_actions(user_id, session_id),
            'sandbox_grants': [{**grant, 'key': _grant_key(grant)} for grant in grants],
            'sandbox_security': get_runtime_settings(user_id, session_id).approval.sandbox_security}


def _grant_key(grant):
    return hashlib.sha256(json.dumps(grant, sort_keys=True).encode()).hexdigest()


def add_permission(user_id, session_id, *, kind, target='', tool_name='', arguments=None):
    """Owner configuration adds an exact grant; hard limits still apply."""
    if len(json.dumps(arguments or {})) > 32_000:
        raise ValueError('工具参数超过 32 KB。')
    if kind != 'tool':
        from webot.runtime_settings import remember_sandbox_grant
        remember_sandbox_grant(user_id, session_id=session_id, access=kind, target=target)
        return
    from webot.engine.tool_aliases import resolve_tool_call
    from webot.engine.tool_catalog import TOOL_CATEGORIES
    from webot.engine.agent import USER_INJECTED_TOOLS, SESSION_INJECTED_TOOLS
    from webot.permission_context import resolve_permission_context, remember_approval_in_policy
    if any(key.startswith('_') for key in (arguments or {})):
        raise ValueError('工具参数不能包含系统内部字段。')
    name, args = resolve_tool_call(tool_name, arguments or {})
    if name not in set().union(*TOOL_CATEGORIES.values()):
        raise ValueError('未知 ClawCross 工具名称。')
    if name == 'run_command' and args.get('sandbox_access', 'default') != 'default':
        raise ValueError('沙盒权限请按联网、只读或读写目标添加。')
    if name in USER_INJECTED_TOOLS:
        args['username'] = user_id
    if name in SESSION_INJECTED_TOOLS:
        args[SESSION_INJECTED_TOOLS[name]] = session_id
    permission = resolve_permission_context(user_id=user_id, session_id=session_id, tool_name=name, args=args)
    if not permission.allowed and not permission.requires_approval:
        raise ValueError(permission.reason or '当前硬限制不允许此操作。')
    remember_approval_in_policy(user_id=user_id, session_id=session_id, tool_name=name, args=permission.args)


def revoke_sandbox_permission(user_id, session_id, access, key):
    from webot.runtime_settings import stored_sandbox_grants, forget_sandbox_grant
    for grant in stored_sandbox_grants(user_id, session_id):
        if grant['access'] == access and _grant_key(grant) == key:
            return forget_sandbox_grant(user_id, session_id=session_id, **grant)
    return False
