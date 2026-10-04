"""
Permission context and approval flow helpers for WeBot.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import uuid
from typing import Any
from webot.approval_actions import bind_file_target, canonical_action_args, file_target_outside_workspace

from webot.policy import (
    WeBotToolPolicy,
    evaluate_tool_policy,
    get_tool_policy,
    save_tool_policy_config,
    serialize_tool_policy,
    approval_args_key,
)
from webot.runtime_store import (
    ToolApprovalRecord,
    create_tool_approval_request,
    find_active_approval_for_action,
    find_pending_approval_for_action,
    update_tool_approval_status,
    set_approval_review_metadata,
)

_POLICY_EXEMPT_TOOLS = {
    "resolve_tool_approval",
    "list_tool_approvals",
}


@dataclass(frozen=True)
class PermissionContext:
    decision: str
    allowed: bool
    requires_approval: bool
    reason: str
    matched_rule: str
    tool_name: str
    args: dict[str, Any]
    policy: WeBotToolPolicy
    approval: ToolApprovalRecord | None = None


def resolve_permission_context(
    *,
    user_id: str,
    session_id: str,
    tool_name: str,
    args: dict[str, Any] | None = None,
    policy: WeBotToolPolicy | None = None,
) -> PermissionContext:
    effective_policy = policy or get_tool_policy(user_id)
    normalized_args = bind_file_target(tool_name, dict(args or {}), user_id, session_id)
    if tool_name in _POLICY_EXEMPT_TOOLS:
        return PermissionContext(
            decision="allow",
            allowed=True,
            requires_approval=False,
            reason="审批流程核心工具默认放行，避免自锁。",
            matched_rule="",
            tool_name=tool_name,
            args=normalized_args,
            policy=effective_policy,
        )
    base_decision = evaluate_tool_policy(effective_policy, tool_name,
                                         {**normalized_args, '_approval_session': session_id})
    if file_target_outside_workspace(normalized_args) and (base_decision.allowed or base_decision.requires_approval):
        from webot.policy import ToolPolicyDecision
        base_decision = ToolPolicyDecision(
            allowed=False, requires_approval=True,
            reason=f"文件目标超出当前工作区，需要批准：{normalized_args['_resolved_path']}",
        )

    if base_decision.allowed:
        return PermissionContext(
            decision="allow",
            allowed=True,
            requires_approval=False,
            reason=base_decision.reason,
            matched_rule=base_decision.matched_rule,
            tool_name=tool_name,
            args=normalized_args,
            policy=effective_policy,
        )

    if base_decision.requires_approval:
        approval = find_active_approval_for_action(user_id, session_id, tool_name, normalized_args)
        if approval is not None:
            return PermissionContext(
                decision="allow",
                allowed=True,
                requires_approval=False,
                reason=approval.resolution_reason or "已使用既有人工批准。",
                matched_rule=base_decision.matched_rule,
                tool_name=tool_name,
                args=normalized_args,
                policy=effective_policy,
                approval=approval,
            )

        pending = find_pending_approval_for_action(user_id, session_id, tool_name, normalized_args)
        return PermissionContext(
            decision="ask",
            allowed=False,
            requires_approval=True,
            reason=base_decision.reason,
            matched_rule=base_decision.matched_rule,
            tool_name=tool_name,
            args=normalized_args,
            policy=effective_policy,
            approval=pending,
        )

    return PermissionContext(
        decision="deny",
        allowed=False,
        requires_approval=False,
        reason=base_decision.reason,
        matched_rule=base_decision.matched_rule,
        tool_name=tool_name,
        args=normalized_args,
        policy=effective_policy,
    )


def create_or_reuse_permission_request(
    *,
    user_id: str,
    session_id: str,
    tool_name: str,
    args: dict[str, Any] | None = None,
    reason: str = "",
) -> ToolApprovalRecord:
    normalized_args = bind_file_target(tool_name, dict(args or {}), user_id, session_id)
    existing = find_pending_approval_for_action(user_id, session_id, tool_name, normalized_args)
    if existing is not None:
        return existing
    return create_tool_approval_request(
        user_id,
        session_id,
        approval_id=f"approval-{uuid.uuid4().hex[:10]}",
        tool_name=tool_name,
        args=normalized_args,
        request_reason=reason or f"{tool_name} 需要人工批准。",
    )


def remember_approval_in_policy(
    *,
    user_id: str,
    tool_name: str,
    args: dict[str, Any],
    session_id: str = '',
) -> None:
    if (tool_name == 'run_command' and args.get('sandbox_access', 'default') != 'default'):
        from webot.runtime_settings import remember_sandbox_grant
        remember_sandbox_grant(user_id, session_id=session_id or args.get('session_id', ''),
            access=args['sandbox_access'], target=args['escalation_target'])
        return
    current = serialize_tool_policy(get_tool_policy(user_id))
    current.pop("source", None)
    current.pop("definition_path", None)
    tools = current.setdefault("tools", {})
    # Creating a specific rule must retain restrictions inherited from '*'.
    tool_entry = dict(tools.get(tool_name) or tools.get("*") or {})
    tool_entry.setdefault("approval", "manual")
    approved_args = list(tool_entry.get("approved_args") or [])
    scoped = {**args, '_approval_session': session_id} if session_id else args
    key = approval_args_key(canonical_action_args(tool_name, scoped))
    if key not in approved_args:
        approved_args.append(key)
    tool_entry["approved_args"] = approved_args
    tools[tool_name] = tool_entry
    save_tool_policy_config(user_id, current)


def resolve_permission_request(
    *,
    user_id: str,
    approval_id: str,
    action: str,
    reason: str = "",
    remember: bool = False,
) -> ToolApprovalRecord | None:
    normalized = (action or "").strip().lower()
    if normalized not in {"approved", "denied"}:
        raise ValueError(f"Unsupported approval action: {action}")
    updated = update_tool_approval_status(
        approval_id,
        user_id,
        status=normalized,
        resolution_reason=reason,
    )
    if updated is None:
        return None
    if normalized == 'denied' and updated.tool_name == 'run_command':
        from webot.runtime_store import close_sandbox_retry_chain
        close_sandbox_retry_chain(user_id, updated.session_id, json.loads(updated.args_json or '{}').get('sandbox_approval_chain', []))
    metadata = json.loads(updated.review_metadata_json or '{}')
    metadata['human_resolution'] = normalized
    set_approval_review_metadata(approval_id, user_id, metadata)
    if remember and normalized == "approved":
        try:
            remember_approval_in_policy(
                user_id=user_id,
                tool_name=updated.tool_name,
                args=json.loads(updated.args_json or "{}"),
                session_id=updated.session_id,
            )
            from webot.approval_review import policy_binding
            metadata['remembered'] = True
            if metadata.get("binding"):
                metadata["binding"]["policy_hash"] = policy_binding(user_id, updated.session_id)
            set_approval_review_metadata(approval_id, user_id, metadata)
        except Exception as exc:
            metadata['remember_error'] = type(exc).__name__
            set_approval_review_metadata(approval_id, user_id, metadata)
            return update_tool_approval_status(approval_id, user_id, status='denied',
                resolution_reason='无法保存 KEEP Y 授权，本次操作未执行。', expected_status='approved')
    from webot.runtime_store import get_tool_approval
    return get_tool_approval(approval_id, user_id)
