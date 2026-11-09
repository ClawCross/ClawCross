"""
Permission context and approval flow helpers for WeBot.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import uuid
from typing import Any

from webot.policy import (
    WeBotToolPolicy,
    ToolPolicyRule,
    evaluate_tool_policy,
    get_tool_policy,
    save_tool_policy_config,
    serialize_tool_policy,
)
from webot.runtime_store import (
    ToolApprovalRecord,
    create_tool_approval_request,
    find_active_approval_for_action,
    find_pending_approval_for_action,
    update_tool_approval_status,
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
    normalized_args = dict(args or {})
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
    base_decision = evaluate_tool_policy(effective_policy, tool_name, normalized_args)

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
    normalized_args = dict(args or {})
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


def _append_exact_pattern(existing: list[str], exact_value: str) -> list[str]:
    escaped = f"^{re.escape(exact_value)}$"
    if exact_value and escaped not in existing:
        existing.append(escaped)
    return existing


def remember_approval_in_policy(
    *,
    user_id: str,
    tool_name: str,
    args: dict[str, Any],
) -> None:
    current = serialize_tool_policy(get_tool_policy(user_id))
    current.pop("source", None)
    current.pop("definition_path", None)
    tools = current.setdefault("tools", {})
    tool_entry = dict(tools.get(tool_name) or {})
    content = ""
    path = ""
    if tool_name == "run_command":
        content = str(args.get("command") or "").strip()
    elif tool_name == "run_python_code":
        content = str(args.get("code") or "").strip()
    elif tool_name in {"read_file", "write_file", "append_file", "delete_file"}:
        path = str(args.get("filename") or "").strip()

    tool_entry.setdefault("approval", "manual")
    if content:
        tool_entry["content_allow_patterns"] = _append_exact_pattern(
            list(tool_entry.get("content_allow_patterns") or []),
            content,
        )
    if path:
        tool_entry["path_allow_patterns"] = _append_exact_pattern(
            list(tool_entry.get("path_allow_patterns") or []),
            path,
        )
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
    if remember and normalized == "approved":
        try:
            remember_approval_in_policy(
                user_id=user_id,
                tool_name=updated.tool_name,
                args=json.loads(updated.args_json or "{}"),
            )
        except Exception:
            pass
    return updated
