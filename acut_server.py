# MATRIX AI INDUSTRIAL STANDARDS - BACKEND CONDUCT:
# STARTUP GUARD: All import/init errors are captured to STARTUP_ERROR.TXT before any logging is available.
import sys, os, traceback

# --- SUBPROCESS ROUTING FOR FROZEN ENVIRONMENT ---
# 拦截 Worker 子进程调用，防止重启主服务器引发单例锁互杀
if getattr(sys, 'frozen', False) and len(sys.argv) > 1 and sys.argv[1].endswith('.py'):
    script_name = os.path.basename(sys.argv[1])
    sys.argv[0] = sys.argv[1]
    del sys.argv[1]
    
    try:
        if script_name == 'acut_ai_scan.py':
            import acut_ai_scan
            acut_ai_scan.run_scan_pipeline()
            sys.exit(0)
        elif script_name == 'acut_ai_select.py':
            import acut_ai_select
            acut_ai_select.run_ai_select_pipeline()
            sys.exit(0)
        elif script_name == 'acut_synthesis.py':
            import acut_synthesis
            acut_synthesis.run_synthesis()
            sys.exit(0)
    except Exception as e:
        traceback.print_exc()
        sys.exit(1)
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



def _write_startup_error(e):
    """Last-resort error sink - writes crash info before any other system is online"""
    try:
        _exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        _int_dir = os.path.join(_exe_dir, '_internal_')
        os.makedirs(_int_dir, exist_ok=True)
        _err_path = os.path.join(_int_dir, 'STARTUP_ERROR.TXT')
        with open(_err_path, 'w', encoding='utf-8') as _f:
            _f.write(f"[STARTUP CRASH] {type(e).__name__}: {e}\n\n")
            _f.write(traceback.format_exc())
            _f.write(f"\nPython: {sys.version}")
            _f.write(f"\nFrozen: {getattr(sys, 'frozen', False)}")
            _f.write(f"\nExecutable: {sys.executable}")
        print(f"Crash report written to: {_err_path}")
    except Exception:
        pass

import os
os.environ["CT2_CUDA_ALLOCATOR"] = "cub_caching"
import typing
import re
import json
import sys
import webbrowser
import threading
import time
import subprocess
import shutil
import psutil
import platform
import gc
from datetime import datetime
from flask import Flask, render_template, jsonify, send_from_directory, send_file, request
from flask_socketio import SocketIO
import acut_engine as autocut

# --- 1. INDUSTRIAL STABILITY AUDIT (Pre-flight) ---
# Moved to top: Fail-fast before Flask attempts to map non-existent directories
ok, err = autocut.perform_integrity_audit()
if not ok:
    _write_startup_error(RuntimeError(err))
    print(f"\n[FATAL] Integrity Check Failed:\n{err}")
    print("Detailed logs saved to STARTUP_ERROR.TXT")
    time.sleep(5)
    sys.exit(1)

import acut_license as license_manager
import acut_backup
import mimetypes
mimetypes.add_type('application/wasm', '.wasm')
mimetypes.add_type('application/javascript', '.js')
mimetypes.add_type('application/javascript', '.mjs')

# Inherit base pathing from the core logic to keep things unified
BASE_DIR = autocut.BASE_DIR
# Using get_resource_path for bundled UI assets (internal to EXE)
app = Flask(__name__,
            template_folder=autocut.get_resource_path('templates'),
            static_folder=autocut.get_resource_path('static'))

# --- 工业级模型下发逻辑：支持完全离线部署 ---
@app.route('/models/<path:filename>')
def serve_models(filename):
    """从本地目录提供 AI 模型权重文件，支持完全离线的端侧推理"""
    model_dir = autocut.get_path('models')
    if not os.path.exists(model_dir):
        # 允许静默失败，前端将尝试从 HF 同步
        return "Models directory not found", 404
    return send_from_directory(model_dir, filename)

try:
    # Use threading async mode. Maximize packaging compatibility.
    socketio = SocketIO(typing.cast(typing.Any, app), async_mode='threading', cors_allowed_origins=["http://127.0.0.1:5010", "http://localhost:5010"])
except Exception as e:
    _write_startup_error(e)
    print(f"[FATAL] SocketIO Init failed. Wrote to STARTUP_ERROR.TXT: {e}")
    sys.exit(1)

# UTF-8 Audit Fixes: Ensure JSON responses are not escaped to ASCII
app.config['JSON_AS_ASCII'] = False
# app.json.ensure_ascii = False  # Removed due to JSONProvider compatibility issues in some environments

DEBUG_FILE = os.path.join(autocut.INTERNAL_DIR, "debug.txt")
WEB_DEBUG_FILE = os.path.join(autocut.INTERNAL_DIR, "debug_web.txt")

# [System Boot] Reset audit files and silence the console terminal
timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
with open(DEBUG_FILE, 'w', encoding='utf-8') as f: 
    f.write(f"--- Cold Boot (System): {timestamp} ---\n")
with open(WEB_DEBUG_FILE, 'w', encoding='utf-8') as f: 
    f.write(f"--- Cold Boot (Web UI): {timestamp} ---\n")
import logging; logging.getLogger('werkzeug').disabled = True

# [Industrial Global State]
_GLOBAL_TASK_LOCK = threading.Lock()
_TASK_STOP_EVENT = threading.Event()
_QUEUE_LOCK = threading.Lock()
_LOG_WRITE_LOCK = threading.Lock()

# [Task Registry] 记录当前运行的任务元数据和子进程 PID
# 结构: {'task': 'AI_SCAN'|'SYNTHESIS', 'project_id': str, 'ffmpeg_pids': set()}
_ACTIVE_TASK: dict = {}
_ACTIVE_TASK_LOCK = threading.Lock()

API_TRANSLATIONS = {
    '/api/semantic_map': 'Sync AI Semantic Index (inc. Smart Selection)',
    '/api/upload': 'Import External Video Source',
    '/api/export_out_dir': 'Export Final Rendered Clips',
    '/api/clear_temp': 'Purge Workspace Cache',
    '/api/delete_project': 'Terminate & Delete Project',
    '/api/queue_action': 'Queue Action (Action-Reducer)',
    '/api/license_activate': 'Verify Environment Key',
    '/api/prep_audio': 'Extract Standardized Audio Stream',
    '/api/optimize_config': 'Adaptive Hardware Tuning',
    '/api/config': 'Update System-level Configuration',
    '/api/ai_select': 'AI Semantic Segment Selection'
}

VERSION = autocut.CONFIG_HARD.get("version")
if not VERSION or VERSION == "unknown":
    VERSION = "12.8"

# --- Industrial Cold Start: Global Environment Sanitization ---
def _global_industrial_reset():
    """归零自愈：开机强制清理所有残留的物理锁，确保管线真正从 0 开始"""
    if not os.path.exists(autocut.WORKDIR): return
    audit_log("▶ Initializing Global Cold Start: Purging orphaned locks...", "INFO")
    count = 0
    for root, dirs, files in os.walk(autocut.WORKDIR):
        if ".lock" in files:
            try:
                os.remove(os.path.join(root, ".lock"))
                count += 1
            except: pass
    if count > 0: audit_log(f"▶ Cold Start Self-Healing: Purged {count} orphaned locks.", "SUCCESS")

