"""
Continuous per-camera parking slot monitor.

For every camera that has a calibrated slot layout, a background thread:
  1. takes the newest frame from the RTSP worker,
  2. detects vehicles (full frame, then a zoomed second pass on empty slots),
  3. assigns vehicles to the calibrated slot polygons by footprint overlap,
  4. smooths occupancy over several frames (no flicker from one bad detection),
  5. reads the plate of each parked vehicle across several frames and votes,
  6. once a car has stayed parked for the dwell time and its plate is stable,
     checks it against today's booking for that slot,
  7. records ONE violation per parking session with evidence images, and
     notifies HR.

Only the calibrated slot areas are analysed; anything outside them is ignored.
"""
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from services.anpr_service import ANPRService
from services.detection_service import DetectionService
from services.identity_service import IdentityService, normalize_plate, plate_distance
from services.violation_store import ViolationStore
from services import notifier

VEHICLE_CLASSES = ["car", "truck", "bus", "motorcycle"]

# Occupancy smoothing (in analysis cycles)
ENTER_CYCLES = 2          # consecutive "occupied" observations to confirm a car arrived
EXIT_CYCLES = 3           # consecutive "empty" observations to confirm it left
# Plate voting
MIN_AGREEING_READS = 2    # identical reads needed before a plate is trusted
MAX_READS = 12
OCR_INTERVAL = 1.0        # seconds between OCR attempts for one slot while reading
OCR_SLOW_INTERVAL = 8.0   # after the plate has been unreadable for a while
REVERIFY_INTERVAL = 90.0  # re-read a locked plate occasionally to catch a car swap
MAX_OCR_SLOTS_PER_CYCLE = 2
MAX_ZOOM_SLOTS_PER_CYCLE = 3

STATUS_COLORS = {  # BGR
    "VACANT": (60, 180, 75),
    "DETECTING": (200, 200, 200),
    "READING_PLATE": (0, 200, 255),
    "OCCUPIED_NO_PLATE": (0, 215, 255),
    "VERIFYING": (255, 191, 0),
    "CORRECT_PARKING": (0, 200, 0),
    "WRONG_SLOT": (0, 140, 255),
    "UNAUTHORIZED": (0, 0, 255),
}


# ----------------------------------------------------------------------
# Geometry helpers
# ----------------------------------------------------------------------
def poly_to_px(points: List[List[float]], w: int, h: int) -> np.ndarray:
    pts = []
    for x, y in points:
        pts.append([x * w if x <= 1.0 else x, y * h if y <= 1.0 else y])
    return np.array(pts, dtype=np.float32)


def _convex(poly: np.ndarray) -> np.ndarray:
    return cv2.convexHull(poly.astype(np.float32)).reshape(-1, 2)


def _area(poly: np.ndarray) -> float:
    return float(abs(cv2.contourArea(poly.astype(np.float32)))) if len(poly) >= 3 else 0.0


def _rect_poly(x1, y1, x2, y2) -> np.ndarray:
    return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)


def _intersection(a: np.ndarray, b: np.ndarray) -> float:
    try:
        inter, _ = cv2.intersectConvexConvex(_convex(a), _convex(b))
        return float(inter)
    except Exception:
        return 0.0


