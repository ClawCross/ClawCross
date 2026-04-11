#!/bin/bash
# WeBot 自动环境配置脚本 (Linux / macOS)
# 参考腾讯内网版 OpenClaw install_prepare.sh 的组件状态跟踪模式

set -e
cd "$(dirname "$0")/.."

# ---------------------------------------------------------------------------
# Color definitions (compatible with bash 3.2+)
# ---------------------------------------------------------------------------
BOLD='\033[1m'
SUCCESS='\033[38;2;0;229;204m'
WARN='\033[38;2;255;176;32m'
ERROR='\033[38;2;230;57;70m'
MUTED='\033[38;2;90;100;128m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Install result tracking (bash 3.2 compatible, no associative arrays)
# ---------------------------------------------------------------------------
COMPONENT_KEYS=""
COMPONENT_STATUS=""
COMPONENT_LABELS=""

register_component() {
    local key="$1" label="$2"
    COMPONENT_KEYS="${COMPONENT_KEYS} ${key}"
    COMPONENT_STATUS="${COMPONENT_STATUS}|${key}:skip"
    COMPONENT_LABELS="${COMPONENT_LABELS}|${key}:${label}"
}

_get_field() {
    local store="$1" key="$2"
    local segment
    segment="$(echo "${store}" | tr '|' '\n' | grep "^${key}:")"
    echo "${segment#${key}:}"
}

_set_field() {
    local varname="$1" key="$2" value="$3"
    local store
    store="$(eval echo "\$$varname")"
    local new_store
    new_store="$(echo "${store}" | tr '|' '\n' | sed "s|^${key}:.*|${key}:${value}|" | tr '\n' '|')"
    eval "${varname}=\"${new_store}\""
}

get_status()  { _get_field "${COMPONENT_STATUS}" "$1"; }
get_label()   { _get_field "${COMPONENT_LABELS}" "$1"; }
mark_ok()     { _set_field COMPONENT_STATUS "$1" "ok"; }
mark_fail()   { _set_field COMPONENT_STATUS "$1" "fail"; }

run_install() {
    local key="$1"
    local fn="$2"
    # 注意: 不能用 ( "$fn" ) 子 shell，因为 check_venv 的 source .venv/bin/activate
    # 和 check_uv 的 export PATH 必须在当前 shell 中生效
    if "$fn"; then
        mark_ok "$key"
    else
        mark_fail "$key"
        echo -e "${WARN}!${NC} 组件 $(get_label "${key}") 安装/检查失败，继续安装其他组件..."
    fi
}

# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
ui_info()    { echo -e "${MUTED}·${NC} $*"; }
ui_warn()    { echo -e "${WARN}!${NC} $*"; }
ui_success() { echo -e "${SUCCESS}✓${NC} $*"; }
ui_error()   { echo -e "${ERROR}✗${NC} $*"; }
ui_section() { echo ""; echo -e "${BOLD}$*${NC}"; }

# ---------------------------------------------------------------------------
# Semver comparison (from install_prepare.sh, bash 3.2 compatible)
# Returns 0 if $1 >= $2, 1 otherwise.
# ---------------------------------------------------------------------------
semver_gte() {
    local a="$1" b="$2"
    local a1 a2 a3 b1 b2 b3
    a="${a#v}"; a="${a#go}"
    b="${b#v}"; b="${b#go}"
    IFS='.' read -r a1 a2 a3 <<< "${a}"
    IFS='.' read -r b1 b2 b3 <<< "${b}"
    a1="${a1:-0}"; a2="${a2:-0}"; a3="${a3:-0}"
    b1="${b1:-0}"; b2="${b2:-0}"; b3="${b3:-0}"
    if (( a1 > b1 )); then return 0; fi
    if (( a1 < b1 )); then return 1; fi
    if (( a2 > b2 )); then return 0; fi
    if (( a2 < b2 )); then return 1; fi
    if (( a3 >= b3 )); then return 0; fi
    return 1
}

# ---------------------------------------------------------------------------
# Detect internal OpenClaw runtime environment
# ---------------------------------------------------------------------------
INTERNAL_RUNTIME_BASE="${HOME}/.local/lib/openclaw-internal/runtime"
INTERNAL_INIT_ENV="${INTERNAL_RUNTIME_BASE}/init_env.sh"

