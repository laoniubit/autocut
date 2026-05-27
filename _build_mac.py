"""
AutoCut Matrix AI - Mac Build Script
Target: macOS (Apple Silicon arm64 / Intel x86_64)

Usage:
  # Build for current machine architecture:
  python _build_mac.py

  # Build specifically for Apple Silicon (M-chip):
  python _build_mac.py --arch arm64

  # Build for Intel Mac:
  python _build_mac.py --arch x86_64

  # Build universal binary (runs on both, ~2x size):
  python _build_mac.py --arch universal2

Note:
  - PyInstaller can ONLY build for the CURRENT machine's architecture.
  - To build arm64 on an Intel Mac, use GitHub Actions (macos-14 runner).
  - See .github/workflows/build_mac_arm64.yml for CI/CD automation.
"""
import PyInstaller.__main__
import os
import sys
import argparse
import shutil
from datetime import datetime

# ---- Argument Parsing ----
parser = argparse.ArgumentParser(description="AutoCut Mac Build Script")
parser.add_argument(
    "--arch",
    choices=["arm64", "x86_64", "universal2"],
    default=None,
    help="Target architecture. Defaults to current machine arch."
)
args = parser.parse_args()

# ---- Base Config ----
base = os.path.dirname(os.path.abspath(__file__))
date_short = datetime.now().strftime("%Y%m%d")
date_full = datetime.now().strftime("%Y%m%d_%H%M%S")
target_arch = args.arch  # None = PyInstaller auto-detect

import platform as _platform
current_arch = _platform.machine()  # arm64 or x86_64
print(f"[Build Engine] Current machine arch : {current_arch}")
print(f"[Build Engine] Target arch          : {target_arch or 'auto (' + current_arch + ')'}")

arch_label = target_arch or current_arch
exe_name = f"AutoCut_Mac_{date_short}_{arch_label}"
dir_name = f"AutoCut_Mac_{date_full}_{arch_label}"

# ---- Check Required Mac Binaries ----
mac_internal = os.path.join(base, "_internal")
ffmpeg_mac = os.path.join(mac_internal, "ffmpeg")
whisper_mac = os.path.join(mac_internal, "whisper-cli")
vad_onnx = os.path.join(base, "_internal", "silero_vad_v6.onnx")
tflite_model = os.path.join(base, "_internal", "face_detection_full_range_sparse.tflite")

errors = []
if not os.path.exists(ffmpeg_mac):
    errors.append(
        f"[!] ERROR: Mac ffmpeg binary not found at: {ffmpeg_mac}\n"
        f"    Run: bash setup_mac_deps.sh  OR  brew install ffmpeg && cp $(which ffmpeg) {ffmpeg_mac}"
    )
if not os.path.exists(whisper_mac):
    errors.append(
        f"[!] ERROR: Mac whisper-cli not found at: {whisper_mac}\n"
        f"    Run: bash setup_mac_deps.sh  to compile whisper.cpp for Mac."
    )
if not os.path.exists(vad_onnx):
    errors.append(f"[!] ERROR: silero_vad_v6.onnx not found at: {vad_onnx}")
if not os.path.exists(tflite_model):
    errors.append(f"[!] ERROR: TFLite face model not found at: {tflite_model}")

if errors:
    for e in errors:
        print(e)
    print("\n[Build Engine] Setup incomplete. Run 'bash setup_mac_deps.sh' first.")
    sys.exit(1)

# Ensure executable permissions on Mac binaries
for bin_path in [ffmpeg_mac, whisper_mac]:
    if os.path.exists(bin_path):
        os.chmod(bin_path, 0o755)

print(f"[Build Engine] Mac binaries verified OK.")
print(f"[Build Engine] Target: dist/{dir_name}")

# ---- Generate Spec Content ----
arch_arg = f"'{target_arch}'" if target_arch else "None"

spec_content = f"""# -*- mode: python ; coding: utf-8 -*-
# AutoCut Matrix AI - Mac Build Spec (Generated)
from PyInstaller.utils.hooks import collect_all

datas = [
    ('_internal/templates', 'templates'),
    ('_internal/static', 'static'),
    ('_internal/silero_vad_v6.onnx', '.'),
    ('_internal/face_detection_full_range_sparse.tflite', '.'),
]
binaries = [
    ('_internal/ffmpeg', '.'),
    ('_internal/whisper-cli', '.'),
]

hiddenimports = ['acut_ai_scan', 'acut_ai_select', 'acut_synthesis', 'acut_deepseek', 'acut_zoom']

# Package core Python dependencies
for pkg in ['cryptography', 'flask_socketio', 'engineio', 'socketio', 'psutil']:
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

a = Analysis(
    ['acut_server.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
        # Exclude Windows-only heavy packages
        'torch', 'transformers', 'ctranslate2',
        'tensorflow', 'keras',
        'nvidia', 'cupy',
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [('O', None, 'OPTION')],
    exclude_binaries=True,
    name='{exe_name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,           # Mac: strip debug symbols to reduce size
    upx=False,            # UPX not recommended on Mac
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,  # Disable for CLI tools
    target_arch={arch_arg},
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=False,
    upx_exclude=[],
    name='{dir_name}',
)
"""

spec_path = os.path.join(base, "AutoCut_Mac_temp.spec")
with open(spec_path, "w", encoding="utf-8") as f:
    f.write(spec_content)

print(f"[Build Engine] Dynamic Spec generated: {spec_path}")

try:
    PyInstaller.__main__.run([
        spec_path,
        '--clean',
        '--noconfirm',
        '--log-level=WARN',
    ])
finally:
    if os.path.exists(spec_path):
        try:
            os.remove(spec_path)
            print("[Build Engine] Temp spec cleaned up.")
        except Exception as e:
            print(f"[Build Warning] Failed to delete temp spec: {e}")

# ---- Post-Build: Copy models directory ----
dist_dir = os.path.join(base, "dist", dir_name)
if os.path.exists(dist_dir):
    dest_models_dir = os.path.join(dist_dir, "models", "whisper-cpp")
    os.makedirs(dest_models_dir, exist_ok=True)

    src_guide = os.path.join(base, "models", "DEPLOY_GUIDE.md")
    if os.path.exists(src_guide):
        shutil.copy2(src_guide, os.path.join(dist_dir, "models", "DEPLOY_GUIDE.md"))
        print("[Build Engine] Copied DEPLOY_GUIDE.md")

    src_model = os.path.join(base, "models", "whisper-cpp", "ggml-tiny.bin")
    if os.path.exists(src_model):
        shutil.copy2(src_model, os.path.join(dest_models_dir, "ggml-tiny.bin"))
        print("[Build Engine] Copied ggml-tiny.bin")

    # Ensure Mac binaries in dist have execute permissions
    for binary in ["ffmpeg", "whisper-cli"]:
        bin_in_dist = os.path.join(dist_dir, binary)
        if os.path.exists(bin_in_dist):
            os.chmod(bin_in_dist, 0o755)
            print(f"[Build Engine] Set +x on {binary}")

    print(f"\n[SUCCESS] Mac build completed: dist/{dir_name}/")
    print(f"  Executable : dist/{dir_name}/{exe_name}")
    print(f"  Target arch: {arch_label}")
else:
    print("\n[ERROR] Build output directory not found. Check PyInstaller logs above.")
