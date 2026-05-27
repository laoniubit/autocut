import os
import sys

# Align frozen environment argv structure with development environment
if sys.argv and not sys.argv[0].endswith('.py'):
    if len(sys.argv) > 1 and sys.argv[1].endswith('.py'):
        sys.argv[0] = sys.argv[1]
        del sys.argv[1]

import json
import acut_engine as autocut
from acut_deepseek import deepseek_elite_selector

# Enforce UTF-8 for piped output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def _log(msg, **kwargs):
    """Real-time log bridge to parent process."""
    if 'pct' in kwargs:
        print(f"{msg} [DeepSeek] Progress: {kwargs['pct']}%", flush=True)
    else:
        print(msg, flush=True)

import argparse

def run_ai_select_pipeline():
    """CLI-based Pipeline Entry Point for DeepSeek Selection"""
    parser = argparse.ArgumentParser(description="AutoCut Matrix AI Selection Worker")
    parser.add_argument("--project_id", required=True, help="Target project ID")
    parser.add_argument("--high_score_theme", default="", help="High score keyword guidance")
    parser.add_argument("--low_score_theme", default="", help="Low score keyword guidance")
    
    args = parser.parse_args()
    project_id = args.project_id
    
    # [Pure ID-Driven] Self-discover input file path from ID
    ip = autocut.get_project_input_file(project_id)
    if not ip:
        _log(f"ERROR: No input video found for project {project_id}.")
        sys.exit(1)
    
    # [ID-Core Unification] Fallback to global CONFIG if themes are not provided via CLI
    high_score_theme = args.high_score_theme
    low_score_theme = args.low_score_theme
    
    if not high_score_theme:
        high_score_theme = autocut.CONFIG.get('ai_specs', {}).get('high_score_theme', '')
    if not low_score_theme:
        low_score_theme = autocut.CONFIG.get('ai_specs', {}).get('low_score_theme', '')
    
    if not project_id:
        _log("ERROR: Missing project_id.")
        sys.exit(1)

    workspace = os.path.join(autocut.WORKDIR, project_id, "temp")
    index_path = os.path.join(workspace, "semantic_index.json")
    queue_path = os.path.join(workspace, "synthesis_queue.json")

    if not os.path.exists(index_path):
        _log(f"ERROR: No semantic data found for project {project_id}.")
        sys.exit(1)

    try:
        with open(index_path, 'r', encoding='utf-8') as f:
            segments = json.load(f)
    except Exception as e:
        _log(f"ERROR: Failed to read semantic index: {e}")
        sys.exit(1)

    _log(f"▶ [AI Selector] Starting DeepSeek semantic analysis for Project {project_id}...")
    
    try:
        ai_specs = autocut.CONFIG.get('ai_specs', {})
        target_duration = int(ai_specs.get('auto_select_limit_s', 90))
        
        selected_ids = deepseek_elite_selector(
            segments, 
            target_duration=target_duration, 
            high_score_theme=high_score_theme, 
            low_score_theme=low_score_theme,
            config=ai_specs,
            project_id=project_id,
            workdir=autocut.WORKDIR,
            log_fn=_log
        )

        with open(queue_path, 'w', encoding='utf-8') as f:
            json.dump(selected_ids, f, ensure_ascii=False)
            
        _log(f"SUCCESS: AI Selection complete. {len(selected_ids)} segments selected.")
            
    except Exception as e:
        _log(f"CRITICAL ERROR during AI selection: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    run_ai_select_pipeline()
