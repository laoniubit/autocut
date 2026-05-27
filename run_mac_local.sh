#!/usr/bin/env bash
# =============================================================================
# AutoCut Matrix AI - Mac 本地运行与环境初始化脚本
#
# 使用方法：
#   1. 开启您的代理/VPN（以便终端能正常连接官方 PyPI 服务器）
#   2. 运行脚本：
#      chmod +x run_mac_local.sh
#      ./run_mac_local.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
# Check if virtual environment exists
if [ ! -d "venv_mac" ]; then
    echo "[!] 警告: 未找到 venv_mac 虚拟环境目录。"
    echo "    正在使用当前环境的 Python 3.12 创建虚拟环境..."
    /usr/local/Cellar/python@3.12/3.12.10/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m venv venv_mac
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
pip install -r requirements_mac.txt --only-binary=:all:
echo "✅ 依赖检查/安装完成！"
echo ""

# Run Server
echo "[Step 3/3] 正在启动 AutoCut 服务端..."
echo "启动成功后请在浏览器中打开: http://127.0.0.1:5010"
echo "按 Ctrl+C 可以停止运行。"
echo "============================================"
echo ""

python3 acut_server.py