def get_hardware_footprint(force=False):
    """Industrial Utility: Unified comprehensive hardware identification (One-Time Scan)
    
    macOS 原生自适应检测：
      - CPU: sysctl machdep.cpu.brand_string (穿透 Rosetta 2，直读物理芯片型号)
      - GPU: system_profiler SPDisplaysDataType (支持 Intel 核显 / AMD 独显 / Apple Silicon M系列统一内存)
    Windows 探针保留 wmic + nvidia-smi 原始逻辑。
    """
    profile = autocut.CONFIG_HARD.get('hardware_profile', {})
    if not force and profile.get('scanned', False):
        return profile

    cpu_name = "Unknown CPU"
    gpu_list = []

    # ================================================================
    # macOS 原生硬件探针 - 获取详细 CPU 品牌型号
    # ================================================================
    try:
        res = subprocess.run(
            ['sysctl', '-n', 'machdep.cpu.brand_string'],
            capture_output=True, text=True, errors="ignore", timeout=3
        )
        brand = res.stdout.strip() if res.returncode == 0 else ""
        if not brand:
            brand = platform.processor() or platform.machine() or "Unknown CPU"
        cpu_name = brand
    except Exception:
        # Fallback to checking processor architecture or platform.processor()
        proc = platform.processor() or platform.machine() or "Unknown CPU"
        cpu_name = proc


    # 2. GPU 探测：system_profiler 解析所有显示适配器
    #    支持以下所有场景：
    #    - Intel 核显        (Intel UHD Graphics 630)
    #    - AMD 独显          (AMD Radeon Pro 5600M)
    #    - Apple Silicon GPU (Apple M1 Pro / M2 Max 统一内存显存)
    #    VRAM 解析兼容 "Dynamic, Max" / "Shared" / "Total" 多种标注格式
    try:
        res = subprocess.run(
            ['system_profiler', 'SPDisplaysDataType'],
            capture_output=True, text=True, errors="ignore", timeout=10
        )
        if res.returncode == 0:
            current_gpu: dict = {}
            for line in res.stdout.split('\n'):
                line = line.strip()
                if line.startswith("Chipset Model:"):
                    if current_gpu.get("name"):
                        gpu_list.append(current_gpu)
                    current_gpu = {
                        "name": line.split(":", 1)[1].strip(),
                        "vram_mb": 0,
                        "driver": "Metal"
                    }
                elif "VRAM" in line and ":" in line and current_gpu.get("name"):
                    # 兼容解析：VRAM (Total): 8 GB / VRAM (Dynamic, Max): 1536 MB / VRAM (Shared): 16 GB
                    vram_str = line.split(":", 1)[1].strip()
                    digits_str = "".join([c for c in vram_str if c.isdigit() or c == '.'])
                    if digits_str:
                        try:
                            val = float(digits_str)
                            if "GB" in vram_str.upper():
                                current_gpu["vram_mb"] = int(val * 1024)
                            else:
                                current_gpu["vram_mb"] = int(val)
                        except ValueError:
                            pass
            if current_gpu.get("name"):
                gpu_list.append(current_gpu)
    except Exception:
        pass

    ram = psutil.virtual_memory()
    disk = psutil.disk_usage(autocut.WORKDIR)

    profile["scanned"] = True
    profile["cpu"] = cpu_name
    profile["cores_physical"] = psutil.cpu_count(logical=False) or 0
    profile["cores_logical"] = psutil.cpu_count(logical=True) or 0
    profile["total_ram_gb"] = round(ram.total / (1024**3), 1)
    typing.cast(typing.Any, profile)["gpu_list"] = gpu_list
    profile["disk_free_gb"] = round(disk.free / (1024**3), 1)
    profile["os_info"] = platform.system() + " " + platform.release()

    autocut.CONFIG_HARD['hardware_profile'] = typing.cast(typing.Any, profile)
    autocut.save_json_atomic(autocut.CONFIG_HARD_PATH, autocut.CONFIG_HARD)
    return profile

def translate_to_ui(msg):
    """[Industrial Telemetry Engine] Translates raw logs into highly professional and unambiguous technical progress messages."""
    # Progress Patterns
    if "[DeepSeek] Progress:" in msg:
        match = re.search(r"\[DeepSeek\] Progress: (\d+)%", msg)
        if match: return f"[AI Selector] 正在调度 DeepSeek 筛选语义切片并优化剪辑流... 进度 {match.group(1)}%"
    if "Progress:" in msg and "%" in msg:
        match = re.search(r"Progress: (\d+)%", msg)
        if match: return f"[Render Engine] 正在拉起 FFmpeg 物理拼接 Shard 序列，并基于 ASS 轨道执行软字幕硬烧录... 进度 {match.group(1)}%"
        
    # Status Patterns (Whitelisted & Translated)
    if "Initializing Global Cold Start" in msg: return "[SYSTEM] 初始化全局冷启动：正在清理孤立的物理锁以恢复环境..."
    if "正在提取音频轨道" in msg: return "[DISK_IO] 正在从视频中提取 PCM 16kHz mono 临时音频轨道..."
    if "正在初始化 AI 模型" in msg: return "[ASR Engine] 正在加载 AI 语义分析引擎与 Faster-Whisper 模型权重..."
    if "正在进行语音识别" in msg: return "[ASR Engine] 正在解析语音内容并构建切片索引，请稍候..."
    if "TRANSCRIPTION_SUCCESS" in msg:
        return msg.replace("TRANSCRIPTION_SUCCESS: ", "[ASR Engine] ")
    if "TRANSCRIPTION_DONE" in msg: return "[ASR Engine] 语音语义识别任务已完成"
    if "Visual assets materialized" in msg: return "[DISK_IO] 视觉预览资源及智能锚点已就绪"
    if "Generating" in msg and "visual anchors" in msg: return "[DISK_IO] 正在生成视频智能锚点预览图..."
    if "Slicing" in msg or "Processing slice" in msg: return "[Render Engine] 正在执行分片物理分割与对齐..."
    if "Stitching" in msg or "Finalizing output" in msg: return "[Render Engine] 正在拼合最终视频成品并烧录 ASS 轨道..."
    if "SUCCESS" in msg or "DONE" in msg: return msg # Pass specific success signals
    
    # Filter out technical noise: subtitles, ffmpeg details, internal logic paths
    return None

def audit_log(msg, level="INFO", broadcast=True, channel='log', is_progress=False, write_physical=True):
    """
    [Triple-Tier Logging Engine]
    Tier 1: debug.txt (Backend Raw)
    Tier 2: debug_web.txt (Web API / Interaction Raw)
    Tier 3: UI Translation (User-Friendly Filtered)
    """
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted = f"[{timestamp}] {msg}\n"
    
    if write_physical:
        # 1. Physical Backend Log (Everything)
        try:
            with _LOG_WRITE_LOCK:
                with open(DEBUG_FILE, 'a', encoding='utf-8') as f:
                    f.write(formatted)
        except: pass
        
        # 2. Web/Business Log (Strategic Info)
        is_web = (channel == 'web' or "API_HIT" in msg or "接收指令" in msg or "License" in msg)
        if is_web:
            try:
                with _LOG_WRITE_LOCK:
                    with open(WEB_DEBUG_FILE, 'a', encoding='utf-8') as f:
                        f.write(formatted)
            except: pass

    # 3. UI Translation Layer (Broadcast)
    try:
        if broadcast and 'socketio' in globals():
            ui_msg = translate_to_ui(msg)
            if ui_msg:
                socketio.emit(channel, {'msg': ui_msg, 'level': level, 'is_progress': is_progress})
            elif level in ["ERROR", "SUCCESS", "WARNING"]: # Critical alerts always pass
                socketio.emit(channel, {'msg': msg, 'level': level, 'is_progress': is_progress})
    except Exception as e:
        sys.stderr.write(f"Log Broadcast Error: {e}\n")

def _ai_log(msg, level="INFO", is_progress=False):
    """Bridge for AI-specific logs to the unified logging engine."""
    audit_log(msg, level=level, channel='log_ai', is_progress=is_progress)

# [Pure ID-Driven] get_project_input_file is now provided by autocut engine core.

# --- Execute Global Reset once auditing is ready ---
_global_industrial_reset()

@app.before_request
def log_request_info():
    """Industrial Audit: Trace significant interactions"""
    # Quiet high-frequency background sync tasks
    IGNORED_LOG_POSTS = ['/api/queue_action']
    
    if request.path in API_TRANSLATIONS and request.path not in IGNORED_LOG_POSTS:
        func_name = API_TRANSLATIONS[request.path]
        audit_log(f"▶ 接收指令 | 准备启动：{func_name} ...", level="INFO")
    elif request.path.startswith('/api/') and request.path != '/api/log_debug':
        audit_log(f"API_HIT: {request.method} {request.path} | Remote: {request.remote_addr}", level="DEBUG_SILENT", broadcast=False)

@app.route('/')
def index():
    return render_template('index.html', version=VERSION)

@app.route('/api/files')
def get_files():
    """扫描项目目录并整合输入输出数据"""
    projects = []
    if os.path.exists(autocut.WORKDIR):
        # 按数字自然排序获取文件夹
        try:
            p_folders = sorted([d for d in os.listdir(autocut.WORKDIR) if d.isdigit()], key=int, reverse=True)
            for pid in p_folders:
                p_path = os.path.join(autocut.WORKDIR, pid)
                in_dir = os.path.join(p_path, "in")
                out_dir = os.path.join(p_path, "out")
                
                def scan(d):
                    ext = ('.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv')
                    if not os.path.exists(d): return []
                    files = [f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f)) and f.lower().endswith(ext)]
                    # [Industrial Spec] Sort by modification time (Newest First)
                    files.sort(key=lambda x: os.path.getmtime(os.path.join(d, x)), reverse=True)
                    return files

                inputs = scan(in_dir)
                outputs = scan(out_dir)
                
                # [Cold Start Audit] Check if a task is currently pinned to this project
                lock_path = os.path.join(p_path, "temp", ".lock")
                is_busy = False
                task_type = None
                if os.path.exists(lock_path):
                    is_busy = True
                    try:
                        with open(lock_path, 'r', encoding='utf-8') as lf:
                            task_type = lf.read().strip()
                    except: pass
                
                is_exported = False
                
                if inputs or outputs:
                    projects.append({
                        "id": pid,
                        "inputs": inputs,
                        "outputs": outputs,
                        "is_busy": is_busy,
                        "task_type": task_type,
                        "is_exported": is_exported
                    })
        except Exception as e:
            sys.stderr.write(f"Projects Scan Error: {e}\n")
            audit_log(f"Projects Scan Error: {e}", "ERROR")
    
    return jsonify({
        "projects": projects
    })

