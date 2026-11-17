import os
import time
import base64
import threading
import numpy as np
import cv2
from typing import List, Dict, Any, Optional
from datetime import datetime
from ultralytics import YOLO

class DetectionService:
    _instance = None
    _model: Optional[YOLO] = None
    _device: str = "cpu"
    _loaded_model_name: str = ""
    # YOLO models are not thread-safe; camera monitor threads and API requests share one model.
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.load_model()

    def _resolve_model_path(self, model_name: str) -> str:
        """Find model weights locally to avoid downloading over the internet."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        service_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.dirname(base_dir)

        search_paths = [
            os.path.join(base_dir, model_name),
            os.path.join(service_dir, model_name),
            os.path.join(root_dir, model_name),
            os.path.join(os.getcwd(), model_name),
            os.path.join(os.getcwd(), "backend", model_name),
        ]

        for p in search_paths:
            if os.path.isfile(p) and os.path.getsize(p) > 100000:
                return p

        # Fallback to yolov8n.pt if yolov8s.pt is not found locally
        if model_name != "yolov8n.pt":
            for p in search_paths:
                fallback_p = p.replace(model_name, "yolov8n.pt")
                if os.path.isfile(fallback_p) and os.path.getsize(fallback_p) > 100000:
                    print(f"[DetectionService] Primary {model_name} not found locally, using {fallback_p}")
                    return fallback_p

        return model_name

    def load_model(self, model_name: str = "yolov8s.pt"):
        """Load YOLO model with device auto-detection and local path resolution."""
        try:
            import torch
            if torch.cuda.is_available():
                self._device = "cuda"
            else:
                self._device = "cpu"
        except Exception:
            self._device = "cpu"

        resolved_path = self._resolve_model_path(model_name)
        print(f"[DetectionService] Loading model from {resolved_path} on {self._device}...")
        self._model = YOLO(resolved_path)
        self._loaded_model_name = os.path.basename(resolved_path)

        # Pre-warm model with dummy image at 640x640 inference size
        try:
            dummy_img = np.zeros((640, 640, 3), dtype=np.uint8)
            self._model.predict(dummy_img, device=self._device, imgsz=640, verbose=False)
            print(f"[DetectionService] Model {self._loaded_model_name} loaded and pre-warmed successfully!")
        except Exception as e:
            print(f"[DetectionService] Pre-warm note: {e}")

    def get_class_names(self) -> Dict[int, str]:
        if self._model and hasattr(self._model, "names"):
            return self._model.names
        return {}

    def decode_base64_image(self, b64_str: str) -> np.ndarray:
        if "," in b64_str:
            b64_str = b64_str.split(",", 1)[1]
        img_bytes = base64.b64decode(b64_str)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Failed to decode base64 image data")
        return img

    def preprocess_for_detection(self, image: np.ndarray) -> np.ndarray:
        """
        Adaptive contrast balance only when image has extremely low dynamic range (dark or hazy).
        Preserves natural RGB color gradients for normal frames.
        """
        if image is None or image.size == 0:
            return image

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        std_dev = float(np.std(gray))
        mean_val = float(np.mean(gray))

        # Only enhance if severely under-exposed or ultra-low contrast
        if std_dev < 24.0 or mean_val < 35.0:
            lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
            lab_norm = cv2.merge([clahe.apply(l), a, b])
            return cv2.cvtColor(lab_norm, cv2.COLOR_LAB2BGR)

        return image

    def detect(
        self,
        image: np.ndarray,
        confidence_threshold: float = 0.25,
        classes_to_filter: Optional[List[str]] = None,
        enable_tracking: bool = True,
        imgsz: int = 640,
        augment: bool = False
    ) -> Dict[str, Any]:
        if self._model is None:
            raise RuntimeError("YOLO model not loaded")

        height, width = image.shape[:2]
        processed_image = self.preprocess_for_detection(image)

        t0 = time.perf_counter()

        # Build filter classes mapping
        # Expand friendly terms like 'van' to ['car', 'truck', 'bus']
        expanded_filters = None
        if classes_to_filter:
            expanded = set()
            for c in classes_to_filter:
                cl = c.strip().lower()
                if cl in ('van', 'suv', 'auto', 'auto-rickshaw'):
                    expanded.update(['car', 'truck', 'bus'])
                elif cl in ('bike', 'scooter'):
                    expanded.update(['motorcycle', 'bicycle'])
                else:
                    expanded.add(cl)
            expanded_filters = expanded

        common = dict(conf=confidence_threshold, iou=0.45, imgsz=imgsz,
                      device=self._device, verbose=False, max_det=100)
        if augment:
            common["augment"] = True  # test-time augmentation (flips/scales): better recall on still photos
        with self._lock:
            try:
                if enable_tracking:
                    results = self._model.track(processed_image, persist=True, **common)
                else:
                    results = self._model.predict(processed_image, **common)
            except Exception:
                # Fallback to simple predict if the tracker failed
                results = self._model.predict(processed_image, **common)

        inference_time_ms = round((time.perf_counter() - t0) * 1000, 2)

        detections = []
        class_counts: Dict[str, int] = {}
        names_dict = self._model.names

        if results and len(results) > 0:
            boxes = results[0].boxes
            if boxes is not None:
                for box in boxes:
                    cls_id = int(box.cls[0].item())
                    cls_name = names_dict.get(cls_id, f"class_{cls_id}")
                    conf = float(box.conf[0].item())

                    # Check filter
                    if expanded_filters is not None and cls_name.lower() not in expanded_filters:
                        continue

                    # Bounding box coordinates [x1, y1, x2, y2] clamped to frame boundaries
                    xyxy = box.xyxy[0].tolist()
                    x1 = max(0.0, min(float(xyxy[0]), float(width)))
                    y1 = max(0.0, min(float(xyxy[1]), float(height)))
                    x2 = max(x1, min(float(xyxy[2]), float(width)))
                    y2 = max(y1, min(float(xyxy[3]), float(height)))

                    track_id = int(box.id[0].item()) if (hasattr(box, 'id') and box.id is not None and len(box.id) > 0) else None

                    detections.append({
                        "class_id": cls_id,
                        "class_name": cls_name,
                        "confidence": round(conf, 3),
                        "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                        "track_id": track_id
                    })

                    class_counts[cls_name] = class_counts.get(cls_name, 0) + 1

        return {
            "detections": detections,
            "total_count": len(detections),
            "class_counts": class_counts,
            "inference_time_ms": inference_time_ms,
            "timestamp": datetime.now().isoformat(),
            "frame_width": width,
            "frame_height": height
        }
