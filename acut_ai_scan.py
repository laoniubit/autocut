import os
import sys

# Align frozen environment argv structure with development environment
if sys.argv and not sys.argv[0].endswith('.py'):
    if len(sys.argv) > 1 and sys.argv[1].endswith('.py'):
        sys.argv[0] = sys.argv[1]
        del sys.argv[1]

import json
import subprocess
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from acut_asr import asr_service
import re

# Enforce UTF-8 for piped output to prevent Windows console encoding crashes
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def get_base_path():
    if getattr(sys, 'frozen', False): return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_path()
WORKDIR = os.path.join(BASE_DIR, "workdir")
INTERNAL_DIR = os.path.join(BASE_DIR, "_internal_")

def get_ffmpeg_config():
    candidates = [
        os.path.join(BASE_DIR, "_internal", "ffmpeg"),
        os.path.join(BASE_DIR, "ffmpeg"),
    ]
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        candidates.insert(0, os.path.join(sys._MEIPASS, "ffmpeg"))
    for p in candidates:
        if os.path.exists(p):
            return os.path.abspath(p)
    return "ffmpeg" # Fallback to system path

FFMPEG_CMD = get_ffmpeg_config()

def _extract_single_frame(args):
    FFMPEG_CMD, start, ip, thumb_path = args
    if not os.path.exists(thumb_path):
        # -ss strictly before -i triggers native Discard Seeking (20-50ms seek)
        subprocess.run([
            FFMPEG_CMD, "-y", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-i", ip,
            "-frames:v", "1", "-s", "320x570", thumb_path
        ], capture_output=True)

def _log(msg, **kwargs):
    msg_str = str(msg)
    # 捕获推理进度，并转换为纯净强类型 JSON_EVENT
    if "Inferencing" in msg_str:
        match = re.search(r'(\d+)%', msg_str)
        if match:
            raw_pct = int(match.group(1))
            print(f'JSON_EVENT: {{"event": "ASR_INFER", "pct": {raw_pct}}}', flush=True)
            return

    # 普通日志与进度正常输出
    if kwargs.get('is_progress') or "%" in msg_str:
        print(f"PROGRESS: {msg}", flush=True)
    else:
        print(msg, flush=True)

