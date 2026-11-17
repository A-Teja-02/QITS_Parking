"""
Parking surveillance API.

Heavy endpoints are plain `def` (not `async def`) so FastAPI runs them in its
thread pool - otherwise one slow AI call would freeze every live video stream.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

import base64
import cv2
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from models.parking_schemas import (
    AutoSuggestSlotsRequest, CameraConfigCreate, CameraTestRequest, PhotoCheckRequest,
    MultiSlotVerifyRequest, ParkingEvent, SlotPolygon, VehicleDetection, VirtualLine,
)
from services.cctv_manager import CCTVManager
from services.identity_service import IdentityService
from services.violation_store import ViolationStore

from api.auth_guard import require_admin

# Every CCTV / AI endpoint is admin-only (same login as the main app)
router = APIRouter(prefix="/api/parking", tags=["Parking Surveillance & ANPR"], dependencies=[Depends(require_admin)])


def _mgr() -> CCTVManager:
    return CCTVManager.get_instance()


def _cam_or_404(camera_id: str):
    cam = _mgr().cameras.get(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    return cam


# ----------------------------------------------------------------------
# Cameras
# ----------------------------------------------------------------------
class CameraUpdateRequest(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    source_type: Optional[str] = None
    brand: Optional[str] = None
    rtsp_url: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    stream_path: Optional[str] = None
    channel: Optional[int] = None
    transport: Optional[str] = None
    stream_profile: Optional[str] = None
    assigned_slot_id: Optional[str] = None
    monitored_slots: Optional[List[SlotPolygon]] = None
    enable_plate_recognition: Optional[bool] = None
    min_confidence: Optional[float] = None
    monitoring_enabled: Optional[bool] = None
    analysis_interval: Optional[float] = None
    detection_imgsz: Optional[int] = None
    violation_dwell_seconds: Optional[int] = None
    flag_unreadable_plates: Optional[bool] = None
    virtual_line: Optional[VirtualLine] = None


class UpdateCameraSlotsRequest(BaseModel):
    monitored_slots: List[SlotPolygon]


@router.get("/cameras")
def list_cameras():
    return _mgr().get_cameras()


@router.post("/cameras")
def add_camera(payload: CameraConfigCreate):
    if payload.source_type in ("rtsp", "nvr", "http") and not (payload.rtsp_url or payload.host):
        raise HTTPException(status_code=400, detail="Provide a stream URL or the camera IP address")
    return _mgr().add_camera(payload)


@router.post("/cameras/test")
def test_camera(payload: CameraTestRequest):
    return _mgr().test_connection(payload)


@router.put("/cameras/{camera_id}")
def update_camera(camera_id: str, payload: CameraUpdateRequest):
    _cam_or_404(camera_id)
    fields = payload.model_dump(exclude_none=True)
    if "monitored_slots" in fields:
        fields["monitored_slots"] = payload.monitored_slots
    if "virtual_line" in fields:
        fields["virtual_line"] = payload.virtual_line
    return _mgr().update_camera(camera_id, fields)


@router.delete("/cameras/{camera_id}")
def delete_camera(camera_id: str):
    if not _mgr().delete_camera(camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    return {"success": True, "message": f"Camera {camera_id} removed"}


@router.post("/cameras/{camera_id}/start")
def start_camera_stream(camera_id: str):
    cam = _cam_or_404(camera_id)
    _mgr().start_camera(camera_id)
    return {"success": True, "message": f"Camera '{cam.name}' started", "camera_id": camera_id}


@router.post("/cameras/{camera_id}/stop")
def stop_camera_stream(camera_id: str):
    _cam_or_404(camera_id)
    _mgr().stop_camera(camera_id)
    return {"success": True, "message": "Camera stopped"}


@router.get("/cameras/{camera_id}/stream")
def get_camera_stream(camera_id: str, annotated: bool = True, fps: float = Query(8.0, ge=1, le=25)):
    _cam_or_404(camera_id)
    return StreamingResponse(
        _mgr().generate_mjpeg_stream(camera_id, annotated=annotated, fps=fps),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/cameras/{camera_id}/snapshot")
def get_camera_snapshot(camera_id: str, annotated: bool = False, max_width: int = Query(0, ge=0, le=3840),
                        quality: int = Query(90, ge=30, le=95)):
    """Latest frame as one JPEG. The live view polls this (works everywhere, unlike MJPEG in <img>)."""
    _cam_or_404(camera_id)
    frame = _mgr().latest_frame(camera_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="No frame yet - camera is not streaming")
    mon = _mgr().monitors.get(camera_id)
    if annotated and mon:
        frame = mon.draw_overlay(frame)
    if max_width and frame.shape[1] > max_width:
        h, w = frame.shape[:2]
        frame = cv2.resize(frame, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return Response(content=buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/cameras/{camera_id}/latest-frame")
def get_latest_camera_frame(camera_id: str):
    """Full-resolution latest frame as base64 (used by the slot layout editor)."""
    _cam_or_404(camera_id)
    mgr = _mgr()
    frame = mgr.latest_frame(camera_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="Camera stream not active or no frames yet")
    w = mgr.workers.get(camera_id)
    _, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return {
        "camera_id": camera_id,
        "image_base64": "data:image/jpeg;base64," + base64.b64encode(buf).decode("utf-8"),
        "width": frame.shape[1], "height": frame.shape[0],
        "resolution": w.resolution if w else f"{frame.shape[1]}x{frame.shape[0]}",
        "fps": w.current_fps if w else 0, "status": w.status if w else "unknown",
    }


@router.put("/cameras/{camera_id}/slots")
def update_camera_slots(camera_id: str, payload: UpdateCameraSlotsRequest):
    """Save the slot layout (polygons) this camera should monitor."""
    _cam_or_404(camera_id)
    ids = [s.slot_id.strip().upper() for s in payload.monitored_slots]
    if any(not i for i in ids):
        raise HTTPException(status_code=400, detail="Every slot needs a slot ID")
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail="Each slot ID can only be used once per camera")
    # Slots are created only in Admin -> Parking Lots; a layout can only use those slots
    ident = IdentityService.get_instance()
    by_id = {(x["id"] or "").upper(): x["id"] for x in ident.slots()}
    by_name = {(x["label"] or "").strip().upper(): x["id"] for x in ident.slots()}
    for s in payload.monitored_slots:
        key = s.slot_id.strip().upper()
        real = by_id.get(key) or by_name.get(key)
        if not real:
            raise HTTPException(status_code=400, detail=f"'{s.slot_id}' is not a parking slot. Create it in Admin -> Parking Lots first, then pick it here.")
        s.slot_id = real
    ids = [s.slot_id.upper() for s in payload.monitored_slots]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=400, detail="Each parking slot can only be used once per camera")
    for s in payload.monitored_slots:
        if len(s.points) < 3:
            raise HTTPException(status_code=400, detail=f"Slot {s.slot_id} needs at least 3 corner points")
        s.slot_id = s.slot_id.strip().upper()
        s.points = [[min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y)))] for x, y in s.points]
    cam = _mgr().update_camera(camera_id, {"monitored_slots": payload.monitored_slots})
    return {"success": True, "camera_id": camera_id, "total_slots": len(payload.monitored_slots),
            "monitored_slots": cam["monitored_slots"]}


@router.get("/cameras/{camera_id}/live-status")
def get_camera_live_status(camera_id: str):
    """Current slot-by-slot occupancy, plate and verdict from the continuous monitor."""
    cam = _cam_or_404(camera_id)
    mgr = _mgr()
    mon = mgr.monitors.get(camera_id)
    w = mgr.workers.get(camera_id)
    base = {"camera_id": camera_id, "camera_name": cam.name,
            "stream_status": w.status if w else "stopped", "stream_error": w.last_error if w else None,
            "fps": w.current_fps if w else 0, "resolution": w.resolution if w else None}
    if not mon:
        return {**base, "running": False, "total_slots": len(cam.monitored_slots or []), "slots": [],
                "occupied_count": 0, "vacant_count": 0, "violation_count": 0}
    return {**base, **mon.status()}


@router.get("/live-status")
def get_all_live_status():
    return [get_camera_live_status(cid) for cid in list(_mgr().cameras)]


@router.post("/cameras/{camera_id}/baseline")
def capture_empty_baseline(camera_id: str, clear: bool = False):
    """
    Remember how each slot looks when EMPTY. Do this once when the slots are empty;
    it lets the monitor notice cars the vehicle detector misses (odd angles, partial views).
    """
    _cam_or_404(camera_id)
    mon = _mgr().monitors.get(camera_id)
    if not mon:
        raise HTTPException(status_code=400, detail="Start the camera first")
    if clear:
        mon.clear_baseline()
        return {"success": True, "message": "Empty-slot baseline cleared"}
    mon.request_baseline_capture()
    return {"success": True, "message": "Empty-slot baseline will be captured on the next analysis cycle"}


# ----------------------------------------------------------------------
# Parking slots / bookings (read from the main parking DB)
# ----------------------------------------------------------------------
@router.get("/slots")
def list_parking_slots():
    ident = IdentityService.get_instance()
    today = datetime.now().strftime("%Y-%m-%d")
    res = {(r["slot_id"] or "").upper(): r for r in ident.reservations_for(today)}
    out = []
    for s in ident.slots():
        exp = ident.slot_expectation(s["id"], today)
        out.append({**s, "booked_by": exp.get("user_name"), "booked_vehicle": exp.get("vehicle_number"),
                    "booking_source": exp.get("source"), "has_reservation": s["id"].upper() in res})
    return out


@router.get("/reservations/today")
def get_today_reservations(date: Optional[str] = None):
    from services.parking_verifier import ParkingVerifier
    return ParkingVerifier.get_instance().get_today_reservations(date)


# ----------------------------------------------------------------------
# Violations
# ----------------------------------------------------------------------
class ViolationUpdate(BaseModel):
    status: Optional[str] = None           # open | resolved | false_alarm
    notes: Optional[str] = None
    corrected_plate: Optional[str] = None


@router.get("/violations")
def list_violations(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                    status: Optional[str] = None, slot_id: Optional[str] = None,
                    camera_id: Optional[str] = None, plate: Optional[str] = None, date: Optional[str] = None,
                    date_from: Optional[str] = None, date_to: Optional[str] = None):
    """Violation history. Filter by one day (date) or a range (date_from / date_to, YYYY-MM-DD)."""
    store = ViolationStore.get_instance()
    if not store.ready:
        raise HTTPException(status_code=503, detail=f"Violations table not available in the parking database: {store.error}")
    return store.list(limit=limit, offset=offset, status=status, slot_id=slot_id, camera_id=camera_id,
                      plate=plate, date=date, date_from=date_from, date_to=date_to)


@router.get("/violations/{violation_id}/image/{kind}")
def get_violation_image(violation_id: str, kind: str):
    """Evidence photo (scene / vehicle / plate) stored in the database."""
    data = ViolationStore.get_instance().get_image(violation_id, kind)
    if not data:
        raise HTTPException(status_code=404, detail="Image not found")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/violations/{violation_id}")
def get_violation(violation_id: str):
    v = ViolationStore.get_instance().get(violation_id)
    if not v:
        raise HTTPException(status_code=404, detail="Violation not found")
    return v


@router.patch("/violations/{violation_id}")
def update_violation(violation_id: str, payload: ViolationUpdate):
    store = ViolationStore.get_instance()
    v = store.get(violation_id)
    if not v:
        raise HTTPException(status_code=404, detail="Violation not found")
    if payload.status and payload.status not in ("open", "resolved", "false_alarm"):
        raise HTTPException(status_code=400, detail="status must be open, resolved or false_alarm")
    fields: Dict[str, Any] = payload.model_dump(exclude_none=True)
    if payload.corrected_plate:
        # Re-identify the vehicle owner with the corrected plate
        ident = IdentityService.get_instance()
        m = ident.match_plate(payload.corrected_plate)
        fields["corrected_plate"] = payload.corrected_plate.strip().upper()
        from services.slot_monitor import _public_employee
        fields["matched_user"] = _public_employee(m.get("employee"), m.get("reservation"))
    return store.update(violation_id, fields)


@router.delete("/violations/{violation_id}")
def delete_violation(violation_id: str):
    if not ViolationStore.get_instance().delete(violation_id):
        raise HTTPException(status_code=404, detail="Violation not found")
    return {"success": True}


# ----------------------------------------------------------------------
# One-shot tests on uploaded images
# ----------------------------------------------------------------------
class FrameProcessRequest(BaseModel):
    image_base64: str
    camera_id: str = "default_camera"


class SlotVerifyRequest(BaseModel):
    image_base64: str
    slot_id: Optional[str] = None
    camera_id: str = "photo_test"


@router.post("/anpr/process-frame", response_model=List[VehicleDetection])
def process_anpr_frame(payload: FrameProcessRequest):
    return _mgr().process_vehicle_frame(payload.image_base64, payload.camera_id)


@router.post("/anpr/verify-slot")
def verify_parking_slot(payload: SlotVerifyRequest):
    from services.parking_verifier import ParkingVerifier
    return ParkingVerifier.get_instance().verify_parking_slot(payload.image_base64, payload.slot_id, payload.camera_id)


@router.post("/anpr/verify-multi-slots")
def verify_multi_parking_slots(payload: MultiSlotVerifyRequest):
    """Test a still image against a slot layout. Does not record violations."""
    from services.parking_verifier import ParkingVerifier
    return ParkingVerifier.get_instance().verify_multi_slots(payload.image_base64, payload.monitored_slots, payload.camera_id)


@router.post("/cameras/{camera_id}/verify-multi-slots")
def verify_camera_multi_slots(camera_id: str):
    """Run a one-shot check of the camera's saved layout on its latest frame."""
    cam = _cam_or_404(camera_id)
    frame = _mgr().latest_frame(camera_id)
    if frame is None:
        return {"error": f"Camera '{cam.name}' is not streaming. Start the camera first.", "camera_id": camera_id}
    if not cam.monitored_slots:
        raise HTTPException(status_code=400, detail="No slot layout saved for this camera yet")
    _, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    from services.parking_verifier import ParkingVerifier
    res = ParkingVerifier.get_instance().verify_multi_slots(
        "data:image/jpeg;base64," + base64.b64encode(buf).decode(), cam.monitored_slots, camera_id)
    return {**res, "camera_id": camera_id, "camera_name": cam.name}


