import os
import sys

# Align frozen environment argv structure with development environment
if sys.argv and not sys.argv[0].endswith('.py'):
    if len(sys.argv) > 1 and sys.argv[1].endswith('.py'):
        sys.argv[0] = sys.argv[1]
        del sys.argv[1]

import json, time, re, shutil, subprocess, threading, cv2, psutil, acut_zoom

# macOS Only Constant
_CREATE_NO_WINDOW = 0

# Suppress AI Engine C++ Logs
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['GLOG_minloglevel'] = '2'

# Enforce UTF-8 for piped output to prevent Windows console encoding crashes
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


# ----------------------------
# 1. 环境与配置 (Industrial Environment)
# ----------------------------
def get_path(rel): 
    base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
    return os.path.abspath(os.path.join(base, rel))

def log_backend(msg):
    print(f"[BACKEND_DEBUG] {msg}", flush=True)

def log_web(msg):
    print(f"[FRONTEND_DEBUG] {msg}", flush=True)

BASE_DIR = get_path(".")
INTERNAL = get_path("_internal_")
WORKDIR = get_path("workdir")

def _find_ffmpeg() -> str:
    """macOS FFmpeg 二进制路径自适应。"""
    candidates = [
        os.path.join(get_path("_internal"), "ffmpeg"),
        os.path.join(BASE_DIR, "ffmpeg"),
        shutil.which("ffmpeg") or "",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return os.path.abspath(p)
    return ""

FFMPEG = _find_ffmpeg()

def load_json(path):
    if not os.path.exists(path): return {}
    with open(path, 'r', encoding='utf-8') as f: return json.load(f)

def to_ffmpeg_path(p):
    """[Industrial Standard] Converts to forward-slash relative path to avoid Windows drive colon issues in filters."""
    try:
        rel = os.path.relpath(p, BASE_DIR)
        return rel.replace('\\', '/')
    except:
        return p.replace('\\', '/').replace(':', '\\:')

def get_physical_duration(path):
    """Physically measure video duration using static FFMPEG binary to ensure portability."""
    try:
        # Using FFMPEG -i as it's a guaranteed static 223MB binary in our project
        cmd = [FFMPEG, "-i", path]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        # Extract Duration: 00:00:05.00 from stderr
        match = re.search(r"Duration:\s+(\d+):(\d+):(\d+\.\d+)", res.stderr)
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except: pass
    return 0

# ----------------------------
# 2. 工业级守卫 (Industrial Guards)
# ----------------------------
def force_release_directory_handles(target_path):
    pass

def background_purge(path):
    """Background thread for heavy IO cleanup."""
    try:
        force_release_directory_handles(path)
        shutil.rmtree(path)
    except: pass

# ----------------------------
# 3. 核心算法 (Verified v9.5 Algorithms)
# ----------------------------

def generate_ass_file(segments, v_temp, tw, th):
    """Generates a high-quality ASS subtitle script."""
    ass_path = os.path.join(v_temp, "subtitles.ass")
    cum = 0.0
    events = []
    
    def format_ts(s):
        return f"{int(s//3600)}:{int((s%3600)//60):02d}:{int(s%60):02d}.{int((s-int(s))*100):02d}"

    for s in segments:
        dur = s.get('physical_dur', s['end'] - s['start'])
        txt = s.get('text', '').strip()
        if not txt or "~~~" in txt:
            cum += dur
            continue
            
        # [Smart Splitting] Visual Length Chunking (Chinese=1.0, ASCII=0.5), Max 12.0
        max_l = 12.0
        def v_weight(c): return 0.5 if ord(c) < 128 else 1.0
        v_len = sum(v_weight(c) for c in txt)
        
        if v_len <= max_l:
            events.append(f"Dialogue: 0,{format_ts(cum)},{format_ts(cum+dur)},Default,,0,0,0,,{txt}\n")
        else:
            import math
            n = int(math.ceil(v_len / max_l))
            target_vlen = v_len / n
            
            sub_cum = cum
            curr = 0
            for i in range(n):
                if i == n - 1:
                    sub_txt = txt[curr:]
                else:
                    acc = 0.0
                    split_idx = curr
                    while split_idx < len(txt) and acc < target_vlen:
                        acc += v_weight(txt[split_idx])
                        split_idx += 1
                    sub_txt = txt[curr:split_idx]
                    curr = split_idx
                
                sub_vlen = sum(v_weight(c) for c in sub_txt)
                sub_dur = dur * (sub_vlen / v_len)
                events.append(f"Dialogue: 0,{format_ts(sub_cum)},{format_ts(sub_cum+sub_dur)},Default,,0,0,0,,{sub_txt}\n")
                sub_cum += sub_dur
        
        cum += dur
    
    if not events: return None
    
    margin_v = int(th * 0.25) # Raised position (25% from bottom)
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: {tw}\nPlayResY: {th}\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Microsoft YaHei,80,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,30,30,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    ).format(tw=tw, th=th)
    
    with open(ass_path, 'w', encoding='utf-8-sig') as f:
        f.write(header)
        f.writelines(events)
    return ass_path

