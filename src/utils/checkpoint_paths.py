"""Checkpoint storage path helpers.

Runtime checkpoints are stored as one SQLite DB per thread under
``data/agent_checkpoints``.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

from utils.runtime_paths import DATA_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT_DB_DIR = DATA_DIR / "agent_checkpoints"

_PATH_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1F]+')
_LEGACY_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _as_path(path: str | os.PathLike | None) -> Path:
    if path is None:
        return DEFAULT_CHECKPOINT_DB_DIR
    return Path(path)


def is_checkpoint_db_file(path: str | os.PathLike | None) -> bool:
    candidate = _as_path(path)
    return candidate.suffix == ".db"


def checkpoint_store_exists(path: str | os.PathLike | None = None) -> bool:
    candidate = _as_path(path)
    if candidate.is_file():
        return True
    if candidate.is_dir() and any(candidate.glob("*.db")):
        return True
    return False


def checkpoint_db_name_for_thread(thread_id: str) -> str:
    raw = (thread_id or "default").strip() or "default"
    readable = _PATH_UNSAFE_RE.sub("_", raw).strip(" .") or "default"
    return f"{readable}.db"


def legacy_hashed_checkpoint_db_name_for_thread(thread_id: str) -> str:
    raw = (thread_id or "default").strip() or "default"
    readable = _LEGACY_SAFE_NAME_RE.sub("_", raw).strip("._-") or "default"
    readable = readable[:96]
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{readable}__{digest}.db"


def checkpoint_db_path_for_thread(
    thread_id: str,
    checkpoint_dir: str | os.PathLike | None = None,
) -> Path:
    root = _as_path(checkpoint_dir)
    if is_checkpoint_db_file(root):
        return root
    root.mkdir(parents=True, exist_ok=True)
    return root / checkpoint_db_name_for_thread(thread_id)


def legacy_hashed_checkpoint_db_path_for_thread(
    thread_id: str,
    checkpoint_dir: str | os.PathLike | None = None,
) -> Path:
    root = _as_path(checkpoint_dir)
    if is_checkpoint_db_file(root):
        return root
    root.mkdir(parents=True, exist_ok=True)
    return root / legacy_hashed_checkpoint_db_name_for_thread(thread_id)


def iter_checkpoint_db_paths(
    store_path: str | os.PathLike | None = None,
) -> list[Path]:
    candidate = _as_path(store_path)
    paths: list[Path] = []
    seen: set[Path] = set()

    if candidate.is_file():
        paths.append(candidate)
        seen.add(candidate.resolve())
    elif candidate.is_dir():
        for path in sorted(candidate.glob("*.db")):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                paths.append(path)

    return paths


def candidate_checkpoint_db_paths_for_thread(
    store_path: str | os.PathLike | None,
    thread_id: str,
) -> list[Path]:
    candidate = _as_path(store_path)
    paths: list[Path] = []
    seen: set[Path] = set()

    if is_checkpoint_db_file(candidate):
        if candidate.is_file():
            paths.append(candidate)
    else:
        thread_path = checkpoint_db_path_for_thread(thread_id, candidate)
        if thread_path.is_file():
            paths.append(thread_path)
            seen.add(thread_path.resolve())
        legacy_thread_path = legacy_hashed_checkpoint_db_path_for_thread(thread_id, candidate)
        if legacy_thread_path.is_file():
            resolved = legacy_thread_path.resolve()
            if resolved not in seen:
                paths.append(legacy_thread_path)
    return paths
