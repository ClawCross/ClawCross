#!/bin/bash
# 添加用户脚本 (Linux / macOS)

PROJECT_ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"

source "$PROJECT_ROOT/selfskill/scripts/_paths.sh"
clawcross_init_paths
cd "$CLAWCROSS_WORKSPACE_DIR"

# 激活虚拟环境（如果存在）
if [ -f "$CLAWCROSS_VENV_DIR/bin/activate" ]; then
    source "$CLAWCROSS_VENV_DIR/bin/activate"
fi

VENV_PY="$CLAWCROSS_VENV_DIR/bin/python"
if [ -x "$VENV_PY" ]; then
    exec "$VENV_PY" "$PROJECT_ROOT/tools/gen_password.py"
fi
exec python "$PROJECT_ROOT/tools/gen_password.py"
