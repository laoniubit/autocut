#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoCut Matrix AI - Version Auto-Increment Utility
Reads, increments, and updates the version key in '_internal_/config_hard.json'.
"""
import json
import os
import sys

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "_internal_", "config_hard.json")
    
    if not os.path.exists(config_path):
        print(f"[Error] config_hard.json not found at: {config_path}", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[Error] Failed to read JSON file: {e}", file=sys.stderr)
        sys.exit(1)
        
    current_version = data.get("version", "1.0").strip()
    
    # Parse version parts and increment the last integer segment
    parts = current_version.split('.')
    if parts:
        try:
            parts[-1] = str(int(parts[-1]) + 1)
        except ValueError:
            parts.append('1')
    else:
        parts = ['1', '0', '1']
        
    new_version = '.'.join(parts)
    data["version"] = new_version
    
    # Save back to config_hard.json atomically
    temp_path = config_path + ".tmp"
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        if os.path.exists(config_path):
            os.remove(config_path)
        os.rename(temp_path, config_path)
        print(f"[Build Version Controller] Version bumped: {current_version} -> {new_version}")
    except Exception as e:
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except: pass
        print(f"[Error] Failed to write updated version: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