def check_videotoolbox_functional(ffmpeg_path) -> bool:
    """Mac VideoToolbox 硬件加速探针。"""
    try:
        res = subprocess.run([
            ffmpeg_path, "-y", "-f", "lavfi", "-i", "color=c=black:s=640x480:d=0.1",
            "-c:v", "h264_videotoolbox", "-f", "null", "-"
        ], capture_output=True)
        return res.returncode == 0
    except Exception:
        return False

def select_encoder(ffmpeg_path: str) -> tuple:
    """自动选择最佳编码器和预设。
    优先级: VideoToolbox (Mac) > libx264 (CPU 兼容)
    Returns: (codec_name, preset_str)
    """
    if check_videotoolbox_functional(ffmpeg_path):
        return "h264_videotoolbox", ""  # VideoToolbox 无 -preset
    return "libx264", "medium"

def solidify_physical_slices(ip, segments, slices_dir, v_temp, cfg, z_cfg, focus_crop=False):
    """Stage 3: Renders segments with optional Sticky Face Tracking."""
    spec = cfg['output_specs']
    tw, th, fps = spec['width'], spec['height'], spec['fps']

    # Encoder Selection
    v_codec, preset = select_encoder(FFMPEG)
    if v_codec == "libx264":
        preset = cfg.get('effects', {}).get('cpu_preset', 'medium')

    sticky_state = None # (scale, cx, cy)
    total = len(segments)
    
    # Pre-initialize AI Model (Optimization: Move out of loop to reduce warnings & overhead)
    norm = None
    if focus_crop:
        try: 
            norm = acut_zoom.FaceNormalizer(z_cfg)
            log_web("AI Focus Engine Initialized Successfully.")
        except Exception as e:
            log_web(f"AI Focus Engine Failed to Load: {str(e)}")

    try:
        for i, s in enumerate(segments):
            start, dur = s['start'], s['end'] - s['start']
            target = os.path.join(slices_dir, f"seg_{s['id']}.mp4")
            
            # Default Filter: Center Crop & Scale
            v_filter = f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},setsar=1,fps={fps},format=yuv420p"
            
            if focus_crop and norm:
                t_p = os.path.join(v_temp, f"focus_sample_{s['id']}.jpg")
                try:
                    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", f"{start + z_cfg.get('sample_offset_s', 0.1):.3f}", "-i", ip, "-vframes", "1", t_p], capture_output=True)
                    frame = cv2.imread(t_p)
                    if frame is not None:
                        h, w = frame.shape[:2]
                        valid, _ = acut_zoom.detect_faces(norm.detector, cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), z_cfg, (w, h))

                        min_s = max(tw/w, th/h)
                        scale = min_s * z_cfg.get("min_zoom", 1.01)
                        cx, cy = (w - (tw/scale)) // 2, (h - (th/scale)) // 2
                        
                        if valid:
                            best = max(valid, key=lambda x: x[0])
                            target_y = z_cfg.get("face_center_y", 0.25)
                            face_y_abs = best[2] * h
                            ch_needed = (h - face_y_abs) / (1.0 - target_y + 1e-6)
                            scale = max(th / (ch_needed + 1e-6), scale)
                            
                            cw_i, ch_i = int(tw/scale), int(th/scale)
                            cx = int(best[1] * w - cw_i * z_cfg.get("face_center_x", 0.5))
                            cy = int(best[2] * h - ch_i * z_cfg.get("face_center_y", 0.25))
                        
                        # Sticky Algorithm: Prevents jitter
                        if sticky_state:
                            ls, lcx, lcy = sticky_state
                            s_err = abs(scale - ls) / ls
                            p_err = (abs(cx - lcx) / w) + (abs(cy - lcy) / h)
                            if s_err < 0.2 and p_err < 0.2: scale, cx, cy = ls, lcx, lcy
                            else: sticky_state = (scale, cx, cy)
                        else: sticky_state = (scale, cx, cy)
                        
                        cw, ch = int(tw/scale), int(th/scale)
                        cx_f = max(0, min(w - cw, cx))
                        cy_f = h - ch # Locked to bottom relative to face y
                        
                        v_filter = f"crop={cw}:{ch}:{cx_f}:{cy_f},scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},setsar=1,fps={fps},format=yuv420p"
                        
                        if z_cfg.get("show_zoom_label", True):
                            multiplier = scale / (min_s + 1e-6)
                            # Avoid '|' character as it conflicts with FFmpeg filter syntax
                            txt = f"{total} - {i+1} - {multiplier:.2f}"
                            # Simple Relative Path Strategy
                            font_p = to_ffmpeg_path(os.path.join(INTERNAL, "fonts", "arial.ttf"))
                            v_filter += f",drawtext=text='{txt}':fontfile='{font_p}':fontcolor=white:fontsize={z_cfg.get('font_size',32)}:x=(w-text_w)/2:y=h*0.98-text_h:shadowcolor=black@0.5:shadowx=2:shadowy=2"
                except:
                    pass
                finally:
                    if os.path.exists(t_p):
                        try: os.remove(t_p)
                        except: pass
            
            cmd = [
                FFMPEG, "-y", "-ss", f"{start:.3f}", "-i", ip, "-t", f"{dur:.3f}",
                "-vf", v_filter, "-c:v", v_codec,
            ]
            # VideoToolbox 无 -preset / -crf 参数，用 -b:v 设定目标码率防止 qscale 报错
            if v_codec == "h264_videotoolbox":
                # 动态设定码率 (1080p 对应约 6000k, 720p 对应约 3000k)
                pixel_count = tw * th
                if pixel_count >= 3840 * 2160:
                    bitrate = "20000k"
                elif pixel_count >= 1920 * 1080:
                    bitrate = "6000k"
                elif pixel_count >= 1280 * 720:
                    bitrate = "3000k"
                else:
                    bitrate = "1500k"
                cmd += ["-b:v", bitrate]
            else:
                cmd += ["-preset", preset, "-crf", "23"]
            cmd += [
                "-c:a", "aac", "-ar", str(spec['audio_sample_rate']), "-ac", "2", "-b:a", spec['audio_bitrate'],
                "-avoid_negative_ts", "make_zero", target
            ]
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)
            if p.stdout is not None:
                for line in iter(p.stdout.readline, ''):
                    if not line: break
                    print(f"[FFMPEG_SLICE] {line.strip()}", flush=True)
            p.wait()
            
            # [Physical Alignment] Measure actual output duration
            if os.path.exists(target):
                s['physical_dur'] = get_physical_duration(target)
            
            if not os.path.exists(target):
                log_web(f"ERROR: Slice failed to render: {target}")
            else:
                # Progress is the ONLY signal intended for the Frontend Log (UI)
                print(f"[Render Engine] Progress: {int((i+1)/len(segments)*100)}%", flush=True)
    finally:
        # Explicitly release AI resources
        if norm:
            try: del norm
            except: pass


