#!/usr/bin/env bash
# ============================================================
# UpdateBot 一键安装脚本
# 使用 uv 管理 Python 环境和依赖
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "===== UpdateBot 安装脚本 ====="
echo "项目目录: $PROJECT_DIR"

# ---------- 检查 uv ----------
if ! command -v uv &>/dev/null; then
    echo ">>> 未检测到 uv，正在安装..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # 刷新 PATH
    export PATH="$HOME/.cargo/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        echo "错误: uv 安装失败，请手动安装: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi
fi

echo ">>> uv 版本: $(uv --version)"

# ---------- 创建虚拟环境并安装依赖 ----------
cd "$PROJECT_DIR"

echo ">>> 创建虚拟环境..."
uv venv

echo ">>> 安装依赖..."
uv pip install -e .

# ---------- 生成 .env 文件 ----------
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ">>> 已创建 .env 文件，请编辑填入 API Key 和 PAT:"
    echo "    $PROJECT_DIR/.env"
else
    echo ">>> .env 文件已存在，跳过"
fi

# ---------- 验证安装 ----------
echo ">>> 验证安装..."
if uv run updatebot --help &>/dev/null; then
    echo ""
    echo "===== 安装完成 ====="
    echo ""
    echo "下一步:"
    echo "1. 编辑 .env 文件，填入 LLM_API_KEY 和 GITHUB_PAT"
    echo "   vim $PROJECT_DIR/.env"
    echo ""
    echo "2. 编辑 config.yaml，配置你的仓库地址和工作目录"
    echo "   vim $PROJECT_DIR/config.yaml"
    echo ""
    echo "3. 启动服务:"
    echo "   cd $PROJECT_DIR && ./scripts/run.sh"
    echo ""
else
    echo "错误: 安装验证失败"
    exit 1
fi
