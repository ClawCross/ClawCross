"""Local conductor for keeping remote Claude workers moving.

The conductor is intentionally conservative: it sends short continuation or
assignment messages to remote workers, and it refuses to auto-approve risky
requests. Dashboard remains the task board; this module is the ClawCross-side
control loop.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from harness.dashboard_sync import (
    HOST_VERIFIED_COMMENT_KIND,
    has_result_comment,
    import_dashboard_todos,
    publish_dashboard_tasks,
    requires_machine_verifier,
    sync_dashboard_to_supabase,
    sync_harness_to_dashboard,
    task_has_host_verification,
)
from harness.store import apply_harness_event, get_harness_state
from integrations.remote_claude_agents import (
    close_remote_claude_session,
    list_remote_claude_sessions,
    rename_remote_claude_session,
    send_remote_claude_message,
)
from utils.runtime_paths import DATA_DIR, ENV_FILE


CONDUCTOR_AGENT_ID = "clawcross-main@local"
DEFAULT_COOLDOWN_SECONDS = 180
DEFAULT_REMOTE_LIMIT = 12
DEFAULT_PROJECT_ID = os.getenv("CLAWCROSS_HARNESS_PROJECT_ID", "").strip()
TERMINAL_PROJECT_STATUSES = {"done", "completed", "closed", "archived", "cancelled"}

RISKY_PATTERNS = (
    r"\bsudo\b",
    r"\brm\s+-[^\n]*[rf]",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bchmod\s+777\b",
    r"\bchown\s+.*\s+/",
    r"\bcurl\b[^\n|;&]*\|\s*(?:sh|bash|zsh|python)",
    r"\bwget\b[^\n|;&]*\|\s*(?:sh|bash|zsh|python)",
    r"\bssh-keygen\b",
    r"\bssh-add\b",
    r"\bpasswd\b",
    r"\bsecurity\s+find-generic-password\b",
    r"\bopen\s+.*keychain\b",
    r"\b\.ssh\b",
    r"\b\.aws\b",
    r"\b\.config/gcloud\b",
    r"(?<![\w.-])\.env(?:\.[A-Za-z0-9_-]+)?(?![\w-])",
    r"\bapi[_-]?key\b",
    r"\bsecret\b",
    r"\btoken\b",
    r"外传",
    r"上传.*(?:密钥|token|secret|日志)",
)

WAITING_PATTERNS = (
    r"需要.*(?:确认|批准|同意|输入|用户|拍板)",
    r"(?:permission|approval|approve|confirm|confirmation|required|waiting for user)",
    r"(?:prompt injection|untrusted|unsafe|user confirmation)",
    r"(?:是否|可以|能否).*(?:继续|执行|运行|安装|读取)",
    r"等用户",
    r"needs[_ -]?user",
)

SAFE_ALLOW_TEXT = (
    "允许安全读取 dashboard/TODO/ClawCross harness 状态，允许在当前 worktree 内读写任务相关文件，"
    "允许运行项目验证、评测、git status/diff/log、pytest/npm test 等非破坏性命令。"
    "禁止 sudo、删除系统/密钥、读取或外传 secrets、curl|sh/wget|sh。"
)

LLM_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
OPENCLAW_GATEWAY_BASE_URL = "http://127.0.0.1:18789/v1"
CLAWCROSS_AGENT_COMPLETIONS_URL = "http://127.0.0.1:51200/v1/chat/completions"
CODEX_REVIEW_ACTIONS = {"accept", "reopen", "needs_user", "skip"}
CODEX_REVIEW_STATUSES = {"active", "blocked", "needs_user", "review"}


def _env_truthy(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_csv_set(key: str) -> set[str]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return set()
    return {item.strip() for item in raw.replace(";", ",").split(",") if item.strip()}


def _env_json_mapping(key: str) -> dict[str, str]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in value.items() if str(k).strip() and str(v).strip()}


def _env_or_file_value(key: str) -> str:
    value = os.getenv(key, "").strip()
    if value:
        return value
    try:
        for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if not raw_line.startswith(f"{key}="):
                continue
            return raw_line.split("=", 1)[1].strip().strip("'\"")
    except Exception:
        return ""
    return ""


def _call_clawcross_agent_json(prompt: str) -> dict[str, Any]:
    """Ask the local ClawCross Webot agent endpoint for a JSON decision."""

    import urllib.error
    import urllib.request

    token = _env_or_file_value("INTERNAL_TOKEN")
    if not token:
        return {"error": "INTERNAL_TOKEN is not configured"}
    user_id = (
        os.getenv("CLAWCROSS_HARNESS_USER")
        or os.getenv("CLAWCROSS_USER_ID")
        or os.getenv("USER")
        or "system"
    )
    url = os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_CLAWCROSS_AGENT_URL", CLAWCROSS_AGENT_COMPLETIONS_URL).strip()
    payload = {
        "model": os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_CLAWCROSS_AGENT_MODEL", "webot").strip() or "webot",
        "user": user_id,
        "session_id": os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_CLAWCROSS_AGENT_SESSION", "harness-conductor"),
        "messages": [
            {
                "role": "user",
                "content": (
                    "你是 ClawCross harness conductor 的本机 Webot 决策接口。"
                    "必须只返回一个 JSON 对象，不要解释，不要 Markdown。\n\n"
                    + prompt
                ),
            }
        ],
        "max_tokens": 1600,
        "enabled_tools": [],
        "max_turns": 1,
        "stream": False,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}:{user_id}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"error": f"clawcross agent HTTP {exc.code}: {body[:500]}"}
    except Exception as exc:
        return {"error": str(exc)}
    try:
        data = json.loads(body)
    except Exception:
        return {"error": f"clawcross agent returned non-JSON HTTP body: {body[:500]}"}
    choices = data.get("choices") if isinstance(data, dict) else None
    message = (choices or [{}])[0].get("message") if isinstance(choices, list) and choices else {}
    content = message.get("content") if isinstance(message, dict) else ""
    parsed = _parse_llm_json(str(content or ""))
    if not parsed:
        return {"error": f"clawcross agent returned no JSON object: {str(content or '')[:500]}"}
    parsed.setdefault("_llm_source", "clawcross_agent")
    return parsed


def _json_preview(value: Any, limit: int = 1600) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "..."


def _parse_llm_json(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except Exception:
        match = LLM_JSON_RE.search(raw)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def _call_webot_llm_json(prompt: str) -> dict[str, Any]:
    """Ask the configured Webot/ClawCross LLM for a bounded JSON decision."""

    conductor_model = os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_LLM_MODEL", "").strip()
    conductor_api_key = os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_LLM_API_KEY", "").strip()
    conductor_base_url = os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_LLM_BASE_URL", "").strip()
    conductor_provider = os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_LLM_PROVIDER", "").strip()
    attempts: list[tuple[str, dict[str, str]]] = []
    errors: list[str] = []
    if _env_truthy("CLAWCROSS_HARNESS_CONDUCTOR_CLAWCROSS_AGENT", True):
        result = _call_clawcross_agent_json(prompt)
        if not result.get("error"):
            return result
        errors.append(f"clawcross_agent: {result.get('error')}")
    if conductor_model or conductor_api_key or conductor_base_url or conductor_provider:
        attempts.append(
            (
                "conductor_override",
                {
                    "model": conductor_model,
                    "api_key": conductor_api_key,
                    "base_url": conductor_base_url,
                    "provider": conductor_provider,
                },
            )
        )
    if os.getenv("LLM_MODEL", "").strip():
        attempts.append(("webot_env", {}))
    if _env_truthy("CLAWCROSS_HARNESS_CONDUCTOR_OPENCLAW_FALLBACK", False):
        attempts.append(
            (
                "openclaw_gateway",
                {
                    "model": os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_OPENCLAW_MODEL", "openclaw").strip() or "openclaw",
                    "api_key": os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_OPENCLAW_API_KEY", "openclaw").strip() or "openclaw",
                    "base_url": os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_OPENCLAW_BASE_URL", OPENCLAW_GATEWAY_BASE_URL).strip()
                    or OPENCLAW_GATEWAY_BASE_URL,
                    "provider": "openai",
                },
            )
        )

    try:
        from services.llm_factory import create_chat_model, extract_text

        for source, overrides in attempts or [("webot_env", {})]:
            kwargs = {k: v for k, v in overrides.items() if v}
            try:
                llm = create_chat_model(temperature=0.1, max_tokens=1400, timeout=45, **kwargs)
                response = llm.invoke(prompt)
                content = response.content if hasattr(response, "content") else str(response)
                parsed = _parse_llm_json(extract_text(content))
                parsed.setdefault("_llm_source", source)
                return parsed
            except Exception as exc:
                errors.append(f"{source}: {exc}")
    except Exception as exc:
        errors.append(str(exc))
    return {"error": "; ".join(errors) or "webot llm unavailable"}


def _llm_choose_assignment(
    agent: dict[str, Any],
    session: dict[str, Any],
    candidate_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    if not candidate_tasks:
        return {}
    prompt = f"""
