"""
OpenClaw agent-restore 计时与结果：追加 JSON 行到本地文件（不依赖终端里能否看到 logger）。

默认路径：<Clawcross 仓库>/logs/restore_timing.jsonl
自定义：环境变量 OPENCLAW_RESTORE_TIMING_LOG=/绝对路径/xxx.jsonl

查看：tail -f logs/restore_timing.jsonl
      或：tail -f logs/restore_timing.jsonl | python -m json.tool
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from utils.runtime_paths import LOGS_DIR


def default_restore_timing_path() -> str:
    log_dir = LOGS_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    return str(log_dir / "restore_timing.jsonl")


def append_agent_restore_record(
    *,
    agent_name: str,
    ok: bool,
    restore_timing_ms: dict,
    restore_workspace_files_written: int,
    errors: list,
) -> str | None:
    """追加一行 JSON。成功返回写入路径，失败返回 None（不抛异常，避免影响恢复）。"""
    raw = (os.environ.get("OPENCLAW_RESTORE_TIMING_LOG") or "").strip()
    path = raw or default_restore_timing_path()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "agent_restore",
        "source": "oasis",
        "agent_name": agent_name,
        "ok": ok,
        "restore_timing_ms": restore_timing_ms,
        "restore_workspace_files_written": restore_workspace_files_written,
        "errors": errors,
    }
    try:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
        return path
    except Exception:
        return None
