#!/usr/bin/env python3
"""Migrate legacy in-package runtime files into CLAWCROSS_HOME."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def _home() -> Path:
    explicit = os.environ.get("CLAWCROSS_HOME")
    return Path(explicit).expanduser() if explicit else Path.home() / ".clawcross"


def _ts() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


class Migration:
    def __init__(self) -> None:
        self.home = _home()
        self.log_dir = Path(os.environ.get("CLAWCROSS_LOG_DIR", self.home / "logs")).expanduser()
        self.config_dir = Path(os.environ.get("CLAWCROSS_CONFIG_DIR", self.home / "config")).expanduser()
        self.data_dir = Path(os.environ.get("CLAWCROSS_DATA_DIR", self.home / "data")).expanduser()
        self.bin_dir = Path(os.environ.get("CLAWCROSS_BIN_DIR", self.home / "bin")).expanduser()
        self.run_dir = Path(os.environ.get("CLAWCROSS_RUN_DIR", self.home / "run")).expanduser()
        self.backup_dir = self.home / "migration-backups" / _ts()
        self.records: list[dict[str, str]] = []

    def ensure_dirs(self) -> None:
        for path in (self.home, self.log_dir, self.config_dir, self.data_dir, self.bin_dir, self.run_dir):
            path.mkdir(parents=True, exist_ok=True)

    def log(self, message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
        print(line)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / "migration.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def record(self, src: Path, dst: Path, status: str, detail: str = "") -> None:
        self.records.append({"source": str(src), "target": str(dst), "status": status, "detail": detail})
        suffix = f" ({detail})" if detail else ""
        self.log(f"{status}: {src} -> {dst}{suffix}")

    def _backup_source(self, src: Path) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        target = self.backup_dir / src.name
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            shutil.copy2(src, target)

    def move_if_missing(self, src: Path, dst: Path, *, backup_on_conflict: bool = False) -> None:
        if not src.exists():
            return
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                if backup_on_conflict:
                    self._backup_source(src)
                    self.record(src, dst, "backed_up", f"target exists; backup under {self.backup_dir}")
                else:
                    self.record(src, dst, "skipped", "target exists")
                return
            shutil.move(str(src), str(dst))
            if dst.name.startswith("cloudflared") and os.name != "nt":
                dst.chmod(dst.stat().st_mode | 0o111)
            self.record(src, dst, "migrated")
        except Exception as exc:
            self.record(src, dst, "failed", repr(exc))

    def copy_config_template_if_missing(self) -> None:
        env_file = self.config_dir / ".env"
        template = PROJECT_ROOT / "config" / ".env.example"
        if not env_file.exists() and template.exists():
            try:
                shutil.copy2(template, env_file)
                self.record(template, env_file, "copied_template")
            except Exception as exc:
                self.record(template, env_file, "failed", repr(exc))

    def migrate(self) -> None:
        if _truthy(os.environ.get("CLAWCROSS_USE_LEGACY_PATHS")):
            self.log("legacy mode enabled; migration skipped")
            return
        stamp = self.home / ".migration_done"
        if stamp.exists():
            return

        self.ensure_dirs()
        self.log(f"starting migration from {PROJECT_ROOT} to {self.home}")
        self.move_if_missing(PROJECT_ROOT / "config" / ".env", self.config_dir / ".env", backup_on_conflict=True)
        self.move_if_missing(PROJECT_ROOT / "config" / "users.json", self.config_dir / "users.json", backup_on_conflict=True)
        self.move_if_missing(PROJECT_ROOT / "config" / "tinyfish_targets.json", self.config_dir / "tinyfish_targets.json", backup_on_conflict=True)
        self.copy_config_template_if_missing()

        for pattern in ("*.db", "*.db-wal", "*.db-shm"):
            for src in sorted((PROJECT_ROOT / "data").glob(pattern)):
                self.move_if_missing(src, self.data_dir / src.name)
        for dirname in (
            "agent_checkpoints", "external_agent_history", "user_files", "oasis_discussions",
            "python_workflow_runs", "trajectories", "timeset", "runtime",
        ):
            self.move_if_missing(PROJECT_ROOT / "data" / dirname, self.data_dir / dirname)
        for pattern in ("whitelist.json", "telegram_whitelist.json", "weclaw_qr.txt", "debug_llm_payload_last.json", "checkpoint_export_*.json"):
            for src in sorted((PROJECT_ROOT / "data").glob(pattern)):
                self.move_if_missing(src, self.data_dir / src.name)

        for src in sorted((PROJECT_ROOT / "logs").glob("*.log")):
            self.move_if_missing(src, self.log_dir / src.name)
        self.move_if_missing(PROJECT_ROOT / "chatbot" / "logs", self.log_dir / "chatbot")
        self.move_if_missing(PROJECT_ROOT / "chatbot" / "botpy.log", self.log_dir / "chatbot" / "botpy.log")
        self.move_if_missing(PROJECT_ROOT / "bin" / ("cloudflared.exe" if os.name == "nt" else "cloudflared"), self.bin_dir / ("cloudflared.exe" if os.name == "nt" else "cloudflared"))
        self.move_if_missing(PROJECT_ROOT / ".clawcross.pid", self.run_dir / "clawcross.pid")
        self.move_if_missing(PROJECT_ROOT / ".tunnel.pid", self.run_dir / "tunnel.pid")
        self.move_if_missing(PROJECT_ROOT / ".restart_flag", self.run_dir / "restart_flag")

        legacy_src_data = PROJECT_ROOT / "src" / "data"
        if legacy_src_data.exists():
            for pattern in ("*.db", "*.db-wal", "*.db-shm"):
                for src in sorted(legacy_src_data.glob(pattern)):
                    self.move_if_missing(src, self.data_dir / src.name)
            self.move_if_missing(legacy_src_data / "user_files", self.data_dir / "user_files")

        manifest = {
            "project_root": str(PROJECT_ROOT),
            "clawcross_home": str(self.home),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "records": self.records,
        }
        (self.home / "migration-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stamp.write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n", encoding="utf-8")
        self.log("migration complete")


if __name__ == "__main__":
    Migration().migrate()