# ==========================================
# 🛑 授权校验通道
# ==========================================
@app.route('/api/license_status', methods=['GET'])
def get_license_status():
    """Device handshake status endpoint"""
    is_ok, msg, state, is_online = license_manager.verify_license()
    # Industrial Audit: License telemetry is now silent
    return jsonify({
        "authorized": is_ok,
        "message": msg,
        "state": state,
        "online": is_online,
        "hwid": license_manager.get_hwid()
    })

@app.route('/api/license_status_offline', methods=['GET'])
def get_license_status_offline():
    """Device offline certificate check - no network request, reads local cache only"""
    is_ok, msg, state = license_manager.verify_license_offline()
    # [Audit] 完整信息写入 debug.txt，不广播到 UI
    audit_log(f"[License Offline] state={state} ok={is_ok}", level="DEBUG_SILENT", broadcast=False)
    return jsonify({
        "authorized": is_ok,
        "message": msg,
        "state": state,
        "hwid": license_manager.get_hwid()
    })

@app.route('/api/license_activate', methods=['POST'])
def handle_license_activate():
    """Environment key verification endpoint"""
    data = request.json
    key = data.get('cd_key', '').strip()
    if not key: return jsonify({"status": "error", "message": "Module Key Required"}), 400
    
    is_ok, msg, state, is_online = license_manager.verify_license(key)
    # Logging silenced for commercial confidentiality
    return jsonify({
        "status": "ok" if is_ok else "error",
        "message": msg,
        "state": state,
        "online": is_online
    })


@app.route('/video/<folder>/<filename>')
def serve_video(folder, filename):
    """供浏览器预览视频流，仅支持项目路径 proj_ID_in/out"""
    if folder.startswith('proj_'):
        parts = folder.split('_')
        if len(parts) < 3: return "Invalid Path Structure", 400
        
        pid, typ = parts[1], parts[2]
        # [Industrial Security] Validate ID and whitelist folder types
        if not pid.isdigit() or typ not in ['in', 'out']:
            audit_log(f"SECURITY_ALERT: Unauthorized video access attempt on {folder}", level="ERROR")
            return "Forbidden Path Pattern", 403
            
        base = os.path.join(autocut.WORKDIR, pid, typ)
        # Flask send_from_directory inherently prevents traversal, but we add base existence check
        if not os.path.exists(base): return "Project Segment Not Found", 404
        
        return send_from_directory(base, filename)
    return "Forbidden Legacy Path", 403

@app.route('/video_temp/<path:filename>')
def serve_video_temp(filename):
    """仅支持新项目 temp 结构"""
    # filename 格式应为 "1/temp/seg.mp4"
    parts = filename.split('/')
    if len(parts) >= 3 and parts[0].isdigit() and parts[1] == 'temp':
        video_path = os.path.normpath(os.path.join(autocut.WORKDIR, filename))
        # [Industrial Security] Ensure the resolved path is still within WORKDIR
        if not video_path.startswith(os.path.normpath(autocut.WORKDIR)):
            return "Traversal Attempt Blocked", 403
            
        if os.path.exists(video_path):
            mime, _ = mimetypes.guess_type(video_path)
            return send_file(video_path, mimetype=mime or 'video/mp4')
    return "Not found or Legacy", 404



@app.route('/static/js/<path:filename>')
def serve_static_js(filename):
    """离线 WASM 引擎 / transformers.js 静态资源 (支持内建回退)"""
    # 1. 优先检查外部目录 (允许工业用户热替换/热修复引擎脚本)
    ext_path = os.path.join(autocut.BASE_DIR, 'static', 'js')
    if os.path.exists(os.path.join(ext_path, filename)):
        return send_from_directory(ext_path, filename)
    
    # 2. 回退至 EXE 内部打包的资源目录
    int_path = autocut.get_resource_path('static/js')
    return send_from_directory(int_path, filename)

@app.route('/api/thumb_image')
def serve_thumb_image():
    """Industrial Spec: Unified high-speed thumbnail service with Pre-bake support"""
    path = request.args.get('path')
    if not path: return "Missing Path", 400
    
    # Resolution priority:
    # 1. Pre-baked .jpg in workdir
    # 2. On-the-fly fetch from source video (Fallback)
    
    # Path is typically "PID/temp/thumbs/raw_seg_N.jpg"
    full_path = os.path.normpath(os.path.join(autocut.WORKDIR, path))
    
    if os.path.exists(full_path):
        mimetype = 'image/jpeg' if full_path.lower().endswith('.jpg') else 'video/mp4'
        return send_file(full_path, mimetype=mimetype)
        
    # Fallback/Legacy logic removed in v9.0.0 (High Precision Refactor)
    return "Not Found", 404

