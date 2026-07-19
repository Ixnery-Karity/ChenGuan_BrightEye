#!/usr/bin/env bash
# 宸观 BrightEye · 大模型一键安装（Linux / macOS）
set -e
echo "=============================================="
echo "  宸观 BrightEye 大模型一键安装"
echo "=============================================="

if ! command -v ollama >/dev/null 2>&1; then
    echo "[1/3] 未检测到 Ollama，执行官方安装脚本..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

echo "[2/3] 拉取聊天模型 qwen2.5:7b-instruct（约 4.7GB）..."
ollama pull qwen2.5:7b-instruct || echo "[!] 拉取失败，可稍后重跑本脚本续传"

echo "[3/3] 拉取复盘模型 deepseek-r1:7b（约 4.7GB）..."
ollama pull deepseek-r1:7b || echo "[!] 拉取失败，可稍后重跑本脚本续传"

echo "完成！直接启动宸观 BrightEye 即可，软件会自动拉起 Ollama 服务。"