@router.post("/anpr/auto-suggest-slots")
def auto_suggest_parking_slots(payload: AutoSuggestSlotsRequest):
    """Slot outlines from painted floor lines (yellow/blue/white) and/or parked vehicles."""
    from services.parking_verifier import ParkingVerifier
    return ParkingVerifier.get_instance().suggest_hybrid_slots(
        payload.image_base64, payload.num_slots_hint or 2, payload.method, payload.colors)


@router.post("/anpr/photo-check")
def photo_check(payload: PhotoCheckRequest):
    """
    Full check of a still photo against a slot layout: occupancy, number plates, owner lookup,
    booking check. With record_violations=true, violations are saved to the Violations log
    (with photos) and HR is notified - re-checking the same photo does not duplicate them.
    """
    if not payload.monitored_slots:
        raise HTTPException(status_code=400, detail="Add at least one slot")
    from services.parking_verifier import ParkingVerifier
    return ParkingVerifier.get_instance().verify_multi_slots(
        payload.image_base64, payload.monitored_slots, record=payload.record_violations, thorough=payload.thorough)


# ----------------------------------------------------------------------
# Legacy ANPR event log (browser webcam ANPR)
# ----------------------------------------------------------------------
class CorrectionRequest(BaseModel):
    corrected_plate: str
    notes: Optional[str] = None