def run_scan_pipeline():
    """Industrial Coordinator: Consolidated single-script AI Analysis pipeline."""
    parser = argparse.ArgumentParser(description="AutoCut Matrix AI Analysis Worker")
    parser.add_argument("--project_id", required=True, help="Target project ID")
    parser.add_argument("--ip", required=True, help="Input video path")
    parser.add_argument("--model_id", default="tiny", help="ASR model size")
    parser.add_argument("--force_device", default="auto", help="Hardware device (cuda/cpu/auto)")
    parser.add_argument("--initial_prompt", default="", help="AI transcription hint")
    parser.add_argument("--segments_path", default="", help="Path to pre-existing segments JSON")
    
    args = parser.parse_args()
    
    project_id = args.project_id
    ip = args.ip
    model_id = args.model_id
    force_device = args.force_device
    initial_prompt = args.initial_prompt
    segments_path = args.segments_path
    
    project_dir = os.path.join(WORKDIR, project_id)
    v_temp_dir = os.path.join(project_dir, "temp")
    os.makedirs(v_temp_dir, exist_ok=True)
    
    segments = []
    if segments_path and os.path.exists(segments_path):
        _log(f"▶ [Asset Sync] Loading pre-existing segments from {segments_path}")
        with open(segments_path, 'r', encoding='utf-8') as f:
            segments = json.load(f)
    
    if not segments:
        # --- STAGE 1: Audio Extraction (Forced Direct Re-encoding & ASCII Sandbox Enforced) ---
        audio_path = os.path.join(v_temp_dir, "temp_audio.wav")
        
        # 1. 无条件物理删除已存在缓存，彻底铲除历史残留
        if os.path.exists(audio_path):
            try: os.remove(audio_path)
            except Exception as e:
                _log(f"WARNING: Failed to clear old ASR audio cache: {e}")
                
        _log("正在提取音频轨道 (强制直接物理重编码)...")
        # 2. 强制使用 FFmpeg 进行标准采样率、声道、PCM 格式强压
        cmd = [FFMPEG_CMD, "-y", "-loglevel", "error", "-i", ip, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio_path]
        subprocess.run(cmd, capture_output=True)
        
        # 3. 物理规格刚性校验，确保波形 RIFF 头与文件大小绝对合规
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) <= 44:
            raise RuntimeError(f"[ASR AUDIO ERROR] Audio extraction failed or output is empty: {audio_path}")
            
        try:
            import wave
            with wave.open(audio_path, 'rb') as w:
                params = w.getparams()
                if params.framerate != 16000 or params.nchannels != 1 or params.sampwidth != 2:
                    raise ValueError(f"WAV parameters mismatch: rate={params.framerate}, channels={params.nchannels}, width={params.sampwidth}")
        except Exception as e:
            if os.path.exists(audio_path):
                try: os.remove(audio_path)
                except: pass
            raise RuntimeError(f"[ASR AUDIO ERROR] WAV header physical integrity audit failed: {e}")
        
        # --- STAGE 2: ASR Transcription (In-Process with lifecycle management) ---
        try:
            _log(f"正在初始化 AI 模型 ({model_id})...")
            target_device = None if force_device == 'auto' else force_device
            
            # Explicit initialization within the consolidated script
            asr_service.initialize(model_id=model_id, device=target_device, log_fn=_log)
            
            _log("AI 正在解析语音内容，请稍候...")
            segments, info = asr_service.transcribe(
                audio_path, 
                log_fn=_log, 
                stop_event=threading.Event(), 
                initial_prompt=initial_prompt
            )
            
            # Standardize & Save Raw ASR
            for i, s in enumerate(segments):
                s["id"] = i + 1
                s["duration"] = round(s["end"] - s["start"], 3)
                
            asr_raw_path = os.path.join(v_temp_dir, "asr_raw.json")
            with open(asr_raw_path, "w", encoding="utf-8") as f:
                json.dump(segments, f, ensure_ascii=False, indent=2)
                
            _log("语音语义识别任务已完成")
        finally:
            # [Serious Resource Management]
            # Ensuring model is unloaded even if transcription fails
            asr_service.unload_model()
            try: 
                if os.path.exists(audio_path): os.remove(audio_path)
            except: pass

    # --- STAGE 3: Visual Asset Generation ---
    _log(f"[Asset Library] Generating {len(segments)} visual anchors...")
    thumbs_dir = os.path.join(v_temp_dir, "thumbs")
    os.makedirs(thumbs_dir, exist_ok=True)
    
    total_segs = len(segments)
    
    # Package tasks for concurrent execution
    tasks = []
    for i, s in enumerate(segments):
        sid = s.get("id")
        start = s.get("start")
        target_fn = f"raw_seg_{sid}.jpg"
        s["thumb"] = f"{project_id}/temp/thumbs/{target_fn}"
        thumb_path = os.path.join(thumbs_dir, target_fn)
        tasks.append((FFMPEG_CMD, start, ip, thumb_path))
        
    # Execute batch fast seeks concurrently with ThreadPoolExecutor (optimal multi-core utilization)
    completed = 0
    cpu_count = os.cpu_count() or 4
    max_workers = max(4, min(16, cpu_count))
    last_pct = -1
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_extract_single_frame, task) for task in tasks]
        for _ in as_completed(futures):
            completed += 1
            # Real-time stdout progress sync for UI progressbar (Map 50%-100%)
            raw_pct = int(completed / total_segs * 100) if total_segs > 0 else 100
            if raw_pct != last_pct:
                print(f'JSON_EVENT: {{"event": "VISUAL_ANCHOR", "pct": {raw_pct}}}', flush=True)
                last_pct = raw_pct
            
    semantic_index_path = os.path.join(v_temp_dir, "semantic_index.json")
    with open(semantic_index_path, "w", encoding="utf-8") as f:
        json.dump(segments, f, ensure_ascii=False, indent=2)
            
    _log("SUCCESS: Visual assets materialized.")

if __name__ == "__main__":
    run_scan_pipeline()
