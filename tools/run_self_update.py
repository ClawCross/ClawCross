#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from api.update_manager import collect_repo_state, read_update_status, write_update_status


def _run_shell(command: str, *, step: str) -> None:
    if not command.strip():
        return
    write_update_status(step, f"执行命令: {command}")
    proc = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        shell=True,
        text=True,
        capture_output=True,
    )
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    if proc.stderr:
        print(proc.stderr, end="" if proc.stderr.endswith("\n") else "\n", file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"命令执行失败 ({proc.returncode}): {command}")


def _default_restart_command() -> str:
    if os.name == "nt":
        return r'powershell -ExecutionPolicy Bypass -Command "& .\selfskill\scripts\run.ps1 stop; & .\selfskill\scripts\run.ps1 start"'
    return "bash selfskill/scripts/run.sh stop && bash selfskill/scripts/run.sh start"


def _run_git(*args: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), *args],
        capture_output=True,
        text=True,
    )
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    if proc.stderr:
        print(proc.stderr, end="" if proc.stderr.endswith("\n") else "\n", file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {proc.stderr.strip() or proc.stdout.strip()}")


def _git_force_sync(branch: str) -> None:
    upstream = f"origin/{branch}"
    _run_git("fetch", "--quiet", "origin", branch)
    _run_git("checkout", "-B", branch, upstream)
    _run_git("reset", "--hard", upstream)
    _run_git("clean", "-fd")


def _run_git_update(run_id: str, branch_arg: str) -> int:
    write_update_status("checking", "检查 Git 更新状态。", run_id=run_id)
    target_branch = str(branch_arg or "").strip()
    before = collect_repo_state(fetch_remote=True)
    if not target_branch:
        target_branch = str(before.get("branch") or "").strip() or "main"
    write_update_status(
        "checking",
        "已同步远端引用，检查可用更新。",
        run_id=run_id,
        branch=target_branch,
        upstream=f"origin/{target_branch}",
        current_commit=before.get("current_commit", ""),
        current_short_commit=before.get("current_short_commit", ""),
        latest_commit=before.get("latest_commit", ""),
        latest_short_commit=before.get("latest_short_commit", ""),
    )

    branch_changed = target_branch != str(before.get("branch") or "").strip()
    if not before.get("has_update") and not branch_changed:
        write_update_status(
            "done",
            "已经是最新版本，无需更新。",
            run_id=run_id,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            current_commit=before.get("current_commit", ""),
            current_short_commit=before.get("current_short_commit", ""),
            latest_commit=before.get("latest_commit", ""),
            latest_short_commit=before.get("latest_short_commit", ""),
        )
        return 0

    write_update_status("updating", f"完整更新到分支 {target_branch}，远端内容将覆盖本地。", run_id=run_id)
    _git_force_sync(target_branch)

    post_pull = (os.getenv("CLAWCROSS_UPDATE_POST_PULL_CMD", "") or "").strip()
    if post_pull:
        _run_shell(post_pull, step="updating")

    write_update_status("restarting", "代码更新完成，正在重启服务。", run_id=run_id)
    restart_cmd = (os.getenv("CLAWCROSS_UPDATE_RESTART_CMD", "") or "").strip() or _default_restart_command()
    _run_shell(restart_cmd, step="restarting")

    after = collect_repo_state(fetch_remote=False)
    write_update_status(
        "done",
        "更新完成，服务已重启。",
        run_id=run_id,
        completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        branch=target_branch,
        upstream=f"origin/{target_branch}",
        current_commit=after.get("current_commit", ""),
        current_short_commit=after.get("current_short_commit", ""),
        latest_commit=after.get("latest_commit", ""),
        latest_short_commit=after.get("latest_short_commit", ""),
    )
    return 0


def _run_npm_update(run_id: str, dist_tag: str) -> int:
    from api.update_manager import collect_npm_state, NPM_PACKAGE_NAME

    dist_tag = str(dist_tag or "").strip() or "latest"
    write_update_status("checking", f"检查 npm 通道 {dist_tag} 上的最新版本。", run_id=run_id)
    before = collect_npm_state(fetch_remote=True, dist_tag=dist_tag)
    write_update_status(
        "checking",
        "已查询 registry，检查可用版本。",
        run_id=run_id,
        branch=dist_tag,
        current_commit=before.get("current_commit", ""),
        current_short_commit=before.get("current_short_commit", ""),
        latest_commit=before.get("latest_commit", ""),
        latest_short_commit=before.get("latest_short_commit", ""),
    )

    if not before.get("has_update"):
        write_update_status(
            "done",
            "当前已经是最新版本，无需更新。",
            run_id=run_id,
            completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            current_commit=before.get("current_commit", ""),
            current_short_commit=before.get("current_short_commit", ""),
            latest_commit=before.get("latest_commit", ""),
            latest_short_commit=before.get("latest_short_commit", ""),
        )
        return 0

    write_update_status("updating", f"运行 npm install -g {NPM_PACKAGE_NAME}@{dist_tag}", run_id=run_id)
    npm_bin = "npm.cmd" if os.name == "nt" else "npm"
    _run_shell(f"{npm_bin} install -g {NPM_PACKAGE_NAME}@{dist_tag}", step="updating")

    write_update_status("restarting", "npm 包已替换，正在重启服务。", run_id=run_id)
    restart_cmd = (os.getenv("CLAWCROSS_UPDATE_RESTART_CMD", "") or "").strip() or _default_restart_command()
    _run_shell(restart_cmd, step="restarting")

    after = collect_npm_state(fetch_remote=False)
    write_update_status(
        "done",
        "npm 更新完成，服务已重启。",
        run_id=run_id,
        completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        branch=dist_tag,
        current_commit=after.get("current_commit", ""),
        current_short_commit=after.get("current_short_commit", ""),
        latest_commit=before.get("latest_commit", ""),
        latest_short_commit=before.get("latest_short_commit", ""),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=["git", "npm"], default="git")
    parser.add_argument("--branch", default="")
    parser.add_argument("--dist-tag", default="")
    args = parser.parse_args()

    status = read_update_status()
    run_id = args.run_id
    if status.get("run_id") and status.get("run_id") != run_id:
        print(f"Skip run_id={run_id}; active run is {status.get('run_id')}")
        return 0

    try:
        if args.mode == "npm":
            return _run_npm_update(run_id, args.dist_tag or args.branch)
        return _run_git_update(run_id, args.branch)
    except Exception as exc:
        write_update_status(
            "failed",
            f"更新失败: {exc}",
            run_id=run_id,
        )
        print(f"[self-update] failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
