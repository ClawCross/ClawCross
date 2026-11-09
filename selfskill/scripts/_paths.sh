#!/bin/bash

clawcross_init_paths() {
    if [ -z "${PROJECT_ROOT:-}" ]; then
        echo "PROJECT_ROOT must be set before sourcing _paths.sh" >&2
        return 1
    fi

    if [ "${CLAWCROSS_USE_LEGACY_PATHS:-0}" = "1" ]; then
        export CLAWCROSS_HOME="$PROJECT_ROOT"
        export CLAWCROSS_VENV_DIR="$PROJECT_ROOT/.venv"
        export CLAWCROSS_DATA_DIR="$PROJECT_ROOT/data"
        export CLAWCROSS_LOG_DIR="$PROJECT_ROOT/logs"
        export CLAWCROSS_CONFIG_DIR="$PROJECT_ROOT/config"
        export CLAWCROSS_RUN_DIR="$PROJECT_ROOT"
        export CLAWCROSS_BIN_DIR="$PROJECT_ROOT/bin"
        export CLAWCROSS_WORKSPACE_DIR="$PROJECT_ROOT"
    else
        export CLAWCROSS_HOME="${CLAWCROSS_HOME:-$HOME/.clawcross}"
        export CLAWCROSS_VENV_DIR="${CLAWCROSS_VENV_DIR:-$CLAWCROSS_HOME/venv}"
        export CLAWCROSS_DATA_DIR="${CLAWCROSS_DATA_DIR:-$CLAWCROSS_HOME/data}"
        export CLAWCROSS_LOG_DIR="${CLAWCROSS_LOG_DIR:-$CLAWCROSS_HOME/logs}"
        export CLAWCROSS_CONFIG_DIR="${CLAWCROSS_CONFIG_DIR:-$CLAWCROSS_HOME/config}"
        export CLAWCROSS_RUN_DIR="${CLAWCROSS_RUN_DIR:-$CLAWCROSS_HOME/run}"
        export CLAWCROSS_BIN_DIR="${CLAWCROSS_BIN_DIR:-$CLAWCROSS_HOME/bin}"
        export CLAWCROSS_WORKSPACE_DIR="${CLAWCROSS_WORKSPACE_DIR:-$CLAWCROSS_HOME/workspace}"
    fi

    export CLAWCROSS_STATE_DIR="${CLAWCROSS_STATE_DIR:-$CLAWCROSS_HOME}"
    # 字节码缓存集中放到 CLAWCROSS_HOME 下，代码目录保持干净的同时保留
    # .pyc 缓存（此前 PYTHONDONTWRITEBYTECODE=1 导致每次启动全量重新编译）
    export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-$CLAWCROSS_HOME/pycache}"
    mkdir -p "$(dirname "$CLAWCROSS_VENV_DIR")" "$CLAWCROSS_DATA_DIR" "$CLAWCROSS_LOG_DIR" \
             "$CLAWCROSS_CONFIG_DIR" "$CLAWCROSS_RUN_DIR" "$CLAWCROSS_BIN_DIR" \
             "$CLAWCROSS_WORKSPACE_DIR" \
             "$CLAWCROSS_STATE_DIR"
}

clawcross_run_migration_if_needed() {
    if [ "${CLAWCROSS_USE_LEGACY_PATHS:-0}" = "1" ] || [ -f "$CLAWCROSS_HOME/.migration_done" ]; then
        return 0
    fi
    if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
        "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/migrate_to_user_home.py" || true
    elif command -v python3 >/dev/null 2>&1; then
        python3 "$PROJECT_ROOT/scripts/migrate_to_user_home.py" || true
    fi
}
