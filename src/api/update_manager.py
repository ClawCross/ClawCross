from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from utils.runtime_paths import DATA_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NPM_PACKAGE_NAME = "clawcross"
NPM_REGISTRY_BASE = os.getenv("CLAWCROSS_NPM_REGISTRY", "https://registry.npmjs.org").rstrip("/")
NPM_VALID_DIST_TAG_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def _deployment_mode() -> str:
    """Detect whether running from a git working tree or an npm-installed copy."""
    if (PROJECT_ROOT / ".git").is_dir():
        return "git"
    if "node_modules" in PROJECT_ROOT.parts:
        return "npm"
    pkg = PROJECT_ROOT / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            if data.get("name") == NPM_PACKAGE_NAME:
                return "npm"
        except Exception:
            pass
    return "unknown"


def _status_dir() -> Path:
    return DATA_DIR / "runtime"


def _status_path() -> Path:
    return _status_dir() / "self_update_status.json"


def _log_path() -> Path:
    return _status_dir() / "self_update.log"


# Backward-compat module-level constants (lazy redirect; older imports keep working)
STATUS_DIR = _status_dir()
STATUS_FILE = _status_path()
LOG_FILE = _log_path()

IN_PROGRESS_STATUSES = {"queued", "checking", "updating", "restarting"}


def _ensure_status_dir() -> None:
    _status_dir().mkdir(parents=True, exist_ok=True)


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_status_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_update_status() -> dict[str, Any]:
    if not STATUS_FILE.is_file():
        return {
            "status": "idle",
            "message": "尚未执行更新。",
            "updated_at": "",
        }
    try:
        payload = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {
        "status": "failed",
        "message": "更新状态文件损坏。",
        "updated_at": _utc_timestamp(),
    }


def write_update_status(status: str, message: str, **extra: Any) -> dict[str, Any]:
    current = read_update_status()
    payload = {
        "run_id": extra.pop("run_id", current.get("run_id") or ""),
        "status": status,
        "message": message,
        "updated_at": _utc_timestamp(),
        "started_at": extra.pop("started_at", current.get("started_at") or ""),
        "completed_at": extra.pop("completed_at", current.get("completed_at") or ""),
    }
    payload.update(extra)
    _atomic_write_json(STATUS_FILE, payload)
    return payload


def _run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    if not shutil.which("git"):
        raise RuntimeError("git 不在 PATH 中，无法执行更新。")
    return subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _git_output(*args: str) -> str:
    return _run_git(*args).stdout.strip()


def _git_output_or_default(default: str, *args: str) -> str:
    try:
        return _git_output(*args)
    except Exception:
        return default