def slot_vehicle_score(poly: np.ndarray, box: List[float]) -> Tuple[float, float, float]:
    """
    How strongly a vehicle box belongs to a slot polygon.
    Uses the lower part of the box (where the car touches the floor) because in
    angled CCTV views the top of a car visually overlaps the slot behind it.
    Returns (score, footprint_overlap, slot_coverage).
    """
    x1, y1, x2, y2 = box
    bh = max(1.0, y2 - y1)
    fp = _rect_poly(x1, y1 + 0.45 * bh, x2, y2)
    fp_area = max(1.0, _area(fp))
    poly_area = max(1.0, _area(poly))
    fp_ov = _intersection(poly, fp) / fp_area
    cover = _intersection(poly, _rect_poly(x1, y1, x2, y2)) / poly_area
    bottom_mid = (float((x1 + x2) / 2.0), float(y2 - 0.08 * bh))
    inside = cv2.pointPolygonTest(poly.astype(np.float32).reshape(-1, 1, 2), bottom_mid, False) >= 0
    score = max(fp_ov, 0.85 * cover) + (0.15 if inside else 0.0)
    return score, fp_ov, cover


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def draw_evidence(frame: np.ndarray, poly: np.ndarray, vehicle_box: Optional[List[float]], vtype: str,
                  slot_id: str, plate: Optional[str]) -> np.ndarray:
    """Full scene with the offending slot and vehicle highlighted and a red caption banner."""
    img = frame.copy()
    fh, fw = img.shape[:2]
    p = poly.astype(np.int32)
    overlay = img.copy()
    cv2.fillPoly(overlay, [p], (0, 0, 255))
    img = cv2.addWeighted(overlay, 0.18, img, 0.82, 0)
    cv2.polylines(img, [p], True, (0, 0, 255), max(2, fw // 400))
    if vehicle_box:
        x1, y1, x2, y2 = [int(v) for v in vehicle_box]
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), max(2, fw // 500))
    scale = max(0.6, fw / 1600.0)
    bar = int(46 * scale)
    cv2.rectangle(img, (0, 0), (fw, bar), (0, 0, 160), -1)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    txt = f"VIOLATION: {vtype.replace('_', ' ')} | SLOT {slot_id} | PLATE {plate or 'UNREADABLE'} | {ts}"
    cv2.putText(img, txt, (12, int(bar * 0.68)), cv2.FONT_HERSHEY_SIMPLEX, 0.6 * scale, (255, 255, 255), 2, cv2.LINE_AA)
    return img


def zoom_detect(detector, frame: np.ndarray, poly: np.ndarray, min_conf: float,
                existing: List[Dict[str, Any]], max_area_ratio: float = 0.5) -> Optional[Dict[str, Any]]:
    """
    Second, zoomed-in detection pass around one slot. Small or distant cars that the
    full-frame pass misses are much easier to see when the slot fills the model input.
    """
    fh, fw = frame.shape[:2]
    x, y, w, h = cv2.boundingRect(poly.astype(np.int32))
    if w <= 0 or h <= 0 or (w * h) > max_area_ratio * fw * fh:
        return None  # slot already large in frame: full-frame pass is enough
    mx, my = int(w * 0.35), int(h * 0.35)
    x1, y1 = max(0, x - mx), max(0, y - my)
    x2, y2 = min(fw, x + w + mx), min(fh, y + h + my)
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    det = detector.detect(crop, confidence_threshold=max(0.2, min_conf - 0.05),
                          classes_to_filter=VEHICLE_CLASSES, enable_tracking=False, imgsz=640)
    best = None
    for d in det["detections"]:
        bx = d["box"]
        box = [bx[0] + x1, bx[1] + y1, bx[2] + x1, bx[3] + y1]
        if any(_iou(box, e["box"]) > 0.5 for e in existing):
            continue
        score, fp_ov, cover = slot_vehicle_score(poly, box)
        if (fp_ov >= 0.30 or cover >= 0.45) and (best is None or score > best[0]):
            best = (score, {**d, "box": [round(v, 1) for v in box], "zoom": True})
    return best[1] if best else None


def warp_slot_patch(frame: np.ndarray, poly: np.ndarray, size: int = 96) -> Optional[np.ndarray]:
    """Top-down normalised grey patch of a slot, used for the empty-slot baseline comparison."""
    try:
        if len(poly) == 4:
            dst = np.array([[0, 0], [size - 1, 0], [size - 1, size - 1], [0, size - 1]], dtype=np.float32)
            M = cv2.getPerspectiveTransform(poly.astype(np.float32), dst)
            patch = cv2.warpPerspective(frame, M, (size, size))
        else:
            x, y, w, h = cv2.boundingRect(poly.astype(np.int32))
            crop = frame[max(0, y):y + h, max(0, x):x + w]
            if crop.size == 0:
                return None
            patch = cv2.resize(crop, (size, size))
        g = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        g = cv2.GaussianBlur(g, (5, 5), 0)
        return cv2.equalizeHist(g)
    except Exception:
        return None


def patch_changed(patch: np.ndarray, ref: np.ndarray) -> Tuple[bool, float, float]:
    diff = float(np.mean(cv2.absdiff(patch, ref)))
    a = patch.astype(np.float32) - patch.mean()
    b = ref.astype(np.float32) - ref.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum())) or 1.0
    ncc = float((a * b).sum() / denom)
    return (diff > 22.0 and ncc < 0.55), diff, ncc


