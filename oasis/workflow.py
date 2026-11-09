"""
oasis.workflow — single-import entry point for ClawCross Python workflows.

Minimal usage:

    from oasis.workflow import workflow, Context

    @workflow
    async def main(ctx: Context):
        agents = ctx.list_agents()
        await ctx.publish(f"loaded {len(agents)} agents", author="workflowpy")
        ctx.set_result({"agent_count": len(agents)})

Explicit usage (if you have helpers after `main` or want manual control):

    from oasis.workflow import Context, run

    async def main(ctx: Context):
        ...

    if __name__ == "__main__":
        raise SystemExit(run(main))

What this module does at import time, in order:
  1. Re-exec into $CLAWCROSS_VENV_DIR/bin/python if it exists and the current
     interpreter is something else (guarded by OASIS_REEXEC=1 to avoid loops).
  2. Add <project> and <project>/src to sys.path.
  3. Load $CLAWCROSS_CONFIG_DIR/.env if present.
  4. Re-export Context (= StandaloneWorkflowContext), run (= run_cli), and the
     @workflow decorator.

Project root is derived from this file's location: oasis/ lives at
<project>/oasis/, so once `import oasis` works, the venv and config are found
automatically.
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

_PKG_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PKG_DIR.parent


def _find_venv_python(root: Path) -> Path | None:
    configured = os.environ.get("CLAWCROSS_VENV_DIR")
    if configured:
        for rel in ("bin/python", "bin/python3", "Scripts/python.exe"):
            candidate = Path(configured) / rel
            if candidate.is_file():
                return candidate
    for rel in (".venv/bin/python", ".venv/bin/python3", ".venv/Scripts/python.exe"):
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


def _maybe_reexec_into_venv() -> None:
    if os.environ.get("OASIS_REEXEC") == "1":
        return
    venv_python = _find_venv_python(_PROJECT_ROOT)
    if venv_python is None:
        return
    try:
        current = Path(sys.executable).resolve()
        target = venv_python.resolve()
    except OSError:
        return
    if current == target:
        return
    os.execve(
        str(target),
        [str(target), *sys.argv],
        {**os.environ, "OASIS_REEXEC": "1"},
    )


def _ensure_paths() -> None:
    for entry in (str(_PROJECT_ROOT), str(_PROJECT_ROOT / "src")):
        if entry not in sys.path:
            sys.path.insert(0, entry)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        from src.utils.runtime_paths import ENV_FILE, set_subprocess_env
    except Exception:
        env_path = Path(os.environ.get("CLAWCROSS_CONFIG_DIR", _PROJECT_ROOT / "config")) / ".env"
    else:
        set_subprocess_env(os.environ)
        env_path = ENV_FILE
    if env_path.is_file():
        load_dotenv(dotenv_path=str(env_path))


_maybe_reexec_into_venv()
_ensure_paths()
_load_env()

from oasis.python_workflow_cli import StandaloneWorkflowContext, run_cli  # noqa: E402

Context = StandaloneWorkflowContext
run = run_cli

WorkflowFunc = Callable[[StandaloneWorkflowContext], Awaitable[Any]]


def workflow(func: WorkflowFunc) -> WorkflowFunc:
    """
    Decorator. If the decorated function is defined inside the script's
    `__main__` module, immediately execute it via run_cli and exit with the
    resulting code. Otherwise return the function unchanged so it can be
    imported and unit-tested.
    """
    caller = inspect.stack()[1]
    if caller.frame.f_globals.get("__name__") == "__main__":
        raise SystemExit(run_cli(func))
    return func


PROJECT_ROOT = _PROJECT_ROOT

__all__ = [
    "Context",
    "PROJECT_ROOT",
    "StandaloneWorkflowContext",
    "run",
    "run_cli",
    "workflow",
]
