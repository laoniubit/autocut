import subprocess
import os
import re
import json
import sys
import shutil
import typing
import time
import platform
from pathlib import Path
import psutil
from acut_asr import asr_service

# ---- macOS Only Constant ----
_CREATE_NO_WINDOW = 0

class LogFileWrapper:
    def __init__(self, original_stream):
        self.original_stream = original_stream
        _exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        self.filepath = os.path.join(_exe_dir, '_internal_', 'debug.txt')
        try: os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        except: pass
    def write(self, data):
        if not data: return
        if self.original_stream is not None:
            try: self.original_stream.write(data)
            except: pass
        try:
            with open(self.filepath, 'a', encoding='utf-8') as f:
                f.write(data)
        except: pass
    def flush(self):
        if self.original_stream is not None:
            try: self.original_stream.flush()
            except: pass

sys.stdout = LogFileWrapper(sys.stdout)
sys.stderr = LogFileWrapper(sys.stderr)



# ----------------------------
# Path and Environment Setup
# ----------------------------
def get_base_path():
    if getattr(sys, 'frozen', False): return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_resource_path(rel_path):
    if hasattr(sys, '_MEIPASS'): return os.path.join(sys._MEIPASS, rel_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "_internal", rel_path)

BASE_DIR = get_base_path()
def get_path(rel_path): return os.path.join(BASE_DIR, rel_path)

INTERNAL_DIR = get_path("_internal_")
os.makedirs(INTERNAL_DIR, exist_ok=True)
WORKDIR = get_path("workdir")
os.makedirs(WORKDIR, exist_ok=True)

# ----------------------------
# Global Config Management
# ----------------------------
def _default_export_path() -> str:
    """macOS 默认导出目录：用户桌面。"""
    return str(Path.home() / "Desktop" / "autocut_out")

CONFIG: typing.Dict[str, typing.Any] = {
    "output_specs": {
        "width": 1080, "height": 1920, "fps": 30,
        "audio_bitrate": "192k", "audio_sample_rate": 48000
    },
    "effects": {
        "nvenc_preset": "p4",
        "cpu_preset": "medium",
        "audio_noise_threshold": -30,
        "audio_min_silence_duration": 0.3
    },
    "ai_specs": {
        "high_score_theme": "", "low_score_theme": "",
        "auto_select_limit_s": 90,
        "deepseek_api_key": "",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_model": "deepseek-v4-flash",
        "deepseek_temperature": 0.1,
        "deepseek_timeout": 120
    },
    "export_base_path": _default_export_path(),
    "synthesis_options": {
        "subtitle": False,
        "focus_crop": False,
        "original_audio": True
    }
}

def load_config():
    config_file = os.path.join(INTERNAL_DIR, "config.json")
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                _c = json.load(f)
                for k, v in _c.items():
                    if k in CONFIG and isinstance(v, dict): CONFIG[k].update(v)
                    else: CONFIG[k] = v
        except: pass

def save_config():
    config_file = os.path.join(INTERNAL_DIR, "config.json")
    save_json_atomic(config_file, CONFIG)

def save_json_atomic(path, data):
    temp_path = path + ".tmp"
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        if os.path.exists(path): os.remove(path)
        os.rename(temp_path, path)
        return True
    except:
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except: pass
        return False