# ----------------------------------------------------------------------
# Per-slot state
# ----------------------------------------------------------------------
class SlotState:
    def __init__(self, slot_id: str, points: List[List[float]], label: Optional[str] = None):
        self.slot_id = slot_id
        self.points = points
        self.label = label or slot_id
        self.occupied = False
        self.hits = 0
        self.misses = 0
        self.occupied_since: Optional[float] = None
        self.vehicle_box: Optional[List[float]] = None
        self.vehicle_class: Optional[str] = None
        self.det_confidence = 0.0
        self.occupancy_source: Optional[str] = None
        self.empty_ref: Optional[np.ndarray] = None
        self.appearance_diff: Optional[float] = None
        self.status = "VACANT"
        self.message = "Slot is empty"
        self.expectation: Optional[Dict[str, Any]] = None
        self.reset_session()

    def reset_session(self):
        self.reads: List[Dict[str, Any]] = []
        self.plate: Optional[str] = None
        self.raw_plate: Optional[str] = None
        self.plate_conf = 0.0
        self.plate_match = "none"
        self.match: Optional[Dict[str, Any]] = None
        self.locked_at: Optional[float] = None
        self.last_ocr_at = 0.0
        self.unreadable_reason: Optional[str] = None
        self.plate_crop_b64: Optional[str] = None
        self.mismatch_reads = 0
        self.verdict: Optional[str] = None
        self.violation_type: Optional[str] = None
        self.violation_id: Optional[str] = None
        self.review_logged = False

    def to_dict(self) -> Dict[str, Any]:
        now = time.time()
        exp = self.expectation or {}
        m = self.match or {}
        emp = m.get("employee") or {}
        return {
            "slot_id": self.slot_id,
            "slot_name": exp.get("slot_label") or self.slot_id,
            "label": self.label,
            "polygon": self.points,
            "occupancy_status": "occupied" if self.occupied else "vacant",
            "occupancy_source": self.occupancy_source if self.occupied else None,
            "occupied_seconds": int(now - self.occupied_since) if (self.occupied and self.occupied_since) else 0,
            "verification_status": self.status,
            "message": self.message,
            "vehicle_box": self.vehicle_box if self.occupied else None,
            "vehicle_class": self.vehicle_class if self.occupied else None,
            "detected_plate": self.plate,
            "raw_plate": self.raw_plate,
            "confidence": round(self.plate_conf, 2),
            "plate_match": self.plate_match if self.plate else None,
            "plate_reads": len(self.reads),
            "plate_crop_base64": self.plate_crop_b64 if self.occupied else None,
            "unreadable_reason": self.unreadable_reason if (self.occupied and not self.plate) else None,
            "expected_user": exp.get("user_name"),
            "expected_plate": exp.get("vehicle_number"),
            "expected_source": exp.get("source"),
            "actual_driver": emp.get("name"),
            "matched_user": _public_employee(emp, m.get("reservation")) if emp else None,
            "violation_type": self.violation_type,
            "violation_id": self.violation_id,
            "alert_triggered": bool(self.violation_id),
            "has_empty_baseline": self.empty_ref is not None,
        }


