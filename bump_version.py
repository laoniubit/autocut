#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoCut Matrix AI - Version Auto-Increment Utility
Reads, increments, and updates the version key in the tracked 'VERSION' file,
and synchronizes it to 'acut_engine.py' and optional '_internal_/config_hard.json'.
"""
import os
import sys
import re
import json

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    version_path = os.path.join(base_dir, "VERSION")
    
    # 1. Read current version from tracked VERSION file
    if not os.path.exists(version_path):
        # Fallback if somehow deleted
        current_version = "12.92"
    else:
        try:
            with open(version_path, "r", encoding="utf-8") as f:
                current_version = f.read().strip()
        except Exception as e:
            print(f"[Error] Failed to read VERSION file: {e}", file=sys.stderr)
            sys.exit(1)
            
    if not current_version:
        current_version = "12.92"
        
    # 2. Parse version parts and increment the last integer segment
    parts = current_version.split('.')
    if parts:
        try:
            parts[-1] = str(int(parts[-1]) + 1)
        except ValueError:
            parts.append('1')
    else:
        parts = ['12', '92']
        
    new_version = '.'.join(parts)
    
    # 3. Save back to VERSION file atomically
    temp_version_path = version_path + ".tmp"
    try:
        with open(temp_version_path, 'w', encoding='utf-8') as f:
            f.write(new_version + "\n")
        if os.path.exists(version_path):
            os.remove(version_path)
        os.rename(temp_version_path, version_path)
        print(f"[Build Version Controller] VERSION file updated: {current_version} -> {new_version}")
    except Exception as e:
        if os.path.exists(temp_version_path):
            try: os.remove(temp_version_path)
            except: pass
        print(f"[Error] Failed to write updated VERSION: {e}", file=sys.stderr)
        sys.exit(1)

    # 4. Synchronize version to acut_engine.py (default config version)
    engine_path = os.path.join(base_dir, "acut_engine.py")
    if os.path.exists(engine_path):
        try:
            with open(engine_path, "r", encoding="utf-8") as f:
                content = f.read()
            new_content = re.sub(r'"version":\s*"[^"]*"', f'"version": "{new_version}"', content)
            with open(engine_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            print(f"[Build Version Controller] Synced version to acut_engine.py")
        except Exception as e:
            print(f"[Warning] Failed to sync version to acut_engine.py: {e}", file=sys.stderr)

    # 5. Optionally update '_internal_/config_hard.json' if it exists (does not fail on clean clone)
    config_path = os.path.join(base_dir, "_internal_", "config_hard.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["version"] = new_version
            
            temp_config_path = config_path + ".tmp"
            with open(temp_config_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            if os.path.exists(config_path):
                os.remove(config_path)
            os.rename(temp_config_path, config_path)
            print(f"[Build Version Controller] Synced version to config_hard.json")
        except Exception as e:
            print(f"[Warning] Failed to sync version to config_hard.json: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
