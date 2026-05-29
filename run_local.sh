#!/usr/bin/env bash
# =============================================================================
# AutoCut Matrix AI - Mac 本地打包编译脚本
#
# 使用方法：
#   # 1. 编译当前机器架构的版本 (您的 Intel Mac 会生成 x86_64 版本)：
#   ./run_local.sh
#
#   # 2. 编译特定架构版本 (例如 Apple Silicon 架构需在 M芯片 Mac 上执行)：
#   ./run_local.sh --arch arm64
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
# Check if virtual environment exists
if [ ! -d "venv_mac" ]; then
    echo "[!] 错误: 未找到 venv_mac 虚拟环境。请先运行一次 ./run.sh 以初始化环境。"
    exit 1
fi

# Activate virtual environment
echo "[Step 1/2] 正在激活 Mac 专属虚拟环境 (venv_mac)..."
source venv_mac/bin/activate
echo "✅ 虚拟环境已激活"
echo ""

# Auto-increment local version in config_hard.json
echo "[Step 2/3] 正在自动递增版本号..."
python3 bump_version.py
echo ""

# Run PyInstaller build script
echo "[Step 3/3] 正在运行 PyInstaller 本地编译引擎..."
python3 _build_mac.py "$@"

echo "============================================"
