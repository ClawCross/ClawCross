#!/usr/bin/env bash
# Explicit compatibility entrypoint for the checksum-verified optional installer.
set -euo pipefail
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$script_dir/../selfskill/scripts/run.sh" install-component weclaw "$@"