def write_debug_log(msg: str):
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        debug_path = os.path.join(INTERNAL_DIR, "debug.txt")
        with open(debug_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except: pass

# Initial Load
load_config()
save_config()

# --- CONFIG_HARD (Restored) ---
CONFIG_HARD_PATH = os.path.join(INTERNAL_DIR, "config_hard.json")

CONFIG_HARD = {
    "asr_processing": {
        "model_id": "tiny", "device": "cpu"
    },
    "version": "12.81"
}
def load_hard_config():
    if os.path.exists(CONFIG_HARD_PATH):
        try:
            with open(CONFIG_HARD_PATH, 'r', encoding='utf-8') as f:
                _h = json.load(f)
                for k, v in _h.items():
                    if k in CONFIG_HARD and isinstance(v, dict): CONFIG_HARD[k].update(v)
                    else: CONFIG_HARD[k] = v
        except: pass
    save_json_atomic(CONFIG_HARD_PATH, CONFIG_HARD)

load_hard_config()

# --- CONFIG_ZOOM (Restored) ---
CONFIG_ZOOM_PATH = os.path.join(INTERNAL_DIR, "config_zoom.json")
CONFIG_ZOOM = {
    "face_center_x": 0.5,
    "face_center_y": 0.25,
    "min_zoom": 1.01,
    "font_size": 32,
    "sample_offset_s": 0.1,
    "show_zoom_label": True,
    "ai_confidence": 0.5,
    "min_face_size": 50,
    "detection_zone": [0.0, 0.0, 1080.0, 1300.0]
}
def load_zoom_config():
    if os.path.exists(CONFIG_ZOOM_PATH):
        try:
            with open(CONFIG_ZOOM_PATH, 'r', encoding='utf-8') as f:
                _z = json.load(f)
                for k, v in _z.items():
                    CONFIG_ZOOM[k] = v
        except: pass
    save_json_atomic(CONFIG_ZOOM_PATH, CONFIG_ZOOM)

load_zoom_config()


# ----------------------------
# Industrial Guard & I/O
# ----------------------------
def _find_ffmpeg_binary() -> str:
    """macOS FFmpeg 二进制路径发现。"""
    candidates = [
        get_resource_path("ffmpeg"),
        os.path.join(BASE_DIR, "_internal", "ffmpeg"),
        os.path.join(BASE_DIR, "ffmpeg"),
        shutil.which("ffmpeg") or "",  # 系统 PATH 兜底
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return os.path.abspath(p)
    return ""

def get_ffmpeg_config():
    f_cmd = _find_ffmpeg_binary()
    return f_cmd

FFMPEG_CMD = get_ffmpeg_config()

def force_release_directory_handles(target_path, log_fn=None):
    return True

def perform_integrity_audit():
    missing = []
    if not FFMPEG_CMD:
        missing.append("ffmpeg (not found)")
    if not os.path.exists(get_resource_path("templates")):
        missing.append("templates")
    if missing:
        err = "[INTEGRITY FAILED] Missing: " + ", ".join(missing)
        return False, err
    return True, "Integrity OK"

# ----------------------------
# ASR Analysis Workflow
# ----------------------------
def process_asr_workflow(ip, project_id, model_id='tiny', force_device='cuda', log_fn=None, stop_event=None, initial_prompt=''):
    ip_abs = os.path.abspath(ip)
    project_dir = os.path.join(WORKDIR, project_id)
    v_temp_dir = os.path.join(project_dir, "temp")
    os.makedirs(v_temp_dir, exist_ok=True)
    
    audio_path = os.path.join(v_temp_dir, "temp_audio.wav")
    if log_fn: log_fn("正在提取音频轨道...")
    if not FFMPEG_CMD: raise RuntimeError("FFmpeg binary missing")
        
    cmd = [str(FFMPEG_CMD), "-y", "-loglevel", "error", "-i", ip_abs, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio_path]
    subprocess.run(cmd, capture_output=True)
    
    if log_fn: log_fn(f"正在初始化 AI 模型 ({model_id})...")
    target_device = force_device
    asr_service.initialize(model_id=model_id, device=target_device, log_fn=log_fn)
    
    if log_fn: log_fn("正在进行语音识别...")
    segments, info = asr_service.transcribe(audio_path, log_fn=log_fn, stop_event=stop_event, initial_prompt=initial_prompt)
    
    for i, s in enumerate(segments):
        s["id"] = i
        s["duration"] = round(s["end"] - s["start"], 3)
        
    asr_raw_path = os.path.join(v_temp_dir, "asr_raw.json")
    save_json_atomic(asr_raw_path, segments)
    try: os.remove(audio_path)
    except: pass
    return segments

def list_projects():
    if not os.path.exists(WORKDIR): return []
    projects = []
    for d in os.listdir(WORKDIR):
        p_path = os.path.join(WORKDIR, d)
        if os.path.isdir(p_path) and d.isdigit():
            in_dir = os.path.join(p_path, "in")
            inputs = []
            if os.path.exists(in_dir):
                inputs = [f for f in os.listdir(in_dir) if f.lower().endswith(('.mp4','.mov','.avi','.mkv'))]
            projects.append({'id': d, 'inputs': inputs})
    return projects

def get_project_input_file(project_id):
    """[Industrial Discovery] Automatically locates the primary source video for a project ID."""
    if not project_id: return None
    in_dir = os.path.join(WORKDIR, str(project_id), "in")
    if not os.path.exists(in_dir): return None
    src_files = [f for f in os.listdir(in_dir) if f.lower().endswith(('.mp4','.mov','.avi','.mkv'))]
    if not src_files: return None
    return os.path.abspath(os.path.join(in_dir, src_files[0]))

def ensure_project_dirs(project_id):
    pass

if __name__ == "__main__":
    print("Matrix ASR Engine Standby.")