@router.get("/events", response_model=List[ParkingEvent])
def get_parking_events(limit: int = Query(50, ge=1, le=200), plate: Optional[str] = None,
                       event_type: Optional[str] = None):
    events = _mgr().events
    if plate:
        q = plate.upper().replace(' ', '')
        events = [e for e in events if q in e.plate_number.replace(' ', '')]
    if event_type:
        events = [e for e in events if e.event_type == event_type]
    return events[:limit]


@router.put("/events/{event_id}/correction", response_model=ParkingEvent)
def correct_event_plate(event_id: str, payload: CorrectionRequest):
    ev = _mgr().update_event_correction(event_id, payload.corrected_plate, payload.notes)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    return ev


class ManualPlateRequest(BaseModel):
    slot_id: str
    plate: str


@router.post("/anpr/classify-plate")
def classify_manual_plate(payload: ManualPlateRequest):
    """Admin typed the plate (OCR could not read it): check owner and booking for that slot."""
    from services.parking_verifier import classify_plate
    from services.slot_monitor import _public_employee
    c = classify_plate(payload.slot_id.strip().upper(), payload.plate.strip().upper(), IdentityService.get_instance())
    return {"verification_status": c["status"], "violation_type": c["violation_type"], "message": c["message"],
            "detected_plate": c["plate"], "plate_match": c["match"]["match"],
            "matched_user": _public_employee(c["match"].get("employee"), c["match"].get("reservation"))}


