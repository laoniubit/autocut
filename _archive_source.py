import os
import json
import zipfile
from datetime import datetime

def archive():
    # 1. Get Version from config_hard.json
    version = "unknown"
    try:
        config_hard_path = os.path.join("_internal_", "config_hard.json")
        with open(config_hard_path, "r", encoding="utf-8") as f:
            version = json.load(f).get("version", "unknown")
    except Exception as e:
        print(f"Warning: Could not read version: {e}")

    # 2. Setup Archive naming
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"SNAPSHOT_v{version}_{timestamp}.zip"
    backups_dir = "backups"
    if not os.path.exists(backups_dir):
        os.makedirs(backups_dir)
    
    archive_path = os.path.join(backups_dir, archive_name)

    # 3. Define Exclusions
    exclude_dirs = {
        'venv', 
        '__pycache__', 
        'backups', 
        'build', 
        'dist', 
        'workdir', 
        'models', 
        '.git', 
        '.idea', 
        '.vscode',
        '.gemini'
    }
    exclude_files = {
        'ffmpeg.exe',
        archive_name
    }

    # 4. Create ZIP
    print(f"[*] Starting backup: {archive_name}")
    try:
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk('.'):
                # Modify dirs in-place to exclude unwanted directories
                dirs[:] = [d for d in dirs if d not in exclude_dirs]
                
                for file in files:
                    if file in exclude_files:
                        continue
                    
                    file_path = os.path.join(root, file)
                    # Get arcname (relative path)
                    arcname = os.path.relpath(file_path, '.')
                    print(f"  + Adding: {arcname}")
                    zipf.write(file_path, arcname)
        
        print(f"\n[SUCCESS] Backup completed successfully.")
        print(f"Location: {os.path.abspath(archive_path)}")
        
        # Log to a textual log file
        with open("backups/history.log", "a", encoding="utf-8") as log:
            log.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Created {archive_name}\n")
            
    except Exception as e:
        print(f"[ERROR] Backup failed: {e}")

if __name__ == "__main__":
    archive()
