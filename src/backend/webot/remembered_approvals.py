"""Inspect and revoke exact KEEP Y tool actions belonging to one Agent."""
import hashlib
import json

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
             'summary': ', '.join(sorted(field for field in args if not field.startswith('_')))}
            for name, encoded, args in _actions(policy, session_id)]


def revoke_tool_action(user_id, session_id, tool_name, key):
    policy = serialize_tool_policy(get_tool_policy(user_id))
    for name, encoded, _ in _actions(policy, session_id):
        if name == tool_name and hashlib.sha256(encoded.encode()).hexdigest() == key:
            policy['tools'][name]['approved_args'].remove(encoded)
            save_tool_policy_config(user_id, policy)
            return True
    return False
