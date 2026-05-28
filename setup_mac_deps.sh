#!/usr/bin/env bash
# =============================================================================
# AutoCut Matrix AI - Mac 依赖一键安装脚本
# 功能：获取 ffmpeg 和 whisper-cli 的 Mac 原生二进制，放入 _internal/
#
# 使用方法：
#   chmod +x setup_mac_deps.sh
#   bash setup_mac_deps.sh
#
# 依赖：Homebrew (https://brew.sh)
# =============================================================================

set -e  # 任何命令失败立即退出

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAC_INTERNAL="$SCRIPT_DIR/_internal"

echo "============================================"
#  AutoCut Matrix AI - Mac 依赖安装向导
#  目标目录: $MAC_INTERNAL
# ============================================

# 创建目录
mkdir -p "$MAC_INTERNAL"

# ---- 检测编译依赖工具 ----
if ! command -v git &>/dev/null; then
    echo "[ERROR] 未检测到 git，请先安装 git。"
    exit 1
fi

CMAKE_OK=1
if ! command -v cmake &>/dev/null; then
    echo "[WARNING] 未检测到 cmake，本地将无法全新编译 whisper-cli。"
    if [ -f "$MAC_INTERNAL/whisper-cli" ]; then
        echo "          检测到已存在 $MAC_INTERNAL/whisper-cli，将跳过编译阶段直接复用该引擎。"
        CMAKE_OK=0
    else
        echo "[ERROR] 未检测到 cmake 且 _internal/whisper-cli 不存在，无法继续。"
        exit 1
    fi
fi

if [ "$CMAKE_OK" = "1" ]; then
    echo "[OK] 编译工具检测正常 (git, cmake)"
else
    echo "[OK] 工具检测完成（已启用已存引擎复用模式）"
fi
echo ""

# ============================================================
# 1. FFmpeg
# ============================================================
echo "[Step 1/2] 安装 FFmpeg..."
if command -v ffmpeg &>/dev/null; then
    FFMPEG_PATH="$(which ffmpeg)"
    echo "  检测到本地系统已安装 FFmpeg: $FFMPEG_PATH"
    cp "$FFMPEG_PATH" "$MAC_INTERNAL/ffmpeg"
    echo "  ✅ 已将本地 ffmpeg 复制到 _internal/ffmpeg"
else
    ARCH=$(uname -m)
    if [ "$ARCH" = "x86_64" ]; then
        URL="https://github.com/shaka-project/static-ffmpeg-binaries/releases/download/n7.1-1/ffmpeg-osx-x64"
    else
        URL="https://github.com/shaka-project/static-ffmpeg-binaries/releases/download/n7.1-1/ffmpeg-osx-arm64"
    fi
    echo "  本地未检测到 ffmpeg，正在从下载源获取适用于 $ARCH 的静态版 FFmpeg..."
    curl -L -o "$MAC_INTERNAL/ffmpeg" "$URL"
    echo "  ✅ 静态版 ffmpeg 已下载并保存到 _internal/ffmpeg"
fi

chmod +x "$MAC_INTERNAL/ffmpeg"
FFMPEG_SIZE=$(du -sh "$MAC_INTERNAL/ffmpeg" | cut -f1)
echo "  大小: $FFMPEG_SIZE"
echo ""

# ============================================================
# 2. whisper.cpp (编译 Mac 原生版本)
# ============================================================
if [ "$CMAKE_OK" = "1" ]; then
    echo "[Step 2/2] 编译 whisper.cpp (Mac 原生 / Metal 加速)..."
    echo ""

    WHISPER_BUILD_DIR="/tmp/whisper_cpp_build_$$"
    mkdir -p "$WHISPER_BUILD_DIR"

    # 克隆 whisper.cpp 最新稳定版
    echo "  正在克隆 whisper.cpp 仓库..."
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "$WHISPER_BUILD_DIR/whisper.cpp"

    cd "$WHISPER_BUILD_DIR/whisper.cpp"

    # 检测架构
    ARCH=$(uname -m)
    echo "  当前架构: $ARCH"

    # 编译参数：禁用共享库编译，确保静态链接，防止打包后动态库缺失
    CMAKE_ARGS="-DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON -DBUILD_SHARED_LIBS=OFF"

    # Metal GPU 加速（Apple Silicon 和 Intel Mac 均支持）
    if system_profiler SPDisplaysDataType 2>/dev/null | grep -q "Metal"; then
        echo "  检测到 Metal GPU 支持，开启 Metal 加速..."
        CMAKE_ARGS="$CMAKE_ARGS -DGGML_METAL=ON"
    else
        echo "  未检测到 Metal，使用 CPU 模式..."
        CMAKE_ARGS="$CMAKE_ARGS -DGGML_METAL=OFF"
    fi

    # 执行 CMake 构建
    mkdir -p build && cd build
    cmake .. $CMAKE_ARGS -DCMAKE_BUILD_TYPE=Release
    cmake --build . --config Release -j$(sysctl -n hw.logicalcpu)

    # 复制编译产物
    WHISPER_BIN="$WHISPER_BUILD_DIR/whisper.cpp/build/bin/whisper-cli"
    if [ -f "$WHISPER_BIN" ]; then
        cp "$WHISPER_BIN" "$MAC_INTERNAL/whisper-cli"
        chmod +x "$MAC_INTERNAL/whisper-cli"
        WHISPER_SIZE=$(du -sh "$MAC_INTERNAL/whisper-cli" | cut -f1)
        echo "  ✅ whisper-cli 已静态编译并复制到 _internal/whisper-cli ($WHISPER_SIZE)"
    else
        echo "  [ERROR] 编译失败，未找到 whisper-cli 二进制"
        echo "  构建目录: $WHISPER_BUILD_DIR/whisper.cpp/build/bin/"
        ls "$WHISPER_BUILD_DIR/whisper.cpp/build/bin/" 2>/dev/null || true
        exit 1
    fi

    # ---- 清理临时目录 ----
    cd "$SCRIPT_DIR"
    rm -rf "$WHISPER_BUILD_DIR"
    echo "  构建临时目录已清理"
    echo ""
else
    echo "[Step 2/2] 跳过 whisper.cpp 编译（已复用现有 whisper-cli 引擎）"
    echo ""
fi

# ============================================================
# 3. 验证二进制签名（可选）
# ============================================================
echo "[验证] 检查二进制文件..."
for BIN in "$MAC_INTERNAL/ffmpeg" "$MAC_INTERNAL/whisper-cli"; do
    if [ -f "$BIN" ]; then
        ARCH_INFO=$(lipo -info "$BIN" 2>/dev/null || file "$BIN")
        echo "  ✅ $(basename $BIN): $ARCH_INFO"
    fi
done

echo ""
echo "============================================"
echo "  ✅ Mac 依赖安装完成！"
echo ""
echo "  下一步："
echo "  1. 将 Whisper GGML 模型放入 models/whisper-cpp/"
echo "     例如: ggml-small.bin (推荐) 或 ggml-tiny.bin (快速)"
echo "     下载: https://huggingface.co/ggerganov/whisper.cpp"
echo ""
echo "  2. 安装 Python 依赖:"
echo "     pip install -r requirements_mac.txt"
echo ""
echo "  3. 启动开发服务器:"
echo "     python acut_server.py"
echo ""
echo "  4. 编译 Mac 发布包:"
echo "     python _build_mac.py"
echo "     或编译 M芯片专用包 (需在 M芯片 Mac 或 GitHub Actions 上执行):"
echo "     python _build_mac.py --arch arm64"
echo "============================================"
