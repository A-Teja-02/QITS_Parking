from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

class VirtualLine(BaseModel):
    enabled: bool = True
    x1: float = Field(0.1, description="Normalized X start (0-1)")
    y1: float = Field(0.5, description="Normalized Y start (0-1)")
    x2: float = Field(0.9, description="Normalized X end (0-1)")
    y2: float = Field(0.5, description="Normalized Y end (0-1)")
    entry_direction: str = Field("top_to_bottom", description="top_to_bottom or bottom_to_top or left_to_right or right_to_left")

class SlotPolygon(BaseModel):
    slot_id: str
    points: List[List[float]] = Field(..., description="4 normalized [[x, y]] corner coordinates from 0.0 to 1.0")
    label: Optional[str] = None

class ParkingZone(BaseModel):
    id: str
    name: str = "Main Parking Lot"
    virtual_line: Optional[VirtualLine] = None

class CameraConfigCreate(BaseModel):
    name: str
    source_type: str = Field("rtsp", description="rtsp, nvr, http, usb, file")
    zone_id: str = "zone_1"
    location: Optional[str] = None
    channel: int = 1
    brand: Optional[str] = "generic"
    rtsp_url: Optional[str] = None
    host: Optional[str] = None
    port: int = 554
    username: Optional[str] = None
    password: Optional[str] = None
    stream_path: Optional[str] = None
    transport: str = Field("tcp", description="tcp or udp")
    stream_profile: str = Field("main", description="main or sub")
    enable_vehicle_detection: bool = True
    enable_plate_recognition: bool = True
    min_confidence: float = 0.35
    virtual_line: Optional[VirtualLine] = None
    assigned_slot_id: Optional[str] = Field(None, description="Parking slot ID monitored by this CCTV camera (e.g. P101, P102)")
    monitored_slots: Optional[List[SlotPolygon]] = Field(default_factory=list, description="Multiple parking slots calibrated for this camera")
    # Continuous slot monitoring
    monitoring_enabled: bool = True
    analysis_interval: float = Field(1.5, description="Seconds between AI analysis cycles")
    detection_imgsz: int = Field(0, description="YOLO input size; 0 = auto (1280 for HD streams)")
    violation_dwell_seconds: int = Field(20, description="Car must stay parked this long before a violation is raised")
    flag_unreadable_plates: bool = Field(False, description="Log a review event when a parked car's plate stays unreadable")

class CameraConfig(CameraConfigCreate):
    id: str
    is_active: bool = True
    status: str = "disconnected" # connected, disconnected, connecting, error
    last_error: Optional[str] = None
    last_frame_time: Optional[str] = None
    resolution: Optional[str] = None
    fps: float = 0.0
    created_at: str
    updated_at: str

class CameraTestRequest(BaseModel):
    camera_id: Optional[str] = None  # when editing: fill masked credentials from the saved camera
    source_type: str = "rtsp"
    brand: Optional[str] = "generic"
    channel: int = 1
    stream_profile: str = "main"
    rtsp_url: Optional[str] = None
    host: Optional[str] = None
    port: int = 554
    username: Optional[str] = None
    password: Optional[str] = None
    stream_path: Optional[str] = None
    transport: str = "tcp"

class PlateCandidate(BaseModel):
    plate_text: str
    confidence: float
    is_valid_format: bool
    state_code: Optional[str] = None

class PlateRecognitionResult(BaseModel):
    plate_text: str
    confidence: float
    status: str = "CONFIRMED" # CONFIRMED, REVIEW_REQUIRED, UNREADABLE
    is_valid_format: bool
    state_code: Optional[str] = None
    plate_box: Optional[List[int]] = None # [x1, y1, x2, y2]
    quality_score: float = 0.0
    plate_crop_base64: Optional[str] = None
    candidates: List[PlateCandidate] = []
    frames_aggregated: int = 1
    unreadable_reason: Optional[str] = None

class VehicleDetection(BaseModel):
    track_id: Optional[int] = None
    class_name: str
    confidence: float
    box: List[float] # [x1, y1, x2, y2]
    plate: Optional[PlateRecognitionResult] = None
    crossing_event: Optional[str] = None # None, ENTRY, EXIT

class ParkingEvent(BaseModel):
    id: str
    camera_id: str
    camera_name: str
    zone_id: str
    timestamp: str
    event_type: str = "VEHICLE_ENTRY" # VEHICLE_ENTRY, VEHICLE_EXIT, PLATE_CAPTURED, MANUAL_AUDIT
    vehicle_type: str = "car"
    track_id: Optional[int] = None
    plate_number: str
    plate_status: str = "CONFIRMED" # CONFIRMED, REVIEW_REQUIRED, UNREADABLE
    confidence: float
    is_valid_format: bool
    image_url: Optional[str] = None
    vehicle_crop_url: Optional[str] = None
    plate_crop_url: Optional[str] = None
    candidates: List[PlateCandidate] = []
    review_status: str = "auto_approved" # auto_approved, pending_review, audited
    manual_corrected_plate: Optional[str] = None
    audit_notes: Optional[str] = None

class MultiSlotVerifyRequest(BaseModel):
    image_base64: str
    monitored_slots: List[SlotPolygon] = Field(..., description="List of slot polygons to verify")
    camera_id: str = "webcam_multi"

class SingleSlotStatus(BaseModel):
    slot_id: str
    occupancy_status: str # vacant, occupied, error
    verification_status: str # CORRECT_PARKING, WRONG_SLOT, UNAUTHORIZED, VACANT, OCCUPIED_NO_PLATE, ERROR
    detected_plate: Optional[str] = None
    confidence: float = 0.0
    expected_plate: Optional[str] = None
    expected_user: Optional[str] = None
    actual_driver: Optional[str] = None
    message: str = ""
    polygon: List[List[float]] = []
    vehicle_box: Optional[List[float]] = None
    plate_crop_base64: Optional[str] = None
    alert_triggered: bool = False
    unreadable_reason: Optional[str] = None

class MultiSlotVerifyResponse(BaseModel):
    total_slots: int
    occupied_count: int
    vacant_count: int
    violation_count: int
    slots: List[SingleSlotStatus]
    detections: List[Dict[str, Any]] = []
    annotated_frame_base64: Optional[str] = None

class AutoSuggestSlotsRequest(BaseModel):
    image_base64: str
    num_slots_hint: Optional[int] = 2
    method: str = Field("auto", description="auto | lines | vehicles")
    colors: Optional[List[str]] = Field(None, description="Painted line colours: yellow, blue, white")


class PhotoCheckRequest(BaseModel):
    image_base64: str
    monitored_slots: List[SlotPolygon]
    record_violations: bool = False
    thorough: bool = True

class AutoSuggestSlotsResponse(BaseModel):
    suggested_slots: List[SlotPolygon]
    count: int
    source: str
    message: str
