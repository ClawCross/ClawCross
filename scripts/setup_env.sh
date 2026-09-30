#!/usr/bin/env bash
# Compatibility entrypoint. The canonical wrapper bootstraps Python, then
# scripts/runtime_control.py prepares core dependencies.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$script_dir/../selfskill/scripts/run.sh" setup "$@"