@app.route('/api/thumbnails')
def get_thumbnails():
    """Industrial Precision: Support both physical slices and high-speed virtual fragment previews"""
    project_id = request.args.get('project_id')
    filename = request.args.get('filename') # For virtual link generation

    if not project_id: return jsonify({"slices": []})
    
    workspace = os.path.join(autocut.WORKDIR, project_id, "temp")
    slices = []
    saved_queue = []

    if os.path.exists(workspace):
        index_path = os.path.join(workspace, "semantic_index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    slices = json.load(f)
            except Exception as e:
                audit_log(f"Metadata Parse Error (Project {project_id}): {e}", "ERROR")

        # Load queue (Full Objects)
        synthesis_queue_path = os.path.join(workspace, "synthesis_queue.json")
        if os.path.exists(synthesis_queue_path):
            try:
                # Return pure ID list - synthesis_queue.json is an ordered index, not data storage
                with open(synthesis_queue_path, 'r', encoding='utf-8') as f:
                    saved_queue = json.load(f)
            except Exception as e:
                audit_log(f"Queue State Parse Error (Project {project_id}): {e}", "ERROR")
            
    return jsonify({"slices": slices, "queue": saved_queue})

@app.route('/api/queue_action', methods=['POST'])
def queue_action():
    """Data Sovereignty: Action-Reducer State Machine for Editing Queue"""
    data = request.json or {}
    project_id = data.get('project_id')
    action = data.get('action')
    payload = data.get('payload', {})
    
    if not project_id or not action:
        return jsonify({"error": "Missing project_id or action"}), 400
        
    workspace = os.path.join(autocut.WORKDIR, project_id, "temp")
    os.makedirs(workspace, exist_ok=True)
    queue_path = os.path.join(workspace, "synthesis_queue.json")
    
    with _QUEUE_LOCK:
        try:
            with open(queue_path, 'r', encoding='utf-8') as f:
                queue = json.load(f)
        except: 
            queue = []
            
        asset_id = payload.get('asset_id')
        
        if action == 'push':
            if asset_id and asset_id not in queue:
                queue.append(asset_id)
        elif action == 'insert':
            target_idx = payload.get('target_idx', len(queue))
            if asset_id and asset_id not in queue:
                queue.insert(target_idx, asset_id)
        elif action == 'remove':
            if asset_id in queue:
                queue.remove(asset_id)
        elif action == 'move':
            target_idx = payload.get('target_idx')
            if asset_id in queue and target_idx is not None:
                queue.remove(asset_id)
                queue.insert(target_idx, asset_id)
        elif action == 'clear':
            queue = []
            
        autocut.save_json_atomic(queue_path, queue)
        socketio.emit('queue_sync', {'project_id': project_id, 'queue': queue})
        
    return jsonify({"status": "success", "action": action, "queue_len": len(queue)})


@app.route('/api/batch_replace_subtitles', methods=['POST'])
def batch_replace_subtitles():
    """Industrial Clean: Atomic global find-and-replace for project subtitles."""
    data = request.json or {}
    project_id = data.get('project_id')
    find_text = data.get('find_text')
    replace_text = data.get('replace_text', '')
    
    if not project_id or find_text is None:
        return jsonify({"error": "Missing params"}), 400
        
    workspace = os.path.join(autocut.WORKDIR, project_id, "temp")
    if not os.path.exists(workspace):
        return jsonify({"error": "Project workspace not found"}), 404
        
    with _QUEUE_LOCK:
        # 1. Update semantic_index.json (Primary UI Source)
        index_path = os.path.join(workspace, "semantic_index.json")
        count = 0
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    slices = json.load(f)
                
                for s in slices:
                    if 'text' in s and find_text in s['text']:
                        s['text'] = s['text'].replace(find_text, replace_text)
                        count += 1
                
                autocut.save_json_atomic(index_path, slices)
            except Exception as e:
                return jsonify({"error": f"Failed to update index: {str(e)}"}), 500
                
        # 2. Update asr_raw.json (Raw Data Backup)
        asr_path = os.path.join(workspace, "asr_raw.json")
        if os.path.exists(asr_path):
            try:
                with open(asr_path, 'r', encoding='utf-8') as f:
                    asr_data = json.load(f)
                
                for s in asr_data:
                    if 'text' in s and find_text in s['text']:
                        s['text'] = s['text'].replace(find_text, replace_text)
                
                autocut.save_json_atomic(asr_path, asr_data)
            except: pass # Non-critical if asr_raw fails
        
    audit_log(f"SUBTITLE_EDIT: Global replace '{find_text}' -> '{replace_text}' in project {project_id} ({count} hits)", level="SUCCESS")
    return jsonify({"status": "success", "hits": count})


@socketio.on('start_ai')
def start_ai_task(data):
    """
    [Backend ASR Intelligence] - Unified ID-Driven Socket Handler
    Triggers the 100% backend transcription + segmentation pipeline.
    """
    pid = data.get('project_id')
    if not pid: return
    
    # 清理旧的输出文件，防止前端状态机误判已合成
    out_dir = os.path.join(autocut.WORKDIR, pid, "out")
    if os.path.exists(out_dir):
        try:
            for f in os.listdir(out_dir):
                fp = os.path.join(out_dir, f)
                if os.path.isfile(fp):
                    os.remove(fp)
        except: pass
        

    
    # [Concurrency Shield] 物理防重叠
    if _ACTIVE_TASK:
        audit_log(f"[Warning] Rejected concurrent ASR request for project {pid}. System is BUSY.", level="WARNING")
        return

    # Auto-detect source file from project's in/ directory
    ip = autocut.get_project_input_file(pid)
    if not ip:
        audit_log(f"项目 {pid} 未找到输入视频", level="ERROR")
        return
    filename = os.path.basename(ip)

    # Pull params from SSOT (config_hard.json)
    c = autocut.CONFIG_HARD.get('asr_processing', {})
    model_id = c.get('model_id', 'tiny')
    force_device = c.get('device', 'auto')
    initial_prompt = c.get('initial_prompt', '')

    def run_analysis():
        with _ACTIVE_TASK_LOCK:
            _ACTIVE_TASK.update({'task': 'AI_SCAN', 'project_id': pid, 'ffmpeg_pids': set()})
        socketio.emit('sys_state', {'task': 'AI_SCAN', 'status': 'running', 'project_id': pid})
        lock_path = os.path.join(autocut.WORKDIR, pid, "temp", ".lock")
        try:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            with open(lock_path, 'w', encoding='utf-8') as lf:
                lf.write('AI_SCAN')
        except: pass
        try:
            audit_log(f"ASR_START: Initiating Local AI Analysis for {filename}", level="INFO", channel='log_ai')
            
            def _sk_log(msg, level="INFO", is_progress=False):
                if _TASK_STOP_EVENT.is_set(): return
                color = "#2CD758" if level == "SUCCESS" else ("#FF453A" if level == "ERROR" else "#8fb2ff")
                audit_log(msg, level=level, is_progress=is_progress, broadcast=False)
                socketio.emit('log', {'msg': msg, 'color': color, 'is_progress': is_progress})
                
            def _sk_log_ai(msg, level="INFO", is_progress=False):
                if _TASK_STOP_EVENT.is_set(): return
                color = "#32D74B" if level == "SUCCESS" else ("#BF5AF2" if level == "ERROR" else "#8fb2ff")
                
                # [结构化适配器]
                payload = { "msg": msg, "level": level, "type": "STATUS" }

                # 识别 CONTENT 类型 (字幕识别结果)
                import re
                quote_match = re.search(r'"(.*?)"', msg)
                if quote_match:
                    payload["type"] = "CONTENT"
                    payload["content"] = quote_match.group(1)
                
                # 识别 METRIC 类型
                elif "Inferencing" in msg or is_progress:
                    payload["type"] = "METRIC"
                    p_match = re.search(r'(\d+)%', msg)
                    s_match = re.search(r'([\d.]+)x', msg)
                    e_match = re.search(r'ETA:\s*([\d.s]+)', msg)
                    payload["metric"] = {
                        "pct": p_match.group(1) if p_match else "0",
                        "speed": s_match.group(1) if s_match else "-",
                        "eta": e_match.group(1) if e_match else "-"
                    }

                socketio.emit('log_ai', payload)

            # --- PHASE 1: Consolidated AI Analysis (Audio -> ASR -> Visual Anchors) ---
            audit_log(f"▶ 正在初始化 AI 分析引擎 [{str(model_id).upper()}]...", level="INFO")
            _sk_log_ai(f"▶ 正在初始化 AI 分析引擎 [{str(model_id).upper()}]...", level="INFO")
            
            script_path = os.path.join(autocut.BASE_DIR, "acut_ai_scan.py")
            cmd = [
                sys.executable, script_path, 
                "--project_id", pid, 
                "--ip", ip,
                "--model_id", str(model_id),
                "--force_device", str(force_device),
                "--initial_prompt", str(initial_prompt)
            ]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1
            )
            
            if process.stdout is not None:
                for line in iter(process.stdout.readline, ''):
                    if not line: break
                    line = line.strip()
                    if line:
                        # 物理日志全量写入 (无条件且仅物理写入，打上 [ASR_WORKER] 前缀)
                        audit_log(f"[ASR_WORKER] {line}", level="INFO", broadcast=False, write_physical=True)

                        # 拦截新式强类型结构化 JSON 进度事件，并流式广播到前端
                        if line.startswith("JSON_EVENT: "):
                            try:
                                evt_data = json.loads(line[len("JSON_EVENT: "):])
                                socketio.emit('log_ai', {
                                    'type': 'STRUCT_EVENT',
                                    'event': evt_data.get('event'),
                                    'pct': evt_data.get('pct')
                                })
                            except Exception as e:
                                audit_log(f"[JSON Decode Err] {str(e)}: {line}", level="ERROR", write_physical=False)
                            continue

                        level = "SUCCESS" if "SUCCESS" in line else ("ERROR" if "ERROR" in line or "CRITICAL ERROR" in line or "ASR_FATAL" in line else "INFO")
                        
                        # [Dual-AI Telemetry Sync] 提取并剥离进度特征前缀，防止大小写敏感失效
                        is_progress = False
                        clean_line = line
                        if line.startswith("PROGRESS: "):
                            clean_line = line[len("PROGRESS: "):]
                            is_progress = True
                        elif "Progress:" in line or "Visual Progress:" in line or "progress" in line.lower():
                            is_progress = True
                            
                        # 双通道高能联动：主日志翻译过滤 + ASR 专用面板流式实时输出
                        audit_log(clean_line, level=level, is_progress=is_progress, write_physical=False)
                        _sk_log_ai(clean_line, level=level, is_progress=is_progress)
            
            process.wait()
            if process.returncode != 0:
                raise RuntimeError(f"AI Analysis process failed with error code {process.returncode}")
                
            audit_log(f"Execution Success: AI Scanning and Analysis complete.", level="SUCCESS")
            socketio.emit('refresh_lists')
            socketio.emit('sys_state', {'task': 'AI_SCAN', 'status': 'completed', 'project_id': pid})
            socketio.emit('done', {'msg': "AI 分析与语义建模完成"})
            
        except Exception as e:
            audit_log(f"ASR_FATAL: {str(e)}", level="ERROR")
            socketio.emit('sys_state', {'task': 'AI_SCAN', 'status': 'failed', 'project_id': pid})
            socketio.emit('done', {'msg': f"AI 分析失败: {str(e)}", "status": "error"})
        finally:
            try:
                if os.path.exists(lock_path):
                    os.remove(lock_path)
            except: pass
            with _ACTIVE_TASK_LOCK:
                _ACTIVE_TASK.clear()

    _TASK_STOP_EVENT.clear()
    threading.Thread(target=run_analysis, daemon=True).start()