你是 ClawCross 本机 conductor 的 Webot LLM 决策层。你只做任务调度，不执行 shell。

目标：给远端 worker 选择一个最合适的 TODO，并写一段要发给该 worker 的中文指令。
硬约束：
- 只能选择候选 TODO 里的 task_id。
- 只能分配给同一 project 的任务。
- 指令必须要求 worker 先用 clawcross-harness-agent 标记 doing/active，并持续写 comment/done/needs_user。
- 实验/评测/推理任务必须要求 run_id、git_sha、命令、日志/metrics 路径和 verifier。
- 不要批准 sudo、删除系统文件、读取/外传 secrets、curl|sh/wget|sh。
- 只返回 JSON，不要 Markdown。

worker:
{_json_preview(agent)}

session:
{_json_preview({k: session.get(k) for k in ("display_id", "remote_key", "status", "title", "cwd", "last_message")})}

候选 TODO:
{_json_preview([{k: t.get(k) for k in ("task_id", "project_id", "title", "description", "priority", "due_at", "status")} for t in candidate_tasks], 6000)}

返回格式：
{{"task_id":"...","message":"...","reason":"..."}}
""".strip()
    result = _call_webot_llm_json(prompt)
    if result.get("error"):
        return {"decision_source": "rules_fallback", "llm_error": str(result.get("error") or "")[:800]}
    task_ids = {str(task.get("task_id") or "") for task in candidate_tasks}
    task_id = str(result.get("task_id") or "").strip()
    if task_id not in task_ids:
        return {"decision_source": "rules_fallback", "llm_error": "webot llm returned no valid candidate task_id"}
    message = str(result.get("message") or "").strip()
    if not message:
        return {
            "task_id": task_id,
            "reason": str(result.get("reason") or "webot selected task"),
            "decision_source": "webot_llm",
            "llm_source": result.get("_llm_source") or "",
        }
    return {
        "task_id": task_id,
        "message": message[:5000],
        "reason": str(result.get("reason") or "webot selected task")[:800],
        "decision_source": "webot_llm",
        "llm_source": result.get("_llm_source") or "",
    }


def _llm_refine_decision(decision: ConductorDecision, session: dict[str, Any], state: dict[str, Any]) -> ConductorDecision:
    task = _task_by_id(state, decision.task_id) or {}
    agent = next((a for a in state.get("agents", []) if a.get("agent_id") == decision.agent_id), {})
    prompt = f"""
你是 ClawCross 本机 conductor 的 Webot LLM 决策层。你要决定如何回复一个已绑定 TODO 的远端 worker。

硬约束：
- 如果远端消息要求危险操作（sudo、rm -rf、密钥、外传 secret、curl|sh/wget|sh），返回 action=needs_user。
- 否则返回 action=send，并写一段简短中文回复，让 worker 继续当前 TODO。
- 回复必须提醒用 clawcross-harness-agent 更新状态/comment/done/needs_user。
- 不要虚构结果，不要说任务完成，除非 worker 已给出 verifier/result 证据。
- 只返回 JSON，不要 Markdown。

默认规则回复:
{decision.message}

decision:
{_json_preview(decision.__dict__)}

agent:
{_json_preview(agent)}

task:
{_json_preview({k: task.get(k) for k in ("task_id", "project_id", "title", "description", "status", "comments")}, 5000)}

session:
{_json_preview({k: session.get(k) for k in ("display_id", "remote_key", "status", "title", "cwd", "last_message")}, 3000)}

