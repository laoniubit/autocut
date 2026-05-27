import os
import json
import time

# Industrial Path Standard
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BENCH_FILE = os.path.join(BASE_DIR, "acut_benchmarks.json")

class ACUTBenchmarkCenter:
    """
    [AI Performance Auditor]
    Tracks and predicts inference speeds based on historical localized data.
    """
    def __init__(self):
        self.data = self._load()
        # Fallback Speed Factors (Real-Time Factor: duration / processing_time)
        # Note: These are safe, conservative defaults.
        self.default_factors = {
            "cuda_tiny": 40.0,
            "cuda_base": 30.0,
            "cuda_small": 15.0,
            "cuda_medium": 8.0,
            "cuda_large-v3": 4.0,
            "cpu_tiny": 10.0,
            "cpu_base": 5.0,
            "cpu_small": 2.0,
            "cpu_medium": 1.0,
            "cpu_large-v3": 0.3
        }

    def _load(self):
        if os.path.exists(BENCH_FILE):
            try:
                with open(BENCH_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: pass
        return {"history": [], "stats": {}}

    def _save(self):
        try:
            with open(BENCH_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4)
        except: pass

    def get_rt_factor(self, model_id, device):
        """Returns the processing speed factor (e.g. 15.0 means 15x real-time)"""
        key = f"{device}_{model_id}"
        
        # 1. Try verified history first
        if key in self.data.get("stats", {}):
            return self.data["stats"][key].get("avg_factor", self.default_factors.get(key, 1.0))
        
        # 2. Return tuned defaults
        return self.default_factors.get(key, 1.0)

    def predict_sec(self, model_id, device, duration_sec):
        """Predicts total SECONDS needed for transcription"""
        factor = self.get_rt_factor(model_id, device)
        # Add 5s Overhead for audio extraction and initialization
        prediction = (duration_sec / factor) + 5.0
        return round(prediction, 1)

    def record_run(self, model_id, device, media_duration, actual_time):
        """Persists the performance metrics for future refinement"""
        if media_duration <= 0 or actual_time <= 0: return
        
        factor = media_duration / actual_time
        key = f"{device}_{model_id}"
        
        # Append to history (Cap at 50 runs for privacy/storage)
        self.data["history"].append({
            "ts": int(time.time()),
            "key": key,
            "v_dur": round(media_duration, 1),
            "real_time": round(actual_time, 1),
            "factor": round(factor, 2)
        })
        if len(self.data["history"]) > 50:
            self.data["history"].pop(0)

        # Update running average
        if key not in self.data["stats"]:
            self.data["stats"][key] = {"runs": 0, "avg_factor": 0.0}
            
        stats = self.data["stats"][key]
        old_avg = stats["avg_factor"]
        old_runs = stats["runs"]
        
        new_avg = ((old_avg * old_runs) + factor) / (old_runs + 1)
        stats["avg_factor"] = round(new_avg, 2)
        stats["runs"] += 1
        
        self._save()
        return round(factor, 2)

# Global singleton instance
bench_center = ACUTBenchmarkCenter()
