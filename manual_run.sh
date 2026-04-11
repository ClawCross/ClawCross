#!/bin/bash
# WeBot 一键运行（环境配置 + API Key + 注册用户 + 启动服务）

# 锁定绝对路径：确保无论在哪启动，都能找到项目根目录
export PROJECT_ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
export WEBOT_HEADLESS=0
cd "$PROJECT_ROOT"
echo "========== 1/4 环境检查与配置 =========="
bash scripts/setup_env.sh
if [ $? -ne 0 ]; then
    echo "❌ 环境配置失败，请检查错误信息"
    exit 1
fi

# 激活虚拟环境（如果存在），后续所有 python 调用均使用虚拟环境
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
else
    echo "❌ 虚拟环境 .venv 不存在，请先运行: bash selfskill/scripts/run.sh setup"
    echo "   直接使用系统 python 可能是 Python 2.x，无法运行本项目"
    exit 1
fi

# 检查 Python 版本（防止系统 python 指向 Python 2）
_PY_MAJOR=$(python -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo "0")
if [ "$_PY_MAJOR" -lt 3 ]; then
    echo "❌ 当前 python 指向 Python $_PY_MAJOR（$(which python)）"
    echo "   本项目需要 Python 3.11+，请确保虚拟环境已正确激活"
    echo "   推荐使用: bash selfskill/scripts/run.sh start"
    exit 1
fi

echo ""
echo "========== 2/4 API Key 配置 =========="
# 建议加上判断，防止配置失败后继续运行
bash scripts/setup_apikey.sh
if [ $? -ne 0 ]; then
    echo "⚠️  API Key 配置未完成（可能已跳过或出错）"
fi

MODEL_NAME=""
if [ -f config/.env ]; then
    MODEL_NAME=$(grep '^LLM_MODEL=' config/.env | cut -d'=' -f2-)
fi
if [ -z "$MODEL_NAME" ]; then
    echo ""
    echo "⚠️  LLM_MODEL 尚未配置，先列出可用模型："
    bash selfskill/scripts/run.sh auto-model || true
    echo ""
    echo "请先设置模型，例如："
    echo "  bash selfskill/scripts/run.sh configure LLM_MODEL gpt-5.4-mini"
    exit 1
fi

echo ""
echo "========== 3/4 用户管理 =========="
read -p "是否需要添加新用户？(y/N): " answer
if [[ "$answer" =~ ^[Yy]$ ]]; then
    bash scripts/adduser.sh
fi

echo ""
echo "========== 4/4 启动服务 =========="

# 清理旧的 Clawcross 进程（按进程名匹配，pgrep/pkill 在 Linux 和 macOS 上均可用）
_TC_SCRIPTS="scripts/launcher.py src/utils/scheduler_service.py oasis/server.py src/mainagent.py src/front.py"
_TC_KILLED=0
for _script in $_TC_SCRIPTS; do
    _pids=$(pgrep -f "$_script" 2>/dev/null || true)
    if [ -n "$_pids" ]; then
        echo "🧹 发现旧进程 $_script (PID: $(echo $_pids | tr '\n' ' '))，正在清理..."
        pkill -f "$_script" 2>/dev/null || true
        _TC_KILLED=1
    fi
done
if [ "$_TC_KILLED" = "1" ]; then
    sleep 2
    # 二次检查，强制杀残留
    for _script in $_TC_SCRIPTS; do
        if pgrep -f "$_script" >/dev/null 2>&1; then
            echo "⚠️  $_script 仍在运行，强制终止..."
            pkill -9 -f "$_script" 2>/dev/null || true
        fi
    done
    sleep 1
    echo "✅ 旧进程已清理"
fi

# 询问是否部署公网
read -p "是否部署到公网？(y/N): " tunnel_answer
if [[ "$tunnel_answer" =~ ^[Yy]$ ]]; then
    echo "🌐 正在后台启动 Cloudflare Tunnel..."
    # 使用 venv 中的 python（已由上面 source .venv/bin/activate 激活）
    python scripts/tunnel.py &
    TUNNEL_PID=$!
    # 确保主进程退出时也关闭隧道
    trap "kill $TUNNEL_PID 2>/dev/null" EXIT
    sleep 2
fi

# exec 替换当前进程，确保信号（Ctrl+C、kill、终端关闭）直达 launcher.py
exec python scripts/launcher.py
