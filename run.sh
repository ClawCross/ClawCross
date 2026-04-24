#!/bin/bash
# ClawCross root entry shim.
# Forwards all arguments to the canonical script under selfskill/scripts.

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$PROJECT_ROOT/selfskill/scripts/run.sh" "$@"
