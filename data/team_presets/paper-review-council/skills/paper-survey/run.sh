#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find_clawcross_root() {
  local dir="$SCRIPT_DIR"
  while [[ "$dir" != "/" ]]; do
    if [[ -f "$dir/oasis/agent_center.py" && -d "$dir/src" ]]; then
      printf '%s\n' "$dir"
      return 0
    fi
    dir="$(dirname "$dir")"
  done
  return 1
}

select_python() {
  local root="${1:-}"
  if [[ -n "$root" && -x "$root/.venv/bin/python" ]]; then
    printf '%s\n' "$root/.venv/bin/python"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return 0
  fi
  printf 'No Python interpreter found. Expected repo .venv/bin/python, python3, or python.\n' >&2
  return 1
}

CLAWCROSS_ROOT="$(find_clawcross_root || true)"
PYTHON_BIN="$(select_python "$CLAWCROSS_ROOT")"

cd "$SCRIPT_DIR"
exec "$PYTHON_BIN" run.py "$@"
