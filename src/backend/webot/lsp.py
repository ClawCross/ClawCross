"""
Best-effort workspace diagnostics inspired by OpenSeek's LSP front door.

This module deliberately keeps the contract small and non-throwing: callers ask
for diagnostics for one file, and receive a structured payload even when the
language toolchain is not installed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from webot.workspace import resolve_session_workspace

TS_EXTENSIONS = {".ts", ".tsx"}
JS_EXTENSIONS = {".js", ".jsx", ".mjs", ".cjs"}
PY_EXTENSIONS = {".py"}
JSON_EXTENSIONS = {".json"}
RESERVED_EXTENSIONS = {".go", ".rs", ".c", ".h", ".cpp", ".cc", ".hpp"}

_TSC_PAREN_RE = re.compile(r"^(.+?)\((\d+),(\d+)\):\s+(error|warning|info)\s+TS(\d+):\s+(.+)$")
_TSC_COLON_RE = re.compile(r"^(.+?):(\d+):(\d+)\s+-\s+(error|warning|info)\s+TS(\d+):\s+(.+)$")
_NODE_CHECK_RE = re.compile(r"^(.+?):(\d+)\s*$")
_PY_FILE_RE = re.compile(r'^\s*File "(.+?)", line (\d+)')


@dataclass(frozen=True)
class LspDiagnostic:
    file: str
    line: int
    col: int
    severity: str
    message: str
    source: str = ""

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def _safe_timeout(value: int | float | None, default: int = 30, maximum: int = 120) -> int:
    try:
        parsed = int(value or default)
    except Exception:
        parsed = default
    return max(1, min(parsed, maximum))


def _workspace_cwd(username: str, session_id: str = "") -> Path:
    return Path(resolve_session_workspace(username, session_id).cwd).resolve()


def resolve_file_path(username: str, session_id: str, file: str) -> Path:
    requested = os.path.expanduser((file or "").strip())
    if not requested:
        raise ValueError("file is required")
    path = Path(requested)
    if not path.is_absolute():
        path = _workspace_cwd(username, session_id) / path
    return path.resolve()


def _run(cmd: list[str], *, cwd: Path, timeout_seconds: int) -> tuple[int, str, str, str]:
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return completed.returncode, completed.stdout or "", completed.stderr or "", ""
    except FileNotFoundError as exc:
        return 127, "", "", str(exc)
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", exc.stderr or "", f"timed out after {timeout_seconds}s"
    except Exception as exc:
        return 1, "", "", str(exc)


def parse_tsc_output(stdout: str, stderr: str) -> list[LspDiagnostic]:
    diagnostics: list[LspDiagnostic] = []
    for raw in f"{stdout}\n{stderr}".splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _TSC_PAREN_RE.match(line) or _TSC_COLON_RE.match(line)
        if not match:
            continue
        file, line_no, col_no, severity, code, message = match.groups()
        diagnostics.append(
            LspDiagnostic(
                file=file,
                line=int(line_no),
                col=int(col_no),
                severity=severity,
                message=message,
                source=f"tsc TS{code}",
            )
        )
    return diagnostics


def _probe_typescript(file_path: Path, *, cwd: Path, timeout_seconds: int) -> tuple[list[LspDiagnostic], dict[str, Any]]:
    cmd = ["npx", "--yes", "tsc", "--noEmit", "--pretty", "false", str(file_path)]
    rc, stdout, stderr, error = _run(cmd, cwd=cwd, timeout_seconds=timeout_seconds)
    diagnostics = parse_tsc_output(stdout, stderr)
    return diagnostics, {
        "runner": "tsc",
        "command": " ".join(cmd),
        "returncode": rc,
        "error": error,
    }


def _probe_javascript(file_path: Path, *, cwd: Path, timeout_seconds: int) -> tuple[list[LspDiagnostic], dict[str, Any]]:
    cmd = ["node", "--check", str(file_path)]
    rc, stdout, stderr, error = _run(cmd, cwd=cwd, timeout_seconds=timeout_seconds)
    if rc == 0:
        return [], {"runner": "node --check", "command": " ".join(cmd), "returncode": rc, "error": error}

    lines = (stderr or stdout or error).splitlines()
    line_no = 1
    for raw in lines:
        match = _NODE_CHECK_RE.match(raw.strip())
        if match:
            line_no = int(match.group(2))
            break
    message = next((line.strip() for line in reversed(lines) if line.strip()), error or "JavaScript syntax check failed")
    return [
        LspDiagnostic(
            file=str(file_path),
            line=line_no,
            col=1,
            severity="error",
            message=message,
            source="node --check",
        )
    ], {"runner": "node --check", "command": " ".join(cmd), "returncode": rc, "error": error}


def _probe_python(file_path: Path, *, cwd: Path, timeout_seconds: int) -> tuple[list[LspDiagnostic], dict[str, Any]]:
    cmd = [sys.executable, "-m", "py_compile", str(file_path)]
    rc, stdout, stderr, error = _run(cmd, cwd=cwd, timeout_seconds=timeout_seconds)
    if rc == 0:
        return [], {"runner": "py_compile", "command": " ".join(cmd), "returncode": rc, "error": error}

    text = stderr or stdout or error
    file_name = str(file_path)
    line_no = 1
    for raw in text.splitlines():
        match = _PY_FILE_RE.match(raw)
        if match:
            file_name = match.group(1)
            line_no = int(match.group(2))
    message = next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "Python compile failed")
    return [
        LspDiagnostic(
            file=file_name,
            line=line_no,
            col=1,
            severity="error",
            message=message,
            source="py_compile",
        )
    ], {"runner": "py_compile", "command": " ".join(cmd), "returncode": rc, "error": error}


def _probe_json(file_path: Path) -> tuple[list[LspDiagnostic], dict[str, Any]]:
    try:
        json.loads(file_path.read_text(encoding="utf-8"))
        return [], {"runner": "json", "returncode": 0, "error": ""}
    except json.JSONDecodeError as exc:
        return [
            LspDiagnostic(
                file=str(file_path),
                line=exc.lineno,
                col=exc.colno,
                severity="error",
                message=exc.msg,
                source="json",
            )
        ], {"runner": "json", "returncode": 1, "error": str(exc)}
    except Exception as exc:
        return [
            LspDiagnostic(
                file=str(file_path),
                line=1,
                col=1,
                severity="error",
                message=str(exc),
                source="json",
            )
        ], {"runner": "json", "returncode": 1, "error": str(exc)}


def probe_diagnostics(
    *,
    username: str,
    session_id: str = "",
    file: str,
    timeout_seconds: int = 30,
    max_diagnostics: int = 50,
) -> dict[str, Any]:
    """Run best-effort diagnostics for one workspace file."""
    timeout = _safe_timeout(timeout_seconds)
    max_items = max(1, min(int(max_diagnostics or 50), 200))
    try:
        file_path = resolve_file_path(username, session_id, file)
    except Exception as exc:
        return {
            "ok": False,
            "file": file,
            "language": "unknown",
            "diagnostics": [],
            "error": str(exc),
            "meta": {"runner": "resolver"},
        }

    if not file_path.exists():
        return {
            "ok": False,
            "file": str(file_path),
            "language": "unknown",
            "diagnostics": [],
            "error": "file does not exist",
            "meta": {"runner": "resolver"},
        }
    if not file_path.is_file():
        return {
            "ok": False,
            "file": str(file_path),
            "language": "unknown",
            "diagnostics": [],
            "error": "path is not a file",
            "meta": {"runner": "resolver"},
        }

    cwd = _workspace_cwd(username, session_id)
    ext = file_path.suffix.lower()
    language = ext.lstrip(".") or "unknown"

    if ext in TS_EXTENSIONS:
        diagnostics, meta = _probe_typescript(file_path, cwd=cwd, timeout_seconds=timeout)
    elif ext in JS_EXTENSIONS:
        diagnostics, meta = _probe_javascript(file_path, cwd=cwd, timeout_seconds=timeout)
    elif ext in PY_EXTENSIONS:
        diagnostics, meta = _probe_python(file_path, cwd=cwd, timeout_seconds=timeout)
    elif ext in JSON_EXTENSIONS:
        diagnostics, meta = _probe_json(file_path)
    elif ext in RESERVED_EXTENSIONS:
        diagnostics, meta = [], {"runner": "reserved", "returncode": 0, "error": ""}
    else:
        diagnostics, meta = [], {"runner": "unsupported", "returncode": 0, "error": ""}

    clipped = diagnostics[:max_items]
    return {
        "ok": True,
        "file": str(file_path),
        "language": language,
        "diagnostics": [item.to_payload() for item in clipped],
        "diagnostic_count": len(diagnostics),
        "truncated": len(diagnostics) > len(clipped),
        "meta": meta,
    }


def format_diagnostics(payload: dict[str, Any]) -> str:
    """Return a concise text form suitable for an MCP tool result."""
    if not payload.get("ok"):
        return f"[lsp] failed for {payload.get('file', '')}: {payload.get('error', 'unknown error')}"
    diagnostics = payload.get("diagnostics") or []
    if not diagnostics:
        runner = (payload.get("meta") or {}).get("runner", "")
        return f"[lsp] no diagnostics for {payload.get('file', '')} ({runner or 'none'})"
    lines = [f"[lsp] {len(diagnostics)} diagnostic(s) for {payload.get('file', '')}"]
    for item in diagnostics:
        source = item.get("source") or "lsp"
        lines.append(
            f"- {item.get('file')}:{item.get('line')}:{item.get('col')} "
            f"{item.get('severity', 'info')} {source}: {item.get('message', '')}"
        )
    if payload.get("truncated"):
        lines.append(f"... truncated from {payload.get('diagnostic_count', len(diagnostics))} diagnostics")
    return "\n".join(lines)