def _detect_upstream(branch: str) -> str:
    upstream = _git_output_or_default("", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream:
        return upstream
    return f"origin/{branch}"


def _tail_log(limit: int = 120) -> list[str]:
    if not LOG_FILE.is_file():
        return []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    return lines[-limit:]


def _log_sections() -> list[tuple[str, list[str]]]:
    """Split update log into run-scoped sections keyed by run_id."""
    if not LOG_FILE.is_file():
        return []
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    sections: list[tuple[str, list[str]]] = []
    current_run_id = ""
    current_lines: list[str] = []
    marker = "queued update-"

    for line in lines:
        if marker in line:
            if current_lines:
                sections.append((current_run_id, current_lines))
            current_lines = [line]
            tail = line.split(marker, 1)[1].strip()
            current_run_id = tail.split()[0] if tail else ""
        elif current_lines:
            current_lines.append(line)

    if current_lines:
        sections.append((current_run_id, current_lines))
    return sections


def _tail_log_for_run(run_id: str = "", limit: int = 120) -> list[str]:
    sections = _log_sections()
    if not sections:
        return []

    target_lines: list[str] | None = None
    clean_run_id = str(run_id or "").strip()
    if clean_run_id:
        for section_run_id, section_lines in reversed(sections):
            if section_run_id == clean_run_id:
                target_lines = section_lines
                break
    if target_lines is None:
        target_lines = sections[-1][1]
    return target_lines[-limit:]


def collect_repo_state(*, fetch_remote: bool = False) -> dict[str, Any]:
    branch = _git_output_or_default("unknown", "rev-parse", "--abbrev-ref", "HEAD")
    upstream = _detect_upstream(branch)
    if fetch_remote:
        _run_git("fetch", "--quiet", "--tags", "origin")

    current_commit = _git_output_or_default("", "rev-parse", "HEAD")
    current_short = _git_output_or_default("", "rev-parse", "--short", "HEAD")
    current_subject = _git_output_or_default("", "log", "-1", "--pretty=%s", "HEAD")
    latest_commit = _git_output_or_default("", "rev-parse", upstream)
    latest_short = _git_output_or_default("", "rev-parse", "--short", upstream)
    latest_subject = _git_output_or_default("", "log", "-1", "--pretty=%s", upstream)
    dirty = bool(_git_output_or_default("", "status", "--porcelain"))
    has_update = bool(current_commit and latest_commit and current_commit != latest_commit)
    return {
        "repo_root": str(PROJECT_ROOT),
        "branch": branch,
        "upstream": upstream,
        "current_commit": current_commit,
        "current_short_commit": current_short,
        "current_subject": current_subject,
        "latest_commit": latest_commit,
        "latest_short_commit": latest_short,
        "latest_subject": latest_subject,
        "dirty": dirty,
        "has_update": has_update,
    }


def _npm_local_version() -> str:
    pkg = PROJECT_ROOT / "package.json"
    if not pkg.is_file():
        return ""
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
        version = data.get("version")
        if isinstance(version, str):
            return version
    except Exception:
        pass
    return ""


def _npm_registry_version(specifier: str = "latest", timeout: float = 6.0) -> str:
    clean_specifier = str(specifier or "").strip() or "latest"
    if any(ch not in NPM_VALID_DIST_TAG_CHARS for ch in clean_specifier):
        return ""
    url = f"{NPM_REGISTRY_BASE}/{NPM_PACKAGE_NAME}/{clean_specifier}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        version = payload.get("version")
        if isinstance(version, str):
            return version
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ""
        raise
    except Exception:
        return ""
    return ""


def collect_npm_state(*, fetch_remote: bool = False, dist_tag: str = "latest") -> dict[str, Any]:
    clean_dist_tag = str(dist_tag or "").strip() or "latest"
    current_version = _npm_local_version()
    latest_version = _npm_registry_version(clean_dist_tag) if fetch_remote else ""
    has_update = bool(current_version and latest_version and current_version != latest_version)
    return {
        "repo_root": str(PROJECT_ROOT),
        "branch": clean_dist_tag,
        "upstream": f"{NPM_REGISTRY_BASE}/{NPM_PACKAGE_NAME}",
        "current_commit": current_version,
        "current_short_commit": f"v{current_version}" if current_version else "",
        "current_subject": "",
        "latest_commit": latest_version,
        "latest_short_commit": f"v{latest_version}" if latest_version else "",
        "latest_subject": "",
        "dirty": False,
        "has_update": has_update,
        "deployment_mode": "npm",
    }


def current_update_snapshot(*, fetch_remote: bool = False) -> dict[str, Any]:
    mode = _deployment_mode()
    status = read_update_status()
    if mode == "npm":
        repo = collect_npm_state(
            fetch_remote=fetch_remote,
            dist_tag=str(status.get("branch") or "latest"),
        )
    else:
        repo = collect_repo_state(fetch_remote=fetch_remote)
        repo.setdefault("deployment_mode", mode)
    merged = dict(status)
    merged.update(repo)
    merged["log_tail"] = _tail_log_for_run(merged.get("run_id", ""))
    merged["in_progress"] = merged.get("status") in IN_PROGRESS_STATUSES
    return merged


def _start_git_update_process(requested_by: str, snapshot: dict[str, Any], branch: str) -> dict[str, Any]:
    if not shutil.which("git"):
        raise RuntimeError("git 不在 PATH 中，无法执行更新。")
    branch = str(branch or "").strip()
    if branch and not all(ch.isalnum() or ch in "._/-" for ch in branch):
        raise RuntimeError("分支名不合法。")
    target_branch = branch or str(snapshot.get("branch") or "").strip() or "main"
    target_upstream = f"origin/{target_branch}"

    run_id = f"update-{uuid.uuid4().hex[:10]}"
    now = _utc_timestamp()
    write_update_status(
        "queued",
        "完整更新任务已创建，等待后台执行。",
        run_id=run_id,
        started_at=now,
        requested_by=requested_by,
        current_commit=snapshot.get("current_commit", ""),
        current_short_commit=snapshot.get("current_short_commit", ""),
        latest_commit=snapshot.get("latest_commit", ""),
        latest_short_commit=snapshot.get("latest_short_commit", ""),
        branch=target_branch,
        upstream=target_upstream,
        dirty=bool(snapshot.get("dirty")),
        deployment_mode="git",
    )

    runner = PROJECT_ROOT / "tools" / "run_self_update.py"
    if not runner.is_file():
        raise RuntimeError(f"未找到更新脚本: {runner}")

    with _log_path().open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n[{_utc_timestamp()}] queued {run_id} by {requested_by} (mode=git)\n")
        log_handle.flush()
        subprocess.Popen(
            [sys.executable, str(runner), "--mode", "git", "--run-id", run_id, "--branch", target_branch],
            cwd=str(PROJECT_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return read_update_status()


def _start_npm_update_process(requested_by: str, snapshot: dict[str, Any], dist_tag: str) -> dict[str, Any]:
    if not shutil.which("npm"):
        raise RuntimeError("npm 不在 PATH 中，无法执行更新。")
    dist_tag = str(dist_tag or "").strip() or "latest"
    if not dist_tag or any(ch not in NPM_VALID_DIST_TAG_CHARS for ch in dist_tag):
        raise RuntimeError("dist-tag 不合法。")

    run_id = f"update-{uuid.uuid4().hex[:10]}"
    now = _utc_timestamp()
    write_update_status(
        "queued",
        f"npm 更新任务已创建，目标通道 {dist_tag}。",
        run_id=run_id,
        started_at=now,
        requested_by=requested_by,
        current_commit=snapshot.get("current_commit", ""),
        current_short_commit=snapshot.get("current_short_commit", ""),
        latest_commit=snapshot.get("latest_commit", ""),
        latest_short_commit=snapshot.get("latest_short_commit", ""),
        branch=dist_tag,
        upstream=f"{NPM_REGISTRY_BASE}/{NPM_PACKAGE_NAME}",
        dirty=False,
        deployment_mode="npm",
    )

    runner = PROJECT_ROOT / "tools" / "run_self_update.py"
    if not runner.is_file():
        raise RuntimeError(f"未找到更新脚本: {runner}")

    with _log_path().open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n[{_utc_timestamp()}] queued {run_id} by {requested_by} (mode=npm tag={dist_tag})\n")
        log_handle.flush()
        subprocess.Popen(
            [sys.executable, str(runner), "--mode", "npm", "--run-id", run_id, "--dist-tag", dist_tag],
            cwd=str(PROJECT_ROOT),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return read_update_status()


def start_update_process(requested_by: str, *, branch: str = "") -> dict[str, Any]:
    snapshot = current_update_snapshot(fetch_remote=False)
    if snapshot.get("status") in IN_PROGRESS_STATUSES:
        raise RuntimeError("已有更新任务在执行中。")
    if _deployment_mode() == "npm":
        return _start_npm_update_process(requested_by, snapshot, branch)
    return _start_git_update_process(requested_by, snapshot, branch)
