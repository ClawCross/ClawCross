"""Read-only view of approvals and their separately stored remembered scopes."""
from __future__ import annotations

import hashlib
import json

from webot import runtime_store
from webot.policy import get_tool_policy
from webot.runtime_settings import get_runtime_settings


def approval_registry(user_id: str, session_id: str, *, status: str = '', limit: int = 20) -> dict:
    status = status.strip().lower()
    if status not in {'', 'pending', 'approved', 'denied', 'used', 'expired'}:
        raise ValueError('Unknown approval status')
    now = runtime_store.utc_now()
    records = []
    for record in runtime_store.list_tool_approvals(user_id, session_id, limit=500):
        state = 'expired' if record.status in {'pending', 'approved'} and record.expires_at <= now else record.status
        if status and state != status:
            continue
        meta = json.loads(record.review_metadata_json or '{}')
        execution = meta.get('execution') or {}
        records.append({'id': record.approval_id, 'tool': record.tool_name, 'status': state,
            'reviewer': meta.get('reviewer', 'user'), 'decision': meta.get('human_resolution') or (meta.get('verdict') or {}).get('decision', ''),
            'reason': record.resolution_reason or record.request_reason,
            'remembered': bool(meta.get('remembered')), 'remember_error': meta.get('remember_error', ''),
            'execution': {'status': execution.get('status', ''), 'at': execution.get('at', '')},
            'created_at': record.created_at, 'expires_at': record.expires_at})
        if len(records) >= max(1, min(limit, 50)):
            break
    settings = get_runtime_settings(user_id, session_id).approval
    actions = []
    for name, rule in get_tool_policy(user_id).tools.items():
        for key in rule.approved_args:
            try:
                args = json.loads(key)
            except ValueError:
                continue
            if args.get('_approval_session') != session_id:
                continue
            # Complete arguments may contain credentials or user content. Show
            # their names and a stable fingerprint, never echo their values.
            actions.append({'tool': name, 'fingerprint': hashlib.sha256(key.encode()).hexdigest()[:16],
                            'fields': sorted(k for k in args if not k.startswith('_'))})
    return {'session_id': session_id, 'records': records,
        'saved_permissions': {'sandbox': [g.model_dump() for g in settings.sandbox_grants],
                             'network_targets': settings.sandbox_allowed_domains, 'actions': actions},
        'sandbox_security': settings.sandbox_security,
        'note': 'Y 仅批准一次；KEEP Y 保存具体操作或沙盒目标。登记批准不等于执行成功；保存权限仍受工具名单、明确拒绝和最大权限限制，严格沙盒不使用提权。撤销请在当前 Agent 的运行设置中删除相应权限。'}