@socketio.on('start_ai_select')
def handle_ai_select(data):
    """[Pure ID-Driven] Socket trigger for DeepSeek analysis."""
    pid = data.get('project_id')
    if not pid: return
    
    # 清理旧的输出文件，防止前端状态机误判已合成
    out_dir = os.path.join(autocut.WORKDIR, pid, "out")
    if os.path.exists(out_dir):
        try:
            for f in os.listdir(out_dir):
                fp = os.path.join(out_dir, f)
                if os.path.isfile(fp):
                    os.remove(fp)
        except: pass

    # DS代理模式支持：移除对本地 api_key 的强制非空检测
    # 鉴权与配额由代理服务器基于 HWID 进行管理

    socketio.emit('sys_state', {'task': 'AI_SELECT', 'status': 'running', 'project_id': pid})
    
    def run_select():
        lock_path = os.path.join(autocut.WORKDIR, pid, "temp", ".lock")
        try:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            with open(lock_path, 'w', encoding='utf-8') as lf:
                lf.write('AI_SELECT')
        except: pass
        try:
            script_path = os.path.join(autocut.BASE_DIR, "acut_ai_select.py")
            # [Pure ID-Driven] Remove --ip from worker call. Worker will self-discover path from ID.
            cmd = [sys.executable, script_path, "--project_id", pid]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1
            )
            
            if process.stdout is not None:
                for line in iter(process.stdout.readline, ''):
                    if not line: break
                    line = line.strip()
                    if line:
                        # 物理日志全量写入 (带 [AI_SELECT] 前缀)
                        audit_log(f"[AI_SELECT] {line}", level="INFO", broadcast=False, write_physical=True)
                        # 仅做前端 UI 广播
                        audit_log(line, is_progress=True, write_physical=False)
                    
            process.wait()
            if process.returncode != 0:
                audit_log(f"DeepSeek 筛选异常退出 (Code {process.returncode})", level="ERROR")
                socketio.emit('sys_state', {'task': 'AI_SELECT', 'status': 'failed', 'project_id': pid})
            else:
                socketio.emit('refresh_lists')
                queue_path = os.path.join(autocut.WORKDIR, pid, "temp", "synthesis_queue.json")
                if os.path.exists(queue_path):
                    with open(queue_path, 'r', encoding='utf-8') as f:
                        q = json.load(f)
                    socketio.emit('queue_sync', {'project_id': pid, 'queue': q})
                socketio.emit('sys_state', {'task': 'AI_SELECT', 'status': 'completed', 'project_id': pid})
                
        except Exception as e:
            audit_log(f"启动 DeepSeek 失败: {str(e)}", level="ERROR")
            socketio.emit('sys_state', {'task': 'AI_SELECT', 'status': 'failed', 'project_id': pid})
        finally:
            try:
                if os.path.exists(lock_path):
                    os.remove(lock_path)
            except: pass

    threading.Thread(target=run_select, daemon=True).start()

@app.route('/api/stop_task', methods=['POST'])
def stop_task():
    """Industrial Emergency Stop: Signals the registered task to halt and kills only its own FFmpeg PIDs."""
    data = request.json or {}
    requested_project_id = data.get('project_id')

    with _ACTIVE_TASK_LOCK:
        active = _ACTIVE_TASK.copy()

    # Guard: only stop if the request targets the active task's project
    if requested_project_id and active.get('project_id') != requested_project_id:
        return jsonify({"status": "no_match", "msg": "No active task for this project"}), 200

    _TASK_STOP_EVENT.set()
    task_type = active.get('task', 'UNKNOWN')
    project_id = active.get('project_id', 'unknown')
    audit_log(f"[SYSTEM] Emergency stop for {task_type} / project={project_id}", level="WARNING", channel='log_ai')
    
    # [Targeted Kill] Only terminate FFmpeg processes registered by THIS task
    ffmpeg_pids = active.get('ffmpeg_pids', set())
    if ffmpeg_pids:
        try:
            import psutil
            for pid in list(ffmpeg_pids):
                try:
                    proc = psutil.Process(pid)
                    proc.terminate()
                    audit_log(f"[SYSTEM] Terminated registered FFmpeg PID={pid}", level="WARNING")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            audit_log(f"[SYSTEM] FFmpeg kill error: {e}", level="WARNING")
    else:
        audit_log("[SYSTEM] No registered FFmpeg PIDs to terminate.", level="INFO")

    # Emit transition state immediately so frontend enters 'stopping' mode (not optimistic unlock)
    socketio.emit('sys_state', {'task': task_type, 'status': 'stopping', 'project_id': project_id})
    return jsonify({"status": "stopping", "task": task_type, "project_id": project_id})

@app.route('/api/models', methods=['GET'])
def get_model_status():
    """Audit asset inventory for the AI Engine"""
    from acut_asr import asr_service
    installed = asr_service.get_installed_models()
    return jsonify({
        "installed": installed,
        "current": asr_service.current_model_id or "None",
        "supported": ["tiny", "small", "large-v3-turbo"]
    })

