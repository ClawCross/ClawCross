"""
Lightweight EvoSkill-style skill evolution for ClawCross.

This module upgrades the existing SKILL.md CRUD flow into a failure-driven loop:
- analyze recent trajectory failures and explicit execution errors
- synthesize a small candidate frontier of skill mutations
- persist feedback history and evolution reports
- apply the best mutation back into a skill's managed self-evolution block

It deliberately stays heuristic and benchmark-free for now. The goal is to
adapt EvoSkill's proposer/frontier/feedback loop to ClawCross's existing data
sources (skills, trajectories, insights, runtime failures) without requiring a
separate branch-based benchmark harness.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import sys
from typing import Any

from webot.profiles import slugify
from webot.skills import edit_skill, get_skill, write_skill_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
USER_FILES_DIR = PROJECT_ROOT / "data" / "user_files"

EVOLUTION_BEGIN = "<!-- clawcross:self-evolution:begin -->"
EVOLUTION_END = "<!-- clawcross:self-evolution:end -->"
MAX_FAILURE_SAMPLES = 12
ERROR_TERMS = (
    "error",
    "failed",
    "failure",
    "exception",
    "traceback",
    "timeout",
    "timed out",
    "permission",
    "denied",
    "unauthorized",
    "forbidden",
    "429",
    "rate limit",
    "context",
    "token",
    "json",
    "schema",
    "parse",
    "missing",
    "not found",
)
_STOPWORDS = {
    "the", "and", "for", "that", "with", "this", "from", "into", "then", "when",
    "after", "before", "have", "has", "had", "will", "would", "should", "could",
    "your", "their", "there", "about", "into", "because", "while", "where",
    "which", "what", "just", "than", "them", "they", "were", "been", "being",
    "also", "only", "more", "less", "very", "much", "some", "many", "each",
    "over", "under", "again", "still", "need", "must", "make", "made", "using",
    "used", "user", "users", "session", "sessions", "skill", "skills", "agent",
    "agents", "clawcross", "trace", "failure", "failed", "error", "stderr",
    "stdout", "output", "input", "command", "commands", "default", "recent",
}

_SIGNAL_LIBRARY: list[dict[str, Any]] = [
    {
        "id": "verification-loop",
        "title": "Tighten verification loops",
        "intent": "repair",
        "patterns": [
            "assert", "regression", "broken", "incorrect", "expected", "test",
            "failed", "failure", "bug",
        ],
        "guardrails": [
            "State the exact failure mode before editing anything.",
            "Choose the narrowest reproducer first, then a broader regression command.",
            "Record pass/fail status immediately after each attempted fix.",
        ],
        "validation": [
            "Rerun the minimal reproducer before any broad test suite.",
            "When a fix passes, note the exact verifier command in the skill.",
        ],
    },
    {
        "id": "workspace-preflight",
        "title": "Add repo/workspace preflight checks",
        "intent": "repair",
        "patterns": [
            "no such file", "not found", "module not found", "importerror",
            "modulenotfounderror", "cwd", "path", "relative path", "missing file",
        ],
        "guardrails": [
            "Inspect the repo index and target paths before editing or running commands.",
            "Confirm cwd, interpreter, and entrypoints before assuming a runtime bug.",
            "Prefer absolute or repo-root-relative paths in instructions and validation.",
        ],
        "validation": [
            "List the files you rely on before the main command.",
            "Capture the final working path/entrypoint in the skill.",
        ],
    },
    {
        "id": "approval-auth",
        "title": "Preflight auth and approval constraints",
        "intent": "repair",
        "patterns": [
            "401", "403", "forbidden", "unauthorized", "permission", "approval",
            "token", "credential", "auth",
        ],
        "guardrails": [
            "Check authentication, required tokens, and approval mode before retrying.",
            "Distinguish auth failures from product logic failures.",
            "Document safe fallback behavior when credentials are missing.",
        ],
        "validation": [
            "Verify token presence or auth mode explicitly before the main action.",
            "Avoid repeated retries when the root cause is missing credentials.",
        ],
    },
    {
        "id": "bounded-execution",
        "title": "Bound long-running and flaky execution",
        "intent": "optimize",
        "patterns": [
            "timeout", "timed out", "hang", "hung", "stuck", "deadline",
            "overloaded", "429", "rate limit",
        ],
        "guardrails": [
            "Use smaller repro steps before rerunning expensive workflows.",
            "Call out retry/backoff conditions explicitly instead of looping blindly.",
            "Capture the last known good checkpoint before restarting long flows.",
        ],
        "validation": [
            "Prefer bounded smoke tests and explicit timeouts in verifier commands.",
            "Note rate-limit or overload conditions as operational, not code, failures.",
        ],
    },
    {
        "id": "structured-output",
        "title": "Harden structured-output handling",
        "intent": "repair",
        "patterns": [
            "json", "yaml", "frontmatter", "parse", "schema", "typeerror",
            "valueerror", "keyerror", "syntaxerror", "decode",
        ],
        "guardrails": [
            "Validate expected structured output shape before downstream use.",
            "Call out schema constraints and fallback parsing rules explicitly.",
            "When patching text templates, preserve frontmatter and managed markers.",
        ],
        "validation": [
            "Re-run the parser or schema validator after any template change.",
            "Capture representative failing payloads in the evolution report.",
        ],
    },
    {
        "id": "capability-gap",
        "title": "Capture missing capabilities explicitly",
        "intent": "innovate",
        "patterns": [
            "not supported", "unsupported", "not implemented", "missing feature",
            "cannot", "can't", "no support", "unavailable",
        ],
        "guardrails": [
            "Name the missing capability before changing the skill.",
            "Separate product gaps from environment or auth failures.",
            "Prefer adding a reusable procedure over one-off workarounds.",
        ],
        "validation": [
            "Re-run the exact unsupported scenario after updating the skill.",
            "Document the new supported path and the boundaries that still remain unsupported.",
        ],
    },
    {
        "id": "improvement-suggestion",
        "title": "Turn repeated improvement requests into reusable guidance",
        "intent": "optimize",
        "patterns": [
            "improve", "enhance", "upgrade", "refactor", "simplify", "optimize",
            "clean up", "streamline",
        ],
        "guardrails": [
            "Keep the guidance tied to a concrete recurring pain point.",
            "Prefer workflow simplification over adding more optional branches.",
            "State the expected operator benefit in one sentence.",
        ],
        "validation": [
            "Verify the improved workflow still covers the previous baseline path.",
            "Record the before/after verifier commands when the optimization changes operator steps.",
        ],
    },
    {
        "id": "recurring-error",
        "title": "Deduplicate recurring failures before retrying",
        "intent": "repair",
        "patterns": [
            "again", "still failing", "same error", "repeatedly", "keeps failing",
            "not fixed", "still broken",
        ],
        "guardrails": [
            "Do not retry the same failing path without changing one concrete assumption.",
            "Call out what evidence makes this failure a recurrence instead of a fresh issue.",
            "Bias toward root-cause notes instead of longer retry ladders.",
        ],
        "validation": [
            "Re-run the smallest reproducer that distinguishes the old failure from the new path.",
            "Persist the recurrence signal in the evolution report.",
        ],
    },
    {
        "id": "evolution-stagnation",
        "title": "Break stagnation with a different tactic",
        "intent": "innovate",
        "patterns": [
            "stuck", "same result", "no change", "no progress", "plateau",
            "spinning", "nothing new", "empty cycle",
        ],
        "guardrails": [
            "Stop repeating the same mutation when recent cycles produced no new guidance.",
            "Promote one materially different tactic instead of adding more retries.",
            "Use local state and recent history to explain why the loop is saturated.",
        ],
        "validation": [
            "Verify that the next candidate changes either the operating adjustments or the validation loop.",
            "Record the stagnation trigger so future cycles can suppress the same signal.",
        ],
    },
    {
        "id": "repair-loop-detected",
        "title": "Escalate out of repair loops",
        "intent": "innovate",
        "patterns": [
            "repair loop", "same patch", "same fix", "looping", "retrying",
            "keeps failing", "still broken",
        ],
        "guardrails": [
            "Do not produce another pure repair candidate when recent repair attempts kept failing.",
            "Escalate to a different validation strategy, capability change, or bounded fallback.",
            "Call out the exact repeated assumptions that need to be broken.",
        ],
        "validation": [
            "Verify that the selected candidate changes the intent mix away from pure repair.",
            "Persist repair-loop diagnostics in frontier history.",
        ],
    },
    {
        "id": "force-steady-state",
        "title": "Enter steady-state verification mode",
        "intent": "optimize",
        "patterns": [
            "steady state", "saturation", "empty cycle", "cooldown",
        ],
        "guardrails": [
            "Favor verification and observability over more mutation churn.",
            "Only resume aggressive evolution after a materially new failure or request appears.",
            "Keep the frontier small and explicit while saturated.",
        ],
        "validation": [
            "Ensure the next cycle writes a clearer verifier artifact, not just another heuristic rewrite.",
            "Track empty-cycle counts until saturation clears.",
        ],
    },
]

_STRATEGIES: dict[str, dict[str, Any]] = {
    "balanced": {
        "repair": 0.20,
        "optimize": 0.30,
        "innovate": 0.50,
        "explore": 0.00,
        "repair_loop_threshold": 3,
        "label": "Balanced",
        "description": "Blend repair guidance with proactive skill upgrades.",
    },
    "innovate": {
        "repair": 0.05,
        "optimize": 0.15,
        "innovate": 0.80,
        "explore": 0.00,
        "repair_loop_threshold": 2,
        "label": "Innovation",
        "description": "Bias the frontier toward new capabilities and materially different tactics.",
    },
    "harden": {
        "repair": 0.40,
        "optimize": 0.40,
        "innovate": 0.20,
        "explore": 0.00,
        "repair_loop_threshold": 4,
        "label": "Hardening",
        "description": "Shift toward stability, bounded retries, and verifier quality.",
    },
    "repair-only": {
        "repair": 0.80,
        "optimize": 0.20,
        "innovate": 0.00,
        "explore": 0.00,
        "repair_loop_threshold": 2,
        "label": "Repair Only",
        "description": "Emergency mode for repeated failures where repair guidance must dominate.",
    },
    "early-stabilize": {
        "repair": 0.55,
        "optimize": 0.25,
        "innovate": 0.15,
        "explore": 0.05,
        "repair_loop_threshold": 3,
        "label": "Early Stabilize",
        "description": "Use conservative hardening before pushing broader changes on a new loop.",
    },
    "steady-state": {
        "repair": 0.60,
        "optimize": 0.22,
        "innovate": 0.15,
        "explore": 0.03,
        "repair_loop_threshold": 4,
        "label": "Steady State",
        "description": "Recent cycles are saturated, so favor verification and minimal churn.",
    },
}

_ENV_STATE_KEYS = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "OPENCLAW_API_URL",
    "OPENCLAW_GATEWAY_TOKEN",
    "PUBLIC_DOMAIN",
    "TINYFISH_API_KEY",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _truncate(text: str, limit: int = 240) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(40, limit - 18)].rstrip() + " ...[truncated]"


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _safe_json_load(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    if limit is not None and limit >= 0:
        return entries[-limit:]
    return entries


def _user_root(user_id: str) -> Path:
    root = USER_FILES_DIR / (user_id or "anonymous")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _evolution_root(user_id: str) -> Path:
    root = _user_root(user_id) / "skill_evolution"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _skill_evolution_dir(user_id: str, skill_name: str) -> Path:
    root = _evolution_root(user_id) / "skills" / slugify(skill_name, "skill")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9_/-]{3,}", (text or "").lower())
        if token not in _STOPWORDS
    ]


def _detect_signals(text: str) -> Counter[str]:
    normalized = (text or "").lower()
    counter: Counter[str] = Counter()
    for item in _SIGNAL_LIBRARY:
        hits = sum(1 for pattern in item["patterns"] if pattern in normalized)
        if hits:
            counter[item["id"]] += hits
    return counter


def _find_signal(signal_id: str) -> dict[str, Any]:
    for item in _SIGNAL_LIBRARY:
        if item["id"] == signal_id:
            return item
    return {
        "id": signal_id,
        "title": signal_id,
        "intent": "repair",
        "guardrails": ["Capture the failure precisely before changing the skill."],
        "validation": ["Re-run the narrowest reproducer after updating the skill."],
    }


def _intent_counts(signal_counts: Counter[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for signal_id, count in signal_counts.items():
        signal = _find_signal(signal_id)
        counts[str(signal.get("intent") or "repair")] += count
    return counts


def _feedback_history_path(user_id: str, skill_name: str) -> Path:
    return _skill_evolution_dir(user_id, skill_name) / "feedback_history.jsonl"


def _history_signal_ids(entry: dict[str, Any]) -> list[str]:
    signal_ids = entry.get("signal_ids")
    if isinstance(signal_ids, list):
        return [str(item) for item in signal_ids if str(item).strip()]

    signals = entry.get("signals")
    if isinstance(signals, list):
        return [str(item) for item in signals if str(item).strip()]
    if isinstance(signals, dict):
        return [str(item) for item in signals.keys() if str(item).strip()]
    return []


def _entry_counts_as_failure(entry: dict[str, Any]) -> bool:
    validation_overall_ok = entry.get("validation_overall_ok")
    if validation_overall_ok is not None:
        return not bool(validation_overall_ok)
    return int(entry.get("failure_count") or 0) > 0


def _analyze_recent_history(user_id: str, skill_name: str) -> dict[str, Any]:
    history = _load_jsonl(_feedback_history_path(user_id, skill_name), limit=10)
    tail = history[-8:]
    signal_freq: Counter[str] = Counter()
    recent_intents = [str(entry.get("intent") or "repair") for entry in history]

    for entry in tail:
        signal_freq.update(_history_signal_ids(entry))

    suppressed_signals = sorted(signal_id for signal_id, count in signal_freq.items() if count >= 3)

    consecutive_repair_count = 0
    for entry in reversed(history):
        if str(entry.get("intent") or "repair") == "repair":
            consecutive_repair_count += 1
        else:
            break

    empty_cycle_count = sum(1 for entry in tail if bool(entry.get("empty_cycle")))
    consecutive_empty_cycles = 0
    for entry in reversed(history):
        if bool(entry.get("empty_cycle")):
            consecutive_empty_cycles += 1
        else:
            break

    consecutive_failure_count = 0
    for entry in reversed(history):
        if _entry_counts_as_failure(entry):
            consecutive_failure_count += 1
        else:
            break

    recent_failure_count = sum(1 for entry in tail if _entry_counts_as_failure(entry))
    recent_failure_ratio = round(
        recent_failure_count / len(tail),
        3,
    ) if tail else 0.0

    repair_loop_detected = consecutive_repair_count >= 3 and recent_failure_ratio >= 0.5
    saturation_detected = consecutive_empty_cycles >= 2
    stagnation_detected = saturation_detected or empty_cycle_count >= 3 or bool(suppressed_signals)

    return {
        "history_entries": len(history),
        "suppressed_signals": suppressed_signals,
        "recent_intents": recent_intents,
        "consecutive_repair_count": consecutive_repair_count,
        "empty_cycle_count": empty_cycle_count,
        "consecutive_empty_cycles": consecutive_empty_cycles,
        "consecutive_failure_count": consecutive_failure_count,
        "recent_failure_count": recent_failure_count,
        "recent_failure_ratio": recent_failure_ratio,
        "repair_loop_detected": repair_loop_detected,
        "stagnation_detected": stagnation_detected,
        "saturation_detected": saturation_detected,
        "signal_freq": dict(signal_freq),
    }


def _augment_signal_counts(
    signal_counts: Counter[str],
    history_diagnostics: dict[str, Any],
) -> Counter[str]:
    augmented = Counter(signal_counts)
    if history_diagnostics.get("repair_loop_detected"):
        augmented["repair-loop-detected"] += 2
    if history_diagnostics.get("stagnation_detected"):
        augmented["evolution-stagnation"] += 1 + int(history_diagnostics.get("consecutive_empty_cycles") or 0)
    if history_diagnostics.get("consecutive_failure_count", 0) >= 2:
        augmented["recurring-error"] += 1
    if history_diagnostics.get("saturation_detected"):
        augmented["force-steady-state"] += 2
    return augmented


def _resolve_strategy(
    *,
    strategy: str = "",
    signal_counts: Counter[str],
    history_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    requested = (
        strategy
        or os.getenv("CLAWCROSS_EVOLVE_STRATEGY")
        or os.getenv("EVOLVE_STRATEGY")
        or "auto"
    ).strip().lower()
    force_innovation = (
        str(os.getenv("FORCE_INNOVATION") or os.getenv("EVOLVE_FORCE_INNOVATION") or "")
        .strip()
        .lower()
        == "true"
    )
    rationale: list[str] = []
    auto_mode = requested in {"", "auto"}

    if auto_mode and force_innovation:
        requested = "innovate"
        rationale.append("FORCE_INNOVATION requested an innovation-biased frontier.")
    elif auto_mode and history_diagnostics.get("saturation_detected"):
        requested = "steady-state"
        rationale.append("Recent empty cycles indicate saturation, so the loop should favor verification over churn.")
    elif auto_mode and history_diagnostics.get("repair_loop_detected"):
        requested = "repair-only"
        rationale.append("Recent repair attempts are looping on similar failures, so repair guidance must dominate.")
    elif auto_mode and float(history_diagnostics.get("recent_failure_ratio") or 0.0) >= 0.5:
        requested = "harden"
        rationale.append("Failure pressure is elevated, so the frontier should bias toward hardening.")
    elif auto_mode:
        intent_counts = _intent_counts(signal_counts)
        if intent_counts.get("innovate", 0) > max(intent_counts.get("repair", 0), intent_counts.get("optimize", 0)) and float(history_diagnostics.get("recent_failure_ratio") or 0.0) <= 0.25:
            requested = "innovate"
            rationale.append("Opportunity-style signals dominate while recent failure pressure is low.")
        elif not history_diagnostics.get("history_entries") and sum(signal_counts.values()) <= 2:
            requested = "early-stabilize"
            rationale.append("The loop is still cold, so it should stabilize before broadening its mutations.")
        else:
            requested = "balanced"
            rationale.append("No strong saturation or failure pressure was detected, so keep a balanced frontier.")

    if requested not in _STRATEGIES:
        rationale.append(f"Unknown strategy '{requested}' fell back to balanced.")
        requested = "balanced"

    resolved = dict(_STRATEGIES[requested])
    resolved["name"] = requested
    resolved["rationale"] = rationale or [resolved["description"]]
    return resolved


def _capture_local_state(user_id: str, skill_name: str, skill: dict[str, Any]) -> dict[str, Any]:
    from webot.trajectory import get_trajectory_stats

    history_entries = len(_load_jsonl(_feedback_history_path(user_id, skill_name)))
    runtime_failures = len(_load_jsonl(_evolution_root(user_id) / "runtime_failures.jsonl"))

    return {
        "repo_root": str(PROJECT_ROOT),
        "cwd": str(Path.cwd()),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "skill_path": str(skill.get("path") or ""),
        "support_files_count": len(skill.get("support_files") or []),
        "managed_section_present": EVOLUTION_BEGIN in str(skill.get("content") or ""),
        "feedback_history_entries": history_entries,
        "runtime_failure_entries": runtime_failures,
        "present_env_keys": [key for key in _ENV_STATE_KEYS if os.getenv(key)],
        "trajectory_stats": get_trajectory_stats(user_id=user_id, days=30),
    }


def _build_env_fingerprint(
    *,
    user_id: str = "",
    skill_name: str = "",
    local_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    local = local_state or {}
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.system(),
        "platform_detail": platform.platform(),
        "cwd": str(Path.cwd()),
        "repo_root": str(PROJECT_ROOT),
        "user_id": user_id,
        "skill_name": skill_name,
        "present_env_keys": list(local.get("present_env_keys") or []),
        "captured_at": _utc_now_iso(),
    }


def _build_validation_report(
    *,
    skill_name: str,
    candidate_id: str = "",
    strategy_name: str = "",
    commands: list[dict[str, Any]] | None = None,
    env_fingerprint: dict[str, Any] | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    commands_list = [
        {
            "command": str(item.get("command") or ""),
            "ok": bool(item.get("ok")),
            "stdout": _truncate(_normalize_text(item.get("stdout")), 4000),
            "stderr": _truncate(_normalize_text(item.get("stderr")), 4000),
        }
        for item in (commands or [])
    ]
    overall_ok = bool(commands_list) and all(item["ok"] for item in commands_list)
    report = {
        "type": "ValidationReport",
        "schema_version": "clawcross.skill-evolution/1.0",
        "id": f"vr_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        "candidate_id": candidate_id or None,
        "skill_name": skill_name,
        "strategy": strategy_name or "balanced",
        "env_fingerprint": env_fingerprint or _build_env_fingerprint(skill_name=skill_name),
        "commands": commands_list,
        "overall_ok": overall_ok,
        "duration_ms": duration_ms,
        "created_at": _utc_now_iso(),
    }
    report["asset_id"] = "sha256:" + hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return report


def _validation_entries_from_failures(
    *,
    failures: list[dict[str, Any]],
    command: str = "",
    error_text: str = "",
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
) -> list[dict[str, Any]]:
    if command.strip() or stdout.strip() or stderr.strip() or exit_code is not None:
        return [
            {
                "command": command.strip() or "(external failure context)",
                "ok": exit_code == 0 if exit_code is not None else False,
                "stdout": stdout,
                "stderr": stderr or error_text,
            }
        ]

    entries: list[dict[str, Any]] = []
    for item in failures[:3]:
        cmd = str(item.get("command") or "").strip() or "(historical failure context)"
        stderr_text = str(item.get("error_excerpt") or "").strip()
        entries.append({
            "command": cmd,
            "ok": False,
            "stdout": "",
            "stderr": stderr_text,
        })
    return entries


def _build_cycle_signature(
    *,
    report: dict[str, Any],
    candidate: dict[str, Any],
    command: str = "",
    error_text: str = "",
) -> str:
    payload = {
        "skill_name": report.get("skill_name", ""),
        "strategy": (report.get("strategy") or {}).get("name", ""),
        "candidate_id": candidate.get("candidate_id", ""),
        "signal_id": candidate.get("signal_id", ""),
        "intent": candidate.get("intent", ""),
        "summary": report.get("summary", ""),
        "signals": [item.get("signal_id", "") for item in (report.get("signals") or [])[:4]],
        "failures": [
            {
                "command": item.get("command", ""),
                "error_excerpt": item.get("error_excerpt", ""),
            }
            for item in (report.get("failures") or [])[:4]
        ],
        "command": command.strip(),
        "error_excerpt": _truncate(error_text, 320),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _build_frontier(
    *,
    signal_counts: Counter[str],
    failures: list[dict[str, Any]],
    strategy: dict[str, Any],
    history_diagnostics: dict[str, Any],
) -> list[dict[str, Any]]:
    if not signal_counts:
        signal_counts = Counter({"verification-loop": max(1, len(failures))})

    total_hits = sum(signal_counts.values()) or 1
    frontier: list[dict[str, Any]] = []
    suppressed = set(history_diagnostics.get("suppressed_signals") or [])
    weighted_candidates: list[dict[str, Any]] = []

    for signal_id, count in signal_counts.items():
        signal = _find_signal(signal_id)
        intent = str(signal.get("intent") or "repair")
        strategy_weight = float(strategy.get(intent) or 0.0)
        heuristic_score = 0.26 + (count / total_hits) * 0.34 + min(len(failures), 5) * 0.03 + strategy_weight * 0.32
        rationale_parts = [f"{count} matched failure or governance signals in the recent evidence window."]

        if signal_id in suppressed:
            heuristic_score -= 0.16
            rationale_parts.append("The same signal has repeated in recent evolution cycles, so it is slightly suppressed.")
        if history_diagnostics.get("repair_loop_detected") and intent == "repair":
            heuristic_score -= 0.08
            rationale_parts.append("Recent repair attempts are looping, so another pure repair mutation is de-emphasized.")
        if history_diagnostics.get("stagnation_detected") and intent in {"innovate", "optimize"}:
            heuristic_score += 0.08
            rationale_parts.append("Stagnation diagnostics boost materially different tactics.")

        weighted_candidates.append(
            {
                "candidate_id": slugify(f"{signal_id}-{count}", signal_id),
                "signal_id": signal_id,
                "title": signal["title"],
                "intent": intent,
                "strategy_weight": round(strategy_weight, 3),
                "heuristic_score": round(max(0.05, min(0.99, heuristic_score)), 3),
                "rationale": " ".join(rationale_parts),
                "guardrails": list(signal["guardrails"]),
                "validation": list(signal["validation"]),
            }
        )

    weighted_candidates.sort(key=lambda item: item["heuristic_score"], reverse=True)
    frontier.extend(weighted_candidates[:4])

    if history_diagnostics.get("repair_loop_detected"):
        loop_signal = _find_signal("repair-loop-detected")
        frontier.insert(
            0,
            {
                "candidate_id": "break-repair-loop",
                "signal_id": "repair-loop-detected",
                "title": loop_signal["title"],
                "intent": loop_signal.get("intent", "innovate"),
                "strategy_weight": round(float(strategy.get(loop_signal.get("intent", "innovate")) or 0.0), 3),
                "heuristic_score": round(min(0.99, 0.76 + min(float(history_diagnostics.get("recent_failure_ratio") or 0.0), 0.2)), 3),
                "rationale": "Recent cycles show repeated repair pressure on similar failures, so the next mutation should break the loop instead of repeating it.",
                "guardrails": list(loop_signal["guardrails"]),
                "validation": list(loop_signal["validation"]),
            },
        )

    if history_diagnostics.get("saturation_detected"):
        steady_signal = _find_signal("force-steady-state")
        frontier.insert(
            0,
            {
                "candidate_id": "enter-steady-state",
                "signal_id": "force-steady-state",
                "title": steady_signal["title"],
                "intent": steady_signal.get("intent", "optimize"),
                "strategy_weight": round(float(strategy.get(steady_signal.get("intent", "optimize")) or 0.0), 3),
                "heuristic_score": 0.88,
                "rationale": "Recent empty cycles indicate saturation, so the loop should slow down and emphasize clearer verification artifacts.",
                "guardrails": list(steady_signal["guardrails"]),
                "validation": list(steady_signal["validation"]),
            },
        )

    top_items = frontier[:3]
    if len(top_items) > 1:
        blended = [item["signal_id"] for item in top_items]
        dominant_intent = max(("repair", "optimize", "innovate"), key=lambda key: float(strategy.get(key) or 0.0))
        heuristic_score = round(min(0.99, max(item["heuristic_score"] for item in top_items) + 0.04), 3)
        frontier.append(
            {
                "candidate_id": slugify("blended-" + "-".join(blended), "blended"),
                "signal_id": "blended",
                "title": "Blend the strongest recent failure patterns",
                "intent": dominant_intent,
                "strategy_weight": round(float(strategy.get(dominant_intent) or 0.0), 3),
                "heuristic_score": heuristic_score,
                "rationale": f"Multiple signal clusters repeated recently, so the skill should capture the shared guardrails under the `{strategy.get('name', 'balanced')}` strategy.",
                "guardrails": [
                    "Start from the narrowest reproducible failure before broad retries.",
                    "Record repo/cwd/entrypoint assumptions explicitly when failures mention paths or imports.",
                    "End every fix attempt with an explicit verifier command and observed result.",
                ],
                "validation": [
                    "Run the minimal reproducer first, then the broader regression command.",
                    "Persist the command and result summary in the evolution report.",
                ],
            },
        )

    frontier.sort(key=lambda item: item["heuristic_score"], reverse=True)
    return frontier[:5]


def _extract_failure_samples(
    *,
    user_id: str,
    session_id: str = "",
    days: int = 30,
    limit: int = MAX_FAILURE_SAMPLES,
    extra_error_text: str = "",
    extra_command: str = "",
) -> list[dict[str, Any]]:
    from webot.trajectory import list_trajectories

    cutoff = _utc_now() - timedelta(days=max(1, days))
    entries = list_trajectories(completed=None, limit=10000, user_id=user_id)
    failures: list[dict[str, Any]] = []

    for entry in entries:
        timestamp = _parse_iso(str(entry.get("timestamp") or ""))
        if timestamp is not None and timestamp < cutoff:
            continue
        if session_id and entry.get("session_id") != session_id:
            continue

        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        conversations = entry.get("conversations") if isinstance(entry.get("conversations"), list) else []
        convo_text = "\n".join(_normalize_text(msg.get("value")) for msg in conversations if isinstance(msg, dict))
        searchable = "\n".join(
            part for part in [
                convo_text,
                _normalize_text(metadata.get("error")),
                _normalize_text(metadata.get("stderr")),
                _normalize_text(metadata.get("stdout")),
                _normalize_text(metadata.get("command")),
            ] if part
        ).strip()
        if not searchable:
            continue

        completed = bool(entry.get("completed"))
        looks_like_failure = (not completed) or any(term in searchable.lower() for term in ERROR_TERMS)
        if not looks_like_failure:
            continue

        failures.append(
            {
                "timestamp": str(entry.get("timestamp") or ""),
                "session_id": str(entry.get("session_id") or ""),
                "completed": completed,
                "command": _truncate(_normalize_text(metadata.get("command")), 180),
                "error_excerpt": _truncate(
                    _normalize_text(metadata.get("error") or metadata.get("stderr") or searchable),
                    280,
                ),
                "searchable_text": searchable,
            }
        )

    if extra_error_text.strip():
        failures.insert(
            0,
            {
                "timestamp": _utc_now_iso(),
                "session_id": session_id or "",
                "completed": False,
                "command": _truncate(extra_command, 180),
                "error_excerpt": _truncate(extra_error_text, 280),
                "searchable_text": f"{extra_command}\n{extra_error_text}".strip(),
            },
        )

    return failures[: max(1, min(limit, MAX_FAILURE_SAMPLES))]


def analyze_skill_evolution(
    user_id: str,
    *,
    name: str,
    session_id: str = "",
    days: int = 30,
    limit: int = 8,
    error_text: str = "",
    command: str = "",
    strategy: str = "auto",
) -> dict[str, Any]:
    skill = get_skill(user_id, name=name)
    if not skill:
        return {"success": False, "error": f"Skill '{name}' not found"}

    failures = _extract_failure_samples(
        user_id=user_id,
        session_id=session_id,
        days=days,
        limit=limit,
        extra_error_text=error_text,
        extra_command=command,
    )
    skill_body = str(skill.get("body") or "")
    signal_counts: Counter[str] = Counter()
    vocabulary: Counter[str] = Counter()

    for failure in failures:
        signal_counts.update(_detect_signals(failure["searchable_text"]))
        vocabulary.update(_tokenize(failure["searchable_text"]))

    history_diagnostics = _analyze_recent_history(user_id, name)
    signal_counts = _augment_signal_counts(signal_counts, history_diagnostics)
    resolved_strategy = _resolve_strategy(
        strategy=strategy,
        signal_counts=signal_counts,
        history_diagnostics=history_diagnostics,
    )
    frontier = _build_frontier(
        signal_counts=signal_counts,
        failures=failures,
        strategy=resolved_strategy,
        history_diagnostics=history_diagnostics,
    )
    top_terms = [term for term, _ in vocabulary.most_common(10)]
    top_signals = [
        {
            "signal_id": signal_id,
            "count": count,
            "title": _find_signal(signal_id)["title"],
            "intent": _find_signal(signal_id).get("intent", "repair"),
        }
        for signal_id, count in signal_counts.most_common(5)
    ]
    local_state = _capture_local_state(user_id, name, skill)
    validation_report = _build_validation_report(
        skill_name=name,
        candidate_id=(frontier[0]["candidate_id"] if frontier else ""),
        strategy_name=resolved_strategy["name"],
        commands=_validation_entries_from_failures(
            failures=failures,
            command=command,
            error_text=error_text,
        ),
        env_fingerprint=_build_env_fingerprint(
            user_id=user_id,
            skill_name=name,
            local_state=local_state,
        ),
        duration_ms=None,
    )

    summary_parts = []
    if failures:
        summary_parts.append(f"Observed {len(failures)} recent failure-like traces.")
    else:
        summary_parts.append("No recent failed trajectories were found, so the report relies on explicit execution errors and heuristic defaults.")
    if top_signals:
        summary_parts.append("Dominant signals: " + ", ".join(item["signal_id"] for item in top_signals[:3]) + ".")
    if top_terms:
        summary_parts.append("Recurring terms: " + ", ".join(top_terms[:6]) + ".")
    summary_parts.append(f"Resolved strategy: {resolved_strategy['name']}.")
    if history_diagnostics.get("repair_loop_detected"):
        summary_parts.append("Recent history suggests a repair loop.")
    elif history_diagnostics.get("stagnation_detected"):
        summary_parts.append("Recent history suggests stagnation or repeated empty cycles.")

    report = {
        "success": True,
        "generated_at": _utc_now_iso(),
        "skill_name": name,
        "session_id": session_id,
        "window_days": days,
        "failure_count": len(failures),
        "summary": " ".join(summary_parts),
        "top_terms": top_terms,
        "signals": top_signals,
        "strategy": resolved_strategy,
        "history_diagnostics": history_diagnostics,
        "local_state": local_state,
        "validation_report": validation_report,
        "failures": [
            {
                "timestamp": item["timestamp"],
                "session_id": item["session_id"],
                "command": item["command"],
                "error_excerpt": item["error_excerpt"],
            }
            for item in failures
        ],
        "frontier": frontier,
        "skill_has_managed_section": EVOLUTION_BEGIN in skill_body or EVOLUTION_BEGIN in str(skill.get("content") or ""),
    }
    return report


def render_evolution_report(report: dict[str, Any]) -> str:
    if not report.get("success"):
        return f"# Skill Evolution Report\n\nError: {report.get('error', 'unknown error')}\n"

    lines = [
        "# Skill Evolution Report",
        "",
        f"- Skill: `{report.get('skill_name', '')}`",
        f"- Generated at: `{report.get('generated_at', '')}`",
        f"- Session: `{report.get('session_id', '') or 'all recent sessions'}`",
        f"- Window: last `{report.get('window_days', 0)}` days",
        f"- Failure-like traces: `{report.get('failure_count', 0)}`",
        "",
        "## Summary",
        "",
        report.get("summary", "No summary available."),
        "",
    ]

    strategy = report.get("strategy") or {}
    if strategy:
        lines.extend(
            [
                "## Strategy",
                "",
                f"- Resolved strategy: `{strategy.get('name', 'balanced')}` ({strategy.get('label', 'Balanced')})",
                f"- Intent mix: repair `{strategy.get('repair', 0)}`, optimize `{strategy.get('optimize', 0)}`, innovate `{strategy.get('innovate', 0)}`",
            ]
        )
        for reason in strategy.get("rationale") or []:
            lines.append(f"- {reason}")
        lines.append("")

    history_diagnostics = report.get("history_diagnostics") or {}
    if history_diagnostics:
        lines.extend(
            [
                "## Governance Diagnostics",
                "",
                f"- Suppressed signals: {', '.join(history_diagnostics.get('suppressed_signals') or []) or '(none)'}",
                f"- Consecutive repair cycles: `{history_diagnostics.get('consecutive_repair_count', 0)}`",
                f"- Consecutive empty cycles: `{history_diagnostics.get('consecutive_empty_cycles', 0)}`",
                f"- Recent failure ratio: `{history_diagnostics.get('recent_failure_ratio', 0)}`",
                "",
            ]
        )

    signals = report.get("signals") or []
    if signals:
        lines.extend(["## Dominant Signals", ""])
        for signal in signals:
            lines.append(
                f"- `{signal['signal_id']}` ({signal['count']} hits, intent `{signal.get('intent', 'repair')}`): {signal['title']}"
            )
        lines.append("")

    frontier = report.get("frontier") or []
    if frontier:
        lines.extend(["## Candidate Frontier", ""])
        for index, item in enumerate(frontier, start=1):
            lines.append(
                f"{index}. `{item['candidate_id']}` — score `{item['heuristic_score']}` — {item['title']} (intent `{item.get('intent', 'repair')}`)"
            )
            lines.append(f"   rationale: {item['rationale']}")
        lines.append("")

    failures = report.get("failures") or []
    if failures:
        lines.extend(["## Recent Evidence", ""])
        for item in failures[:5]:
            stamp = item.get("timestamp") or "unknown-time"
            session_id = item.get("session_id") or "unknown-session"
            command = item.get("command") or "(no command captured)"
            excerpt = item.get("error_excerpt") or "(no excerpt)"
            lines.append(f"- `{stamp}` session=`{session_id}` command=`{command}`")
            lines.append(f"  - {excerpt}")
        lines.append("")

    top_terms = report.get("top_terms") or []
    if top_terms:
        lines.extend(["## Recurring Terms", "", "- " + ", ".join(top_terms[:10]), ""])

    local_state = report.get("local_state") or {}
    if local_state:
        lines.extend(
            [
                "## Local State Snapshot",
                "",
                f"- Python/platform: `{local_state.get('python_version', '')}` / `{local_state.get('platform', '')}`",
                f"- Skill path: `{local_state.get('skill_path', '')}`",
                f"- Support files: `{local_state.get('support_files_count', 0)}`",
                f"- Feedback history entries: `{local_state.get('feedback_history_entries', 0)}`",
                f"- Runtime failure entries: `{local_state.get('runtime_failure_entries', 0)}`",
                "",
            ]
        )

    validation_report = report.get("validation_report") or {}
    if validation_report:
        lines.extend(
            [
                "## Validation Report",
                "",
                f"- Overall ok: `{validation_report.get('overall_ok', False)}`",
                f"- Asset id: `{validation_report.get('asset_id', '')}`",
            ]
        )
        for item in validation_report.get("commands") or []:
            lines.append(f"- `{item.get('command', '')}` -> ok=`{item.get('ok', False)}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _build_managed_section(
    *,
    report: dict[str, Any],
    candidate: dict[str, Any],
    command: str = "",
    error_text: str = "",
) -> str:
    lines = [
        "## Self-Evolution Loop",
        "",
        "This block is auto-maintained by ClawCross's lightweight EvoSkill adapter.",
        "Prefer `skill_evolution_report` / `skill_evolution_apply` or `selfskill/scripts/evolve_skill.py` over manual edits here.",
        "",
        f"- Updated at: `{report.get('generated_at', _utc_now_iso())}`",
        f"- Strategy: `{(report.get('strategy') or {}).get('name', 'balanced')}`",
        f"- Heuristic candidate: `{candidate.get('candidate_id', 'unknown')}`",
        f"- Heuristic score: `{candidate.get('heuristic_score', 0)}`",
        "",
        "### Trigger Summary",
        "",
        report.get("summary", "No recent failure summary available."),
        "",
    ]

    strategy = report.get("strategy") or {}
    if strategy:
        lines.extend(
            [
                "### Strategy Rationale",
                "",
                f"- Intent mix: repair `{strategy.get('repair', 0)}`, optimize `{strategy.get('optimize', 0)}`, innovate `{strategy.get('innovate', 0)}`",
            ]
        )
        for reason in strategy.get("rationale") or []:
            lines.append(f"- {reason}")
        lines.append("")

    if command.strip():
        lines.extend(["### Latest Trigger Command", "", f"`{command.strip()}`", ""])
    if error_text.strip():
        lines.extend(["### Latest Error Excerpt", "", "```text", _truncate(error_text, 2000), "```", ""])

    history_diagnostics = report.get("history_diagnostics") or {}
    if history_diagnostics:
        lines.extend(
            [
                "### Governance Snapshot",
                "",
                f"- Suppressed signals: {', '.join(history_diagnostics.get('suppressed_signals') or []) or '(none)'}",
                f"- Consecutive repair cycles: `{history_diagnostics.get('consecutive_repair_count', 0)}`",
                f"- Consecutive empty cycles: `{history_diagnostics.get('consecutive_empty_cycles', 0)}`",
                f"- Recent failure ratio: `{history_diagnostics.get('recent_failure_ratio', 0)}`",
                "",
            ]
        )

    lines.extend(["### Operating Adjustments", ""])
    for index, item in enumerate(candidate.get("guardrails") or [], start=1):
        lines.append(f"{index}. {item}")
    lines.append("")

    lines.extend(["### Validation Loop", ""])
    for index, item in enumerate(candidate.get("validation") or [], start=1):
        lines.append(f"{index}. {item}")
    lines.append("")

    failures = report.get("failures") or []
    if failures:
        lines.extend(["### Recent Evidence", ""])
        for item in failures[:4]:
            lines.append(
                f"- `{item.get('timestamp') or 'unknown-time'}` `{item.get('session_id') or 'unknown-session'}` — {_truncate(item.get('error_excerpt') or '', 160)}"
            )
        lines.append("")

    lines.extend(["### Candidate Frontier Snapshot", ""])
    for item in (report.get("frontier") or [])[:4]:
        lines.append(
            f"- `{item['candidate_id']}` score `{item['heuristic_score']}` — {item['title']} (intent `{item.get('intent', 'repair')}`)"
        )

    local_state = report.get("local_state") or {}
    if local_state:
        lines.extend(
            [
                "",
                "### Local State Snapshot",
                "",
                f"- Python/platform: `{local_state.get('python_version', '')}` / `{local_state.get('platform', '')}`",
                f"- Feedback history entries: `{local_state.get('feedback_history_entries', 0)}`",
                f"- Runtime failure entries: `{local_state.get('runtime_failure_entries', 0)}`",
            ]
        )

    return "\n".join(lines).strip()


def _upsert_managed_block(content: str, block: str) -> str:
    text = content.rstrip()
    replacement = f"{EVOLUTION_BEGIN}\n{block}\n{EVOLUTION_END}"
    pattern = re.compile(
        rf"{re.escape(EVOLUTION_BEGIN)}.*?{re.escape(EVOLUTION_END)}",
        re.DOTALL,
    )
    if pattern.search(text):
        return pattern.sub(replacement, text)
    return text + "\n\n" + replacement + "\n"


def _persist_skill_evolution_state(
    *,
    user_id: str,
    skill_name: str,
    report: dict[str, Any],
    applied_candidate: dict[str, Any],
    report_path: str,
    validation_report_path: str,
    validation_report: dict[str, Any],
    source: str,
    command: str,
    error_text: str,
    cycle_signature: str,
    empty_cycle: bool,
    updated: bool,
) -> tuple[Path, Path]:
    state_dir = _skill_evolution_dir(user_id, skill_name)
    feedback_path = state_dir / "feedback_history.jsonl"
    frontier_path = state_dir / "frontier.json"

    entry = {
        "timestamp": _utc_now_iso(),
        "skill_name": skill_name,
        "source": source,
        "command": command,
        "error_excerpt": _truncate(error_text, 280),
        "candidate_id": applied_candidate.get("candidate_id"),
        "heuristic_score": applied_candidate.get("heuristic_score"),
        "intent": applied_candidate.get("intent", "repair"),
        "strategy": (report.get("strategy") or {}).get("name", "balanced"),
        "signal_ids": [item.get("signal_id", "") for item in (report.get("signals") or [])],
        "report_path": report_path,
        "validation_report_path": validation_report_path,
        "validation_overall_ok": validation_report.get("overall_ok"),
        "failure_count": report.get("failure_count", 0),
        "cycle_signature": cycle_signature,
        "empty_cycle": empty_cycle,
        "updated": updated,
    }
    _append_jsonl(feedback_path, entry)

    frontier_state = _safe_json_load(frontier_path, {"history": [], "frontier": []})
    frontier_state["updated_at"] = _utc_now_iso()
    frontier_state["latest_report"] = report_path
    frontier_state["latest_validation_report"] = validation_report_path
    frontier_state["frontier"] = report.get("frontier") or []
    frontier_state["strategy"] = report.get("strategy") or {}
    frontier_state["history_diagnostics"] = report.get("history_diagnostics") or {}
    frontier_state["local_state"] = report.get("local_state") or {}
    frontier_state.setdefault("history", []).append(entry)
    frontier_state["history"] = frontier_state["history"][-25:]
    _write_json(frontier_path, frontier_state)

    return feedback_path, frontier_path


def apply_skill_evolution(
    user_id: str,
    *,
    name: str,
    session_id: str = "",
    days: int = 30,
    limit: int = 8,
    error_text: str = "",
    command: str = "",
    source: str = "manual",
    strategy: str = "auto",
) -> dict[str, Any]:
    skill = get_skill(user_id, name=name)
    if not skill:
        return {"success": False, "error": f"Skill '{name}' not found"}

    report = analyze_skill_evolution(
        user_id,
        name=name,
        session_id=session_id,
        days=days,
        limit=limit,
        error_text=error_text,
        command=command,
        strategy=strategy,
    )
    if not report.get("success"):
        return report

    frontier = report.get("frontier") or []
    candidate = frontier[0] if frontier else {
        "candidate_id": "fallback",
        "heuristic_score": 0.4,
        "guardrails": [
            "Capture the exact failing command and stderr before changing the skill.",
            "State a deterministic verifier command after every change.",
        ],
        "validation": [
            "Rerun the smallest reproducer after the skill update.",
        ],
    }

    cycle_signature = _build_cycle_signature(
        report=report,
        candidate=candidate,
        command=command,
        error_text=error_text,
    )
    previous_entries = _load_jsonl(_feedback_history_path(user_id, name), limit=1)
    previous_signature = previous_entries[0].get("cycle_signature") if previous_entries else ""
    empty_cycle = bool(previous_signature and previous_signature == cycle_signature)

    managed_block = _build_managed_section(
        report=report,
        candidate=candidate,
        command=command,
        error_text=error_text,
    )
    updated = False
    edit_path = str(skill.get("path") or "")
    if not empty_cycle:
        updated_content = _upsert_managed_block(str(skill.get("content") or ""), managed_block)
        updated = updated_content != str(skill.get("content") or "")
        if updated:
            edit_result = edit_skill(user_id, name=name, content=updated_content)
            if not edit_result.get("success"):
                return edit_result
            edit_path = edit_result.get("path", edit_path)

    report_md = render_evolution_report(report)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    latest_file = f"references/evolution/latest-report.md"
    stamped_file = f"references/evolution/report-{stamp}.md"
    latest_result = write_skill_file(user_id, name=name, file_path=latest_file, file_content=report_md)
    stamped_result = write_skill_file(user_id, name=name, file_path=stamped_file, file_content=report_md)
    validation_report = report.get("validation_report") or {}
    validation_json = json.dumps(validation_report, ensure_ascii=False, indent=2)
    latest_validation_file = "references/evolution/latest-validation-report.json"
    stamped_validation_file = f"references/evolution/validation-report-{stamp}.json"
    latest_validation_result = write_skill_file(
        user_id,
        name=name,
        file_path=latest_validation_file,
        file_content=validation_json,
    )
    stamped_validation_result = write_skill_file(
        user_id,
        name=name,
        file_path=stamped_validation_file,
        file_content=validation_json,
    )

    report_path = (
        stamped_result.get("path")
        or latest_result.get("path")
        or edit_path
        or ""
    )
    validation_report_path = (
        stamped_validation_result.get("path")
        or latest_validation_result.get("path")
        or ""
    )
    feedback_path, frontier_path = _persist_skill_evolution_state(
        user_id=user_id,
        skill_name=name,
        report=report,
        applied_candidate=candidate,
        report_path=str(report_path),
        validation_report_path=str(validation_report_path),
        validation_report=validation_report,
        source=source,
        command=command,
        error_text=error_text,
        cycle_signature=cycle_signature,
        empty_cycle=empty_cycle,
        updated=updated,
    )

    return {
        "success": True,
        "message": (
            f"Detected a repeated self-evolution cycle for skill '{name}'; skipped rewriting the managed block."
            if empty_cycle
            else f"Applied self-evolution block to skill '{name}'"
        ),
        "path": edit_path,
        "updated": updated,
        "empty_cycle": empty_cycle,
        "report_path": report_path,
        "latest_report_path": latest_result.get("path", ""),
        "validation_report_path": validation_report_path,
        "latest_validation_report_path": latest_validation_result.get("path", ""),
        "feedback_history_path": str(feedback_path),
        "frontier_path": str(frontier_path),
        "strategy_name": (report.get("strategy") or {}).get("name", "balanced"),
        "candidate_id": candidate.get("candidate_id"),
        "heuristic_score": candidate.get("heuristic_score"),
        "failure_count": report.get("failure_count", 0),
        "summary": report.get("summary", ""),
    }


def record_failure_feedback(
    *,
    user_id: str,
    session_id: str,
    entry: dict[str, Any],
) -> dict[str, Any]:
    """Persist a lightweight failure digest whenever a failed trajectory is saved."""
    evolution_root = _evolution_root(user_id)
    history_path = evolution_root / "runtime_failures.jsonl"
    digest_path = evolution_root / "latest_failure_digest.md"

    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    searchable = "\n".join(
        part for part in [
            "\n".join(_normalize_text(msg.get("value")) for msg in entry.get("conversations", []) if isinstance(msg, dict)),
            _normalize_text(metadata.get("error")),
            _normalize_text(metadata.get("stderr")),
            _normalize_text(metadata.get("command")),
        ] if part
    ).strip()
    signals = _detect_signals(searchable)
    record = {
        "timestamp": entry.get("timestamp") or _utc_now_iso(),
        "session_id": session_id,
        "command": _truncate(_normalize_text(metadata.get("command")), 180),
        "error_excerpt": _truncate(_normalize_text(metadata.get("error") or metadata.get("stderr") or searchable), 280),
        "signals": dict(signals),
        "signal_ids": list(signals.keys()),
    }
    _append_jsonl(history_path, record)

    recent_lines = [
        "# Latest Failure Digest",
        "",
        f"- Updated at: `{_utc_now_iso()}`",
        f"- Session: `{session_id or 'unknown'}`",
        f"- Command: `{record['command'] or '(not captured)'}`",
        "",
        "## Error Excerpt",
        "",
        record["error_excerpt"] or "(empty)",
        "",
    ]
    if signals:
        recent_lines.extend(["## Signals", ""])
        for signal_id, count in signals.most_common():
            recent_lines.append(f"- `{signal_id}`: {count} hits")
    digest_path.write_text("\n".join(recent_lines).rstrip() + "\n", encoding="utf-8")

    return {
        "success": True,
        "history_path": str(history_path),
        "digest_path": str(digest_path),
        "signals": dict(signals),
    }


def summarize_execution_failure(
    *,
    command: str,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 1,
    strategy: str = "auto",
    duration_ms: int | None = None,
) -> dict[str, Any]:
    text = "\n".join(part for part in [command, stderr, stdout] if part).strip()
    signal_counts = _detect_signals(text)
    history_diagnostics = {
        "history_entries": 0,
        "suppressed_signals": [],
        "recent_intents": [],
        "consecutive_repair_count": 0,
        "empty_cycle_count": 0,
        "consecutive_empty_cycles": 0,
        "consecutive_failure_count": 1 if exit_code != 0 else 0,
        "recent_failure_count": 1 if exit_code != 0 else 0,
        "recent_failure_ratio": 1.0 if exit_code != 0 else 0.0,
        "repair_loop_detected": False,
        "stagnation_detected": False,
        "saturation_detected": False,
        "signal_freq": {},
    }
    resolved_strategy = _resolve_strategy(
        strategy=strategy,
        signal_counts=signal_counts,
        history_diagnostics=history_diagnostics,
    )
    frontier = _build_frontier(
        signal_counts=signal_counts,
        failures=[{"searchable_text": text, "error_excerpt": _truncate(stderr or stdout, 280)}],
        strategy=resolved_strategy,
        history_diagnostics=history_diagnostics,
    )
    summary = [
        f"Command exited with code {exit_code}.",
    ]
    if command.strip():
        summary.append(f"Command: {command.strip()}.")
    if signal_counts:
        summary.append("Signals: " + ", ".join(signal_id for signal_id, _ in signal_counts.most_common(3)) + ".")
    if stderr.strip():
        summary.append("stderr carried the strongest failure evidence.")
    elif stdout.strip():
        summary.append("stdout was used as fallback failure evidence.")

    validation_report = _build_validation_report(
        skill_name="repo-skill",
        candidate_id=(frontier[0]["candidate_id"] if frontier else ""),
        strategy_name=resolved_strategy["name"],
        commands=_validation_entries_from_failures(
            failures=[],
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        ),
        env_fingerprint=_build_env_fingerprint(skill_name="repo-skill"),
        duration_ms=duration_ms,
    )

    return {
        "generated_at": _utc_now_iso(),
        "exit_code": exit_code,
        "command": command,
        "summary": " ".join(summary),
        "stderr_excerpt": _truncate(stderr, 1200),
        "stdout_excerpt": _truncate(stdout, 1200),
        "strategy": resolved_strategy,
        "history_diagnostics": history_diagnostics,
        "validation_report": validation_report,
        "frontier": frontier,
    }


def _guess_repo_root(skill_path: Path) -> Path:
    for candidate in [skill_path.parent, *skill_path.parents]:
        if (candidate / ".git").exists():
            return candidate
    return skill_path.parent


def update_markdown_skill_document(
    *,
    skill_path: str | Path,
    command: str,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 1,
    force: bool = False,
    strategy: str = "auto",
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """Update a repository Markdown skill file with the latest execution learnings."""
    target = Path(skill_path).expanduser().resolve()
    if not target.is_file():
        return {"success": False, "error": f"Skill document not found: {target}"}

    if exit_code == 0 and not force:
        return {
            "success": True,
            "updated": False,
            "message": "Command succeeded; no self-evolution update written.",
        }

    report = summarize_execution_failure(
        command=command,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        strategy=strategy,
        duration_ms=duration_ms,
    )
    best_candidate = (report.get("frontier") or [{}])[0]
    repo_local_state = {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "skill_path": str(target),
        "support_files_count": 0,
        "feedback_history_entries": 0,
        "runtime_failure_entries": 0,
    }
    managed_block = _build_managed_section(
        report={
            "generated_at": report.get("generated_at"),
            "summary": report.get("summary"),
            "strategy": report.get("strategy") or {},
            "history_diagnostics": report.get("history_diagnostics") or {},
            "local_state": repo_local_state,
            "frontier": report.get("frontier") or [],
            "failures": [
                {
                    "timestamp": report.get("generated_at"),
                    "session_id": "repo-skill",
                    "error_excerpt": report.get("stderr_excerpt") or report.get("stdout_excerpt") or "",
                }
            ],
        },
        candidate=best_candidate,
        command=command,
        error_text=stderr or stdout,
    )

    original = target.read_text(encoding="utf-8", errors="replace")
    updated = _upsert_managed_block(original, managed_block)
    target.write_text(updated, encoding="utf-8")

    repo_root = _guess_repo_root(target)
    reports_dir = repo_root / "docs" / "self-evolution"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report_path = reports_dir / f"{slugify(target.stem, 'skill')}-latest.md"
    report_path.write_text(
        "\n".join(
            [
                "# Repository Skill Self-Evolution Report",
                "",
                f"- Skill file: `{target}`",
                f"- Generated at: `{report['generated_at']}`",
                f"- Exit code: `{exit_code}`",
                "",
                "## Summary",
                "",
                report["summary"],
                "",
                "## stderr",
                "",
                "```text",
                report.get("stderr_excerpt") or "(empty)",
                "```",
                "",
                "## stdout",
                "",
                "```text",
                report.get("stdout_excerpt") or "(empty)",
                "```",
                "",
            ]
        ).rstrip()
        + "\n",
        encoding="utf-8",
    )
    stamped_path = reports_dir / f"{slugify(target.stem, 'skill')}-{stamp}.md"
    stamped_path.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    validation_report_path = reports_dir / f"{slugify(target.stem, 'skill')}-validation-latest.json"
    validation_report_path.write_text(
        json.dumps(report.get("validation_report") or {}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    stamped_validation_path = reports_dir / f"{slugify(target.stem, 'skill')}-validation-{stamp}.json"
    stamped_validation_path.write_text(validation_report_path.read_text(encoding="utf-8"), encoding="utf-8")

    return {
        "success": True,
        "updated": True,
        "path": str(target),
        "report_path": str(report_path),
        "archive_report_path": str(stamped_path),
        "validation_report_path": str(validation_report_path),
        "archive_validation_report_path": str(stamped_validation_path),
        "strategy_name": (report.get("strategy") or {}).get("name", "balanced"),
        "candidate_id": best_candidate.get("candidate_id"),
        "heuristic_score": best_candidate.get("heuristic_score"),
        "summary": report.get("summary"),
    }
