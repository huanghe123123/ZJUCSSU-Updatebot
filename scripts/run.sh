#!/usr/bin/env bash
# ============================================================
# UpdateBot 启动脚本
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "错误: .env 文件不存在"
    echo "请先复制 .env.example 并填入密钥:"
    echo "  cp .env.example .env"
    echo "  vim .env"
    exit 1
fi

# 检查 config.yaml
if [ ! -f "config.yaml" ]; then
    echo "错误: config.yaml 不存在"
    exit 1
fi

echo "===== 启动 UpdateBot ====="
echo "配置文件: $PROJECT_DIR/config.yaml"
echo "按 Ctrl+C 停止服务"
echo ""

# 使用 uv 运行
uv run updatebot --config config.yaml "$@"
