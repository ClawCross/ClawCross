#!/bin/bash
# ClawCross root entry shim.
# Forwards all arguments to the canonical script under launch.

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$PROJECT_ROOT/launch/run.sh" "$@"
