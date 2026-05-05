#!/usr/bin/env bash
# WeClaw 安装脚本（github.com/fastclaw-ai/weclaw）
# 安装后在 .env 设置 WECLAW_ENABLED=true 即可由 chatbot 托管启动
set -euo pipefail

if command -v weclaw >/dev/null 2>&1; then
    echo "weclaw 已安装: $(command -v weclaw) ($(weclaw version 2>/dev/null || echo 未知版本))"
    exit 0
fi

echo "正在从 fastclaw-ai/weclaw 安装 weclaw..."
curl -sSL https://raw.githubusercontent.com/fastclaw-ai/weclaw/main/install.sh | sh

if command -v weclaw >/dev/null 2>&1; then
    echo "安装完成: $(command -v weclaw)"
    echo
    echo "下一步："
    echo "  1) 在 config/.env 设置 WECLAW_ENABLED=true"
    echo "  2) 启动 chatbot：python chatbot/main.py --weclaw"
    echo "  3) 终端会出现微信扫码二维码，扫码登录"
else
    echo "安装失败：weclaw 未在 PATH 中" >&2
    exit 1
fi
