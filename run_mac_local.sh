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
if [ ! -d "venv_mac" ]; then
    # 优先检测官方 Python.org macOS Installer 框架版本（以避免 Homebrew 动态库链接污染）
    PYTHON_CMD=""
    for py_path in \
        "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3" \
        "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"; do
        if [ -x "$py_path" ]; then
            PYTHON_CMD="$py_path"
            break
        fi
    done

    if [ -n "$PYTHON_CMD" ]; then
        echo "✅ 检测到官方 Python.org macOS 运行框架: $PYTHON_CMD"
    else
        # 兜底寻找本地 Python 3.12 / 3.11 / python3，并给出安全警告
        if command -v python3.12 &>/dev/null; then
            PYTHON_CMD="python3.12"
        elif command -v python3.11 &>/dev/null; then
            PYTHON_CMD="python3.11"
        else
            PYTHON_CMD="python3"
        fi
        echo "[!] 警告: 未检测到官方 Python.org 框架。当前使用: $(which $PYTHON_CMD)"
        echo "    说明: 如果该 Python 属于 Homebrew 编译版本，本地打包出的 App 会包含 Homebrew 动态链接污染，"
        echo "          分发给未安装 Homebrew 的其他 Mac 用户时会发生启动崩溃 (dyld 报错)。"
        echo "          仅供本地开发调试；若要本地安全打包分发，请安装官方 macOS 运行时:"
        echo "          下载地址: https://www.python.org/downloads/macos/"
        echo ""
    fi

    echo "    正在使用 $PYTHON_CMD 创建虚拟环境..."
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
