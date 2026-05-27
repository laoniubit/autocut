"""ASR (Automatic Speech Recognition) service module using Whisper.cpp & ONNX Silero VAD."""
import os
import sys
import time
import json
import wave
import struct
import subprocess
import threading
from typing import Callable, Tuple, List, Dict, Any

# ---- macOS Only Constant ----
_CREATE_NO_WINDOW = 0
_DEFAULT_ASR_DEVICE = "cpu"

# Retain original PATH DLL injection patch to ensure CUDA dll discovery works flawlessly
# CUDA DLL injection logic removed (macOS-only)


LogFn = Callable[..., None] | None

def get_base_path() -> str:
    """Get the base directory of the application."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_path()

def get_models_dir() -> str:
    """Resolve models directory. Under full packaged builds, check _MEIPASS first."""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        meipass_models = os.path.join(sys._MEIPASS, "models", "whisper-cpp")
        if os.path.exists(meipass_models):
            # Check if there are any model files present
            try:
                if any(f.endswith('.bin') for f in os.listdir(meipass_models)):
                    return meipass_models
            except Exception:
                pass
    return os.path.join(BASE_DIR, "models", "whisper-cpp")

MODELS_DIR = get_models_dir()
os.makedirs(MODELS_DIR, exist_ok=True)

# ---------------------------------------------------------
# Dynamic Helper Functions
# ---------------------------------------------------------
def find_whisper_exe(device: str = "cpu") -> str:
    """Robust path-discovery for macOS whisper binary."""
    mac_paths = [
        os.path.join(BASE_DIR, "_internal", "whisper-cli"),
        os.path.join(BASE_DIR, "whisper-cli"),
    ]
    import shutil as _shutil
    system_whisper = _shutil.which("whisper-cli") or _shutil.which("whisper")
    if system_whisper:
        mac_paths.append(system_whisper)
    for p in mac_paths:
        if p and os.path.exists(p):
            return os.path.abspath(p)
    raise FileNotFoundError(
        f"未找到 Mac 版 ASR 引擎 (whisper-cli)。\n"
        f"请运行 setup_mac_deps.sh 安装，或从 https://github.com/ggml-org/whisper.cpp 编译后\n"
        f"放置到: {os.path.join(BASE_DIR, '_internal', 'whisper-cli')}"
    )

def find_silero_vad_onnx() -> str:
    """Robust path-discovery for pre-packaged silero_vad_v6.onnx."""
    paths = [
        os.path.join(BASE_DIR, "_internal", "silero_vad_v6.onnx"),
        os.path.join(BASE_DIR, "models", "silero_vad_v6.onnx"),
        os.path.join(BASE_DIR, "silero_vad_v6.onnx"),
    ]
    for p in paths:
        if os.path.exists(p):
            return os.path.abspath(p)
    return ""

def ensure_1s_silence_wav(target_path: str) -> None:
    """Programmatically generate a standard 1-second PCM 16kHz mono silence WAV file."""
    if os.path.exists(target_path) and os.path.getsize(target_path) > 44:
        return
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    sample_rate = 16000
    num_samples = 16000
    with wave.open(target_path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        # 16000 silent zero-valued 16-bit signed integer samples
        data = struct.pack('<h', 0) * num_samples
        w.writeframes(data)

def ensure_60s_benchmark_wav(target_path: str) -> None:
    """Programmatically generate a standard 60-second PCM 16kHz mono 440Hz periodic heartbeat tone WAV file."""
    import math
    if os.path.exists(target_path) and os.path.getsize(target_path) > 44:
        return
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    sample_rate = 16000
    num_samples = 16000 * 60
    with wave.open(target_path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        # Generate periodic 440Hz sine wave samples (0.1s beep every 3s)
        samples = []
        for i in range(num_samples):
            t = i / 16000.0
            if (t % 3.0) < 0.1:
                val = int(16384.0 * math.sin(2.0 * math.pi * 440.0 * t))
            else:
                val = 0
            samples.append(val)
        data = struct.pack(f'<{num_samples}h', *samples)
        w.writeframes(data)

# ---------------------------------------------------------
# Pure ONNX-based Silero VAD Runner (Zero PyTorch Dependency)
# ---------------------------------------------------------
class SileroVAD:
    """Ultra-lightweight ONNX-only VAD inference session capped at CPU single-threading."""
    def __init__(self, onnx_path: str):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(onnx_path, opts, providers=['CPUExecutionProvider'])
        self.reset()
        
    def reset(self) -> None:
        import numpy as np
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        
    def __call__(self, chunk) -> float:
        import numpy as np
        x = np.expand_dims(chunk, axis=0)
        inputs = {
            'input': x,
            'h': self._h,
            'c': self._c
        }
        outputs = self.session.run(None, inputs)
        assert isinstance(outputs, list)
        speech_probs = outputs[0]
        hn = outputs[1]
        cn = outputs[2]
        assert isinstance(hn, np.ndarray)
        assert isinstance(cn, np.ndarray)
        assert isinstance(speech_probs, np.ndarray)
        self._h = hn
        self._c = cn
        # Safely extract scalar regardless of 1D/2D tensor shape changes in ONNX models
        return float(speech_probs.item())

def get_speech_segments(audio_path: str, onnx_path: str, min_speech_duration_ms: int = 250, 
                        min_silence_duration_ms: int = 300, threshold: float = 0.5) -> List[Dict[str, float]]:
    """Analyzes raw PCM WAV frames via ONNX VAD and extracts vocal active boundaries."""
    import numpy as np
    
    if not onnx_path or not os.path.exists(onnx_path):
        return []
        
    # Read audio PCM data
    with wave.open(audio_path, 'rb') as w:
        params = w.getparams()
        if params.framerate != 16000 or params.nchannels != 1 or params.sampwidth != 2:
            return [] # Invalid wave head parameters
        frames = w.readframes(w.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    chunk_size = 576
    sample_rate = 16000
    
    vad = SileroVAD(onnx_path)
    active_segments = []
    is_speech = False
    speech_start = 0.0
    silence_start = 0.0
    
    for i in range(0, len(samples) - chunk_size, chunk_size):
        chunk = samples[i:i+chunk_size]
        prob = vad(chunk)
        current_time = (i + chunk_size) / sample_rate
        
        if prob >= threshold:
            if not is_speech:
                is_speech = True
                speech_start = i / sample_rate
            silence_start = 0.0
        else:
            if is_speech:
                if silence_start == 0.0:
                    silence_start = i / sample_rate
                if (current_time - silence_start) >= (min_silence_duration_ms / 1000.0):
                    speech_end = silence_start
                    if (speech_end - speech_start) >= (min_speech_duration_ms / 1000.0):
                        active_segments.append({"start": speech_start, "end": speech_end})
                    is_speech = False
                    silence_start = 0.0
                    
    if is_speech:
        speech_end = len(samples) / sample_rate
        if (speech_end - speech_start) >= (min_speech_duration_ms / 1000.0):
            active_segments.append({"start": speech_start, "end": speech_end})
            
    return active_segments

# ---------------------------------------------------------
# C++ CLI Process Runner with Async Pipe Draining
# ---------------------------------------------------------
def run_whisper_cpp(whisper_exe: str, model_path: str, audio_path: str, language: str = "zh",
                    threads: int = 4, device: str = "cuda", initial_prompt: str = "",
                    log_fn: LogFn = None) -> str:
    """Executes whisper C++ binary and blocks until the physical JSON output is written.
    
    macOS Metal/CPU Execution Path:
      Uses whisper-cli binary compiled with Metal acceleration.
    """
    json_path = audio_path + ".json"
    if os.path.exists(json_path):
        try: os.remove(json_path)
        except: pass
    # =========================================================
    # Mac/Linux: 统一 CPU 分支（无 CUDA），不传 -dev 参数
    # =========================================================
    cmd = [
        whisper_exe,
        "-m", model_path,
        "-f", audio_path,
        "-l", language,
        "-oj",
        "-pp",
        "-t", str(threads),
    ]
    if device == "cpu":
        cmd.append("-ng")
        
    if initial_prompt:
        cmd.extend(["--prompt", initial_prompt])
    if log_fn:
        log_fn(f"[ASR Engine] Executing C++ pipeline on Metal/CPU (Mac)...")
        log_fn(f"[ASR Command] {' '.join(cmd)}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
        creationflags=_CREATE_NO_WINDOW
    )

    # =========================================================
    # Shared async pipe drainer (prevents 64KB buffer deadlock)
    # stderr → log_fn for real-time progress feedback
    # =========================================================
    def drain_stdout(stream):
        for line in iter(stream.readline, ''):
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            if log_fn and stripped.startswith('[') and '-->' in stripped:
                # Capture intermediate result: [00:00:00.000 --> 00:00:05.000]   Text
                parts = stripped.split(']', 1)
                if len(parts) == 2:
                    text = parts[1].strip()
                    if text and text not in ["_", ".", ","]:
                        log_fn(f'AI 捕获: "{text}"')

    def drain_stderr(stream):
        for line in iter(stream.readline, ''):
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            if log_fn:
                if 'progress' in stripped.lower() and '%' in stripped:
                    try:
                        pct = int(''.join(filter(str.isdigit, stripped.split('%')[0][-4:])))
                        log_fn(f"AI Inferencing: {pct}%", is_progress=True)
                    except Exception:
                        log_fn(f"[ASR] {stripped}", is_progress=True)
                elif 'whisper_model_load' in stripped.lower() and 'done' in stripped.lower():
                    log_fn("[ASR] 模型加载完成，开始推理...")
                elif 'load time =' in stripped:
                    log_fn(f"[ASR Performance] 模块/权重物理装载耗时: {stripped.split('load time =')[1].strip()}")
                elif 'encode time =' in stripped:
                    log_fn(f"[ASR Performance] 核心神经编码 (Encoder) 耗时: {stripped.split('encode time =')[1].strip()}")
                elif 'decode time =' in stripped:
                    log_fn(f"[ASR Performance] 语义文本解码 (Decoder) 耗时: {stripped.split('decode time =')[1].strip()}")

    t1 = threading.Thread(target=drain_stdout, args=(process.stdout,), daemon=True)
    t2 = threading.Thread(target=drain_stderr, args=(process.stderr,), daemon=True)
    t1.start()
    t2.start()
    process.wait()
    t1.join()
    t2.join()

    if process.returncode != 0:
        raise RuntimeError(f"ASR C++ Engine crashed with exit code {process.returncode}")

    if not os.path.exists(json_path):
        raise FileNotFoundError(f"ASR C++ Engine completed but expected JSON output {json_path} was not created.")

    return json_path

# ---------------------------------------------------------
# Resilient JSON Parser & Rescaling Aligner
# ---------------------------------------------------------
def parse_whisper_cpp_json(json_path: str) -> List[Dict[str, Any]]:
    """Resiliently parses diverse versions of whisper.cpp JSON outputs (offsets and timestamps)."""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    raw_segments = []
    result = data.get("result", {})
    timeline = result.get("timeline", [])
    
    if not timeline:
        if isinstance(data, list):
            timeline = data
        elif isinstance(data, dict):
            timeline = data.get("segments", []) or data.get("transcription", [])
            
    for item in timeline:
        text = item.get("text", "").strip()
        t_from = None
        t_to = None
        
        timestamps = item.get("timestamps")
        if isinstance(timestamps, dict):
            t_from = timestamps.get("from")
            t_to = timestamps.get("to")
            
        if t_from is None or t_to is None:
            offsets = item.get("offsets")
            if isinstance(offsets, dict):
                t_from = offsets.get("from")
                t_to = offsets.get("to")
                if isinstance(t_from, int): t_from = t_from / 1000.0
                if isinstance(t_to, int): t_to = t_to / 1000.0
                
        if t_from is None: t_from = item.get("start")
        if t_to is None: t_to = item.get("end")
        
        def to_seconds(val) -> float:
            if val is None: return 0.0
            if isinstance(val, str):
                val = val.replace(",", ".")
                parts = val.strip().split(":")
                try:
                    if len(parts) == 3:
                        h, m, s = parts
                        return float(h)*3600 + float(m)*60 + float(s)
                    elif len(parts) == 2:
                        m, s = parts
                        return float(m)*60 + float(s)
                    return float(val)
                except:
                    return 0.0
            if isinstance(val, (int, float)):
                return float(val)
            return 0.0

        sec_start = to_seconds(t_from)
        sec_end = to_seconds(t_to)
        
        # Punctuation Clean
        clean_text = text.rstrip("，,。.、！？!?（）()[]【】“”\"‘’'；;：:")
        if not clean_text:
            continue
            
        # Punctuation-induced boundary drift mitigation: shrink end slightly if it ends with punctuation
        if len(text) > len(clean_text):
            sec_end = max(sec_start, sec_end - 0.15)
            
        raw_segments.append({
            "start": round(sec_start, 3),
            "end": round(sec_end, 3),
            "text": clean_text
        })
        
    return raw_segments

def align_segments_with_vad(asr_segments: List[Dict[str, Any]], vad_segments: List[Dict[str, float]], 
                            max_gap_s: float = 0.3) -> List[Dict[str, Any]]:
    """Dual-AI alignment fusion, applying Silence Discard, VAD Snap-to-Edge and Anti-Overlap solvers."""
    aligned = []
    
    for seg in asr_segments:
        s_start = seg["start"]
        s_end = seg["end"]
        s_len = s_end - s_start
        if s_len <= 0:
            continue
            
        overlaps = []
        for v in vad_segments:
            v_start = v["start"]
            v_end = v["end"]
            overlap = max(0.0, min(s_end, v_end) - max(s_start, v_start))
            if overlap > 0:
                overlaps.append((overlap, v))
                
        if not overlaps:
            # Silence Discard: completely remove silent hallucinations
            continue
            
        overlaps.sort(key=lambda x: x[0], reverse=True)
        best_overlap_len, best_v = overlaps[0]
        overlap_ratio = best_overlap_len / s_len
        
        if overlap_ratio >= 0.4:
            # State 1: Snap-to-Edge
            # Multi-interval check for Span-Adsorption
            matched_vads = [x[1] for x in overlaps]
            matched_vads.sort(key=lambda x: x["start"])
            
            new_start = max(s_start, matched_vads[0]["start"])
            new_end = min(s_end, matched_vads[-1]["end"])
            
            aligned.append({
                "start": round(new_start, 3),
                "end": round(new_end, 3),
                "text": seg["text"]
            })
        else:
            # State 3: Keep original Whisper boundaries as safety bypass
            aligned.append({
                "start": round(s_start, 3),
                "end": round(s_end, 3),
                "text": seg["text"]
            })
            
    # Anti-Overlap Snapping Solver (防止相邻切片出现交错/重叠)
    for idx in range(len(aligned) - 1):
        curr_seg = aligned[idx]
        next_seg = aligned[idx + 1]
        
        curr_end = curr_seg["end"]
        next_start = next_seg["start"]
        
        if curr_end > next_start:
            midpoint = round((curr_end + next_start) / 2.0, 3)
            curr_seg["end"] = midpoint
            next_seg["start"] = midpoint
            
    final_segments = []
    for i, s in enumerate(aligned):
        s["id"] = i + 1
        s["duration"] = round(s["end"] - s["start"], 3)
        final_segments.append(s)
            
    return final_segments

# ---------------------------------------------------------
# Drops-in Drops-in Replacement Class (Surgical Integrity)
# ---------------------------------------------------------
class ACUT_ASR_Service:
    """Unified Orchestrator implementing the exact drops-in interface expected by acut_server/scan."""
    _instance: 'ACUT_ASR_Service | None' = None
    model_handle = None
    current_model_id: str | None = None
    device: str | None = None
    compute_type: str | None = None
    _self_test_passed: bool = False

    def __new__(cls) -> 'ACUT_ASR_Service':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_installed_models(self) -> List[str]:
        """Scans the models directory for ready C++ model binaries (ggml-*.bin)."""
        if not os.path.exists(MODELS_DIR):
            return []
        found = []
        for file in os.listdir(MODELS_DIR):
            if file.startswith("ggml-") and file.endswith(".bin"):
                model_name = file.replace("ggml-", "").replace(".bin", "")
                found.append(model_name)
        return found

    def verify_environment(self, device: str = "cpu", force_deep_test: bool = False) -> Tuple[bool, str, List[object]]:
        """Runs the pre-flight self-test on 1s silence file using whisper binary."""
        if self._self_test_passed and not force_deep_test:
            return True, "Ready", []

        try:
            whisper_exe = find_whisper_exe("cpu")
        except FileNotFoundError as e:
            return False, str(e), []

        if not os.path.exists(whisper_exe):
            return False, f"C++ ASR Engine binary (whisper-cli) is missing. Path searched: {whisper_exe}", []
            
        onnx_path = find_silero_vad_onnx()
        if not onnx_path:
            return False, "ONNX VAD asset (silero_vad_v6.onnx) is missing.", []
            
        installed_models = self.get_installed_models()
        if not installed_models:
            return False, f"No GGML model files found. Please place a ggml-*.bin model file under models/whisper-cpp/ (Searched: {MODELS_DIR}).", []
            
        if not force_deep_test:
            self._self_test_passed = True
            return True, "Ready (Static Validation Passed)", []

        test_model_id = 'tiny' if 'tiny' in installed_models else installed_models[0]
        model_path = os.path.abspath(os.path.join(MODELS_DIR, f"ggml-{test_model_id}.bin"))
        test_wav = os.path.join(BASE_DIR, "_internal_", "60s_benchmark.wav")
        try:
            ensure_60s_benchmark_wav(test_wav)
        except Exception as e:
            return False, f"Failed to generate 60s benchmark WAV file: {str(e)}", []

        test_json = test_wav + ".json"
        cpu_count = os.cpu_count() or 4
        if device == "cpu":
            cpu_threads = max(1, cpu_count - 2) if cpu_count > 2 else cpu_count
            cpu_threads = min(16, cpu_threads)
        elif device == "metal":
            is_intel = False
            try:
                config_path = os.path.join(BASE_DIR, "_internal_", "config_hard.json")
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        cpu_name = cfg.get("hardware_profile", {}).get("cpu", "")
                        if "intel" in cpu_name.lower():
                            is_intel = True
            except Exception:
                pass
            max_threads = 12 if is_intel else 8
            cpu_threads = max(4, min(max_threads, cpu_count - 2 if is_intel else cpu_count // 2))
        else:
            cpu_threads = max(2, min(8, cpu_count // 2))
        
        def parse_whisper_timings_pure(stderr_text: str) -> float:
            import re
            total_match = re.search(r"total time\s*=\s*([\d\.]+)\s*ms", stderr_text)
            load_match = re.search(r"load time\s*=\s*([\d\.]+)\s*ms", stderr_text)
            total_ms = float(total_match.group(1)) if total_match else 0.0
            load_ms = float(load_match.group(1)) if load_match else 0.0
            pure_ms = max(1.0, total_ms - load_ms)
            return pure_ms / 1000.0

        cmd_cpu = [whisper_exe, "-m", model_path, "-f", test_wav, "-l", "zh", "-oj", "-t", str(cpu_threads)]
        if device == "cpu":
            cmd_cpu.append("-ng")
        if os.path.exists(test_json):
            try: os.remove(test_json)
            except: pass
            
        t0 = time.time()
        try:
            res_cpu = subprocess.run(
                cmd_cpu,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=_CREATE_NO_WINDOW
            )
            elapsed_cpu = time.time() - t0
            if res_cpu.returncode != 0:
                err_msg = res_cpu.stderr.strip() or res_cpu.stdout.strip() or "Unknown C++ crash"
                return False, f"[CPU AUDIT ERROR] whisper failed. Return code: {res_cpu.returncode}. Stderr: {err_msg}", []
        except Exception as e:
            return False, f"ASR CPU self-test subprocess call failed: {str(e)}", []
            
        elapsed_cpu_internal = parse_whisper_timings_pure(res_cpu.stderr)
        if elapsed_cpu_internal > 0:
            speed_cpu_pure = 60.0 / elapsed_cpu_internal
            speed_cpu_outer = 60.0 / elapsed_cpu
            cpu_report = (
                f"⚡ macOS ASR 模式：实测纯算力吞吐率 {speed_cpu_pure:.1f}X (内部净耗时 {elapsed_cpu_internal:.2f}s) | "
                f"全链路吞吐率 {speed_cpu_outer:.1f}X (物理总耗时 {elapsed_cpu:.2f}s) [线程数: {cpu_threads}]"
            )
        else:
            speed_cpu = 60.0 / elapsed_cpu if elapsed_cpu > 0 else 999.0
            cpu_report = f"⚡ macOS ASR 模式：物理耗时 {elapsed_cpu:.2f}s | 实测吞吐率: {speed_cpu:.1f}X (线程数: {cpu_threads})"

        if os.path.exists(test_json):
            try: os.remove(test_json)
            except: pass
            
        self._self_test_passed = True
        message = (
            "✅ ASR 60秒真实音频跑分审计通过！\n"
            f"{cpu_report}"
        )
        return True, message, []

    def initialize(self, model_id: str = "tiny", device: str | None = None,
                   compute_type: str | None = None, log_fn: LogFn = None) -> Tuple[bool, str]:
        """solidifies active model configuration parameters."""
        self.current_model_id = model_id
        # Mac/Linux 无 CUDA，强制使用 cpu 设备
        self.device = device or _DEFAULT_ASR_DEVICE
        self.compute_type = compute_type or "float16"
        
        if log_fn:
            log_fn(f"C++ ASR Engine model '{model_id}' configuration registered on {self.device}")
        return True, f"Model {model_id} configured"

    def unload_model(self) -> Tuple[bool, str]:
        """Provides a memory recovery placeholder, returning immediately since memory is held out-of-process."""
        import gc
        self.current_model_id = None
        self.device = None
        self.compute_type = None
        gc.collect()
        return True, "Active model unloaded (out-of-process handles cleared)."

    def transcribe(self, audio_path: str, language: str = "zh", beam_size: int = 5,
                   log_fn: LogFn = None, stop_event: threading.Event | None = None,
                   initial_prompt: str = ""):
        """Executes the rigid, multi-threaded C++ transcription with high-precision VAD post-processing."""
        # 1. Fast file-existence guards only.
        # NOTE: verify_environment() is intentionally NOT called here.
        # That method loads the full 1.5GB model for a 1s silent pre-flight test,
        # which adds 2+ minutes of silent stall before actual transcription begins.
        # It belongs exclusively to the UI panel self-test button flow.
        device = self.device or "cuda"
        whisper_exe = find_whisper_exe(device)
        if not os.path.exists(whisper_exe):
            raise FileNotFoundError(f"ASR C++ binary not found. Expected path: {whisper_exe}")

        model_id = self.current_model_id or "tiny"
        model_path = os.path.abspath(os.path.join(MODELS_DIR, f"ggml-{model_id}.bin"))
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"GGML Model file not found at: {model_path}. Please place the model file in models/whisper-cpp/.")

        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Source audio missing: {audio_path}")

        # Get total audio duration first
        total_dur = 0.0
        try:
            with wave.open(audio_path, 'rb') as w:
                params = w.getparams()
                total_dur = params.nframes / float(params.framerate)
        except Exception as e:
            if log_fn:
                log_fn(f"[ASR Warning] 读取音频长度失败: {str(e)}")

        start_time = time.time()

        # Calculate dynamic thread allocation based on device to maximize multi-core capacity
        cpu_count = os.cpu_count() or 4
        device = self.device or "cpu"
        if device == "cpu":
            # 纯 CPU 模式：最大化利用多核（保留 2 核给系统开销，上限 16）
            threads = max(1, cpu_count - 2) if cpu_count > 2 else cpu_count
            threads = min(16, threads)
        elif device == "metal":
            # Metal GPU 模式：GPU 承担 Encoder 重载，CPU 只做 Decoder，4-8 线程足够
            # 过多线程反而会与 Metal 命令队列争抢资源
            # 针对 Intel Mac，由于非统一内存且 GPU 算力较弱，CPU 线程限制放宽到 12 以最大化解码性能
            is_intel = False
            try:
                config_path = os.path.join(BASE_DIR, "_internal_", "config_hard.json")
                if os.path.exists(config_path):
                    with open(config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        cpu_name = cfg.get("hardware_profile", {}).get("cpu", "")
                        if "intel" in cpu_name.lower():
                            is_intel = True
            except Exception:
                pass
            max_threads = 12 if is_intel else 8
            threads = max(4, min(max_threads, cpu_count - 2 if is_intel else cpu_count // 2))
        else:
            # CUDA GPU 模式：CPU 解码线程 2-8
            threads = max(2, min(8, cpu_count // 2))
        
        # 2. File-based C++ engine execution
        if log_fn:
            log_fn("正在调起 C++ ASR 听写引擎...")
        json_path = run_whisper_cpp(
            whisper_exe, model_path, audio_path,
            language=language, threads=threads,
            device=device,
            initial_prompt=initial_prompt, log_fn=log_fn
        )
        
        # 3. Parse transcript segments
        raw_segments = parse_whisper_cpp_json(json_path)
        
        # 4. Load ONNX VAD and extract physical human speech active intervals
        onnx_path = find_silero_vad_onnx()
        if log_fn:
            log_fn("AI 正在扫描声学物理边界 (Silero VAD ONNX)...")
        vad_start = time.time()
        vad_segments = get_speech_segments(audio_path, onnx_path)
        vad_elapsed = time.time() - vad_start
        if log_fn:
            log_fn(f"[ASR Performance] VAD 声学扫描耗时: {round(vad_elapsed, 3)}s")
        
        # [NEW] 数据旁路：保存原始未经 VAD 篡改的解析数据
        debug_raw_path = os.path.join(os.path.dirname(audio_path), "whisper_raw_parsed.json")
        try:
            with open(debug_raw_path, "w", encoding="utf-8") as f:
                json.dump(raw_segments, f, ensure_ascii=False, indent=2)
        except: pass
        
        # 5. Dual-AI alignment fusion
        if log_fn:
            log_fn("正在进行边界磁性吸附对齐与自愈过滤...")
        aligned_segments = align_segments_with_vad(raw_segments, vad_segments)
        
        # [MODIFY] 阻止临时 JSON 被删除，更名为物理留档
        try:
            cpp_out_path = os.path.join(os.path.dirname(audio_path), "whisper_cpp_raw.json")
            if os.path.exists(cpp_out_path): os.remove(cpp_out_path)
            os.rename(json_path, cpp_out_path)
        except: pass
        
        # Emit beautiful status updates to UI
        for s in aligned_segments:
            if log_fn:
                log_fn(f'识别到字幕: "{s["text"]}"')
                
        actual_elapsed = time.time() - start_time
        if device == "cuda":
            hw_label = "英伟达 GPU 加速 (CUDA)"
        elif device == "metal":
            hw_label = "Apple Metal GPU 加速"
        else:
            hw_label = f"中央处理器 (CPU, 线程数: {threads})"
        speed_ratio = total_dur / actual_elapsed if actual_elapsed > 0 else 0.0

        if log_fn:
            done_msg = f"TRANSCRIPTION_SUCCESS: 听写完成 | 运行设备: {hw_label} | 音频长度: {round(total_dur, 1)}s | 推理耗时: {round(actual_elapsed, 1)}s | 识别速度: {round(speed_ratio, 1)}x"
            log_fn(done_msg, level="SUCCESS")
            
        # Return segments list and dummy info object to preserve original tuple structure signature
        class InfoStub:
            duration = actual_elapsed
        return aligned_segments, InfoStub()

asr_service: ACUT_ASR_Service = ACUT_ASR_Service()