返回格式：
{{"action":"send|needs_user","message":"...","reason":"..."}}
""".strip()
    result = _call_webot_llm_json(prompt)
    if result.get("error"):
        return replace(
            decision,
            reason=(decision.reason + f"; webot llm fallback: {str(result.get('error') or '')[:400]}")[:800],
            decision_source="rules_fallback",
        )
    action = str(result.get("action") or "").strip().lower()
    if action == "needs_user":
        return replace(
            decision,
            should_send=False,
            manual_review=True,
            reason=str(result.get("reason") or "webot requested manual review")[:800],
            message="",
            decision_source="webot_llm",
        )
    if action == "send" and str(result.get("message") or "").strip():
        return replace(
            decision,
            message=str(result.get("message") or "").strip()[:5000],
            reason=str(result.get("reason") or decision.reason)[:800],
            decision_source="webot_llm",
        )
    return decision


@dataclass(frozen=True)
class ConductorDecision:
    should_send: bool
    session_key: str
    agent_id: str
    task_id: str
    project_id: str
    reason: str
    message: str = ""
    manual_review: bool = False
    cache_key: str = ""
    decision_source: str = "rules"


def _now_ts() -> float:
    return datetime.now().timestamp()


def _cache_path() -> Path:
    explicit = os.getenv("CLAWCROSS_HARNESS_CONDUCTOR_CACHE", "").strip()
    return Path(explicit).expanduser() if explicit else DATA_DIR / "harness_conductor_actions.json"


def load_action_cache() -> dict[str, Any]:
    path = _cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "sent": {}}
    if not isinstance(data, dict):
        return {"version": 1, "sent": {}}
    if not isinstance(data.get("sent"), dict):
        data["sent"] = {}
    return data


def save_action_cache(cache: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def session_key(session: dict[str, Any]) -> str:
    for key in ("remote_key", "display_id", "bridge_session_id", "session_id", "id", "job_id"):
        value = str(session.get(key) or "").strip()
        if value:
            return value
    return ""


def _equivalent_session_refs(value: Any) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    refs = {raw}
    if "::" in raw:
        refs.add(raw.rsplit("::", 1)[-1].strip())
    return {ref for ref in refs if ref}


def session_keys(session: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for key in ("remote_key", "display_id", "bridge_session_id", "session_id", "id", "job_id"):
        refs.update(_equivalent_session_refs(session.get(key)))
    remote_key = str(session.get("remote_key") or "").strip()
    remote_prefix = remote_key.rsplit("::", 1)[0].strip() if "::" in remote_key else ""
    if remote_prefix:
        refs.update(
            f"{remote_prefix}::{ref}"
            for ref in list(refs)
            if ref and "::" not in ref
        )
    return refs


def agent_session_refs(agent: dict[str, Any]) -> set[str]:
    return {
        ref
        for key in ("session_ref", "bridge_session_id", "session_id", "job_id")
        for ref in _equivalent_session_refs(agent.get(key))
    }


def last_message_text(session: dict[str, Any]) -> str:
    message = session.get("last_message")
    if isinstance(message, dict):
        return str(message.get("content") or "").strip()
    return ""


def last_message_role(session: dict[str, Any]) -> str:
    message = session.get("last_message")
    if isinstance(message, dict):
        return str(message.get("role") or "").strip().lower()
    return ""


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered, re.IGNORECASE | re.DOTALL) for pattern in patterns)


def is_risky_request(text: str) -> bool:
    return _contains_any(text, RISKY_PATTERNS)


def looks_waiting_for_input(text: str) -> bool:
    return _contains_any(text, WAITING_PATTERNS)


def looks_like_active_tool_command(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    has_command_key = "'command':" in lowered or '"command":' in lowered
    has_tool_metadata = any(
        marker in lowered
        for marker in (
            "'description':",
            '"description":',
            "'timeout':",
            '"timeout":',
            "'stdout':",
            '"stdout":',
            "'stderr':",
            '"stderr":',
        )
    )
    return has_command_key and (has_tool_metadata or lowered.startswith(("{'command':", '{"command":')))


def _fingerprint(*parts: Any) -> str:
    body = "\n".join(str(part or "") for part in parts)
    return sha256(body.encode("utf-8", errors="replace")).hexdigest()[:20]


def _agent_for_session(session: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    keys = session_keys(session)
    if not keys:
        return None
    for agent in state.get("agents", []) or []:
        if agent_session_refs(agent) & keys:
            return agent
    return None


def _agent_clawcross_metadata(agent: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(agent, dict):
        return {}
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
    clawcross = metadata.get("clawcross") if isinstance(metadata.get("clawcross"), dict) else {}
    return clawcross


def _agent_is_survey_pool(agent: dict[str, Any] | None) -> bool:
    clawcross = _agent_clawcross_metadata(agent)
    capabilities = agent.get("capabilities") if isinstance(agent, dict) and isinstance(agent.get("capabilities"), list) else []
    return bool(clawcross.get("survey_pool")) or "survey-pool" in {str(item) for item in capabilities}


def _survey_project_ids_from_state(state: dict[str, Any]) -> set[str]:
    survey_statuses = _env_csv_set("CLAWCROSS_HARNESS_SURVEY_PROJECT_STATUSES") or {"survey"}
    project_ids = set(_env_csv_set("CLAWCROSS_HARNESS_SURVEY_PROJECT_IDS"))
    for project in state.get("projects", []) or []:
        if not isinstance(project, dict):
            continue
        status = str(project.get("status") or "").strip().lower().replace("-", "_")
        project_id = str(project.get("project_id") or "").strip()
        if project_id and status in survey_statuses:
            project_ids.add(project_id)
    return project_ids


def _agent_supported_project_ids(agent: dict[str, Any] | None, state: dict[str, Any] | None = None) -> set[str]:
    if not isinstance(agent, dict):
        return set()
    project_ids = {str(agent.get("project_id") or "").strip()}
    clawcross = _agent_clawcross_metadata(agent)
    for key in ("project_ids", "accepted_project_ids", "survey_pool_project_ids"):
        values = clawcross.get(key)
        if isinstance(values, list):
            project_ids.update(str(item or "").strip() for item in values)
    if _agent_is_survey_pool(agent):
        project_ids.update(_survey_project_ids_from_state(state or {}))
    return {project_id for project_id in project_ids if project_id}


def _clean_agent_id_part(value: Any) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.:@-]+", "-", str(value or "").strip()).strip("-")
    return clean or "worker"


def _split_remote_host(value: Any) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        return "", ""
    if REMOTE_KEY_SEPARATOR in text:
        text = text.split(REMOTE_KEY_SEPARATOR, 1)[0]
    if "@" in text:
        user, host = text.rsplit("@", 1)
        return user.strip(), host.strip()
    return "", text


REMOTE_KEY_SEPARATOR = "::"


def _session_remote_identity(session: dict[str, Any]) -> tuple[str, str]:
    remote = session.get("remote") if isinstance(session.get("remote"), dict) else {}
    user = str(session.get("remote_user") or session.get("user") or remote.get("user") or "").strip()
    host = str(session.get("remote_host") or session.get("host") or remote.get("host") or "").strip()
    if not user or not host:
        key_user, key_host = _split_remote_host(session.get("remote_key"))
        user = user or key_user
        host = host or key_host
    if "@" in host:
        host_user, host_value = _split_remote_host(host)
        user = user or host_user
        host = host_value
    return user, host


def _remote_identity_keys(user: str, host: str) -> set[str]:
    user = str(user or "").strip()
    host = str(host or "").strip()
    keys = {host} if host else set()
    if user and host:
        keys.add(f"{user}@{host}")
    return {key for key in keys if key}


def _project_by_remote_host(state: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for agent in state.get("agents", []) or []:
        if not isinstance(agent, dict):
            continue
        project_id = str(agent.get("project_id") or "").strip()
        if not project_id:
            continue
        user, host = _split_remote_host(agent.get("remote_host"))
        for key in _remote_identity_keys(user, host):
            mapping.setdefault(key, project_id)
    return mapping


def _project_for_unbound_session(session: dict[str, Any], state: dict[str, Any], *, project_id: str) -> str:
    if project_id:
        return project_id
    user, host = _session_remote_identity(session)
    mapping = _project_by_remote_host(state)
    for key in _remote_identity_keys(user, host):
        if key in mapping:
            return mapping[key]
    return ""


def _agent_for_unbound_session(session: dict[str, Any], state: dict[str, Any], *, project_id: str) -> dict[str, Any] | None:
    if not _env_truthy("CLAWCROSS_HARNESS_AUTOBIND_UNBOUND_SESSIONS"):
        return None
    session_status = str(session.get("status") or "").lower()
    if session_status not in {"idle", "done", "completed", "shell"}:
        return None
    key = session_key(session)
    if not key:
        return None
    resolved_project_id = _project_for_unbound_session(session, state, project_id=project_id)
    if not resolved_project_id:
        return None
    user, host = _session_remote_identity(session)
    remote_host = f"{user}@{host}" if user and host else host
    short = _fingerprint(key)[:8]
    agent_id = f"{_clean_agent_id_part(resolved_project_id)}-{_clean_agent_id_part(user or 'remote')}@{_clean_agent_id_part(host or 'local')}-{short}"
    return {
        "agent_id": agent_id,
        "agent_type": "claude-code-worker",
        "project_id": resolved_project_id,
        "current_task_id": "",
        "session_ref": key,
        "remote_host": remote_host,
        "status": "idle",
        "needs_user": False,
    }


def _task_for_agent(agent: dict[str, Any] | None, state: dict[str, Any]) -> dict[str, Any] | None:
    if not agent:
        return None
    task_id = str(agent.get("current_task_id") or "").strip()
    if not task_id:
        return None
    for task in state.get("tasks", []) or []:
        if str(task.get("task_id") or "") == task_id:
            return task
    return None


def _task_by_id(state: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for task in state.get("tasks", []) or []:
        if str(task.get("task_id") or "") == task_id:
            return task
    return None


def _project_by_id(state: dict[str, Any], project_id: str) -> dict[str, Any] | None:
    for project in state.get("projects", []) or []:
        if str(project.get("project_id") or "") == project_id:
            return project
    return None


def _project_is_active(state: dict[str, Any], project_id: str) -> bool:
    if not project_id:
        return False
    project = _project_by_id(state, project_id)
    if not project:
        return False
    status = str(project.get("status") or "active").strip().lower().replace("-", "_")
    return status not in TERMINAL_PROJECT_STATUSES


def _session_counts_by_project(sessions: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for session in sessions:
        agent = _agent_for_session(session, state)
        if not agent:
            continue
        project_id = str(agent.get("project_id") or "").strip()
        key = session_key(session)
        if not project_id or not key or key in seen:
            continue
        seen.add(key)
        counts[project_id] = counts.get(project_id, 0) + 1
    return counts


def _compact_label(value: Any, *, limit: int = 34) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    text = re.sub(r"[^\w .:+/@-]+", "", text, flags=re.ASCII)
    text = text.strip(" .")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip(" .") + "..."


def _project_label(project_id: str) -> str:
    project_id = str(project_id or "").strip()
    if not project_id:
        return "Project"
    return _env_json_mapping("CLAWCROSS_HARNESS_PROJECT_LABELS").get(
        project_id,
        _compact_label(project_id.replace("-", " ").title(), limit=34),
    )


def expected_remote_session_name(agent: dict[str, Any], task: dict[str, Any] | None, session: dict[str, Any]) -> str:
    project_id = str((task or {}).get("project_id") or agent.get("project_id") or "").strip()
    user, host = _session_remote_identity(session)
    remote = session.get("remote") if isinstance(session.get("remote"), dict) else {}
    hostname = str(session.get("remote_hostname") or remote.get("hostname") or "").strip()
    remote_label = _compact_label(hostname, limit=22) or user or _compact_label(host, limit=16) or _compact_label(agent.get("remote_host"), limit=16)
    task_label = _compact_label((task or {}).get("title") or agent.get("current_task_id"), limit=32)
    project_label = "Survey Pool" if _agent_is_survey_pool(agent) else _project_label(project_id)
    parts = ["ClawCross", project_label]
    if remote_label:
        parts.append(_compact_label(remote_label, limit=16))
    if task_label:
        parts.append(task_label)
    return " | ".join(part for part in parts if part)[:120]


def _task_paused_by_user(task: dict[str, Any] | None) -> bool:
    if not isinstance(task, dict):
        return False
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    clawcross_meta = metadata.get("clawcross") if isinstance(metadata.get("clawcross"), dict) else {}
    return bool(clawcross_meta.get("paused_by_user"))


def _has_host_verified_comment(task: dict[str, Any]) -> bool:
    for comment in task.get("comments", []) or []:
        if isinstance(comment, dict) and str(comment.get("kind") or "") == HOST_VERIFIED_COMMENT_KIND:
            return True
    return False


def verify_finished_tasks(user_id: str, *, project_id: str = DEFAULT_PROJECT_ID) -> dict[str, Any]:
    """Host-side acceptance gate for tasks reported as done by workers."""

    state = get_harness_state(user_id)
    runs = [run for run in state.get("runs", []) if isinstance(run, dict)]
    accepted = 0
    moved_to_review = 0
    skipped = 0
    for task in state.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        if project_id and task.get("project_id") != project_id:
            skipped += 1
            continue
        task_status = str(task.get("status") or "").lower()
        if _has_host_verified_comment(task):
            if task_status != "done":
                apply_harness_event(
                    user_id,
                    {
                        "action": "task_status",
                        "agent_id": CONDUCTOR_AGENT_ID,
                        "project_id": task.get("project_id") or project_id,
                        "task_id": task.get("task_id"),
                        "status": "done",
                        "message": "Host verification already exists; restoring TODO to done.",
                    },
                )
                accepted += 1
                continue
            skipped += 1
            continue
        if task_status != "done":
            skipped += 1
            continue

        verified_by_run = task_has_host_verification(task, runs)
        acceptable_result = has_result_comment(task) and not requires_machine_verifier(task)
        if verified_by_run or acceptable_result:
            reason = (
                "verified run with passed verifier"
                if verified_by_run
                else "decision/result task has a non-empty result comment and does not require a machine verifier"
            )
            apply_harness_event(
                user_id,
                {
                    "action": "task_comment",
                    "agent_id": CONDUCTOR_AGENT_ID,
                    "project_id": task.get("project_id") or project_id,
                    "task_id": task.get("task_id"),
                    "kind": HOST_VERIFIED_COMMENT_KIND,
                    "message": f"主机验收通过: {reason}.",
                },
            )
            accepted += 1
            continue

        apply_harness_event(
            user_id,
            {
                "action": "task_status",
                "agent_id": CONDUCTOR_AGENT_ID,
                "project_id": task.get("project_id") or project_id,
                "task_id": task.get("task_id"),
                "status": "review",
                "message": "主机验收未通过自动门槛：缺少 verified run/verifier 或可接受的 result comment，保持 review 等待补证据。",
            },
        )
        moved_to_review += 1
    return {"accepted": accepted, "moved_to_review": moved_to_review, "skipped": skipped}


def _task_review_fingerprint(task: dict[str, Any]) -> str:
    comments = [comment for comment in task.get("comments", []) or [] if isinstance(comment, dict)]
    last = comments[-1] if comments else {}
    return _fingerprint(
        task.get("task_id"),
        task.get("project_id"),
        task.get("status"),
        task.get("updated_at"),
        task.get("run_id"),
        len(comments),
        last.get("kind"),
        last.get("created_at"),
        last.get("body") or last.get("message"),
    )


def _review_task_candidates(state: dict[str, Any], *, project_id: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task in state.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        if project_id and task.get("project_id") != project_id:
            continue
        if str(task.get("status") or "").lower() != "review":
            continue
        if _has_host_verified_comment(task):
            continue
        tasks.append(task)
    return sorted(tasks, key=lambda item: (str(item.get("updated_at") or ""), str(item.get("task_id") or "")))


def _codex_review_prompt(task: dict[str, Any], state: dict[str, Any], sessions: list[dict[str, Any]]) -> str:
    task_id = str(task.get("task_id") or "")
    project_id = str(task.get("project_id") or "")
    related_agents = [
        agent
        for agent in state.get("agents", []) or []
        if isinstance(agent, dict)
        and (
            str(agent.get("current_task_id") or "") == task_id
            or str(agent.get("project_id") or "") == project_id
        )
    ]
    related_sessions = [
        {k: session.get(k) for k in ("remote_key", "display_id", "job_id", "title", "status", "cwd", "last_message", "remote_user", "remote_host")}
        for session in sessions
        if isinstance(session, dict)
        and (
            project_id.lower() in str(session.get("title") or "").lower()
            or any(agent_session_refs(agent) & session_keys(session) for agent in related_agents)
        )
    ]
    dashboard_root = (
        os.getenv("CLAWCROSS_DASHBOARD_ROOT")
        or os.getenv("DASHBOARD_ROOT")
        or "<configured dashboard root>"
    )
    clawcross_repo = Path(__file__).resolve().parents[2]
    return f"""