activate_internal_openclaw_env() {
    # 如果已安装腾讯内网版 OpenClaw，source 其运行时环境
    # 这样可以复用内网版安装的 Node.js/Python/uv 等
    if [ -f "${INTERNAL_INIT_ENV}" ]; then
        ui_info "检测到腾讯内网版 OpenClaw 运行时，加载环境..."
        # shellcheck source=/dev/null
        source "${INTERNAL_INIT_ENV}"
        ui_success "已加载内网版 OpenClaw 运行时环境"
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# 1. uv (Python package manager)
# ---------------------------------------------------------------------------
UV_MIN_VERSION="0.4.0"

check_uv() {
    ui_section "● uv (Python 包管理器)"

    # 尝试加载内网版 OpenClaw 的 uv
    if [ -x "${INTERNAL_RUNTIME_BASE}/uv/bin/uv" ]; then
        export PATH="${INTERNAL_RUNTIME_BASE}/uv/bin:$PATH"
    fi

    if command -v uv &>/dev/null; then
        local current_ver
        current_ver="$(uv --version 2>/dev/null | awk '{print $2}' || echo "0.0.0")"
        if semver_gte "${current_ver}" "${UV_MIN_VERSION}"; then
            ui_success "uv ${current_ver} 已安装（>= ${UV_MIN_VERSION}）"
            return 0
        fi
        ui_info "uv ${current_ver} 版本过低（需要 >= ${UV_MIN_VERSION}），正在升级..."
    else
        ui_info "未检测到 uv，正在安装..."
    fi

    # 尝试从常见路径找
    if [ -x "$HOME/.local/bin/uv" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    elif [ -x "$HOME/.cargo/bin/uv" ]; then
        export PATH="$HOME/.cargo/bin:$PATH"
    fi

    if ! command -v uv &>/dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi

    if command -v uv &>/dev/null; then
        ui_success "uv 已安装: $(uv --version 2>/dev/null)"
        return 0
    fi

    ui_error "uv 安装失败，请手动安装: https://docs.astral.sh/uv/"
    return 1
}

# ---------------------------------------------------------------------------
# 2. Python virtual environment
# ---------------------------------------------------------------------------
PYTHON_MIN_VERSION="3.11.0"

check_venv() {
    ui_section "● Python 虚拟环境"

    # 如果内网版 OpenClaw 安装了 Python，优先使用
    if [ -x "${INTERNAL_RUNTIME_BASE}/python/bin/python3" ]; then
        export PATH="${INTERNAL_RUNTIME_BASE}/python/bin:$PATH"
        ui_info "使用内网版 OpenClaw 安装的 Python: $("${INTERNAL_RUNTIME_BASE}/python/bin/python3" --version 2>/dev/null)"
    fi

    if [ -d ".venv" ]; then
        ui_success "虚拟环境已存在: .venv/"
    else
        ui_info "创建虚拟环境 (.venv, Python 3.11+)..."
        uv venv .venv --python 3.11
        ui_success "虚拟环境创建完成"
    fi

    source .venv/bin/activate

    local python_ver
    python_ver="$(python --version 2>/dev/null | awk '{print $2}' || echo "0.0.0")"
    if semver_gte "${python_ver}" "${PYTHON_MIN_VERSION}"; then
        ui_success "Python ${python_ver} 已激活（>= ${PYTHON_MIN_VERSION}）"
    else
        ui_warn "Python ${python_ver} 版本可能过低（建议 >= ${PYTHON_MIN_VERSION}）"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# 3. Python dependencies
# ---------------------------------------------------------------------------
check_dependencies() {
    ui_section "● Python 依赖"
    ui_info "安装依赖 (config/requirements.txt)..."
    uv pip install -r config/requirements.txt
    ui_success "依赖安装完成"
    return 0
}

# ---------------------------------------------------------------------------
# 4. Node.js & acpx (ACP communication plugin)
# ---------------------------------------------------------------------------
NODE_MIN_VERSION="22.0.0"

check_node_and_acpx() {
    ui_section "● Node.js & acpx"

    # 如果内网版 OpenClaw 安装了 Node.js，优先使用
    if [ -x "${INTERNAL_RUNTIME_BASE}/node/bin/node" ]; then
        export PATH="${INTERNAL_RUNTIME_BASE}/node/bin:$PATH"
        if [ -f "${INTERNAL_RUNTIME_BASE}/.npmrc" ]; then
            export npm_config_userconfig="${INTERNAL_RUNTIME_BASE}/.npmrc"
        fi
    fi

    if command -v node &>/dev/null; then
        local node_ver
        node_ver="$(node -v 2>/dev/null || echo "v0.0.0")"
        node_ver="${node_ver#v}"
        if semver_gte "${node_ver}" "${NODE_MIN_VERSION}"; then
            ui_success "Node.js v${node_ver} 已安装（>= v${NODE_MIN_VERSION}）"
        else
            ui_warn "Node.js v${node_ver} 版本较低（建议 >= v${NODE_MIN_VERSION}），acpx 可能需要更高版本"
        fi
    else
        ui_warn "Node.js 未安装，acpx 及 OpenClaw 功能不可用"
        ui_info "推荐安装方式:"
        if [[ "$(uname -s)" == "Darwin" ]]; then
            ui_info "  brew install node@22"
            ui_info "  或安装腾讯内网版 OpenClaw 自带 Node.js（参见 check-openclaw）"
        else
            ui_info "  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"
            ui_info "  nvm install 22 && nvm use 22"
        fi
        return 0  # Node.js 非必需，不阻断
    fi

    # acpx 检查
    if command -v acpx &>/dev/null; then
        ui_success "acpx 已安装: $(acpx --version 2>/dev/null || echo 'unknown')"
    else
        ui_info "未检测到 acpx，正在安装..."
        if command -v npm &>/dev/null; then
            npm install -g acpx@latest 2>/dev/null || true
            if ! command -v acpx &>/dev/null; then
                local acpx_bin
                acpx_bin=$(find "$(npm prefix -g 2>/dev/null)/lib/node_modules/acpx" -name "cli.js" 2>/dev/null | head -1)
                if [ -n "$acpx_bin" ] && [ -f "$acpx_bin" ]; then
                    ln -sf "$acpx_bin" /usr/local/bin/acpx 2>/dev/null || true
                    chmod +x "$acpx_bin" 2>/dev/null || true
                fi
            fi
            if command -v acpx &>/dev/null; then
                ui_success "acpx 安装成功: $(acpx --version 2>/dev/null || echo 'unknown')"
            else
                ui_warn "acpx 安装后未在 PATH 中找到，群聊 ACP 广播功能可能不可用"
                ui_info "手动安装: npm install -g acpx@latest"
            fi
        fi
    fi
    return 0
}

# ---------------------------------------------------------------------------
# 5. Config files
# ---------------------------------------------------------------------------
check_config() {
    ui_section "● 配置文件"

    if [ -f "config/.env" ]; then
        ui_success "config/.env 已存在"
    else
        ui_warn "config/.env 不存在，请创建并填入: LLM_API_KEY=your_key"
    fi

    if [ -f "config/users.json" ]; then
        ui_success "config/users.json 已存在"
    else
        ui_warn "config/users.json 不存在，请运行 scripts/adduser.sh 创建用户"
    fi
    return 0
}

# ---------------------------------------------------------------------------
# 6. OpenClaw integration check
# ---------------------------------------------------------------------------
check_openclaw_integration() {
    ui_section "● OpenClaw 集成"

    local internal_wrapper="$HOME/.local/lib/openclaw-internal/bin/openclaw"

    if [ -x "$internal_wrapper" ]; then
        local oc_ver
        oc_ver="$("$internal_wrapper" --version 2>/dev/null | head -1 || echo "unknown")"
        ui_success "腾讯内网版 OpenClaw 已安装: ${oc_ver}"
        ui_info "Wrapper: ${internal_wrapper}"
        ui_info "默认 Gateway 端口: 23001"
        return 0
    fi

    if command -v openclaw &>/dev/null; then
        local oc_ver
        oc_ver="$(openclaw --version 2>/dev/null | head -1 || echo "unknown")"
        ui_success "OpenClaw 已安装: ${oc_ver}"
        return 0
    fi

    ui_info "OpenClaw 未安装（可选组件，不影响核心功能）"
    ui_info "安装后可获得可视化工作流编排功能"
    ui_info "执行: bash selfskill/scripts/run.sh check-openclaw"
    return 0
}

# ---------------------------------------------------------------------------
# Print install summary
# ---------------------------------------------------------------------------
print_summary() {
    local has_fail=0
    local key
    for key in ${COMPONENT_KEYS}; do
        if [ "$(get_status "${key}")" = "fail" ]; then
            has_fail=1
            break
        fi
    done

    echo ""
    if [ $has_fail -eq 1 ]; then
        echo -e "${ERROR}[ ❌ 部分组件安装失败 ]${NC}"
    else
        echo -e "${SUCCESS}[ ✅ 环境配置完成 ]${NC}"
    fi
    echo ""
    echo -e "${BOLD}安装结果清单：${NC}"

    for key in ${COMPONENT_KEYS}; do
        local label status
        label="$(get_label "${key}")"
        status="$(get_status "${key}")"
        case "$status" in
            ok)   echo -e "  ${SUCCESS}✓${NC} ${label}: [ 已就绪 ]" ;;
            fail) echo -e "  ${ERROR}✗${NC} ${label}: [ 失败 ]" ;;
            skip) echo -e "  ${MUTED}·${NC} ${label}: [ 跳过 ]" ;;
        esac
    done
    echo ""

    if [ $has_fail -eq 0 ]; then
        echo -e "  启动服务: ${MUTED}scripts/start.sh${NC}"
        echo -e "  或: ${MUTED}bash selfskill/scripts/run.sh start${NC}"
    fi
    echo ""

    return $has_fail
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    register_component "uv"           "uv (Python 包管理器)"
    register_component "venv"         "Python 虚拟环境"
    register_component "deps"         "Python 依赖"
    register_component "node_acpx"    "Node.js & acpx"
    register_component "config"       "配置文件"
    register_component "openclaw"     "OpenClaw 集成"

    echo ""
    echo -e "${BOLD}WeBot 环境自动配置${NC}"
    echo -e "${MUTED}正在检查并配置必要的运行组件...${NC}"

    # 尝试加载内网版 OpenClaw 运行时（复用其 Node.js/Python/uv）
    activate_internal_openclaw_env 2>/dev/null || true

    run_install "uv"        check_uv
    run_install "venv"      check_venv
    run_install "deps"      check_dependencies
    run_install "node_acpx" check_node_and_acpx
    run_install "config"    check_config
    run_install "openclaw"  check_openclaw_integration

    print_summary
}

main