# ----------------------------------------------------------------------
# ONVIF: find cameras on the network and read their RTSP addresses
# ----------------------------------------------------------------------
class OnvifStreamsRequest(BaseModel):
    host: str
    port: int = 80
    username: str = ""
    password: str = ""


@router.post("/onvif/discover")
def onvif_discover(timeout: float = Query(3.0, ge=1.0, le=10.0)):
    """Broadcast an ONVIF probe on the AI server's network and list cameras/NVRs that answer."""
    from services.onvif_service import discover
    devices = discover(timeout)
    return {"count": len(devices), "devices": devices}


@router.post("/onvif/streams")
def onvif_streams(payload: OnvifStreamsRequest):
    """Log in to an ONVIF camera and return its stream profiles with RTSP URLs (main stream first)."""
    from services.onvif_service import OnvifError, get_streams
    from services.cctv_manager import redact_url
    try:
        res = get_streams(payload.host, payload.port, payload.username, payload.password)
    except OnvifError as e:
        raise HTTPException(status_code=400, detail=str(e))
    for s in res["streams"]:
        s["display_url"] = redact_url(s["rtsp_url"])
    return res


@router.get("/system/status")
def system_status():
    """Which database the AI engine reads bookings from, and whether it is reachable."""
    return IdentityService.get_instance().status()
