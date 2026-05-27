"""
Runtime path resolver for ClawCross.

Single source of truth for where ClawCross reads / writes runtime data
(venv, SQLite databases, configs, secrets, logs, PID files, downloaded
binaries). The npm package or git checkout directory is treated as replaceable
code/assets; mutable runtime state lives under CLAWCROSS_HOME by default.

Backwards compatibility:
- CLAWCROSS_USE_LEGACY_PATHS=1 forces every path back into the code directory.

Deployment model:
- npm and git installs share the same runtime layout by default:
  CLAWCROSS_HOME defaults to ~/.clawcross.
- Development isolation should use CLAWCROSS_HOME=<repo>/.clawcross-dev
  (or the `clawcross dev` wrapper), not implicit .git detection.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping, MutableMapping


PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent
CODE_ROOT: Path = PROJECT_ROOT
DEFAULT_CLAWCROSS_HOME: Path = Path.home() / ".clawcross"
DEV_HOME: Path = PROJECT_ROOT / ".clawcross-dev"


def _truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in ("1", "true", "yes", "on"))


def is_legacy_mode() -> bool:
    """Return True when explicit old in-code-dir paths are requested."""
    return _truthy(os.environ.get("CLAWCROSS_USE_LEGACY_PATHS"))


def _resolve_home() -> Path:
    explicit = os.environ.get("CLAWCROSS_HOME")
    return Path(explicit).expanduser() if explicit else DEFAULT_CLAWCROSS_HOME


def _resolve_subdir(env_key: str, home: Path, sub: str, *, legacy_path: Path) -> Path:
    if is_legacy_mode():
        return legacy_path
    explicit = os.environ.get(env_key)
    if explicit:
        return Path(explicit).expanduser()
    return home / sub


def _compute_paths() -> dict[str, Path]:
    home = PROJECT_ROOT if is_legacy_mode() else _resolve_home()
    venv_dir = _resolve_subdir("CLAWCROSS_VENV_DIR", home, "venv", legacy_path=PROJECT_ROOT / ".venv")
    data_dir = _resolve_subdir("CLAWCROSS_DATA_DIR", home, "data", legacy_path=PROJECT_ROOT / "data")
    logs_dir = _resolve_subdir("CLAWCROSS_LOG_DIR", home, "logs", legacy_path=PROJECT_ROOT / "logs")
    config_dir = _resolve_subdir("CLAWCROSS_CONFIG_DIR", home, "config", legacy_path=PROJECT_ROOT / "config")
    run_dir = _resolve_subdir("CLAWCROSS_RUN_DIR", home, "run", legacy_path=PROJECT_ROOT)
    bin_dir = _resolve_subdir("CLAWCROSS_BIN_DIR", home, "bin", legacy_path=PROJECT_ROOT / "bin")
    workspace_dir = _resolve_subdir("CLAWCROSS_WORKSPACE_DIR", home, "workspace", legacy_path=PROJECT_ROOT)
    state_dir = Path(os.environ.get("CLAWCROSS_STATE_DIR", str(home))).expanduser()
    return {
        "CLAWCROSS_HOME": home,
        "VENV_DIR": venv_dir,
        "DATA_DIR": data_dir,
        "USER_FILES_DIR": data_dir / "user_files",
        "LOGS_DIR": logs_dir,
        "CONFIG_DIR": config_dir,
        "ENV_FILE": config_dir / ".env",
        "USERS_FILE": config_dir / "users.json",
        "PID_DIR": run_dir,
        "BIN_DIR": bin_dir,
        "WORKSPACE_DIR": workspace_dir,
        "STATE_DIR": state_dir,
    }


_paths = _compute_paths()

CLAWCROSS_HOME: Path = _paths["CLAWCROSS_HOME"]
VENV_DIR: Path = _paths["VENV_DIR"]
DATA_DIR: Path = _paths["DATA_DIR"]
USER_FILES_DIR: Path = _paths["USER_FILES_DIR"]
LOGS_DIR: Path = _paths["LOGS_DIR"]
CONFIG_DIR: Path = _paths["CONFIG_DIR"]
ENV_FILE: Path = _paths["ENV_FILE"]
USERS_FILE: Path = _paths["USERS_FILE"]
PID_DIR: Path = _paths["PID_DIR"]
BIN_DIR: Path = _paths["BIN_DIR"]
WORKSPACE_DIR: Path = _paths["WORKSPACE_DIR"]
STATE_DIR: Path = _paths["STATE_DIR"]


def refresh_paths() -> None:
    global CLAWCROSS_HOME, VENV_DIR, DATA_DIR, USER_FILES_DIR, LOGS_DIR
    global CONFIG_DIR, ENV_FILE, USERS_FILE, PID_DIR, BIN_DIR, WORKSPACE_DIR, STATE_DIR
    global _paths
    _paths = _compute_paths()
    CLAWCROSS_HOME = _paths["CLAWCROSS_HOME"]
    VENV_DIR = _paths["VENV_DIR"]
    DATA_DIR = _paths["DATA_DIR"]
    USER_FILES_DIR = _paths["USER_FILES_DIR"]
    LOGS_DIR = _paths["LOGS_DIR"]
    CONFIG_DIR = _paths["CONFIG_DIR"]
    ENV_FILE = _paths["ENV_FILE"]
    USERS_FILE = _paths["USERS_FILE"]
    PID_DIR = _paths["PID_DIR"]
    BIN_DIR = _paths["BIN_DIR"]
    WORKSPACE_DIR = _paths["WORKSPACE_DIR"]
    STATE_DIR = _paths["STATE_DIR"]


def _is_windows() -> bool:
    return sys.platform == "win32" or os.name == "nt"


def venv_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if _is_windows() else "bin/python")


def cloudflared_path() -> Path:
    return BIN_DIR / ("cloudflared.exe" if _is_windows() else "cloudflared")


def env_template_path() -> Path:
    return PROJECT_ROOT / "config" / ".env.example"


def users_template_path() -> Path:
    return PROJECT_ROOT / "config" / "users.json.example"


def ensure_runtime_dirs() -> None:
    for path in (CLAWCROSS_HOME, DATA_DIR, USER_FILES_DIR, LOGS_DIR, CONFIG_DIR, PID_DIR, BIN_DIR, WORKSPACE_DIR, STATE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def set_subprocess_env(env: MutableMapping[str, str] | Mapping[str, str] | None = None) -> dict[str, str]:
    base = dict(os.environ if env is None else env)
    base["CLAWCROSS_HOME"] = str(CLAWCROSS_HOME)
    base["CLAWCROSS_VENV_DIR"] = str(VENV_DIR)
    base["CLAWCROSS_DATA_DIR"] = str(DATA_DIR)
    base["CLAWCROSS_LOG_DIR"] = str(LOGS_DIR)
    base["CLAWCROSS_CONFIG_DIR"] = str(CONFIG_DIR)
    base["CLAWCROSS_RUN_DIR"] = str(PID_DIR)
    base["CLAWCROSS_BIN_DIR"] = str(BIN_DIR)
    base["CLAWCROSS_WORKSPACE_DIR"] = str(WORKSPACE_DIR)
    base["CLAWCROSS_STATE_DIR"] = str(STATE_DIR)
    base.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    base["PYTHONUTF8"] = "1"
    base["PYTHONIOENCODING"] = "utf-8"
    if is_legacy_mode():
        base["CLAWCROSS_USE_LEGACY_PATHS"] = "1"
    return base
