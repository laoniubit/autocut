#!/usr/bin/env bash
# =============================================================================
# AutoCut Matrix AI - Mac 本地运行与环境初始化脚本
#
# 使用方法：
#   1. 开启您的代理/VPN（以便终端能正常连接官方 PyPI 服务器）
#   2. 运行脚本：
#      chmod +x run.sh
#      ./run.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
if [ ! -d "venv_mac" ]; then
    # 优先检测本地 Python 3.12 / 3.11 / python3
    PYTHON_CMD="python3"
    if command -v python3.12 &>/dev/null; then
        PYTHON_CMD="python3.12"
    elif command -v python3.11 &>/dev/null; then
        PYTHON_CMD="python3.11"
    fi
    echo "    正在使用 $PYTHON_CMD 创建本地虚拟环境..."
    $PYTHON_CMD -m venv venv_mac
    echo "✅ venv_mac 创建成功！"
fi

# Activate virtual environment
echo "[Step 1/3] 正在激活 Mac 专属虚拟环境 (venv_mac)..."
source venv_mac/bin/activate
echo "✅ 虚拟环境已激活"
echo ""

# Install requirements
echo "[Step 2/3] 正在检查并更新依赖包 (使用官方 PyPI 二进制 Wheel)..."
echo "提示: 如果卡住或下载缓慢，请确保您的终端已挂载海外代理/VPN。"
pip install --upgrade pip -q
pip install -r requirements_mac.txt --only-binary=:all: -i https://pypi.tuna.tsinghua.edu.cn/simple
echo "✅ 依赖检查/安装完成！"
echo ""

# Run Server
echo "[Step 3/3] 正在启动 AutoCut 服务端..."
echo "启动成功后请在浏览器中打开: http://127.0.0.1:5010"
echo "按 Ctrl+C 可以停止运行。"
echo "============================================"
echo ""

python3 acut_server.py
