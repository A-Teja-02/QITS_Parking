from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class DetectionBox(BaseModel):
    class_id: int
    class_name: str
    confidence: float
    box: List[float] = Field(..., description="[x1, y1, x2, y2] bounding box coordinates in pixel or normalized space")
    track_id: Optional[int] = Field(None, description="Unique tracking identifier across consecutive frames")

class DetectionRequest(BaseModel):
    image_base64: str
    confidence_threshold: float = Field(0.35, ge=0.05, le=1.0)
    classes_to_filter: Optional[List[str]] = None
    enable_tracking: bool = True

class DetectionResponse(BaseModel):
    detections: List[DetectionBox]
    total_count: int
    class_counts: Dict[str, int]
    inference_time_ms: float
    timestamp: str
    frame_width: int
    frame_height: int

class OCRRequest(BaseModel):
    image_base64: str
    box: Optional[List[int]] = Field(None, description="Optional [x1, y1, x2, y2] ROI to crop before OCR")
    enhance_image: bool = True

class OCRWord(BaseModel):
    text: str
    confidence: float
    bbox: Optional[List[List[int]]] = None

class OCRResponse(BaseModel):
    text: str
    confidence: float
    words: List[OCRWord] = []
    processing_time_ms: float
    status: str
    detected_language: Optional[str] = "en"

class CaptureRule(BaseModel):
    id: str
    name: str
    enabled: bool = True
    target_classes: List[str] = []
    min_confidence: float = 0.4
    min_visible_seconds: float = 0.0
    cooldown_seconds: float = 5.0
    run_ocr: bool = False
    save_crop: bool = True
    max_captures_per_event: int = 1

class CaptureEventCreate(BaseModel):
    timestamp: str
    camera_id: str = "default_camera"
    reason: str
    labels: List[str]
    confidences: List[float]
    ocr_text: Optional[str] = None
    image_base64: str
    crop_base64: Optional[str] = None
    metadata: Dict[str, Any] = {}

class CaptureEventRecord(BaseModel):
    id: str
    timestamp: str
    camera_id: str
    reason: str
    labels: List[str]
    confidences: List[float]
    ocr_text: Optional[str]
    image_url: str
    crop_url: Optional[str]
    metadata: Dict[str, Any]

class UpdateOCRTextRequest(BaseModel):
    ocr_text: str

class SystemStatus(BaseModel):
    status: str
    model_loaded: bool
    model_name: str
    classes_count: int
    device: str
    ocr_available: bool
    ocr_engine: str
    total_captures: int
    host_ip: Optional[str] = None
