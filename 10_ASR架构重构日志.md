# AutoCut ASR 引擎重构变更记录 (v10.4)
> 重构日期：2026-05-18 | 作者：AutoCut Matrix AI Engine Team

## 背景

彻底废弃臃肿且部署复杂的 Python-in-process Faster-Whisper（依赖 PyTorch + CTranslate2 + CUDA 绑定），全面升级为以预编译 C++ 二进制 `whisper.exe` 为核心引擎、以 ONNX Silero VAD 为声学后处理对齐层的全新工业级 ASR 管线。

---

## 变更设计原则

1. **零外延**：改动范围被刚性锁死在 ASR 专属文件，不触碰合成层、服务层及前端一行代码。
2. **原地平替（Drops-in Replacement）**：对外暴露的接口 `asr_service.verify_environment()`、`asr_service.initialize()`、`asr_service.transcribe()`、`asr_service.unload_model()` 保持 100% 签名一致，主控服务感知不到任何变化。
3. **文件交换为主**：彻底丢弃大容量 stdout 管道文本传输，`whisper.exe` 通过 `-oj` 直接落盘物理 JSON，Python 从磁盘物理读取，防止管道死锁与字符集乱码。
4. **强制直接重编码**：废除 WAV 缓存复用旁路，每次无条件删除旧文件并重新 FFmpeg 转码压制。

---

## 新增文件

| 文件路径 | 说明 |
|---|---|
| `_internal/whisper.exe` | Whisper.cpp v1.8.4 预编译 CLI 可执行文件（从 `whisper-cli.exe` 重命名） |
| `_internal/ggml.dll` | GGML 张量计算核心动态库 |
| `_internal/whisper.dll` | Whisper 声学模型推理动态库 |
| `_internal/ggml-base.dll` | GGML 基础算子库 |
| `_internal/ggml-cpu.dll` | GGML CPU 算子加速库 |
| `_internal/SDL2.dll` | 音频输出层依赖库 |
| `models/whisper-cpp/ggml-small.bin` | Whisper small 级别 GGML 声学模型（预置默认） |
| `models/whisper-cpp/ggml-large-v3-turbo.bin` | Whisper Large-v3-Turbo GGML 声学模型（高精度生产用） |

## 修改文件

### `acut_asr.py` — 彻底重写（整文件级重构）

**洗掉了（-）：**
- 全部 `faster-whisper`、`ctranslate2`、`torch` 依赖
- `_ensure_imported()` PyTorch 懒加载机制
- `_transcribe()` 进程内推理调用逻辑

**注入了（+）：**
- `inject_lib_paths()` 保留原样，继续支持 CUDA DLL 路径注入
- `find_whisper_exe()` —— 多路径自适应发现 whisper.exe 二进制
- `find_silero_vad_onnx()` —— 多路径自适应发现 silero_vad_v6.onnx
- `ensure_1s_silence_wav()` —— 用标准库 `wave` 动态生成 1 秒静音预判文件
- `SileroVAD` class —— 基于 `onnxruntime` CPU 单线程的极速 VAD 帧推理器
- `get_speech_segments()` —— 以 36ms 步长扫描 PCM 波形提取物理人声区间
- `run_whisper_cpp()` —— 双线程非阻塞管道消耗器 + 物理 JSON 落盘监控
- `parse_whisper_cpp_json()` —— 兼容浮点秒/毫秒/微秒/时间字符串多格式解析
- `align_segments_with_vad()` —— 静音幻听过滤 + 磁性贴边 + 防重叠中点锚定求解器
- `verify_environment()` —— 调起 `whisper.exe` 进行文件落盘物理预判自检，**强拦截，绝不降级回退**
- `get_installed_models()` —— 扫描 `models/whisper-cpp/ggml-*.bin` 文件列表

### `acut_ai_scan.py` — 精准修改（Stage 1 音频前置处理区）

**修改前**：存在缓存旁路 `if not os.path.exists(audio_path)`，跳过重编码。

**修改后**：
1. 无条件 `os.remove` 物理粉碎旧缓存
2. 无条件 FFmpeg 强制重编码 16kHz/Mono/16-bit PCM WAV
3. 用 `wave` 库核算 RIFF 物理头参数合规性，不合规则立刻硬性报错阻断

---

## 未改动文件（设计保证）

| 文件 | 保护原因 |
|---|---|
| `acut_server.py` | 通过接口调用 ASR，原地平替后无需感知 |
| `acut_engine.py` | 系统级配置层，与 ASR 引擎型号无关 |
| `acut_synthesis.py` | 合成层，与 ASR 上游完全独立 |
| 所有前端 JS/HTML/CSS | 零逻辑前端原则，纯展示层 |

---

## 模型路径约定

| 用途 | 物理路径规范 |
|---|---|
| GGML 模型文件 | `models/whisper-cpp/ggml-{model_id}.bin` |
| whisper.exe 引擎 | `_internal/whisper.exe` |
| Silero VAD ONNX | `_internal/faster_whisper/assets/silero_vad_v6.onnx` 或 venv 路径 |
| 1s 自检静音文件 | `_internal_/1s_silence.wav`（启动时自动生成） |