@app.route('/api/unload_ai', methods=['POST'])
def unload_ai():
    """Unload active model from memory and report footprint delta."""
    def get_mem():
        # RAM
        import psutil, subprocess
        ram_mb = int(psutil.virtual_memory().used / (1024**2))
        try:
            cmd = ['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits']
            res = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
            if res.returncode == 0 and res.stdout.strip():
                return ram_mb, int(res.stdout.strip().split('\n')[0])
        except: pass
        return ram_mb, 0
        
    ram_before, vram_before = get_mem()
    
    from acut_asr import asr_service
    success, msg = asr_service.unload_model()
    
    import time; time.sleep(0.5) # Allow GC and OS to register the flush
    ram_after, vram_after = get_mem()
    
    delta = {
        "ram_freed_mb": max(0, ram_before - ram_after),
        "vram_freed_mb": max(0, vram_before - vram_after)
    }
    
    return jsonify({
        "status": "ok" if success else "empty", 
        "msg": msg,
        "delta": delta
    })

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Industrial Spec: Sequential project injection with secure naming and real-time auditing"""
    if 'files' not in request.files: return "No files", 400
    files = request.files.getlist('files')
    
    # Industrial Constraint: Limit concurrent injection to 3 files to prevent pipeline congestion
    MAX_IMPORT = 3
    if len(files) > MAX_IMPORT:
        audit_log(f"LIMIT_TRIGGER: Batch size {len(files)} exceeds limit. Truncating to {MAX_IMPORT}.", level="WARNING")
        files = files[:MAX_IMPORT]

    os.makedirs(autocut.WORKDIR, exist_ok=True)
    existing_ids = [int(d) for d in os.listdir(autocut.WORKDIR) if d.isdigit()]
    next_id = max(existing_ids + [0]) + 1
    
    total = len(files)
    safe_name = ""
    for i, f in enumerate(files):
        if not f.filename: continue
        
        # 1. 工业级文件名脱敏 (Secure Filename)
        safe_name = re.sub(r'[\\/:*?"<>| ]', '_', f.filename)
        
        p_path = os.path.join(autocut.WORKDIR, str(next_id))
        in_dir = os.path.join(p_path, "in")
        temp_dir = os.path.join(p_path, "temp")
        out_dir = os.path.join(p_path, "out")
        
        # 2. 目录树一次性建立
        for d in [in_dir, temp_dir, out_dir, os.path.join(temp_dir, "thumbs"), os.path.join(temp_dir, "slices")]:
            os.makedirs(d, exist_ok=True)
        
        target_path = os.path.join(in_dir, safe_name)
        f.save(target_path)
        threading.Thread(target=acut_backup.report_import, args=(target_path,), daemon=True).start()
        
        # 3. 实时进度反馈
        socketio.emit('log', {
            'msg': f"[DISK_IO] 素材物理导入完毕: {safe_name} -> Project_{next_id} (音轨规格: 16000Hz, Mono, 16bit PCM)",
            'color': '#BF5AF2' # 采用紫色强调导入流
        })
        
        next_id += 1

    audit_log(f"Execution Success: {total} external assets injected into pipeline.", level="SUCCESS")
    return jsonify({
        "status": "ok",
        "project_id": str(next_id - 1),
        "filename": safe_name
    })


@app.route('/api/export_out_dir', methods=['POST'])
def export_out_dir():
    """读取预设路径并执行秒级物理备份"""
    project_id = request.json.get('project_id')
    if not project_id: return jsonify({"error": "Missing project_id"}), 400
    
    # 1. 获取预设导出路径
    dest_base = autocut.CONFIG.get('export_base_path', '')
    if isinstance(dest_base, str): dest_base = dest_base.strip()
    else: dest_base = ""
    if not dest_base:
        return jsonify({"error": "Config Required: Please set global export path first."}), 400
        
    # 2. Location of source and final destination
    src_dir = os.path.join(autocut.WORKDIR, project_id, "out")
    if not os.path.exists(src_dir):
        return jsonify({"error": "Resource Error: No rendered files found to export."}), 404
        
    final_dest = os.path.join(dest_base, f"Project_{project_id}")
    
    try:
        os.makedirs(final_dest, exist_ok=True)
        files = [f for f in os.listdir(src_dir) if os.path.isfile(os.path.join(src_dir, f))]
        if not files:
            return jsonify({"error": "Handshake Error: Source directory is empty."}), 404
            
        count = 0
        for f in files:
            shutil.copy2(os.path.join(src_dir, f), os.path.join(final_dest, f))
            count += 1
            
        # 3. Industrial Sync: Open system explorer (macOS native open)
        subprocess.run(["open", final_dest], check=False)
        
        audit_log(f"Execution Success: {count} assets physical synced to {final_dest}", level="SUCCESS")
        return jsonify({"status": "ok", "dest": final_dest, "count": count})
    except Exception as e:
        audit_log(f"EXPORT_FAILED: {str(e)}", "ERROR")
        return jsonify({"error": f"Export operation failed. Check permissions: {str(e)}"}), 500

@app.route('/api/export_subtitles', methods=['POST'])
def export_subtitles():
    """将当前项目的 asr_raw.json 字幕文件导出到全局导出目录的项目子目录"""
    project_id = request.json.get('project_id')
    if not project_id:
        return jsonify({"error": "Missing project_id"}), 400

    # 1. 校验 asr_raw.json 是否存在
    src_file = os.path.join(autocut.WORKDIR, project_id, "temp", "asr_raw.json")
    if not os.path.exists(src_file):
        return jsonify({"error": "字幕文件不存在，请先执行 AI 分析。"}), 404

    # 2. 读取导出根目录（与视频导出共用同一配置项）
    dest_base = autocut.CONFIG.get('export_base_path', '')
    if isinstance(dest_base, str): dest_base = dest_base.strip()
    else: dest_base = ""
    if not dest_base:
        return jsonify({"error": "Config Required: 请先在配置中设置全局导出路径。"}), 400

    # 3. 目标目录：{export_base_path}/Project_{project_id}/
    final_dest = os.path.join(dest_base, f"Project_{project_id}")
    try:
        os.makedirs(final_dest, exist_ok=True)
        dest_file = os.path.join(final_dest, "asr_raw.json")
        shutil.copy2(src_file, dest_file)

        # 4. 打开资源管理器定位到目标目录 (macOS native open)
        subprocess.run(["open", final_dest], check=False)

        audit_log(f"SUBTITLE_EXPORT: asr_raw.json -> {dest_file}", level="SUCCESS")
        return jsonify({"status": "ok", "dest": dest_file})
    except Exception as e:
        audit_log(f"SUBTITLE_EXPORT_FAILED: {str(e)}", "ERROR")
        return jsonify({"error": f"导出失败：{str(e)}"}), 500

@app.route('/api/clear_temp', methods=['POST'])
def clear_temp():
    """Perform physical cleanup of project temp directory"""
    project_id = request.json.get('project_id')
    if not project_id: return "Missing project_id", 400
    
    # Security audit: Verify project ID validity
    valid_projects = [p['id'] for p in autocut.list_projects()]
    if project_id not in valid_projects:
        socketio.emit('log', {'msg': f"[Security] Audit Denied: Unauthorized directory access attempt for ID {project_id}.", 'color': 'var(--danger)'})
        return "Unauthorized", 403
    
    workspace = os.path.join(autocut.WORKDIR, project_id, "temp")
    if os.path.exists(workspace):
        # Physical deletion of entire directory tree
        shutil.rmtree(workspace)
        os.makedirs(workspace, exist_ok=True)
        audit_log(f"DISK_IO: Shard purge successful for project {project_id}", level="SUCCESS")
        return jsonify({"status": "ok"})
    return "Not found", 404

@app.route('/api/delete_project', methods=['POST'])
def delete_project():
    """Perform physical deletion of entire project directory"""
    project_id = request.json.get('project_id')
    if not project_id: return jsonify({"error": "Missing project_id"}), 400
    
    # Security audit: Verify project ID validity
    valid_projects = [p['id'] for p in autocut.list_projects()]
    if project_id not in valid_projects:
        socketio.emit('log', {'msg': f"[Security] Audit Denied: Unauthorized project delete attempt for ID {project_id}.", 'color': 'var(--danger)'})
        return "Unauthorized", 403
        
    project_path = os.path.join(autocut.WORKDIR, project_id)
    if os.path.exists(project_path):
        try:
            # Force garbage collection to release file handles before deletion (Windows optimization)
            gc.collect()
            time.sleep(0.1)
            shutil.rmtree(project_path)
            audit_log(f"DISK_IO: Structural delete successful for {project_id}", level="SUCCESS")
            return jsonify({"status": "ok"})
        except Exception as e:
            import traceback
            err_trace = traceback.format_exc()
            audit_log(f"Delete Project Failed: {str(e)}", "ERROR")
            audit_log(err_trace, "ERROR")
            return jsonify({"error": str(e)}), 500
            
    return "Not found", 404

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    """Access or persist system configuration (nested structure support)"""
    if request.method == 'GET':
        return jsonify(autocut.CONFIG)
    else:
        new_data = request.json
        c = autocut.CONFIG
        # Specs
        if 'fps' in new_data: c['output_specs']['fps'] = int(new_data['fps'])
        # Effects (Remaining defaults)
        if 'auto_select_limit_s' in new_data: 
            if 'ai_specs' not in c: c['ai_specs'] = {}
            c['ai_specs']['auto_select_limit_s'] = int(new_data['auto_select_limit_s'])

        # DeepSeek API
        if 'deepseek_api_key' in new_data:
            if 'ai_specs' not in c: c['ai_specs'] = {}
            c['ai_specs']['deepseek_api_key'] = new_data['deepseek_api_key'].strip()
        if 'deepseek_base_url' in new_data:
            if 'ai_specs' not in c: c['ai_specs'] = {}
            c['ai_specs']['deepseek_base_url'] = new_data['deepseek_base_url'].strip()
        if 'deepseek_model' in new_data:
            if 'ai_specs' not in c: c['ai_specs'] = {}
            c['ai_specs']['deepseek_model'] = new_data['deepseek_model'].strip()
        if 'deepseek_temperature' in new_data:
            if 'ai_specs' not in c: c['ai_specs'] = {}
            c['ai_specs']['deepseek_temperature'] = float(new_data['deepseek_temperature'])
        if 'deepseek_timeout' in new_data:
            if 'ai_specs' not in c: c['ai_specs'] = {}
            c['ai_specs']['deepseek_timeout'] = int(new_data['deepseek_timeout'])

        if 'high_score_theme' in new_data:
            if 'ai_specs' not in c: c['ai_specs'] = {}
            c['ai_specs']['high_score_theme'] = new_data['high_score_theme'].strip()
        if 'low_score_theme' in new_data:
            if 'ai_specs' not in c: c['ai_specs'] = {}
            c['ai_specs']['low_score_theme'] = new_data['low_score_theme'].strip()

        # Global export path audit
        if 'export_base_path' in new_data:
            path = new_data['export_base_path'].strip()
            c['export_base_path'] = path
            if path:
                try:
                    os.makedirs(path, exist_ok=True)
                    audit_log(f"DISK_IO: Global export path verified/created: {path}", level="DEBUG_SILENT", broadcast=False)
                except Exception as e:
                    audit_log(f"PATH_INVALID: Cannot create export path {path} - {str(e)}", "WARNING")

        # Synthesis Options
        if 'synthesis_options' in new_data:
            if 'synthesis_options' not in c: c['synthesis_options'] = {}
            c['synthesis_options'].update(new_data['synthesis_options'])

        autocut.save_config()
        audit_log(f"[DONE] Configuration updated successfully.", level="SUCCESS")
        return jsonify({"status": "ok"})

@app.route('/api/config_hard')
def get_config_hard():
    """Access strict technical constants"""
    return jsonify(autocut.CONFIG_HARD)


@socketio.on('start_batch')
def start_batch(data):
    """[Pure ID-Driven] Socket trigger for Video Synthesis."""
    pid = data.get('project_id')
    if not pid: return
    
    socketio.emit('sys_state', {'task': 'SYNTHESIS', 'status': 'running', 'project_id': pid})
    
    def run_synthesis():
        lock_path = os.path.join(autocut.WORKDIR, pid, "temp", ".lock")
        try:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            with open(lock_path, 'w', encoding='utf-8') as lf:
                lf.write('SYNTHESIS')
        except: pass
        try:
            script_path = os.path.join(autocut.BASE_DIR, "acut_synthesis.py")
            # [Pure ID-Driven] Remove --ip from worker call. Worker will self-discover path from ID.
            cmd = [sys.executable, script_path, "--project_id", pid]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1
            )
            
            if process.stdout is not None:
                for line in iter(process.stdout.readline, ''):
                    if not line: break
                    line = line.strip()
                    if line:
                        # 物理日志全量写入 (带 [SYNTHESIS] 前缀)
                        audit_log(f"[SYNTHESIS] {line}", level="INFO", broadcast=False, write_physical=True)
                        # 仅做前端 UI 广播
                        audit_log(line, is_progress=True, write_physical=False)
                    
            process.wait()
            if process.returncode != 0:
                audit_log(f"视频合成异常退出 (Code {process.returncode})", level="ERROR")
                socketio.emit('sys_state', {'task': 'SYNTHESIS', 'status': 'failed', 'project_id': pid})
            else:
                socketio.emit('sys_state', {'task': 'SYNTHESIS', 'status': 'completed', 'project_id': pid})
                
        except Exception as e:
            audit_log(f"启动合成失败: {str(e)}", level="ERROR")
            socketio.emit('sys_state', {'task': 'SYNTHESIS', 'status': 'failed', 'project_id': pid})
        finally:
            try:
                if os.path.exists(lock_path):
                    os.remove(lock_path)
            except: pass

    threading.Thread(target=run_synthesis, daemon=True).start()

def open_browser():
    """Ensure server is up before launching browser UI"""
    time.sleep(1.5)
    webbrowser.open_new("http://127.0.0.1:5010")

@app.route('/api/prep_audio', methods=['POST'])
def prep_audio():
    """Extract 16kHz mono WAV for front-end AI, minimizing memory overhead for large files"""
    data = request.json
    filename = data.get('filename')
    project_id = data.get('project_id')
    if not filename or not project_id: return jsonify({"error": "Missing params"}), 400
    
    workspace = os.path.join(autocut.WORKDIR, project_id, "temp")
    ip = os.path.join(autocut.WORKDIR, project_id, "in", filename)
    wav_url = f"/video_temp/{project_id}/temp/semantic_audio.wav"

    os.makedirs(workspace, exist_ok=True)
    wav_path = os.path.join(workspace, "semantic_audio.wav")
    
    # Check if extraction already completed
    if not os.path.exists(wav_path):
        rate = "16000"
        ch = "1"
        
        socketio.emit('log', {
            'msg': f"[Audit] Audio Extraction: Generating {rate}Hz PCM stream (Channels: {ch}) for {filename}", 
            'color': '#8fb2ff'
        })
        
        ffmpeg_exe = autocut.FFMPEG_CMD or 'ffmpeg'
        # Force pcm_s16le for high compatibility with transformers.js
        cmd = [ffmpeg_exe, '-y', '-i', ip, '-vn', '-acodec', 'pcm_s16le', '-ar', rate, '-ac', ch, wav_path]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors="ignore")
        
        if res.returncode != 0:
            audit_log(f"FFMPEG PrepAudio Error: Exit {res.returncode}", level="ERROR", broadcast=False)
            socketio.emit('log', {'msg': f"FFMPEG PrepAudio Error: Exit {res.returncode}", 'color': 'var(--danger)'})
            return jsonify({"error": "FFMPEG extraction failed"}), 500
            
        audit_log(f"Execution Success: Audio preparation complete.", level="SUCCESS")
        
    if not os.path.exists(wav_path):
        return jsonify({"error": "WAV file was not created"}), 500
        
    return jsonify({"wav_url": wav_url})

@app.route('/api/exit', methods=['POST'])
def exit_app():
    """
    Industrial Graceful Shutdown: Flush logs and terminate SocketIO server.
    """
    audit_log("[CMD] Shutdown command received | Executing industrial-grade cleanup...", level="WARNING")
    
    def shutdown():
        time.sleep(1.0) # Give UI time to receive the acknowledge
        audit_log("[DONE] Backend service stopped. Pipeline unloaded. Process exiting.", level="SUCCESS", broadcast=False)
        # Execute graceful exit to allow Python resource destructors and OS hooks to run
        sys.exit(0)

    threading.Thread(target=shutdown).start()
    return jsonify({"status": "terminating", "message": "Backend shutting down..."})

@app.route('/api/system_audit')
def system_audit():
    """Industrial-grade hardware transparency: CPU, RAM, & DISCRETE GPU diagnostics"""
    # 1. Hardware Footprint
    profile = get_hardware_footprint()

    # 2. Dynamic Memory Topology
    ram = psutil.virtual_memory()

    return jsonify({
        "cpu": {
            "name": profile.get('cpu'),
            "cores_physical": profile.get('cores_physical'),
            "cores_logical": profile.get('cores_logical'),
        },
        "ram": {
            "total_gb": profile.get('total_ram_gb'),
            "available_gb": round(ram.available / (1024**3), 1)
        },
        "gpu_discrete": profile.get('gpu_list'),
        "disk_free_gb": profile.get('disk_free_gb'),
        "os": profile.get('os_info')
    })

@app.route('/api/optimize_config', methods=['POST'])
def optimize_config():
    """AI Auto-Tuner: Hardware-aware configuration optimization (Industrial Backend Focus)"""
    data = request.json or {}
    forced_device = data.get('force_device')
    model_id = data.get('model_id')
    skip_audit = data.get('skip_audit', False)
    force_deep_test = data.get('force_deep_test', False)

    # 1. Audit Phase
    profile = get_hardware_footprint()
    cpu_name = profile.get('cpu')
    gpu_list = profile.get('gpu_list', [])
    gpu_info = None
    if isinstance(gpu_list, list) and len(gpu_list) > 0:
        # 取列表最后一张（独显排在核显后面，优先级更高）
        primary_gpu = gpu_list[-1]
        if isinstance(primary_gpu, dict):
            gpu_info = {"name": primary_gpu.get('name', 'Unknown'), "vram": primary_gpu.get('vram_mb', 0)}

    # 2. Strategy Phase
    c = autocut.CONFIG_HARD['asr_processing']
    config_changed = False
    
    if model_id and c.get('model_id') != model_id:
        c['model_id'] = model_id
        config_changed = True
    
    # Auto-select best device based on CPU chip architecture (Intel -> CPU, M-series -> Metal GPU)
    if "intel" in str(cpu_name).lower():
        target_device = 'cpu'
    else:
        target_device = 'metal' if len(gpu_list) > 0 else 'cpu'
        
    if c.get('device') != target_device:
        c['device'] = target_device
        config_changed = True
        
    if c.get('device') == 'metal':
        strategy = f"BACKEND_ACCEL (Metal GPU | {gpu_info['name'] if gpu_info else 'Unknown'})"
    else:
        strategy = "BACKEND_OPTIMIZED (CPU MODE)"


    # 3. Deep Readiness Audit (Verify DLLs and Linkage)
    from acut_asr import asr_service
    if skip_audit:
        is_ready, audit_msg, missing = True, "Audit skipped during boot phase", []
    else:
        is_ready, audit_msg, missing = asr_service.verify_environment(
            device=c['device'],
            force_deep_test=force_deep_test
        )

    # 4. Persistence
    if config_changed:
        autocut.save_json_atomic(autocut.CONFIG_HARD_PATH, autocut.CONFIG_HARD)

    audit_log(f"Execution Success: System hot-switched to [{strategy}] architecture mode.", level="SUCCESS", channel='log_ai')
    if not is_ready:
        audit_log(f"READINESS_WARNING: {audit_msg}", level="WARNING", channel='log_ai')

    return jsonify({
        "strategy": strategy,
        "readiness": {
            "is_ready": is_ready,
            "message": audit_msg,
            "missing": missing
        },
        "hardware": {
            "cpu": cpu_name,
            "gpu": gpu_info['name'] if gpu_info else "None",
            "vram": gpu_info['vram'] if gpu_info else 0,
            "total_ram": f"{round(psutil.virtual_memory().total / (1024**3), 1)}GB"
        },
        "params": c
    })


@app.route('/api/config_hard_update', methods=['POST'])
def config_hard_update():
    """Industrial Endpoint: Modify and persist config_hard.json explicit parameters."""
    data = request.json or {}
    c = autocut.CONFIG_HARD
        
    autocut.save_json_atomic(autocut.CONFIG_HARD_PATH, c)
    audit_log("Execution Success: Core hardware configuration saved and solidified.", level="SUCCESS", channel='log_ai')
    return jsonify({"status": "ok", "msg": "底层极客配置已固化"})

@app.route('/api/log_debug', methods=['POST'])
def log_debug():
    """Centralized audit for frontend diagnostic telemetry"""
    data = request.json
    msg = data.get('msg', 'Unknown Error')
    level = data.get('level', 'FRONTEND_DEBUG')
    
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted = f"[{timestamp}] [{level}] {msg}\n"
    
    try:
        with open(WEB_DEBUG_FILE, 'a', encoding='utf-8') as f:
            f.write(formatted)
    except Exception as e:
        sys.stderr.write(f"Web Audit Log Failure: {e}\n")
        
    return jsonify({"status": "ok"})

@app.route('/api/download_models', methods=['POST'])
def download_models():
    """Industrial Asset Pull: Automates model acquisition with progress telemetry"""
    data = request.json or {}
    target = data.get('target', 'tiny')

    def run_download():
        try:
            import requests
            import time
            
            allowed_targets = ["tiny", "small", "large-v3-turbo"]
            if target not in allowed_targets:
                mapped_target = "tiny"
            else:
                mapped_target = target
            
            filename = f"ggml-{mapped_target}.bin"
            url = f"https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/{filename}"
            target_dir = os.path.join(autocut.BASE_DIR, "models", "whisper-cpp")
            os.makedirs(target_dir, exist_ok=True)
            target_path = os.path.join(target_dir, filename)
            
            audit_log(f"ASR_DOWNLOAD_START: Commencing industrial model pull ({filename}) from HF Mirror...", level="INFO", channel='log_ai')
            short_name = mapped_target.upper()
            socketio.emit('log_ai', {'msg': f"▶ 正在从国内镜像源请求 {short_name} 模型核心资产...", 'color': '#BF5AF2'})
            
            # Streaming download with progress emission
            with requests.get(url, stream=True, timeout=15) as r:
                r.raise_for_status()
                total_length = r.headers.get('content-length')
                
                with open(target_path, 'wb') as f:
                    if total_length is None:
                        f.write(r.content)
                        socketio.emit('log_ai', {'msg': f"下载进度: 100% (无法计算总大小)", 'color': '#0A84FF', 'type': 'METRIC', 'metric': {'pct': 100}})
                    else:
                        dl = 0
                        total_length = int(total_length)
                        last_emit_time = 0
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                dl += len(chunk)
                                f.write(chunk)
                                now = time.time()
                                if now - last_emit_time > 1.0:
                                    pct = int(100 * dl / total_length)
                                    mb_dl = dl / (1024 * 1024)
                                    mb_tot = total_length / (1024 * 1024)
                                    socketio.emit('log_ai', {
                                        'msg': f"下载进度: {pct}% ({mb_dl:.1f}MB / {mb_tot:.1f}MB)",
                                        'color': '#0A84FF',
                                        'type': 'METRIC',
                                        'metric': {'pct': pct}
                                    })
                                    last_emit_time = now

            socketio.emit('log_ai', {'msg': f"✔ {short_name} 模型下载且校验成功。", 'color': '#30D158'})
            audit_log(f"Execution Success: AI model ({mapped_target}) synchronization complete.", level="SUCCESS", channel='log_ai')
            socketio.emit('log_ai', {'msg': f"--- {short_name} 模型下载任务完成 ---", 'color': '#30D158'})
            socketio.emit('refresh_lists')
        except Exception as e:
            audit_log(f"DOWNLOAD_FAILED: {str(e)}", "ERROR", channel='log_ai')
            socketio.emit('log_ai', {'msg': f"✖ 资产同步中止：{str(e)}", 'color': 'var(--danger)'})

    threading.Thread(target=run_download, daemon=True).start()
    return jsonify({"status": "ok"})

def perform_system_audit():
    """Concise Environmental Audit (v9.5.1 Backend Focus)"""
    # 1. Engine & Assets
    f_path = autocut.get_ffmpeg_config()
    cpp_model_dir = os.path.join(autocut.BASE_DIR, "models", "whisper-cpp")
    has_model = os.path.exists(cpp_model_dir) and len(os.listdir(cpp_model_dir)) > 0
    
    engine_meta = f"FFMPEG: {f_path} | AI_MODELS: {'LOADED' if has_model else 'PENDING_DOWNLOAD'}"
    
    # 2. Connectivity & Storage
    is_online = False
    try:
        import socket
        # Probe authorization server on port 443 for availability
        socket.create_connection(("abc.nelsoncode.com", 443), timeout=1)
        is_online = True
    except: pass
    net_meta = f"NET: {'ONLINE' if is_online else 'OFFLINE (Air-Gapped)'} | WORKDIR: {'OK' if os.access(autocut.WORKDIR, os.W_OK) else 'DENIED'}"
    
    print(f"[Audit] {engine_meta}")
    print(f"[Audit] {net_meta}")
    return is_online
def _free_port(port=5010):
    """System-wide Port Enforcer: Frees the port if occupied by terminating the offending process."""
    try:
        import psutil
        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr.port == port and conn.status == 'LISTEN':
                pid = conn.pid
                if pid and pid != os.getpid():
                    print(f"[Port Enforcer] Port {port} is occupied by PID {pid}. Terminating process...")
                    try:
                        p = psutil.Process(pid)
                        p.terminate()
                        p.wait(timeout=3)
                        print(f"[Port Enforcer] Success: Terminated conflicting PID {pid}.")
                    except Exception as ex:
                        print(f"[Port Enforcer] Failed to terminate PID {pid}: {ex}")
    except Exception:
        try:
            res = subprocess.run(['lsof', '-t', '-i', f':{port}'], capture_output=True, text=True, errors='ignore')
            if res.returncode == 0 and res.stdout.strip():
                pids = [int(p) for p in res.stdout.strip().split('\n') if p.strip().isdigit()]
                my_pid = os.getpid()
                for pid in pids:
                    if pid != my_pid:
                        print(f"[Port Enforcer] Port {port} is occupied by PID {pid}. Terminating process via lsof...")
                        try:
                            p = psutil.Process(pid)
                            p.terminate()
                            p.wait(timeout=3)
                            print(f"[Port Enforcer] Success: Terminated conflicting PID {pid}.")
                        except Exception as ex:
                            print(f"[Port Enforcer] Failed to terminate PID {pid}: {ex}")
        except Exception as ex:
            print(f"[Port Enforcer] Error freeing port {port}: {ex}")

if __name__ == '__main__':
    # --- 1. PORT ENFORCER ---
    _free_port(5010)

    # --- 2. SINGLE INSTANCE ENFORCER (Force Takeover) ---
    current_pid = os.getpid()
    conflict_pid = None
    
    # Check for existing .lock file
    lock_path = os.path.join(autocut.INTERNAL_DIR, ".lock_server")
    if os.path.exists(lock_path):
        try:
            with open(lock_path, 'r') as f:
                old_pid = int(f.read().strip())
                if psutil.pid_exists(old_pid):
                    p = psutil.Process(old_pid)
                    # Verify it's actually our app (simple name check)
                    if "python" in p.name().lower() or "autocut" in p.name().lower():
                        conflict_pid = old_pid
        except: pass

    if conflict_pid:
        print(f"[Enforcer] Detected stale AutoCut process (PID: {conflict_pid}). Terminating silently to ensure pure environment...")
        try:
            p = psutil.Process(conflict_pid)
            p.terminate()
            p.wait(timeout=3)
            print(f"[Enforcer] Success: Terminated conflicting instance.")
        except Exception as e:
            print(f"[Enforcer] Error: Failed to kill old process: {e}")

    # Update Lock
    try:
        with open(lock_path, 'w') as f:
            f.write(str(current_pid))
    except: pass

    # --- 3. HARDWARE TUNING & READINESS PROBE (Backend Initialization) ---
    print("[Boot] Initializing backend configuration...")
    _boot_device = None

    # 实际硬件检测：以实际物理检测为准，强制重新扫描以防历史残留缓存干扰
    profile = get_hardware_footprint(force=True)
    gpu_list = profile.get('gpu_list', [])

    # [macOS Metal GPU 原生加速判断]
    if gpu_list:
        primary = gpu_list[-1]  # 取列表最后一张（独显优先于核显）
        print(f"[Boot] macOS Metal GPU detected: {primary['name']} ({primary['vram_mb']} MB VRAM). Enabling Metal acceleration.")
        _boot_device = 'metal'
    else:
        print("[Boot] No GPU detected on macOS. Using CPU mode.")
        _boot_device = 'cpu'

    with app.test_request_context(json={'skip_audit': True, 'force_device': _boot_device}):
        optimize_config()
    print("[Boot] Hardware configuration initialized.")

    print("\n=======================================================")
    print(f"   MATRIX AI ENGINE SERVER v{VERSION} IS ONLINE")
    
    # --- 4. SYSTEM LAUNCH ---
    is_online = perform_system_audit()
    
    try:
        os.makedirs(autocut.WORKDIR, exist_ok=True)

        print("\n[INFO] Backend is fully operational. Auto-launching browser...")
        
        def _silent_open():
            time.sleep(1.5) 
            webbrowser.open("http://127.0.0.1:5010")
        threading.Thread(target=_silent_open, daemon=True).start()

        # Final Boot
        # [Frozen Guard] Suppress Click's show_server_banner which crashes in frozen/windowed envs
        # due to Windows console handle issues (OSError: [Errno 22] Invalid argument)
        try:
            import flask.cli
            flask.cli.show_server_banner = lambda *args, **kwargs: None
        except Exception:
            pass
        socketio.run(app, host='127.0.0.1', port=5010, debug=False, allow_unsafe_werkzeug=True)

    except Exception as _startup_err:
        _write_startup_error(_startup_err)
        print(f"\n[FATAL] Startup failed: {_startup_err}")
        input("Press Enter to exit...")