import argparse

def run_synthesis():
    # A. Argument Parsing
    parser = argparse.ArgumentParser(description="AutoCut Matrix Synthesis Worker")
    parser.add_argument("--project_id", required=True, help="Project ID")
    parser.add_argument("--ip", help="Input file path (Optional in ID-Driven mode)")
    parser.add_argument("--mode", default="complex", help="Synthesis mode")
    parser.add_argument("--selected_indices", help="Comma-separated indices")
    parser.add_argument("--subtitle", action="store_true", help="Include subtitles")
    parser.add_argument("--original_audio", action="store_true", help="Include original audio")
    parser.add_argument("--focus_crop", action="store_true", help="Enable AI focus crop")
    
    args = parser.parse_args()
    project_id = args.project_id
    
    # [Pure ID-Driven] Fallback to self-discovery if --ip is missing
    ip = args.ip or load_json(os.path.join(WORKDIR, project_id, "temp", "synthesis_config.json")).get('ip')
    if not ip:
        import acut_engine
        ip = acut_engine.get_project_input_file(project_id)
    
    if not ip:
        print(f"ERROR: Cannot locate input file for project {project_id}")
        sys.exit(1)

    mode = args.mode
    s_indices = [int(x) for x in args.selected_indices.split(',')] if args.selected_indices else None
    
    # B. Path Preparation
    p_dir = os.path.join(WORKDIR, project_id)
    v_temp, v_out = os.path.join(p_dir, "temp"), os.path.join(p_dir, "out")
    os.makedirs(v_out, exist_ok=True)
    
    # C. Configuration
    cfg = load_json(os.path.join(INTERNAL, "config.json"))
    z_cfg = load_json(os.path.join(INTERNAL, "config_zoom.json"))
    
    # D. Segment Parsing
    idx_p = os.path.join(v_temp, "semantic_index.json")
    all_segs = load_json(idx_p)
    
    # [ID-Core Unification] Resolve segments to render
    if not s_indices:
        queue_p = os.path.join(v_temp, "synthesis_queue.json")
        if os.path.exists(queue_p):
            try:
                s_indices = load_json(queue_p)
                log_backend(f"Resolved {len(s_indices)} segments from synthesis_queue.json")
            except: pass
    
    if s_indices:
        # Create a lookup dictionary for O(1) access
        seg_map = {s['id']: s for s in all_segs}
        # Build segments list strictly matching the order of s_indices
        segments = [seg_map[i] for i in s_indices if i in seg_map]
    else:
        # Default ASR behavior: render all discovered segments
        segments = all_segs
        log_backend("No selection found. Rendering all segments (ASR Fallback).")
    
    # E. Shadow Recycling (Stage 3 Shadow)
    slices_dir = os.path.join(v_temp, "slices")
    if os.path.exists(slices_dir):
        trash = slices_dir + f"_trash_{int(time.time())}"
        try: os.rename(slices_dir, trash)
        except: trash = None
        if trash: threading.Thread(target=background_purge, args=(trash,), daemon=True).start()
    os.makedirs(slices_dir, exist_ok=True)
    
    # F. Render Slices
    # [ID-Core Unification] Resolve options from global config
    s_opt = cfg.get('synthesis_options', {})
    do_subtitle = args.subtitle or s_opt.get('subtitle', False)
    do_focus = args.focus_crop or s_opt.get('focus_crop', False)
    do_audio = args.original_audio or s_opt.get('original_audio', False)

    solidify_physical_slices(ip, segments, slices_dir, v_temp, cfg, z_cfg, focus_crop=do_focus)
    
    # G. Final Assembly (Stage 4)
    list_p = os.path.join(v_temp, "list.txt")
    with open(list_p, 'w', encoding='utf-8') as f:
        for s in segments:
            # Use full physical slice to maintain synchronization
            slice_path = os.path.join(slices_dir, f"seg_{s['id']}.mp4").replace('\\', '/')
            f.write(f"file '{slice_path}'\n")
    
    out_filename = os.path.basename(ip)
    out_p = os.path.join(v_out, out_filename)
    ass_p = generate_ass_file(segments, v_temp, cfg['output_specs']['width'], cfg['output_specs']['height']) if do_subtitle else None
    
    cmd = [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_p]
    
    if ass_p:
        # Burning subtitles using relative paths to avoid colon issues
        safe_ass_p = to_ffmpeg_path(ass_p)
        fonts_dir = to_ffmpeg_path(os.path.join(INTERNAL, "fonts"))
        cmd += ["-vf", f"subtitles='{safe_ass_p}':fontsdir='{fonts_dir}':original_size={cfg['output_specs']['width']}x{cfg['output_specs']['height']}"]

        v_codec_final, _ = select_encoder(FFMPEG)
        cmd += ["-c:v", v_codec_final]
        if v_codec_final == "h264_videotoolbox":
            # 动态设定码率 (1080p 对应约 6000k, 720p 对应约 3000k)
            tw, th = cfg['output_specs']['width'], cfg['output_specs']['height']
            pixel_count = tw * th
            if pixel_count >= 3840 * 2160:
                bitrate = "20000k"
            elif pixel_count >= 1920 * 1080:
                bitrate = "6000k"
            elif pixel_count >= 1280 * 720:
                bitrate = "3000k"
            else:
                bitrate = "1500k"
            cmd += ["-b:v", bitrate]
        else:
            cmd += ["-preset", "medium"]
    else:
        cmd += ["-c:v", "copy"]
        
    if do_audio:
        cmd += ["-c:a", "copy" if not ass_p else "aac"]
    else:
        cmd += ["-an"]
        
    cmd.append(out_p)
    log_web(f"Starting Stage 4: Final Assembly for {len(segments)} segments.")
    print("Stitching output video...", flush=True)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', cwd=BASE_DIR)
    if p.stdout is not None:
        for line in iter(p.stdout.readline, ''):
            if not line: break
            print(f"[FFMPEG_STITCH] {line.strip()}", flush=True)
    p.wait()
    
    if os.path.exists(out_p): 
        # SUCCESS signal is intended for the Frontend Log (UI) to settle the task
        print(f"SUCCESS: Synthesis complete. Final file: {out_p}", flush=True)
        log_web(f"Synthesis settled successfully: {out_p}")
    else: 
        log_web("FAILURE: Output missing after Final Assembly.")
        print("FAILURE: Output missing", flush=True)

if __name__ == "__main__":
    run_synthesis()
