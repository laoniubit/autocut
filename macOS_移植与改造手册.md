# AutoCut Matrix AI - macOS 专有（macOS-Only）环境重构与移植手册 (第二版)

本手册详细记录了如何将原 Windows 兼容的 Python 语音/视频剪辑后端项目（AutoCut），**彻底放弃 Windows 兼容性**，重构并优化为 **macOS 专有（macOS-Only）** 的高性能版本。该版本原生支持 macOS 上的 **Intel 芯片 (x86_64)** 与 **Apple Silicon (M-Chip / arm64)**。

---

## 目录
1. [项目背景与重构目标](#1-项目背景与重构目标)
2. [源码 macOS-Only 重构优化 (Codebase Optimization)](#2-源码-macos-only-重构优化)
3. [Mac 本地二进制依赖处理 (Mac Native Binaries)](#3-mac-本地二进制依赖处理)
4. [Python 运行环境与依赖包管理 (Python Virtualenv)](#4-python-运行环境与依赖包管理)
5. [自动化运行与打包脚本 (Helper Scripts)](#5-自动化运行与打包脚本)
6. [CI/CD 远端编译流水线 (GitHub Actions)](#6-cicd-远端编译流水线)

---

## 1. 项目背景与重构目标
* **背景**：在第一阶段，我们编写了兼容 Windows 和 macOS 的跨平台代码。然而，为了追求更干净的逻辑、更高的运行效率和更低的代码维护成本，我们决定**完全放弃 Windows 兼容性**。
* **重构目标**：
  1. **彻底清理冗余代码**：全面删除所有与 `win32` 平台、`.exe` 文件名后缀、Windows 窗口控制标签、以及英伟达 GPU (CUDA/NVENC) 检测与运行相关的所有代码分支。
  2. **释放路径限制**：移除仅用于 Windows 兼容的 ASCII 路径校验，在 macOS 下原生且无缝支持含中文字符和空格的文件夹路径。
  3. **极致的 macOS 加速**：核心视频编码逻辑直接锁死在 macOS 的 **VideoToolbox 硬件加速** 与通用 `libx264` 软解上。

---

## 2. 源码 macOS-Only 重构优化

通过移除所有 `win32` 条件判定与英伟达驱动判定，三个核心模块被精简重构如下：

### 2.1 `acut_engine.py` (核心引擎模块重构)
* **[删除] 中文路径校验限制**：彻底删去了 ASCII 路径检查。项目不再对路径进行编码拦截，现在支持在任意中文及含有空格的目录路径下运行。
* **[删除] NVIDIA GPU 检测**：删除了 `_has_nvidia_gpu()` 函数和 `check_nvenc_functional()` 逻辑。加载 `config_hard.json` 时，第一期用于侦测英伟达显卡以切换 `cuda` 的初始化代码被完全移除，配置设备直接锁定为 macOS 本地（CPU 或 Metal GPU 加速）。
* **[精简] 路径寻址与常量**：
  * 将 `_CREATE_NO_WINDOW` 常量直接硬编码为 `0`（去除了 `subprocess.CREATE_NO_WINDOW` 条件判定）。
  * 默认视频输出路径固化为 `~/Downloads/autocut_out`（系统下载目录，原为桌面）。
  * FFmpeg 寻址固化：`_find_ffmpeg_binary()` 仅检索 macOS 格式的 `ffmpeg` 路径（包含 `_internal_mac/ffmpeg`、`_internal/ffmpeg` 和系统 PATH），不再探测 `ffmpeg.exe`。

### 2.2 `acut_asr.py` (自动语音识别模块重构)
* **[删除] Windows DLL 注入**：删除了专为 Windows 载入 NVIDIA CUDA 运行依赖的 `inject_lib_paths()` 逻辑。
* **[精简] ASR 执行文件判定**：`find_whisper_exe()` 不再包含 Windows CPU/CUDA 分支判定与 `.exe` 寻址，强制寻找 macOS 平台无扩展名的 `whisper-cli`（位于 `_internal_mac/` 或系统 PATH）。
* **[精简] 进程启动逻辑**：在 `run_whisper_cpp()` 中直接拉起 macOS CPU/Metal 运行管线，彻底剔除了英伟达 GPU（如 `-dev 0` 参数绑定）的分支代码。
* **[精简] 预检测试优化**：在 `verify_environment()` 中去除了 NVIDIA CUDA 跑分检测分支，只运行 macOS 本地硬件加速及 CPU 多线程跑分审计。

### 2.3 `acut_synthesis.py` (视频合成导出模块重构)
* **[删除] NVENC 编码器适配**：删除了 `check_nvenc_functional()` 函数，并在进行编码器自动决策时，移除了 `h264_nvenc` 选项。
* **[精简] 编码器精简决策**：在 `select_encoder()` 中仅保留 macOS 本地加速的 `h264_videotoolbox` 和通用 `libx264`（CPU 软解）。在视频缝合和最终组装时，移除有关 NVENC 预设（`preset`）的渲染参数。
* **[精简] 路径与进程控制**：FFmpeg 路径不再检索 `.exe`，同时去除了 Windows-only 的 `creationflags` 进程创建隐藏控制。

---

## 3. Mac 本地二进制依赖处理

为了保证应用分发后的“即装即用”，项目直接打包了 macOS 专属的原生二进制工具：

### 3.1 `ffmpeg`
* 将 macOS x86_64/arm64 版本的 `ffmpeg` 二进制文件放置于 `./_internal_mac/ffmpeg` 目录中。

### 3.2 `whisper-cli` 的“完全静态链接”编译
为了避免在其他 Mac 机器上运行时因缺少 Homebrew 动态库（如 `.dylib` 路径错误）导致崩溃，我们在本地编译时使用了 **完全静态链接（Static Link）** 方案：
* **标准库查找 Bug 修复**：在 CMake 配置中加入 `-DCMAKE_CXX_FLAGS="-isystem /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1"` 选项，解决了编译时找不到 `<array>` 等 C++ 标准库头文件的问题。
* **静态化编译命令**：
  ```bash
  # 配置静态链接，生成自包含的单一 whisper-cli 二进制
  venv_mac/bin/cmake -S /tmp/whisper_cpp_mac_build -B /tmp/whisper_cpp_mac_build/build \
    -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DWHISPER_BUILD_TESTS=OFF \
    -DWHISPER_BUILD_EXAMPLES=ON \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_CXX_FLAGS="-isystem /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1"

  # 开始编译
  venv_mac/bin/cmake --build /tmp/whisper_cpp_mac_build/build --config Release -j$(sysctl -n hw.logicalcpu)
  ```
* 通过 `otool -L` 检查，生成的 `whisper-cli` 只依赖 macOS 系统原生自带的 Frameworks（Metal / Accelerate 等），无需携带任何外置动态库。

---

## 4. Python 运行环境与依赖包管理

### 4.1 专属虚拟环境
* 使用 `Python 3.12` 在本地构建了纯净的 Mac 专属虚拟环境 `venv_mac`。

### 4.2 依赖清单裁减 (`requirements_mac.txt`)
* 移除了 Windows-only 的 CUDA 加速包（如 `torch`、`ctranslate2` 等），保持依赖链的极简和快速安装特性。

### 4.3 极速安全安装规范
* 为了避免在国内网络连接官方 PyPI 产生中断与超时，同时确保不连接任何中国大陆服务器，我们采用了以下方案：
  * 使用 `--only-binary=:all:` 参数确保全部下载预编译好的二进制 Wheel 包，杜绝本地源码编译失败的隐患。
  * 推荐在终端开启您本地的 VPN 或全局代理服务以极速完成包下载。

---

## 5. 自动化运行与打包脚本

项目根目录下提供了两个一键式 Shell 脚本：

### 5.1 本地初始化与启动 (`run_mac_local.sh`)
* **命令**：`./run_mac_local.sh`
* **功能**：激活 `venv_mac` 虚拟环境，使用 binary wheels 规则校验并安装所有 Python 依赖，最终直接启动 AutoCut 后端 Flask 服务器。

### 5.2 本地打包编译 (`build_mac_local.sh`)
* **命令**：`./build_mac_local.sh`
* **功能**：激活虚拟环境并执行 `_build_mac.py` 的 PyInstaller 程序，打包输出适配当前 Mac CPU 架构的免安装应用程序。

---

## 6. CI/CD 远端编译流水线 (GitHub Actions)

由于跨 CPU 架构打包的限制（无法直接在 Intel Mac 上打包出 ARM64 M-Chip 格式的可执行文件），我们采用了 GitHub Actions 方案：

* **工作流文件**：[.github/workflows/build_mac_arm64.yml](file:///Users/laoniubit/MyPython/AUTOCUT/.github/workflows/build_mac_arm64.yml)
* **运行节点**：`macos-14`（原生 Apple Silicon M1 环境）。
* **自动化打包逻辑**：
  1. 检出仓库代码，激活 Python 并安装 macOS 包。
  2. 获取 M 芯片版 `ffmpeg`。
  3. 从源码**静态编译**出支持 Metal GPU 硬件加速的 M 芯片版 `whisper-cli`（附带 `-DBUILD_SHARED_LIBS=OFF` 配置）。
  4. 自动通过 `_build_mac.py --arch arm64` 打包生成发布版 Zip 归档，并自动作为 Release 发布到您的 GitHub 仓库。
