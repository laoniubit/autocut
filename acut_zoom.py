import cv2
import numpy as np
import math
import os
import sys
import tempfile
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision

def _get_model_path():
    base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "_internal", "face_detection_full_range_sparse.tflite"),
        os.path.join(base, "face_detection_full_range_sparse.tflite"),
        os.path.join(tempfile.gettempdir(), "face_detection_full_range_sparse.tflite")
    ]
    for c in candidates:
        if os.path.exists(c): return c
    return ""

def detect_faces(detector, frame_rgb, cfg=None, orig_size=None):
    """[Industrial Alignment] Use Tasks API but align coordinates with verified v9.5 standards."""
    h, w = frame_rgb.shape[:2]
    oh, ow = orig_size[::-1] if orig_size else (h, w)
    candidate_faces = []
    
    # 1. Physical Detection using the available Tasks API
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
    result = detector.detect(mp_image)
    
    if result.detections:
        for d in result.detections:
            bb = d.bounding_box
            # Normalize coordinates to match the Legacy solutions style [0.0, 1.0]
            cx = (bb.origin_x + bb.width / 2.0) / w
            cy = (bb.origin_y + bb.height / 2.0) / h
            # Calculate area for priority sorting
            area = (bb.width * bb.height)
            candidate_faces.append((area, cx, cy, bb.height / h, bb.width / w))
    
    if not candidate_faces: return [], 0
    
    valid = []
    min_sz = (cfg or {}).get("min_face_size", 50)
    z = (cfg or {}).get("detection_zone", [0, 0, w, h])
    is_ratio = all(v <= 1.0 for v in z)
    
    for hw, cx, cy, fh, fw in candidate_faces:
        zx1, zy1, zx2, zy2 = z
        if is_ratio:
            if not (zx1 <= cx <= zx2 and zy1 <= cy <= zy2): continue
        else:
            if not (zx1 <= cx * ow <= zx2 and zy1 <= cy * oh <= zy2): continue
        
        if fw * ow >= min_sz and fh * oh >= min_sz:
            valid.append((hw, cx, cy, fh, fw))
            
    return valid, len(candidate_faces)

class FaceNormalizer:
    """[Industrial Alignment] Tasks-based detector with v9.5 logic shell."""
    def __init__(self, cfg):
        self.cfg = cfg
        model_path = _get_model_path()
        if not model_path:
            raise RuntimeError("MediaPipe model asset (tflite) missing.")
            
        base_options = mp_tasks.BaseOptions(model_asset_path=model_path)
        options = mp_vision.FaceDetectorOptions(
            base_options=base_options,
            min_detection_confidence=cfg.get("ai_confidence", 0.5)
        )
        self.detector = mp_vision.FaceDetector.create_from_options(options)
        self.api = "tasks"

    def __del__(self):
        try:
            if hasattr(self, 'detector'): self.detector.close()
        except: pass