你是本机 Codex reviewer，由 ClawCross harness conductor 调用来处理 dashboard 里 `待你审查/review` 的 TODO。

目标：判断这个 review TODO 能否由本机自动验收，或应该退回远端 worker 继续处理。

硬约束：
- 你可以检查本机文件、dashboard、harness state，也可以通过已有 SSH 连接读取远端 worker 工作区并运行非破坏性验证命令。
- 不要修改代码、dashboard、harness state 或远端文件；不要提交 git；不要安装依赖；不要调用商业付费 API；不要访问或外传 secrets。
- 只有证据足够时才 accept。实验/评测/推理/benchmark/code 任务必须有可复现命令、结果文件、测试/verifier 或明确的 blocker。
- 如果 worker 结果不满足 TODO 范围，返回 reopen，并说明下一步要 worker 做什么。
- 如果需要用户提供密钥/账号/硬件/真实安全决策，返回 needs_user。
- 如果无法判断，返回 skip。
- 只返回一个 JSON 对象，不要 Markdown，不要解释性正文。

JSON 格式：
{{
  "action": "accept|reopen|needs_user|skip",
  "confidence": 0.0,
  "summary": "一句话结论",
  "evidence": ["检查过的文件/命令/指标"],
  "commands": ["实际运行过的验证命令"],
  "reason": "为什么这么判定",
  "new_status": "active|blocked|needs_user|review",
  "worker_message": "如果 reopen/needs_user，要发给 worker 或展示给用户的下一步"
}}

