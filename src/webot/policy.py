"""
Centralized WeBot tool approval, policy, and hook handling.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import subprocess
from pathlib import Path
import re
from typing import Any


_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = _DEFAULT_PROJECT_ROOT
from utils.runtime_paths import USER_FILES_DIR
DEFAULT_POLICY_FILENAME = "webot_tool_policy.json"
DEFAULT_EVENT_LOG_PATH = "logs/webot_tool_events.jsonl"

_APPROVAL_MODES = {"allow", "deny", "manual"}
_POLICY_EVENTS = {
    "before",
    "after",
    "after_error",
    "deny",
    "permission_request",
    "permission_resolved",
    "session_start",
    "user_prompt_submit",
    "pre_compact",
    "stop",
    "subagent_stop",
    "session_end",
}

_CONTENT_ARG_NAMES = {
    "run_command": "command",
    "run_python_code": "code",
}

_PATH_ARG_NAMES = {
    "read_file": "filename",
    "write_file": "filename",
    "append_file": "filename",
    "delete_file": "filename",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_dir(user_id: str, project_root: str | Path | None = None) -> Path:
    if project_root is not None:
        root = Path(project_root) / "data" / "user_files"
    elif Path(PROJECT_ROOT) != _DEFAULT_PROJECT_ROOT:
        root = Path(PROJECT_ROOT) / "data" / "user_files"
    else:
        root = USER_FILES_DIR
    return root / (user_id or "anonymous")


def get_tool_policy_path(
    user_id: str | None,
    *,
    project_root: str | Path | None = None,
) -> Path | None:
    if not user_id:
        return None
    return _user_dir(user_id, project_root=project_root) / DEFAULT_POLICY_FILENAME


@dataclass(frozen=True)
class ToolPolicyHook:
    event: str
    hook_type: str = "write_jsonl"
    path: str = DEFAULT_EVENT_LOG_PATH
    include_args: bool = True
    include_result: bool = False
    command: str = ""
    timeout_seconds: int = 10


@dataclass(frozen=True)
class ToolPolicyRule:
    approval: str | None = None
    content_allow_patterns: tuple[str, ...] = ()
    content_block_patterns: tuple[str, ...] = ()
    path_allow_patterns: tuple[str, ...] = ()
    path_block_patterns: tuple[str, ...] = ()
    hooks: tuple[ToolPolicyHook, ...] = ()


@dataclass(frozen=True)
class WeBotToolPolicy:
    default_approval: str = "allow"
    hooks: tuple[ToolPolicyHook, ...] = ()
    tools: dict[str, ToolPolicyRule] = field(default_factory=dict)
    source: str = "built-in"
    definition_path: str | None = None


@dataclass(frozen=True)
class ToolPolicyDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str = ""
    matched_rule: str = ""


@dataclass(frozen=True)
class ToolHookOutcome:
    args: dict[str, Any]
    decision: ToolPolicyDecision | None = None
    result: Any = None
    notes: tuple[str, ...] = ()


def _normalize_string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return tuple()
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                result.append(normalized)
    return tuple(result)


def _normalize_approval(value: object, default: str = "allow") -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _APPROVAL_MODES:
            return normalized
    return default


def _normalize_hook(raw: object) -> ToolPolicyHook | None:
    if not isinstance(raw, dict):
        return None
    event = str(raw.get("event") or "").strip().lower()
    if event not in _POLICY_EVENTS:
        return None
    hook_type = str(raw.get("type") or raw.get("hook_type") or "write_jsonl").strip().lower()
    if hook_type not in {"write_jsonl", "shell_command"}:
        return None
    path = str(raw.get("path") or DEFAULT_EVENT_LOG_PATH).strip() or DEFAULT_EVENT_LOG_PATH
    timeout_raw = raw.get("timeout_seconds", 10)
    try:
        timeout_seconds = max(1, int(timeout_raw or 10))
    except Exception:
        timeout_seconds = 10
    return ToolPolicyHook(
        event=event,
        hook_type=hook_type,
        path=path,
        include_args=bool(raw.get("include_args", True)),
        include_result=bool(raw.get("include_result", False)),
        command=str(raw.get("command") or "").strip(),
        timeout_seconds=timeout_seconds,
    )


def _normalize_hooks(value: object) -> tuple[ToolPolicyHook, ...]:
    if not isinstance(value, list):
        return tuple()
    hooks: list[ToolPolicyHook] = []
    for item in value:
        hook = _normalize_hook(item)
        if hook is not None:
            hooks.append(hook)
    return tuple(hooks)


def _normalize_rule(raw: object) -> ToolPolicyRule:
    if isinstance(raw, str):
        return ToolPolicyRule(approval=_normalize_approval(raw))
    if not isinstance(raw, dict):
        return ToolPolicyRule()
    approval = raw.get("approval")
    if approval is None:
        approval = raw.get("mode")
    normalized_approval = None
    if approval is not None:
        normalized_approval = _normalize_approval(approval)
    return ToolPolicyRule(
        approval=normalized_approval,
        content_allow_patterns=_normalize_string_tuple(
            raw.get("content_allow_patterns") or raw.get("command_allow_patterns")
        ),
        content_block_patterns=_normalize_string_tuple(
            raw.get("content_block_patterns") or raw.get("command_block_patterns")
        ),
        path_allow_patterns=_normalize_string_tuple(raw.get("path_allow_patterns")),
        path_block_patterns=_normalize_string_tuple(raw.get("path_block_patterns")),
        hooks=_normalize_hooks(raw.get("hooks")),
    )


def _normalize_policy(raw: object, *, source: str, definition_path: str | None) -> WeBotToolPolicy:
    if not isinstance(raw, dict):
        return WeBotToolPolicy(source=source, definition_path=definition_path)

    tools_raw = raw.get("tools")
    if not isinstance(tools_raw, dict):
        tools_raw = {}
    tools = {
        str(tool_name).strip(): _normalize_rule(rule)
        for tool_name, rule in tools_raw.items()
        if isinstance(tool_name, str) and tool_name.strip()
    }
    return WeBotToolPolicy(
        default_approval=_normalize_approval(raw.get("default_approval"), default="allow"),
        hooks=_normalize_hooks(raw.get("hooks")),
        tools=tools,
        source=source,
        definition_path=definition_path,
    )


def get_tool_policy(
    user_id: str | None,
    *,
    project_root: str | Path | None = None,
) -> WeBotToolPolicy:
    path = get_tool_policy_path(user_id, project_root=project_root)
    if path is None or not path.is_file():
        return WeBotToolPolicy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return WeBotToolPolicy(source="user", definition_path=str(path))
    return _normalize_policy(raw, source="user", definition_path=str(path))


def serialize_tool_policy(policy: WeBotToolPolicy) -> dict[str, Any]:
    return {
        "default_approval": policy.default_approval,
        "hooks": [asdict(hook) for hook in policy.hooks],
        "tools": {
            tool_name: {
                "approval": rule.approval,
                "content_allow_patterns": list(rule.content_allow_patterns),
                "content_block_patterns": list(rule.content_block_patterns),
                "path_allow_patterns": list(rule.path_allow_patterns),
                "path_block_patterns": list(rule.path_block_patterns),
                "hooks": [asdict(hook) for hook in rule.hooks],
            }
            for tool_name, rule in policy.tools.items()
        },
        "source": policy.source,
        "definition_path": policy.definition_path,
    }


def save_tool_policy_config(
    user_id: str,
    raw_policy: dict[str, Any],
    *,
    project_root: str | Path | None = None,
) -> Path:
    path = get_tool_policy_path(user_id, project_root=project_root)
    if path is None:
        raise ValueError("user_id is required")
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_policy(raw_policy or {}, source="user", definition_path=str(path))
    payload = serialize_tool_policy(normalized)
    payload.pop("source", None)
    payload.pop("definition_path", None)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _resolve_rule(policy: WeBotToolPolicy, tool_name: str) -> tuple[str, ToolPolicyRule | None]:
    if tool_name in policy.tools:
        return tool_name, policy.tools[tool_name]
    if "*" in policy.tools:
        return "*", policy.tools["*"]
    return "", None


def _extract_content_subject(tool_name: str, args: dict[str, Any]) -> str:
    arg_name = _CONTENT_ARG_NAMES.get(tool_name)
    if not arg_name:
        return ""
    value = args.get(arg_name)
    return value if isinstance(value, str) else ""


def _extract_path_subject(tool_name: str, args: dict[str, Any]) -> str:
    arg_name = _PATH_ARG_NAMES.get(tool_name)
    if not arg_name:
        return ""
    value = args.get(arg_name)
    return value if isinstance(value, str) else ""


def _matches_any(patterns: tuple[str, ...], value: str) -> bool:
    if not patterns or not value:
        return False
    for pattern in patterns:
        try:
            if re.search(pattern, value):
                return True
        except re.error:
            continue
    return False


def evaluate_tool_policy(
    policy: WeBotToolPolicy,
    tool_name: str,
    args: dict[str, Any] | None = None,
) -> ToolPolicyDecision:
    args = args or {}
    matched_rule, rule = _resolve_rule(policy, tool_name)
    approval = policy.default_approval
    if rule and rule.approval:
        approval = rule.approval

    content_subject = _extract_content_subject(tool_name, args)
    path_subject = _extract_path_subject(tool_name, args)

    if rule is not None:
        if _matches_any(rule.content_block_patterns, content_subject):
            return ToolPolicyDecision(
                allowed=False,
                reason=f"工具策略阻止了 {tool_name} 的内容模式。",
                matched_rule=matched_rule,
            )
        if _matches_any(rule.path_block_patterns, path_subject):
            return ToolPolicyDecision(
                allowed=False,
                reason=f"工具策略阻止了 {tool_name} 访问该路径。",
                matched_rule=matched_rule,
            )
        if approval == "manual":
            if rule.content_allow_patterns and not _matches_any(rule.content_allow_patterns, content_subject):
                return ToolPolicyDecision(
                    allowed=False,
                    reason=f"工具策略未批准 {tool_name} 当前内容。",
                    matched_rule=matched_rule,
                )
            if rule.path_allow_patterns and not _matches_any(rule.path_allow_patterns, path_subject):
                return ToolPolicyDecision(
                    allowed=False,
                    reason=f"工具策略未批准 {tool_name} 当前路径。",
                    matched_rule=matched_rule,
                )

    if approval == "deny":
        return ToolPolicyDecision(
            allowed=False,
            reason=f"工具 {tool_name} 已被当前策略明确禁用。",
            matched_rule=matched_rule,
        )
    if approval == "manual":
        return ToolPolicyDecision(
            allowed=False,
            requires_approval=True,
            reason=f"工具 {tool_name} 需要人工批准。",
            matched_rule=matched_rule,
        )
    return ToolPolicyDecision(allowed=True, matched_rule=matched_rule)


def _resolve_hook_path(user_id: str, path: str, *, project_root: str | Path | None = None) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    user_dir = _user_dir(user_id, project_root=project_root)
    target = user_dir / candidate
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _truncate_text(value: Any, limit: int = 2000) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated {len(text)} chars]"


def _coerce_hook_decision(raw: object, fallback: ToolPolicyDecision | None) -> ToolPolicyDecision | None:
    if raw is None:
        return fallback
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized == "allow":
            return ToolPolicyDecision(allowed=True)
        if normalized == "deny":
            return ToolPolicyDecision(allowed=False, reason="Hook denied the tool call.")
        if normalized == "ask":
            return ToolPolicyDecision(
                allowed=False,
                requires_approval=True,
                reason="Hook requested manual approval.",
            )
    if isinstance(raw, dict):
        mode = str(raw.get("decision") or raw.get("action") or "").strip().lower()
        reason = str(raw.get("reason") or "").strip()
        if mode == "allow":
            return ToolPolicyDecision(allowed=True, reason=reason)
        if mode == "deny":
            return ToolPolicyDecision(allowed=False, reason=reason)
        if mode == "ask":
            return ToolPolicyDecision(allowed=False, requires_approval=True, reason=reason)
    return fallback


def _run_shell_hook(
    hook: ToolPolicyHook,
    payload: dict[str, Any],
    current_args: dict[str, Any],
    current_decision: ToolPolicyDecision | None,
    current_result: Any,
) -> ToolHookOutcome:
    if not hook.command:
        return ToolHookOutcome(args=current_args, decision=current_decision, result=current_result)
    proc = subprocess.run(
        hook.command,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        shell=True,
        timeout=hook.timeout_seconds,
        check=False,
    )
    stdout = (proc.stdout or "").strip()
    if proc.returncode != 0 or not stdout:
        return ToolHookOutcome(
            args=current_args,
            decision=current_decision,
            result=current_result,
            notes=(f"hook_exit={proc.returncode}",),
        )
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError:
        return ToolHookOutcome(
            args=current_args,
            decision=current_decision,
            result=current_result,
            notes=("hook_invalid_json",),
        )
    updated_args = dict(current_args)
    if isinstance(response.get("updated_args"), dict):
        updated_args.update(response["updated_args"])
    updated_result = response.get("updated_result", current_result)
    updated_decision = _coerce_hook_decision(response.get("decision"), current_decision)
    notes: list[str] = []
    message = str(response.get("message") or "").strip()
    if message:
        notes.append(message)
    return ToolHookOutcome(
        args=updated_args,
        decision=updated_decision,
        result=updated_result,
        notes=tuple(notes),
    )


def run_tool_policy_hooks(
    policy: WeBotToolPolicy,
    *,
    event: str,
    user_id: str,
    session_id: str,
    tool_name: str,
    args: dict[str, Any] | None = None,
    decision: ToolPolicyDecision | None = None,
    result: Any = None,
    project_root: str | Path | None = None,
) -> ToolHookOutcome:
    if event not in _POLICY_EVENTS:
        return ToolHookOutcome(args=args or {}, decision=decision, result=result)

    _, rule = _resolve_rule(policy, tool_name)
    hooks = list(policy.hooks)
    if rule is not None:
        hooks.extend(rule.hooks)
    if not hooks:
        return ToolHookOutcome(args=args or {}, decision=decision, result=result)

    current_args = dict(args or {})
    current_decision = decision
    current_result = result
    notes: list[str] = []
    payload_base = {
        "timestamp": _utc_now(),
        "event": event,
        "user_id": user_id,
        "session_id": session_id,
        "tool_name": tool_name,
        "allowed": True if current_decision is None else current_decision.allowed,
        "requires_approval": False if current_decision is None else current_decision.requires_approval,
        "reason": "" if current_decision is None else current_decision.reason,
        "matched_rule": "" if current_decision is None else current_decision.matched_rule,
    }

    for hook in hooks:
        if hook.event != event:
            continue
        payload = dict(payload_base)
        if hook.include_args:
            payload["args"] = {
                key: _truncate_text(value, 500)
                for key, value in current_args.items()
            }
        if hook.include_result and current_result is not None:
            payload["result"] = _truncate_text(current_result)
        if hook.hook_type == "write_jsonl":
            target = _resolve_hook_path(user_id, hook.path, project_root=project_root)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            continue
        if hook.hook_type == "shell_command":
            outcome = _run_shell_hook(
                hook,
                payload,
                current_args,
                current_decision,
                current_result,
            )
            current_args = outcome.args
            current_decision = outcome.decision
            current_result = outcome.result
            notes.extend(outcome.notes)
    return ToolHookOutcome(
        args=current_args,
        decision=current_decision,
        result=current_result,
        notes=tuple(notes),
    )
