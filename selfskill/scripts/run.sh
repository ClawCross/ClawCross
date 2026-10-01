#!/usr/bin/env bash
# Bootstrap uv and Python, then pass every command to the Python controller.
set -eo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export PROJECT_ROOT
if [ "$#" -eq 0 ]; then set -- help; fi
if [ "$1" = dev ]; then
    export CLAWCROSS_HOME="$PROJECT_ROOT/.clawcross-dev"
    shift
    set -- start "$@"
fi
case "$CLAWCROSS_USE_LEGACY_PATHS" in
1|true|yes|on)
    export CLAWCROSS_HOME="$PROJECT_ROOT"
    export CLAWCROSS_VENV_DIR="$PROJECT_ROOT/.venv"
    ;;
*)
    export CLAWCROSS_HOME="${CLAWCROSS_HOME:-$HOME/.clawcross}"
    export CLAWCROSS_VENV_DIR="${CLAWCROSS_VENV_DIR:-$CLAWCROSS_HOME/venv}"
    ;;
esac
PYTHON="$CLAWCROSS_VENV_DIR/bin/python"
NEEDS_UV=0
case "$1" in
    start|start-foreground|start-fg|restart|setup|install-component|start-tunnel|cli|clawcross|evolve-skill)
        NEEDS_UV=1
        ;;
    help|-h|--help)
        if [ ! -x "$PYTHON" ]; then
            echo 'ClawCross commands: start, setup, stop, status, configure, components, install-component, logs, cli, help'
            exit 0
        fi
        ;;
    status|stop|components|stop-tunnel|tunnel-status|logs|doctor|check-openclaw|check-openclaw-weixin)
        if [ ! -x "$PYTHON" ] && command -v python3 >/dev/null 2>&1; then
            PYTHON="$(command -v python3)"
        fi
        ;;
esac
if [ "$NEEDS_UV" = 1 ] || [ ! -x "$PYTHON" ]; then
    if ! command -v uv >/dev/null 2>&1; then
        if [ -x "$HOME/.local/bin/uv" ]; then
            export PATH="$HOME/.local/bin:$PATH"
        elif [ -x "$HOME/.cargo/bin/uv" ]; then
            export PATH="$HOME/.cargo/bin:$PATH"
        else
            echo 'Installing uv to prepare Python 3.11...'
            curl -LsSf https://astral.sh/uv/install.sh | sh
            export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        fi
    fi
    command -v uv >/dev/null 2>&1 || { echo 'uv bootstrap failed' >&2; exit 1; }
    if [ ! -x "$PYTHON" ]; then
        uv venv "$CLAWCROSS_VENV_DIR" --python 3.11 || {
            uv python install 3.11
            uv venv "$CLAWCROSS_VENV_DIR" --python 3.11
        }
    fi
fi
if command -v uv >/dev/null 2>&1; then
    export CLAWCROSS_UV_BIN="$(command -v uv)"
fi
export PATH="$(dirname "$PYTHON"):$PATH"
exec "$PYTHON" "$PROJECT_ROOT/launch/runtime_control.py" "$@"