当前 TODO:
{_json_preview({k: task.get(k) for k in ("task_id", "project_id", "title", "description", "status", "priority", "due_at", "updated_at", "comments", "run_id")}, 9000)}

相关 agents:
{_json_preview(related_agents, 5000)}

相关远端 sessions:
{_json_preview(related_sessions, 5000)}

Dashboard state path: {dashboard_root}/state/tasks.json
ClawCross repo path: {clawcross_repo}
""".strip()


def _call_local_codex_review(task: dict[str, Any], state: dict[str, Any], sessions: list[dict[str, Any]]) -> dict[str, Any]:
    binary = (os.getenv("CLAWCROSS_HARNESS_CODEX_BINARY") or "codex").strip()
    timeout_raw = os.getenv("CLAWCROSS_HARNESS_CODEX_REVIEW_TIMEOUT_SEC") or "900"
    try:
        timeout = max(30.0, float(timeout_raw))
    except ValueError:
        timeout = 900.0
    sandbox = (os.getenv("CLAWCROSS_HARNESS_CODEX_REVIEW_SANDBOX") or "read-only").strip() or "read-only"
    model = (os.getenv("CLAWCROSS_HARNESS_CODEX_REVIEW_MODEL") or "").strip()
    profile = (os.getenv("CLAWCROSS_HARNESS_CODEX_REVIEW_PROFILE") or "").strip()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(tempfile.NamedTemporaryFile(prefix="codex-review-", suffix=".txt", dir=DATA_DIR, delete=False).name)
    prompt = _codex_review_prompt(task, state, sessions)
    cmd = [
        binary,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--cd",
        str(Path(__file__).resolve().parents[2]),
        "--sandbox",
        sandbox,
        "--output-last-message",
        str(out_path),
    ]
    if model:
        cmd.extend(["--model", model])
    if profile:
        cmd.extend(["--profile", profile])
    cmd.append("-")
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
    except Exception as exc:
        return {"error": str(exc), "action": "skip"}
    try:
        content = out_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        content = ""
    try:
        out_path.unlink(missing_ok=True)
    except Exception:
        pass
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or content or "").strip()
        return {"error": f"codex exec failed with code {proc.returncode}: {detail[:1200]}", "action": "skip"}
    parsed = _parse_llm_json(content or proc.stdout)
    if not parsed:
        return {"error": f"codex returned no JSON object: {(content or proc.stdout or '')[:1200]}", "action": "skip"}
    parsed.setdefault("_review_source", "local_codex")
    return parsed


def _format_codex_review_message(result: dict[str, Any]) -> str:
    summary = str(result.get("summary") or result.get("reason") or "").strip()
    evidence = result.get("evidence") if isinstance(result.get("evidence"), list) else []
    commands = result.get("commands") if isinstance(result.get("commands"), list) else []
    parts = []
    if summary:
        parts.append(summary)
    if evidence:
        parts.append("Evidence: " + "; ".join(str(item).strip() for item in evidence[:8] if str(item).strip()))
    if commands:
        parts.append("Commands: " + "; ".join(str(item).strip() for item in commands[:6] if str(item).strip()))
    reason = str(result.get("reason") or "").strip()
    if reason and reason != summary:
        parts.append("Reason: " + reason)
    return "\n".join(parts).strip()[:5000] or "Local Codex review completed."


def review_pending_tasks_with_codex(
    user_id: str,
    state: dict[str, Any],
    sessions: list[dict[str, Any]],
    cache: dict[str, Any],
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    dry_run: bool = False,
    limit: int = 1,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    tasks = _review_task_candidates(state, project_id=project_id)
    if not tasks:
        return []
    review_cache = cache.setdefault("codex_review", {})
    results: list[dict[str, Any]] = []
    for task in tasks:
        if len(results) >= limit:
            break
        task_id = str(task.get("task_id") or "")
        fingerprint = _task_review_fingerprint(task)
        cached = review_cache.get(task_id) if isinstance(review_cache.get(task_id), dict) else {}
        if cached.get("fingerprint") == fingerprint:
            continue
        entry = {
            "task_id": task_id,
            "project_id": task.get("project_id") or project_id,
            "ok": False,
            "action": "",
        }
        if dry_run:
            entry.update({"ok": True, "action": "dry_run", "fingerprint": fingerprint})
            results.append(entry)
            continue
        result = _call_local_codex_review(task, state, sessions)
        action = str(result.get("action") or "skip").strip().lower()
        if action not in CODEX_REVIEW_ACTIONS:
            action = "skip"
        entry.update(
            {
                "ok": not bool(result.get("error")),
                "action": action,
                "confidence": result.get("confidence"),
                "summary": str(result.get("summary") or result.get("reason") or "")[:500],
                "error": str(result.get("error") or "")[:1200],
            }
        )
        review_cache[task_id] = {
            "fingerprint": fingerprint,
            "action": action,
            "ok": entry["ok"],
            "reviewed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "summary": entry["summary"],
            "error": entry["error"],
        }
        if result.get("error"):
            results.append(entry)
            continue

        message = _format_codex_review_message(result)
        if action == "accept":
            apply_harness_event(
                user_id,
                {
                    "action": "task_comment",
                    "agent_id": CONDUCTOR_AGENT_ID,
                    "project_id": task.get("project_id") or project_id,
                    "task_id": task_id,
                    "kind": HOST_VERIFIED_COMMENT_KIND,
                    "message": "Local Codex host review accepted this TODO.\n" + message,
                },
            )
            apply_harness_event(
                user_id,
                {
                    "action": "task_status",
                    "agent_id": CONDUCTOR_AGENT_ID,
                    "project_id": task.get("project_id") or project_id,
                    "task_id": task_id,
                    "status": "done",
                    "message": "Local Codex review accepted and closed this TODO.",
                },
            )
        elif action == "reopen":
            new_status = str(result.get("new_status") or "active").strip().lower()
            if new_status not in CODEX_REVIEW_STATUSES:
                new_status = "active"
            worker_message = str(result.get("worker_message") or "").strip()
            apply_harness_event(
                user_id,
                {
                    "action": "task_comment",
                    "agent_id": CONDUCTOR_AGENT_ID,
                    "project_id": task.get("project_id") or project_id,
                    "task_id": task_id,
                    "kind": "review",
                    "message": "Local Codex host review reopened this TODO.\n" + (worker_message or message),
                },
            )
            apply_harness_event(
                user_id,
                {
                    "action": "task_status",
                    "agent_id": CONDUCTOR_AGENT_ID,
                    "project_id": task.get("project_id") or project_id,
                    "task_id": task_id,
                    "status": new_status,
                    "message": worker_message or "Local Codex review found the TODO is not ready to close.",
                },
            )
        elif action == "needs_user":
            apply_harness_event(
                user_id,
                {
                    "action": "task_status",
                    "agent_id": CONDUCTOR_AGENT_ID,
                    "project_id": task.get("project_id") or project_id,
                    "task_id": task_id,
                    "status": "needs_user",
                    "message": str(result.get("worker_message") or message or "Local Codex review requires user input.")[:5000],
                },
            )
            apply_harness_event(
                user_id,
                {
                    "action": "needs_user",
                    "agent_id": CONDUCTOR_AGENT_ID,
                    "project_id": task.get("project_id") or project_id,
                    "task_id": task_id,
                    "message": str(result.get("worker_message") or message or "Local Codex review requires user input.")[:5000],
                },
            )
        results.append(entry)
    return results


def _priority_rank(task: dict[str, Any]) -> int:
    priority = str(task.get("priority") or "").strip().lower()
    return {"urgent": 0, "high": 1, "medium": 2, "normal": 3, "low": 4}.get(priority, 3)


def _open_tasks_for_assignment(state: dict[str, Any], *, project_id: str) -> list[dict[str, Any]]:
    assigned = {
        str(agent.get("current_task_id") or "")
        for agent in state.get("agents", []) or []
        if str(agent.get("current_task_id") or "")
        and str(agent.get("status") or "").lower() not in {"done", "offline", "error"}
    }
    candidates = []
    for task in state.get("tasks", []) or []:
        if not isinstance(task, dict):
            continue
        if project_id and task.get("project_id") != project_id:
            continue
        task_id = str(task.get("task_id") or "").strip()
        if not task_id or task_id in assigned:
            continue
        if _task_paused_by_user(task):
            continue
        if str(task.get("status") or "").lower() != "todo":
            continue
        candidates.append(task)
    return sorted(candidates, key=lambda item: (_priority_rank(item), str(item.get("due_at") or ""), str(item.get("updated_at") or "")))


def _build_assignment_message(agent: dict[str, Any], task: dict[str, Any]) -> str:
    task_id = str(task.get("task_id") or "").strip()
    project_id = str(task.get("project_id") or agent.get("project_id") or DEFAULT_PROJECT_ID).strip()
    title = str(task.get("title") or task_id).strip()
    description = str(task.get("description") or "").strip()
    lines = [
        "是我，ClawCross 本机主控。",
        "你上一个 TODO 已进入主机验收闭环；现在从 dashboard 拉到新的 TODO 分配给你。",
        f"新 TODO: {title} ({task_id})，project {project_id}。",
    ]
    if description:
        lines.append(f"任务描述: {description[:1600]}")
    lines.extend(
        [
            SAFE_ALLOW_TEXT,
            "请立即用 clawcross-harness-agent 把该 TODO 标记为 doing/active，并持续写 comment、needs_user、blocked、run 或 done。",
            f"保持 agent_id 为 {agent.get('agent_id')}; current_task_id 改为 {task_id}。",
            "完成后不要只说完成：决策类任务写 result comment；实验/评测/推理类任务必须给 run_id、git_sha、命令、日志/metrics 路径和 verifier 结果。",
        ]
    )
    return "\n".join(lines)


def assign_next_dashboard_todos(
    user_id: str,
    sessions: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    dry_run: bool = False,
    llm_mode: bool = False,
) -> list[dict[str, Any]]:
    open_tasks = _open_tasks_for_assignment(state, project_id=project_id)
    if not open_tasks:
        return []
    assigned: list[dict[str, Any]] = []
    used_task_ids: set[str] = set()
    for session in sessions:
        agent = _agent_for_session(session, state)
        if not agent:
            agent = _agent_for_unbound_session(session, state, project_id=project_id)
        if not agent:
            continue
        agent_project_id = str(agent.get("project_id") or "").strip()
        current_task = _task_for_agent(agent, state)
        agent_status = str(agent.get("status") or "").lower()
        current_status = str((current_task or {}).get("status") or "").lower()
        current_done = current_task and current_status == "done" and task_has_host_verification(current_task, state.get("runs", []))
        if not current_done and agent_status not in {"idle", "done"}:
            continue
        candidate_tasks = [
            task
            for task in open_tasks
            if str(task.get("task_id") or "") not in used_task_ids
            and (
                bool(project_id)
                or not agent_project_id
                or str(task.get("project_id") or "").strip() in _agent_supported_project_ids(agent, state)
            )
        ]
        llm_assignment = _llm_choose_assignment(agent, session, candidate_tasks) if llm_mode else {}
        next_task = None
        if llm_assignment.get("task_id"):
            next_task = next((task for task in candidate_tasks if str(task.get("task_id") or "") == llm_assignment["task_id"]), None)
        if not next_task:
            next_task = candidate_tasks[0] if candidate_tasks else None
        if not next_task:
            continue
        key = session_key(session)
        if not key:
            continue
        message = str(llm_assignment.get("message") or "").strip() or _build_assignment_message(agent, next_task)
        entry = {
            "session_key": key,
            "agent_id": agent.get("agent_id"),
            "task_id": next_task.get("task_id"),
            "project_id": next_task.get("project_id"),
            "sent": False,
            "ok": False,
            "reason": llm_assignment.get("reason") or ("webot llm assigned next dashboard TODO" if llm_mode else "assigned next dashboard TODO"),
            "decision_source": llm_assignment.get("decision_source") or ("rules_fallback" if llm_mode else "rules"),
            "llm_driven": llm_assignment.get("decision_source") == "webot_llm",
        }
        if llm_assignment.get("llm_error"):
            entry["llm_error"] = llm_assignment.get("llm_error")
        if llm_assignment.get("llm_source"):
            entry["llm_source"] = llm_assignment.get("llm_source")
        if dry_run:
            entry["ok"] = True
            entry["message"] = message
            assigned.append(entry)
            used_task_ids.add(str(next_task.get("task_id") or ""))
            continue
        response = send_remote_claude_message(key, message)
        entry["sent"] = bool(response.get("ok"))
        entry["ok"] = bool(response.get("ok"))
        entry["error"] = response.get("error") or ""
        if response.get("ok"):
            task_id = str(next_task.get("task_id") or "")
            used_task_ids.add(task_id)
            apply_harness_event(
                user_id,
                {
                    "action": "task_status",
                    "agent_id": CONDUCTOR_AGENT_ID,
                    "project_id": next_task.get("project_id") or project_id,
                    "task_id": task_id,
                    "status": "active",
                    "message": f"主机从 dashboard 拉取并分配给 {agent.get('agent_id')} / session {key}.",
                },
            )
            apply_harness_event(
                user_id,
                {
                    "action": "heartbeat",
                    "agent_id": agent.get("agent_id"),
                    "agent_type": agent.get("agent_type") or "claude-code-worker",
                    "project_id": next_task.get("project_id") or project_id,
                    "task_id": task_id,
                    "current_task_id": task_id,
                    "status": "running",
                    "needs_user": False,
                    "session_ref": key,
                    "remote_host": agent.get("remote_host") or "",
                    "message": f"Assigned next dashboard TODO: {next_task.get('title') or task_id}",
                },
            )
        assigned.append(entry)
    return assigned


def cleanup_remote_sessions_without_todos(
    user_id: str,
    sessions: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Close ClawCross-managed remote sessions once they no longer own a live TODO."""

    results: list[dict[str, Any]] = []
    project_session_counts = _session_counts_by_project(sessions, state)
    for session in sessions:
        agent = _agent_for_session(session, state)
        if not agent:
            continue
        if project_id and agent.get("project_id") != project_id:
            continue
        task = _task_for_agent(agent, state)
        task_id = str(agent.get("current_task_id") or "").strip()
        reason = ""
        if not task_id:
            reason = "agent has no current TODO"
        elif not task:
            reason = f"bound TODO {task_id} no longer exists"
        elif _task_paused_by_user(task):
            reason = f"TODO {task_id} is paused by user"
        elif str(task.get("status") or "").lower() == "done" and task_has_host_verification(task, state.get("runs", [])):
            reason = f"TODO {task_id} is done and host verified"
        if not reason:
            continue

        key = session_key(session)
        if not key:
            continue
        agent_id = str(agent.get("agent_id") or "").strip()
        agent_project_id = str(agent.get("project_id") or project_id or "").strip()
        entry = {
            "session_key": key,
            "agent_id": agent_id,
            "task_id": task_id,
            "project_id": agent_project_id,
            "reason": reason,
            "closed": False,
            "deleted_agent": False,
            "kept": False,
            "ok": False,
        }
        if (
            agent_project_id
            and _project_is_active(state, agent_project_id)
            and project_session_counts.get(agent_project_id, 0) <= 1
        ):
            entry["kept"] = True
            entry["ok"] = True
            entry["reason"] = f"{reason}; kept because project is still active and this is its last session"
            if not dry_run:
                apply_harness_event(
                    user_id,
                    {
                        "action": "heartbeat",
                        "agent_id": agent_id,
                        "agent_type": agent.get("agent_type") or "claude-code-worker",
                        "project_id": agent_project_id,
                        "current_task_id": "",
                        "status": "idle",
                        "needs_user": False,
                        "session_ref": key,
                        "remote_host": agent.get("remote_host") or "",
                        "worktree": agent.get("worktree") or "",
                        "message": "项目仍在进行中；当前暂无可自动清理的绑定 TODO，保留为 standby worker。",
                        "metadata": {"clawcross": {"standby": True, "standby_reason": reason}},
                    },
                )
            results.append(entry)
            continue
        if dry_run:
            entry["ok"] = True
            results.append(entry)
            continue

        try:
            close_response = close_remote_claude_session(key, force=True)
        except Exception as exc:
            close_response = {"ok": False, "error": str(exc)}
        entry["remote_close"] = close_response
        close_ok = bool(close_response.get("ok")) or str(close_response.get("error") or "").lower() == "session not found"
        entry["closed"] = close_ok
        if close_ok and agent_id:
            apply_harness_event(
                user_id,
                {
                    "action": "agent_delete",
                    "agent_id": agent_id,
                    "project_id": agent_project_id,
                    "task_id": task_id,
                    "message": f"ClawCross closed remote session {key}: {reason}.",
                },
            )
            entry["deleted_agent"] = True
        else:
            apply_harness_event(
                user_id,
                {
                    "action": "blocked",
                    "agent_id": agent_id,
                    "project_id": agent_project_id,
                    "task_id": task_id,
                    "session_ref": key,
                    "message": f"ClawCross tried to close remote session {key} but failed: {close_response.get('error') or 'unknown error'}",
                },
            )
        entry["ok"] = close_ok
        results.append(entry)
    return results