def _public_employee(emp: Optional[Dict[str, Any]], reservation: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    if not emp:
        return None
    return {
        "id": emp.get("id"),
        "employee_id": emp.get("employee_id"),
        "name": emp.get("name"),
        "email": emp.get("email"),
        "role": emp.get("role"),
        "department": emp.get("department"),
        "mobile_number": emp.get("mobile_number"),
        "vehicle_number": emp.get("vehicle_number"),
        "reserved_slot_today": IdentityService.get_instance().slot_name(reservation.get("slot_id")) if reservation else None,
        "slot_is_permanent": bool(reservation and reservation.get("source") == "manager_assigned"),
        "reserved_vehicle_today": reservation.get("vehicle_number") if reservation else None,
    }


# ----------------------------------------------------------------------
# Camera monitor
# ----------------------------------------------------------------------
class SlotMonitor:
    def __init__(self, manager, camera_id: str):
        self.manager = manager
        self.camera_id = camera_id
        self.detector = DetectionService.get_instance()
        self.anpr = ANPRService.get_instance()
        self.identity = IdentityService.get_instance()
        self.store = ViolationStore.get_instance()
        self.slots: Dict[str, SlotState] = {}
        self._lock = threading.RLock()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.last_cycle_ms = 0.0
        self.last_cycle_at: Optional[float] = None
        self.last_error: Optional[str] = None
        self.cycles = 0
        self.last_detections: List[Dict[str, Any]] = []
        self.frame_size: Tuple[int, int] = (0, 0)
        self._zoom_rr = 0
        self._capture_baseline_requested = False

    # -- lifecycle -------------------------------------------------------
    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, name=f"monitor-{self.camera_id}", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3.0)

    def request_baseline_capture(self):
        self._capture_baseline_requested = True

    def clear_baseline(self):
        with self._lock:
            for st in self.slots.values():
                st.empty_ref = None

    # -- config ----------------------------------------------------------
    def _camera(self):
        return self.manager.cameras.get(self.camera_id)

    def _sync_slots(self):
        cam = self._camera()
        layout = (cam.monitored_slots or []) if cam else []
        with self._lock:
            seen = set()
            for sp in layout:
                sid = sp.slot_id
                seen.add(sid)
                st = self.slots.get(sid)
                if st is None:
                    self.slots[sid] = SlotState(sid, sp.points, sp.label)
                elif st.points != sp.points:
                    st.points = sp.points
                    st.label = sp.label or sid
                    st.empty_ref = None  # geometry changed: old baseline no longer valid
            for sid in list(self.slots):
                if sid not in seen:
                    del self.slots[sid]

    # -- main loop -------------------------------------------------------
    def _run(self):
        last_frame_t = None
        while self.running:
            cam = self._camera()
            interval = max(0.5, float(getattr(cam, "analysis_interval", 1.5) or 1.5)) if cam else 2.0
            worker = self.manager.workers.get(self.camera_id)
            frame, frame_t = (worker.latest_frame() if worker else (None, None))
            if cam is None or not getattr(cam, "monitoring_enabled", True):
                time.sleep(1.0)
                continue
            if frame is None or frame_t == last_frame_t:
                time.sleep(0.3)
                continue
            last_frame_t = frame_t
            t0 = time.time()
            try:
                self._sync_slots()
                if self.slots:
                    self.process_frame(frame)
                self.last_error = None
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.last_error = str(e)
            self.last_cycle_ms = round((time.time() - t0) * 1000, 1)
            self.last_cycle_at = time.time()
            self.cycles += 1
            time.sleep(max(0.05, interval - (time.time() - t0)))

    # -- one analysis cycle ---------------------------------------------
    def process_frame(self, frame: np.ndarray):
        cam = self._camera()
        fh, fw = frame.shape[:2]
        self.frame_size = (fw, fh)
        min_conf = float(getattr(cam, "min_confidence", 0.35) or 0.35)
        imgsz = int(getattr(cam, "detection_imgsz", 0) or 0) or (1280 if max(fw, fh) >= 1200 else 640)

        det = self.detector.detect(frame, confidence_threshold=min_conf, classes_to_filter=VEHICLE_CLASSES,
                                   enable_tracking=False, imgsz=imgsz)
        vehicles = [d for d in det["detections"] if d["class_name"] in VEHICLE_CLASSES]

        with self._lock:
            states = list(self.slots.values())
        polys = {st.slot_id: poly_to_px(st.points, fw, fh) for st in states}

        assigned = self._assign(states, polys, vehicles)

        # Zoomed second pass for empty slots (small / distant / partly hidden cars)
        empty = [st for st in states if st.slot_id not in assigned]
        if empty:
            empty.sort(key=lambda s: (not s.occupied, s.slot_id))  # currently-occupied first (avoid false exits)
            start = self._zoom_rr % max(1, len(empty))
            batch = (empty[start:] + empty[:start])[:MAX_ZOOM_SLOTS_PER_CYCLE]
            self._zoom_rr += 1
            for st in batch:
                found = self._zoom_detect(frame, polys[st.slot_id], min_conf, vehicles)
                if found:
                    vehicles.append(found)
                    assigned[st.slot_id] = (found, 1.0)

        self.last_detections = [{"class_name": v["class_name"], "confidence": v["confidence"], "box": v["box"]}
                                for v in vehicles]

        # Empty-slot baseline comparison
        appearance: Dict[str, bool] = {}
        for st in states:
            patch = warp_slot_patch(frame, polys[st.slot_id])
            if patch is None:
                continue
            if self._capture_baseline_requested:
                st.empty_ref = patch
                continue
            if st.empty_ref is not None:
                changed, diff, _ = patch_changed(patch, st.empty_ref)
                st.appearance_diff = round(diff, 1)
                appearance[st.slot_id] = changed
                # Slowly follow lighting drift while the slot is confirmed empty by both signals
                if not st.occupied and st.slot_id not in assigned and not changed:
                    st.empty_ref = cv2.addWeighted(st.empty_ref, 0.95, patch, 0.05, 0)
        self._capture_baseline_requested = False

        now = time.time()
        for st in states:
            veh = assigned.get(st.slot_id)
            obs = veh is not None or appearance.get(st.slot_id, False)
            with self._lock:
                self._update_occupancy(st, obs, veh, now)

        # Plate reading for parked cars (bounded per cycle)
        need = [st for st in states if st.occupied or st.hits >= 1]
        need = [st for st in need if self._needs_ocr(st, now)]
        need.sort(key=lambda s: s.last_ocr_at)
        for st in need[:MAX_OCR_SLOTS_PER_CYCLE]:
            self._read_plate(st, frame, polys[st.slot_id], now)

        for st in states:
            with self._lock:
                self._evaluate(st, frame, polys, now)

    # -- steps -----------------------------------------------------------
    def _assign(self, states, polys, vehicles) -> Dict[str, Tuple[Dict[str, Any], float]]:
        pairs = []
        for st in states:
            poly = polys[st.slot_id]
            if len(poly) < 3:
                continue
            for vi, v in enumerate(vehicles):
                score, fp_ov, cover = slot_vehicle_score(poly, v["box"])
                if fp_ov >= 0.30 or cover >= 0.45:
                    pairs.append((score, st.slot_id, vi))
        pairs.sort(key=lambda t: -t[0])
        used_v, out = set(), {}
        for score, sid, vi in pairs:
            if sid in out or vi in used_v:
                continue
            out[sid] = (vehicles[vi], score)
            used_v.add(vi)
        return out

    def _zoom_detect(self, frame, poly, min_conf, existing) -> Optional[Dict[str, Any]]:
        return zoom_detect(self.detector, frame, poly, min_conf, existing)

    def _update_occupancy(self, st: SlotState, obs: bool, veh, now: float):
        if veh is not None:
            v, _ = veh
            st.vehicle_box = v["box"]
            st.vehicle_class = v["class_name"]
            st.det_confidence = v["confidence"]
            st.occupancy_source = "detector"
        elif obs:
            st.occupancy_source = "appearance"
            st.vehicle_box = None

        if obs:
            st.hits += 1
            st.misses = 0
        else:
            st.misses += 1
            st.hits = 0

        if not st.occupied and st.hits >= ENTER_CYCLES:
            st.occupied = True
            st.occupied_since = now - ENTER_CYCLES * 1.0
            # keep reads gathered during the pending cycles
        elif st.occupied and st.misses >= EXIT_CYCLES:
            self._end_session(st, now)
        elif not st.occupied and st.hits == 0 and st.reads:
            st.reset_session()  # a car passed by without parking

    def _end_session(self, st: SlotState, now: float):
        if st.violation_id:
            try:
                self.store.update(st.violation_id, {"vehicle_left_at": datetime.now().isoformat(timespec="seconds")})
            except Exception as e:
                print(f"[SlotMonitor] could not mark vehicle left: {e}")
        st.occupied = False
        st.occupied_since = None
        st.vehicle_box = None
        st.occupancy_source = None
        st.reset_session()

    def _needs_ocr(self, st: SlotState, now: float) -> bool:
        cam = self._camera()
        if cam is not None and not getattr(cam, "enable_plate_recognition", True):
            return False
        if st.plate:
            return (now - st.last_ocr_at) >= REVERIFY_INTERVAL
        occupied_for = (now - st.occupied_since) if st.occupied_since else 0
        gap = OCR_SLOW_INTERVAL if (occupied_for > 60 and len(st.reads) == 0) else OCR_INTERVAL
        return (now - st.last_ocr_at) >= gap

    def _vehicle_crop(self, st: SlotState, frame, poly) -> Optional[np.ndarray]:
        fh, fw = frame.shape[:2]
        if st.vehicle_box:
            x1, y1, x2, y2 = st.vehicle_box
            px, py = (x2 - x1) * 0.08, (y2 - y1) * 0.08
            x1, y1 = int(max(0, x1 - px)), int(max(0, y1 - py))
            x2, y2 = int(min(fw, x2 + px)), int(min(fh, y2 + py))
        else:
            x, y, w, h = cv2.boundingRect(poly.astype(np.int32))
            x1, y1, x2, y2 = max(0, x), max(0, y), min(fw, x + w), min(fh, y + h)
        crop = frame[y1:y2, x1:x2]
        return crop if crop.size else None

    def _read_plate(self, st: SlotState, frame, poly, now: float):
        st.last_ocr_at = now
        crop = self._vehicle_crop(st, frame, poly)
        if crop is None:
            return
        try:
            res = self.anpr.recognize_plate(crop)
        except Exception as e:
            print(f"[SlotMonitor] ANPR error on {st.slot_id}: {e}")
            return
        with self._lock:
            if not (res and res.is_valid_format and res.plate_text) or float(res.confidence) < 0.30:
                st.unreadable_reason = getattr(res, "unreadable_reason", None) or "Plate not visible"
                if st.plate:
                    return
                if res and res.plate_crop_base64 and not st.plate_crop_b64:
                    st.plate_crop_b64 = res.plate_crop_base64
                return

            plate_crop = None
            if res.plate_box:
                bx1, by1, bx2, by2 = [int(v) for v in res.plate_box]
                plate_crop = crop[max(0, by1):by2, max(0, bx1):bx2]
            read = {"text": res.plate_text, "norm": normalize_plate(res.plate_text), "conf": float(res.confidence),
                    "t": now, "vehicle_crop": crop.copy(), "plate_crop": plate_crop,
                    "plate_b64": res.plate_crop_base64}

            if st.plate:
                # Re-verification of a locked plate: two clearly different reads => car swapped
                if plate_distance(read["norm"], normalize_plate(st.raw_plate or st.plate)) > 1.5:
                    st.mismatch_reads += 1
                    if st.mismatch_reads >= 2:
                        if st.violation_id:
                            self.store.update(st.violation_id, {"vehicle_left_at": datetime.now().isoformat(timespec="seconds")})
                        st.reset_session()
                        st.occupied_since = now
                        st.reads.append(read)
                else:
                    st.mismatch_reads = 0
                return

            st.reads.append(read)
            del st.reads[:-MAX_READS]
            self._try_lock_plate(st)

    def _try_lock_plate(self, st: SlotState):
        reads = st.reads
        if not reads:
            return
        weights: Dict[str, float] = defaultdict(float)
        counts: Counter = Counter()
        for r in reads:
            weights[r["norm"]] += 0.5 + r["conf"]
            counts[r["norm"]] += 1
        top, _ = max(weights.items(), key=lambda kv: kv[1])
        top_count = counts[top]

        # Character-level consensus over reads of the most common length
        consensus = None
        lens = Counter(len(r["norm"]) for r in reads)
        L, nL = lens.most_common(1)[0]
        if nL >= 3:
            chars = []
            for i in range(L):
                c = defaultdict(float)
                for r in reads:
                    if len(r["norm"]) == L:
                        c[r["norm"][i]] += 0.5 + r["conf"]
                chars.append(max(c.items(), key=lambda kv: kv[1])[0])
            consensus = "".join(chars)

        candidate, agreeing = top, top_count
        if top_count < MIN_AGREEING_READS and consensus:
            ok, _, _ = self.anpr.validate_indian_plate(consensus)
            if ok:
                candidate, agreeing = consensus, nL

        match = self.identity.match_plate(candidate)
        enough = agreeing >= MIN_AGREEING_READS and agreeing / float(len(reads)) >= 0.5
        if not enough and len(reads) >= MAX_READS and top_count >= 2:
            enough = True
        if not enough:
            st.raw_plate = candidate  # show provisional reading
            st.plate_conf = max(r["conf"] for r in reads if r["norm"] == top)
            return

        same = [r for r in reads if r["norm"] == candidate] or reads
        best = max(same, key=lambda r: r["conf"])
        _, formatted, _ = self.anpr.validate_indian_plate(candidate)
        st.raw_plate = formatted or best["text"]
        st.plate = match["plate"] if match["match"] in ("exact", "fuzzy") else st.raw_plate
        st.plate_match = match["match"]
        st.match = match
        st.plate_conf = round(sum(r["conf"] for r in same) / len(same), 3)
        st.plate_crop_b64 = best.get("plate_b64")
        st.locked_at = time.time()
        st.unreadable_reason = None

    def _evaluate(self, st: SlotState, frame, polys, now: float):
        cam = self._camera()
        st.expectation = self.identity.slot_expectation(st.slot_id)
        exp = st.expectation

        if not st.occupied:
            if st.hits >= 1:
                st.status, st.message = "DETECTING", "Vehicle entering slot..."
            else:
                st.status = "VACANT"
                st.message = f"Empty - booked by {exp['user_name']}" if exp.get("user_name") else "Empty"
            return

        dwell = int(getattr(cam, "violation_dwell_seconds", 20) or 20)
        parked_for = now - (st.occupied_since or now)

        if not st.plate:
            if st.raw_plate:
                st.status, st.message = "READING_PLATE", f"Reading plate... ({st.raw_plate}?)"
            elif parked_for > 45:
                st.status = "OCCUPIED_NO_PLATE"
                st.message = f"Vehicle parked - plate unreadable ({st.unreadable_reason or 'not visible'})"
                if getattr(cam, "flag_unreadable_plates", False) and not st.review_logged and parked_for > 120:
                    st.review_logged = True
                    self._record_violation(st, frame, polys, "UNREADABLE_PLATE", "low",
                                           f"Vehicle parked in {exp.get('slot_label') or st.slot_id} for over 2 minutes but its plate could not be read.")
            else:
                st.status, st.message = "READING_PLATE", "Vehicle parked - reading number plate..."
            return

        verdict, vtype, severity, msg = self._classify(st, exp)
        st.verdict = verdict
        st.violation_type = vtype

        if verdict == "CORRECT_PARKING":
            st.status, st.message = verdict, msg
            return
        if parked_for < dwell:
            st.status = "VERIFYING"
            st.message = f"{msg} - confirming ({int(dwell - parked_for)}s)"
            return
        st.status, st.message = verdict, msg
        if not st.violation_id:
            self._record_violation(st, frame, polys, vtype, severity, msg)

    def _classify(self, st: SlotState, exp: Dict[str, Any]):
        plate = st.plate
        sname = exp.get("slot_label") or st.slot_id
        n = normalize_plate(plate)
        allowed = [normalize_plate(p) for p in exp.get("allowed_plates") or []]
        if n and any(n == a or plate_distance(n, a) <= 0.5 for a in allowed):
            who = exp.get("user_name") or "owner"
            return "CORRECT_PARKING", None, None, f"Correct: {plate} ({who}) in {sname}"

        m = st.match or {}
        emp = m.get("employee")
        own_res = m.get("reservation")
        if own_res and (own_res.get("slot_id") or "").upper() == st.slot_id.upper():
            return "CORRECT_PARKING", None, None, f"Correct: {plate} ({own_res.get('user_name')}) in {sname}"
        if emp and exp.get("user_id") and emp.get("id") == exp.get("user_id"):
            return "CORRECT_PARKING", None, None, f"Correct: {plate} ({emp.get('name')}) in {sname}"
        expected_txt = (f"{'reserved for' if exp.get('source') == 'manager_assigned' else 'booked by'} {exp['user_name']} ({exp.get('vehicle_number') or '-'})"
                        if exp.get("user_name") else "not booked by anyone today")

        if own_res and (own_res.get("slot_id") or "").upper() != st.slot_id.upper():
            return ("WRONG_SLOT", "WRONG_SLOT", "high",
                    f"Wrong slot: {plate} ({own_res.get('user_name')}) "
                    f"{'has reserved slot' if own_res.get('source') == 'manager_assigned' else 'belongs in'} "
                    f"{self.identity.slot_name(own_res.get('slot_id'))}, "
                    f"but is parked in {sname} - {expected_txt}")
        if emp:
            return ("UNAUTHORIZED", "NO_BOOKING", "medium",
                    f"No booking: {plate} ({emp.get('name')}) parked in {sname} without a reservation - {expected_txt}")
        return ("UNAUTHORIZED", "UNKNOWN_VEHICLE", "high",
                f"Unknown vehicle {plate} parked in {sname} - {expected_txt}")

    # -- evidence & alerts -----------------------------------------------
    def _record_violation(self, st: SlotState, frame, polys, vtype: str, severity: str, msg: str):
        cam = self._camera()
        vid = f"vio_{datetime.now().strftime('%Y%m%d%H%M%S')}_{st.slot_id}"
        snapshot = self._evidence_frame(frame, polys, st, vtype)
        best = None
        if st.reads:
            same = [r for r in st.reads if normalize_plate(r["text"]) == normalize_plate(st.raw_plate or "")]
            best = max(same or st.reads, key=lambda r: r["conf"])
        veh_crop = best["vehicle_crop"] if best else self._vehicle_crop(st, frame, polys[st.slot_id])
        plate_crop = best["plate_crop"] if best else None
        if plate_crop is not None and plate_crop.size and plate_crop.shape[1] < 240:
            s = 240.0 / plate_crop.shape[1]
            plate_crop = cv2.resize(plate_crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)

        exp = st.expectation or {}
        m = st.match or {}
        rec = {
            "id": vid,
            "camera_id": self.camera_id,
            "camera_name": cam.name if cam else self.camera_id,
            "slot_id": st.slot_id,
            "slot_label": exp.get("slot_label") or st.slot_id,
            "floor": exp.get("floor"),
            "violation_type": vtype,
            "severity": severity,
            "detected_plate": st.plate,
            "raw_plate": st.raw_plate,
            "plate_confidence": st.plate_conf,
            "plate_match": st.plate_match if st.plate else None,
            "plate_reads": len(st.reads),
            "matched_user": _public_employee(m.get("employee"), m.get("reservation")),
            "expected": {
                "source": exp.get("source"),
                "user_id": exp.get("user_id"),
                "user_name": exp.get("user_name"),
                "vehicle_number": exp.get("vehicle_number"),
                "email": (exp.get("employee") or {}).get("email"),
            },
            "message": msg,
            "snapshot_url": ViolationStore.save_image(snapshot, vid, "scene"),
            "vehicle_crop_url": ViolationStore.save_image(veh_crop, vid, "vehicle"),
            "plate_crop_url": ViolationStore.save_image(plate_crop, vid, "plate", quality=95),
            "occupied_since": datetime.fromtimestamp(st.occupied_since).isoformat(timespec="seconds") if st.occupied_since else None,
        }
        try:
            saved = self.store.create(rec)
            st.violation_id = saved["id"]
            print(f"[SlotMonitor] VIOLATION {vtype} slot={st.slot_id} plate={st.plate} cam={self.camera_id}")
            notifier.notify_violation(saved)
        except Exception as e:
            print(f"[SlotMonitor] failed to record violation: {e}")

    def _evidence_frame(self, frame, polys, st: SlotState, vtype: str) -> np.ndarray:
        return draw_evidence(frame, polys[st.slot_id], st.vehicle_box, vtype, (st.expectation or {}).get('slot_label') or st.slot_id, st.plate)

    # -- live overlay & status --------------------------------------------
    def draw_overlay(self, frame: np.ndarray) -> np.ndarray:
        img = frame.copy()
        fh, fw = img.shape[:2]
        scale = max(0.8, fw / 1000.0)
        thick = max(2, int(fw / 640))
        with self._lock:
            states = [((st.expectation or {}).get("slot_label") or st.slot_id, list(st.points), st.status, st.plate or st.raw_plate,
                       st.vehicle_box if st.occupied else None) for st in self.slots.values()]
        overlay = img.copy()
        for sid, pts, status, plate, vbox in states:
            poly = poly_to_px(pts, fw, fh).astype(np.int32)
            color = STATUS_COLORS.get(status, (200, 200, 200))
            cv2.fillPoly(overlay, [poly], color)
        img = cv2.addWeighted(overlay, 0.18, img, 0.82, 0)
        for sid, pts, status, plate, vbox in states:
            poly = poly_to_px(pts, fw, fh).astype(np.int32)
            color = STATUS_COLORS.get(status, (200, 200, 200))
            cv2.polylines(img, [poly], True, color, thick, cv2.LINE_AA)
            if vbox:
                x1, y1, x2, y2 = [int(v) for v in vbox]
                cv2.rectangle(img, (x1, y1), (x2, y2), color, max(1, thick - 1))
            cx, cy = int(poly[:, 0].mean()), int(poly[:, 1].mean())
            lines = [sid, plate or status.replace("_", " ")]
            for i, t in enumerate(lines):
                fs = (0.75 if i == 0 else 0.55) * scale
                (tw, th), _ = cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, fs, 2)
                y = cy + int(i * th * 1.8)
                cv2.rectangle(img, (cx - tw // 2 - 4, y - th - 4), (cx + tw // 2 + 4, y + 4), (20, 20, 20), -1)
                cv2.putText(img, t, (cx - tw // 2, y), cv2.FONT_HERSHEY_SIMPLEX, fs, color, 2, cv2.LINE_AA)
        return img

    def status(self) -> Dict[str, Any]:
        with self._lock:
            slots = [st.to_dict() for st in self.slots.values()]
        occupied = sum(1 for s in slots if s["occupancy_status"] == "occupied")
        violations = sum(1 for s in slots if s["verification_status"] in ("WRONG_SLOT", "UNAUTHORIZED"))
        return {
            "camera_id": self.camera_id,
            "running": self.running,
            "cycles": self.cycles,
            "last_cycle_ms": self.last_cycle_ms,
            "last_cycle_at": datetime.fromtimestamp(self.last_cycle_at).isoformat(timespec="seconds") if self.last_cycle_at else None,
            "last_error": self.last_error,
            "frame_size": list(self.frame_size),
            "total_slots": len(slots),
            "occupied_count": occupied,
            "vacant_count": len(slots) - occupied,
            "violation_count": violations,
            "slots": sorted(slots, key=lambda s: s["slot_id"]),
            "detections": self.last_detections,
        }