def rename_bound_remote_sessions(
    sessions: list[dict[str, Any]],
    state: dict[str, Any],
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Keep Claude Desktop background-agent names aligned with ClawCross projects."""

    results: list[dict[str, Any]] = []
    for session in sessions:
        agent = _agent_for_session(session, state)
        if not agent:
            continue
        task = _task_for_agent(agent, state)
        target = session_key(session)
        expected = expected_remote_session_name(agent, task, session)
        current = str(session.get("title") or session.get("name") or "").strip()
        if not target or not expected or current == expected:
            continue
        entry = {
            "session_key": target,
            "agent_id": agent.get("agent_id"),
            "task_id": (task or {}).get("task_id") or agent.get("current_task_id") or "",
            "project_id": (task or {}).get("project_id") or agent.get("project_id") or "",
            "old_name": current,
            "name": expected,
            "renamed": False,
            "ok": False,
        }
        if dry_run:
            entry["ok"] = True
            results.append(entry)
            continue
        try:
            response = rename_remote_claude_session(target, expected)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        entry["response"] = response
        entry["renamed"] = bool(response.get("ok"))
        entry["ok"] = bool(response.get("ok"))
        entry["error"] = response.get("error") or ""
        results.append(entry)
    return results


def _build_continue_message(
    *,
    agent: dict[str, Any],
    task: dict[str, Any],
    reason: str,
) -> str:
    title = str(task.get("title") or task.get("task_id") or "").strip()
    description = str(task.get("description") or "").strip()
    task_id = str(task.get("task_id") or "").strip()
    project_id = str(task.get("project_id") or agent.get("project_id") or "default").strip()
    lines = [
        "是我，ClawCross 本机主控确认你继续。",
        f"你当前绑定 TODO: {title} ({task_id})，project {project_id}。",
    ]
    if description:
        lines.append(f"任务描述: {description[:1200]}")
    lines.extend(
        [
            SAFE_ALLOW_TEXT,
            "请立刻继续推进这个 TODO：读取 dashboard 的任务/TODO 状态，执行下一步，并用 clawcross-harness-agent 更新 doing/comment/done/needs_user。",
            "如果结果涉及实验或评测，必须给出可验证文件、命令、run_id、git_sha 或 verifier 结果；不要只写自然语言结论。",
            f"主控触发原因: {reason}",
        ]
    )
    return "\n".join(lines)


def decide_for_session(
    session: dict[str, Any],
    state: dict[str, Any],
    cache: dict[str, Any],
    *,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
) -> ConductorDecision | None:
    agent = _agent_for_session(session, state)
    task = _task_for_agent(agent, state)
    key = session_key(session)
    if not key or not agent or not task:
        return None
    task_status = str(task.get("status") or "").lower()
    if task_status in {"done", "review", "needs_user"}:
        return None
    agent_id = str(agent.get("agent_id") or "").strip()
    task_id = str(task.get("task_id") or "").strip()
    project_id = str(task.get("project_id") or agent.get("project_id") or "default").strip()
    text = last_message_text(session)
    text_role = last_message_role(session)
    text_from_worker = text_role not in {"user", "human"}
    session_status = str(session.get("status") or "").lower()
    active_tool_command = text_from_worker and looks_like_active_tool_command(text)
    if session_status in {"busy", "running", "working"} and active_tool_command and not is_risky_request(text):
        return None
    needs_user = bool(agent.get("needs_user")) or str(agent.get("status") or "").lower() == "needs_user"

    reason = ""
    if needs_user:
        reason = "worker marked needs_user"
    elif text_from_worker and text and looks_waiting_for_input(text):
        reason = "session appears to wait for user input"
    elif session_status in {"idle", "shell"} and task_status in {"todo", "active", "blocked", "needs_user"}:
        reason = f"session status is {session_status} while TODO is {task_status}"
    else:
        return None

    sent_key = _fingerprint(key, agent_id, task_id, session_status, text, str(agent.get("updated_at") or ""))
    sent = cache.setdefault("sent", {})
    last_sent = float(sent.get(sent_key, 0) or 0)
    if _now_ts() - last_sent < cooldown_seconds:
        return None

    risky = text_from_worker and is_risky_request(text)
    if risky:
        return ConductorDecision(
            should_send=False,
            manual_review=True,
            session_key=key,
            agent_id=agent_id,
            task_id=task_id,
            project_id=project_id,
            reason="risky request requires human review",
            cache_key=sent_key,
        )

    message = _build_continue_message(agent=agent, task=task, reason=reason)
    return ConductorDecision(
        should_send=True,
        session_key=key,
        agent_id=agent_id,
        task_id=task_id,
        project_id=project_id,
        reason=reason,
        message=message,
        cache_key=sent_key,
    )


def mark_decision_sent(cache: dict[str, Any], session: dict[str, Any], decision: ConductorDecision) -> None:
    sent_key = decision.cache_key or _fingerprint(
        decision.session_key,
        decision.agent_id,
        decision.task_id,
        str(session.get("status") or ""),
        last_message_text(session),
    )
    cache.setdefault("sent", {})[sent_key] = _now_ts()


def _post_comment(user_id: str, decision: ConductorDecision, body: str, *, kind: str) -> None:
    apply_harness_event(
        user_id,
        {
            "action": "task_comment",
            "agent_id": CONDUCTOR_AGENT_ID,
            "project_id": decision.project_id,
            "task_id": decision.task_id,
            "kind": kind,
            "message": body,
        },
    )


def run_conductor_once(
    user_id: str,
    *,
    remote_limit: int = DEFAULT_REMOTE_LIMIT,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    dry_run: bool = False,
    sync_dashboard: bool = True,
    dashboard_root: Path | None = None,
    project_id: str = DEFAULT_PROJECT_ID,
    llm_mode: bool = False,
    codex_review: bool = True,
    codex_review_limit: int = 1,
    publish_dashboard: bool = False,
    sync_supabase: bool = False,
) -> dict[str, Any]:
    os.environ.setdefault("CLAWCROSS_REMOTE_CLAUDE_TIMEOUT_SEC", "45")
    os.environ.setdefault("CLAWCROSS_REMOTE_CLAUDE_CONNECT_TIMEOUT_SEC", "10")
    pull_summary: dict[str, Any] = {}
    verify_summary: dict[str, Any] = {}
    push_summary: dict[str, Any] = {}
    publish_summary: dict[str, Any] = {}
    supabase_summary: dict[str, Any] = {}
    if sync_dashboard and not dry_run:
        pull_summary = import_dashboard_todos(user_id, dashboard_root=dashboard_root, project_id=project_id)
        verify_summary = verify_finished_tasks(user_id, project_id=project_id)
        push_summary = sync_harness_to_dashboard(user_id, dashboard_root=dashboard_root, project_id=project_id)
    state = get_harness_state(user_id)
    cache = load_action_cache()
    remote = list_remote_claude_sessions(limit=remote_limit, tail_lines=80)
    sessions = [item for item in remote.get("sessions", []) if isinstance(item, dict)]
    results: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    cleanup: list[dict[str, Any]] = []
    renames: list[dict[str, Any]] = []
    codex_reviews: list[dict[str, Any]] = []

    if sync_dashboard and codex_review and not dry_run:
        codex_reviews = review_pending_tasks_with_codex(
            user_id,
            state,
            sessions,
            cache,
            project_id=project_id,
            dry_run=dry_run,
            limit=max(0, int(codex_review_limit)),
        )
        if codex_reviews:
            state = get_harness_state(user_id)

    if remote.get("ok"):
        renames = rename_bound_remote_sessions(sessions, state, dry_run=dry_run)
        assignments = assign_next_dashboard_todos(
            user_id,
            sessions,
            state,
            project_id=project_id,
            dry_run=dry_run,
            llm_mode=llm_mode and not dry_run,
        )
        if assignments and not dry_run:
            state = get_harness_state(user_id)
        cleanup = cleanup_remote_sessions_without_todos(
            user_id,
            sessions,
            state,
            project_id=project_id,
            dry_run=dry_run,
        )
        if cleanup and not dry_run:
            state = get_harness_state(user_id)

    for session in (sessions if remote.get("ok") else []):
        if any(item.get("session_key") == session_key(session) and item.get("ok") for item in cleanup):
            continue
        decision = decide_for_session(session, state, cache, cooldown_seconds=cooldown_seconds)
        if not decision:
            continue
        if llm_mode and not dry_run and not decision.manual_review:
            decision = _llm_refine_decision(decision, session, state)
        entry = {
            "session_key": decision.session_key,
            "agent_id": decision.agent_id,
            "task_id": decision.task_id,
            "reason": decision.reason,
            "sent": False,
            "manual_review": decision.manual_review,
            "ok": False,
            "decision_source": decision.decision_source,
            "llm_driven": decision.decision_source == "webot_llm",
        }
        if decision.manual_review:
            if not dry_run:
                apply_harness_event(
                    user_id,
                    {
                        "action": "needs_user",
                        "agent_id": decision.agent_id,
                        "project_id": decision.project_id,
                        "task_id": decision.task_id,
                        "message": "本机主控拦截到疑似危险输入请求，需要人工确认后再继续。",
                    },
                )
                mark_decision_sent(cache, session, decision)
            entry["ok"] = True
            results.append(entry)
            continue

        if dry_run:
            entry["message"] = decision.message
            entry["ok"] = True
            results.append(entry)
            continue

        response = send_remote_claude_message(decision.session_key, decision.message)
        entry["sent"] = bool(response.get("ok"))
        entry["ok"] = bool(response.get("ok"))
        entry["error"] = response.get("error") or ""
        if response.get("ok"):
            mark_decision_sent(cache, session, decision)
            apply_harness_event(
                user_id,
                {
                    "action": "heartbeat",
                    "agent_id": decision.agent_id,
                    "project_id": decision.project_id,
                    "task_id": decision.task_id,
                    "status": "running",
                    "needs_user": False,
                    "message": f"ClawCross 本机主控已回复远端输入请求: {decision.reason}",
                },
            )
            _post_comment(
                user_id,
                decision,
                f"本机主控已向远端 session {decision.session_key} 自动回复继续执行。原因: {decision.reason}",
                kind="conductor_reply",
            )
        results.append(entry)

    if not dry_run:
        save_action_cache(cache)
        if sync_dashboard:
            push_summary = sync_harness_to_dashboard(user_id, dashboard_root=dashboard_root, project_id=project_id)
            if publish_dashboard:
                publish_summary = publish_dashboard_tasks(dashboard_root=dashboard_root)
            if sync_supabase and push_summary.get("changed"):
                supabase_summary = sync_dashboard_to_supabase(dashboard_root=dashboard_root, project_id=project_id)
    return {
        "ok": True,
        "remote_ok": bool(remote.get("ok")),
        "remote_error": remote.get("error") or "",
        "sessions_seen": len(sessions),
        "dashboard_pull": pull_summary,
        "host_verify": verify_summary,
        "dashboard_push": push_summary,
        "dashboard_publish": publish_summary,
        "dashboard_supabase_sync": supabase_summary,
        "codex_reviews": codex_reviews,
        "renames": renames,
        "assignments": assignments,
        "cleanup": cleanup,
        "actions": results,
        "llm_mode": bool(llm_mode),
        "conductor_driver": "webot_llm" if llm_mode else "rules",
    }
