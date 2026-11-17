"""
One-shot parking verification on a still photo ("Photo test").

The live CCTV pipeline (services/slot_monitor.py) gets accuracy from many frames.
A single photo can't vote across frames, so this pipeline spends more effort per image:
  - vehicle detection at high resolution with test-time augmentation,
  - a zoomed second pass on slots that still look empty,
  - a plate-only fallback for close-up photos where the car fills the frame,
  - several plate readings per vehicle (original, upscaled, bumper region) combined by vote,
  - matching against known company plates (exact / one look-alike character).
The same slot geometry and decision rules as the live monitor are used, and violations
can optionally be saved to the Violations log with evidence images.
"""
import base64
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from services.identity_service import IdentityService, normalize_plate, plate_distance
from services.slot_monitor import (
    VEHICLE_CLASSES, STATUS_COLORS, poly_to_px, slot_vehicle_score, zoom_detect, draw_evidence, _public_employee,
)

PHOTO_CAMERA_ID = "photo_test"


def _decode(image_base64: str) -> Optional[np.ndarray]:
    try:
        raw = base64.b64decode(image_base64.split(",")[-1])
        return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


def _b64(img: Optional[np.ndarray], min_w: int = 0, max_w: int = 0, quality: int = 90) -> Optional[str]:
    if img is None or getattr(img, "size", 0) == 0:
        return None
    h, w = img.shape[:2]
    if min_w and w < min_w:
        s = min(4.0, min_w / float(w))
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_CUBIC)
    elif max_w and w > max_w:
        s = max_w / float(w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return ("data:image/jpeg;base64," + base64.b64encode(buf).decode()) if ok else None


def _slot_field(item: Any, name: str, default=None):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def classify_plate(slot_id: str, plate: str, identity: IdentityService) -> Dict[str, Any]:
    """Same decision rules as the live monitor."""
    exp = identity.slot_expectation(slot_id)
    sname = exp.get("slot_label") or slot_id
    match = identity.match_plate(plate)
    shown = match["plate"] if match["match"] in ("exact", "fuzzy") else plate
    n = normalize_plate(shown)
    allowed = [normalize_plate(p) for p in exp.get("allowed_plates") or []]
    emp, own_res = match.get("employee"), match.get("reservation")
    expected_txt = (f"{'reserved for' if exp.get('source') == 'manager_assigned' else 'booked by'} {exp['user_name']} ({exp.get('vehicle_number') or '-'})"
                    if exp.get("user_name") else "not booked by anyone today")

    if (any(n == a or plate_distance(n, a) <= 0.5 for a in allowed)
            or (own_res and (own_res.get("slot_id") or "").upper() == slot_id.upper())
            or (emp and exp.get("user_id") and emp.get("id") == exp.get("user_id"))):
        status, vtype, sev = "CORRECT_PARKING", None, None
        msg = f"Correct: {shown} ({exp.get('user_name') or (emp or {}).get('name')}) in {sname}"
    elif own_res:
        status, vtype, sev = "WRONG_SLOT", "WRONG_SLOT", "high"
        msg = (f"Wrong slot: {shown} ({own_res.get('user_name')}) "
               f"{'has reserved slot' if own_res.get('source') == 'manager_assigned' else 'booked'} "
               f"{identity.slot_name(own_res.get('slot_id'))}, but parked in {sname} - {expected_txt}")
    elif emp:
        status, vtype, sev = "UNAUTHORIZED", "NO_BOOKING", "medium"
        msg = f"No booking: {shown} ({emp.get('name')}) parked in {sname} - {expected_txt}"
    else:
        status, vtype, sev = "UNAUTHORIZED", "UNKNOWN_VEHICLE", "high"
        msg = f"Unknown vehicle {shown} parked in {sname} - {expected_txt}"
    return {"status": status, "violation_type": vtype, "severity": sev, "message": msg, "plate": shown,
            "match": match, "expectation": exp}


def read_plate_thorough(anpr, identity: IdentityService, crop: np.ndarray, thorough: bool = True) -> Dict[str, Any]:
    """
    Reads a vehicle's plate several ways and votes:
      pass 1: the vehicle crop as is
      pass 2: upscaled (small / distant cars)
      pass 3: bumper region only, upscaled
    Stops early when pass 1 already gives a confident read of a known company plate.
    """
    attempts: List[Tuple[Any, Optional[np.ndarray]]] = []
    first = None

    def run(img, budget):
        nonlocal first
        r = anpr.recognize_plate(img, max_ocr_calls=budget)
        if first is None:
            first = r
        if r and r.is_valid_format and r.plate_text:
            pc = None
            if r.plate_box:
                x1, y1, x2, y2 = [int(v) for v in r.plate_box]
                pc = img[max(0, y1):y2, max(0, x1):x2]
            attempts.append((r, pc))
        return r

    run(crop, 8)
    confident = bool(attempts) and attempts[0][0].confidence >= 0.65 and \
        identity.match_plate(attempts[0][0].plate_text)["match"] == "exact"
    if thorough and not confident:
        h, w = crop.shape[:2]
        # Bumper region first: smaller image, so faster, and where the plate usually is
        lower = crop[int(h * 0.4):, :]
        if lower.size:
            lw = lower.shape[1]
            s = min(2.5, max(1.0, 900.0 / max(1, lw)))
            run(cv2.resize(lower, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC) if s > 1 else lower, 4)
        good = any(r.confidence >= 0.6 for r, _ in attempts)
        if w < 1000 and not good:
            s = min(3.0, 1000.0 / max(1, w))
            run(cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC), 4)

    if not attempts:
        guess = getattr(first, "plate_crop_base64", None)
        if not guess:  # best-guess plate region so the admin can read it themselves
            try:
                boxes = anpr._contour_plate_boxes(crop, 1)
                h, w = crop.shape[:2]
                x1, y1, x2, y2 = boxes[0] if boxes else [int(w * 0.2), int(h * 0.55), int(w * 0.8), int(h * 0.95)]
                guess = _b64(crop[y1:y2, x1:x2], min_w=320)
            except Exception:
                guess = None
        return {"plate": None, "confidence": 0.0, "reads": 0, "plate_img": None,
                "plate_b64": guess,
                "reason": getattr(first, "unreadable_reason", None) or "Plate not visible"}

    # Every valid reading so far: (normalized text, confidence)
    reads: List[Tuple[str, float]] = []
    for r, _ in attempts:
        reads.append((normalize_plate(r.plate_text), float(r.confidence)))
        for c in r.candidates[1:]:
            reads.append((normalize_plate(c.plate_text), 0.5 * float(c.confidence)))
    best_r, best_pc = max(attempts, key=lambda t: t[0].confidence)

    # Unsure? Re-read the tight plate crop bigger and with more image treatments.
    if thorough and best_pc is not None and best_pc.size and best_r.confidence < 0.6:
        reads += _refine_plate_reads(anpr, best_pc)

    plate, conf, support = _vote_plate(anpr, identity, reads)
    same = [(r, pc) for r, pc in attempts if normalize_plate(r.plate_text) == plate]
    if same:
        best_r, best_pc = max(same, key=lambda t: t[0].confidence)
    plate_b64 = _b64(best_pc, min_w=320) if best_pc is not None else best_r.plate_crop_base64
    known = identity.match_plate(plate)["match"] == "exact" if plate else False

    if not plate or (conf < MIN_PLATE_CONFIDENCE and not (known and conf >= 0.2)):
        # Too unsure to act on: show the plate photo + the AI's best guess for the admin to confirm
        guess = _format_plate(anpr, plate) if plate else None
        return {"plate": None, "confidence": conf, "reads": support, "plate_img": best_pc, "plate_b64": plate_b64,
                "suggested_plate": guess,
                "reason": f"Plate unclear - AI guess {guess} ({round(conf * 100)}% sure), please confirm" if guess else "Plate unclear"}
    return {"plate": _format_plate(anpr, plate), "confidence": conf, "reads": support, "total_reads": len(reads),
            "plate_img": best_pc, "plate_b64": plate_b64, "reason": None}


MIN_PLATE_CONFIDENCE = 0.40  # below this a reading is shown as a suggestion, never used for a violation


def _format_plate(anpr, norm: str) -> str:
    ok, formatted, _ = anpr.validate_indian_plate(norm)
    return formatted if (ok and formatted) else norm


def _refine_plate_reads(anpr, plate_img: np.ndarray) -> List[Tuple[str, float]]:
    """OCR the plate crop at 2x/3x with several image treatments; returns valid readings."""
    out: List[Tuple[str, float]] = []
    h, w = plate_img.shape[:2]
    for target in (480, 760):
        s = max(1.0, min(5.0, target / float(max(1, w))))
        img = cv2.resize(plate_img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
        img = cv2.copyMakeBorder(img, 12, 12, 12, 12, cv2.BORDER_REPLICATE)  # OCR misses edge characters
        for variant in ("base", "clahe", "sharpened", "otsu", "inverted", "morph_close"):
            try:
                boxes = anpr.ocr_service.read_text_boxes(img, [variant])
            except Exception:
                continue
            for cand in anpr._group_text_boxes(boxes):
                ok, norm, _ = anpr.validate_indian_plate(cand["text"])
                if ok and norm:
                    out.append((normalize_plate(norm), float(cand["confidence"])))
    return out


def _vote_plate(anpr, identity: IdentityService, reads: List[Tuple[str, float]]) -> Tuple[Optional[str], float, int]:
    """
    Combines readings: whole-plate votes plus a character-by-character vote over readings of the
    most common length (fixes single wrong characters such as 3->2 or 9->2 in blurry crops).
    Returns (plate, confidence 0..1, number of agreeing readings).
    """
    reads = [(t, c) for t, c in reads if t and len(t) >= 6]
    if not reads:
        return None, 0.0, 0
    whole: Dict[str, float] = defaultdict(float)
    for t, c in reads:
        whole[t] += c
    lens: Dict[int, float] = defaultdict(float)
    for t, c in reads:
        lens[len(t)] += c
    L = max(lens.items(), key=lambda kv: kv[1])[0]
    same_len = [(t, c) for t, c in reads if len(t) == L]
    chars, char_conf = [], []
    for i in range(L):
        votes: Dict[str, float] = defaultdict(float)
        for t, c in same_len:
            votes[t[i]] += c + 0.05
        ch, v = max(votes.items(), key=lambda kv: kv[1])
        chars.append(ch)
        char_conf.append(v / max(1e-6, sum(votes.values())))
    consensus = "".join(chars)
    cands = {consensus: None, **{t: None for t in whole}}

    def score(t):
        sc = whole.get(t, 0.0)
        if t == consensus and len(same_len) >= 3:
            sc += 0.5 * sum(c for _, c in same_len) / len(same_len)
        if identity.match_plate(t)["match"] == "exact":
            sc += 0.6
        ok, _, _ = anpr.validate_indian_plate(t)
        return sc if ok else sc * 0.3

    best = max(cands, key=score)
    agreeing = [c for t, c in reads if t == best]
    if agreeing:
        conf = sum(agreeing) / len(agreeing)
        conf = min(1.0, conf * (0.85 + 0.05 * min(3, len(agreeing))))  # agreement across reads builds trust
    else:  # consensus string nobody read in full: trust = weakest character's agreement
        conf = min(char_conf) * (sum(c for _, c in same_len) / len(same_len))
    return best, round(float(conf), 3), max(1, len(agreeing))


class ParkingVerifier:
    _instance: Optional['ParkingVerifier'] = None

    @classmethod
    def get_instance(cls) -> 'ParkingVerifier':
        if cls._instance is None:
            cls._instance = ParkingVerifier()
        return cls._instance

    def __init__(self):
        self.identity = IdentityService.get_instance()

    def get_today_reservations(self, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
        """Today's active reservations only (never stale or demo data)."""
        out = []
        for r in self.identity.reservations_for(date_str):
            emp = self.identity.employee_by_id(r["user_id"]) or {}
            out.append({**r, "employee_name": emp.get("name"), "employee_email": emp.get("email"),
                        "registered_vehicle": emp.get("vehicle_number")})
        return out

    # ------------------------------------------------------------------
    def verify_multi_slots(self, image_base64: str, monitored_slots: List[Any], camera_id: str = PHOTO_CAMERA_ID,
                           record: bool = False, thorough: bool = True, camera_name: str = "Photo test") -> Dict[str, Any]:
        from services.detection_service import DetectionService
        from services.anpr_service import ANPRService
        from services.violation_store import ViolationStore
        from services import notifier
        t0 = time.time()
        detector = DetectionService.get_instance()
        anpr = ANPRService.get_instance()

        frame = _decode(image_base64)
        if frame is None:
            return {"total_slots": len(monitored_slots), "occupied_count": 0, "vacant_count": 0,
                    "violation_count": 0, "slots": [], "violations": [], "detections": [], "error": "Invalid image"}
        fh, fw = frame.shape[:2]
        if max(fh, fw) > 2400:  # phone photos: plenty of detail at 2400 px, much faster
            s = 2400.0 / max(fh, fw)
            frame = cv2.resize(frame, (int(fw * s), int(fh * s)), interpolation=cv2.INTER_AREA)
            fh, fw = frame.shape[:2]

        det = detector.detect(frame, confidence_threshold=0.25, classes_to_filter=VEHICLE_CLASSES,
                              enable_tracking=False, imgsz=1280 if max(fw, fh) >= 900 else 640, augment=thorough)
        vehicles = [d for d in det["detections"] if d["class_name"] in VEHICLE_CLASSES]

        slots = [(str(_slot_field(s, "slot_id", "")).upper(), _slot_field(s, "points", [])) for s in monitored_slots]
        polys = {sid: poly_to_px(pts, fw, fh) for sid, pts in slots if len(pts) >= 3}

        # 1. assign detected vehicles to slots (footprint overlap, one vehicle per slot)
        pairs = []
        for sid, poly in polys.items():
            for vi, v in enumerate(vehicles):
                score, fp_ov, cover = slot_vehicle_score(poly, v["box"])
                if fp_ov >= 0.30 or cover >= 0.45:
                    pairs.append((score, sid, vi))
        pairs.sort(key=lambda t: -t[0])
        assigned: Dict[str, Dict[str, Any]] = {}
        used = set()
        for _, sid, vi in pairs:
            if sid not in assigned and vi not in used:
                assigned[sid] = {**vehicles[vi], "source": "detector"}
                used.add(vi)

        # 2. zoomed second pass on slots that still look empty
        for sid in [s for s in polys if s not in assigned][:8]:
            found = zoom_detect(detector, frame, polys[sid], 0.25, vehicles, max_area_ratio=0.6)
            if found:
                vehicles.append(found)
                assigned[sid] = {**found, "source": "zoom"}

        # 3. close-up photos: a clearly readable plate inside an empty slot means a car is there
        plate_only: Dict[str, Dict[str, Any]] = {}
        if thorough:
            for sid in [s for s in polys if s not in assigned][:4]:
                x, y, w, h = cv2.boundingRect(polys[sid].astype(np.int32))
                if vehicles and (w * h) < 0.2 * fw * fh:
                    continue  # only for close-ups (big slot) or when no vehicle was found at all
                crop = frame[max(0, y):y + h, max(0, x):x + w]
                if crop.size == 0:
                    continue
                r = read_plate_thorough(anpr, self.identity, crop, thorough=False)
                if r["plate"] and r["confidence"] >= 0.5:
                    assigned[sid] = {"box": [float(max(0, x)), float(max(0, y)), float(x + w), float(y + h)],
                                     "class_name": "car", "confidence": 0.0, "source": "plate"}
                    plate_only[sid] = r

        store = ViolationStore.get_instance() if record else None
        annotated = frame.copy()
        results: List[Dict[str, Any]] = []
        for sid, pts in slots:
            poly = polys.get(sid)
            exp = self.identity.slot_expectation(sid)
            sname = exp.get("slot_label") or sid
            base = {"slot_id": sid, "slot_name": sname, "polygon": pts, "expected_plate": exp.get("vehicle_number"),
                    "expected_user": exp.get("user_name"), "expected_source": exp.get("source"),
                    "floor": exp.get("floor"), "alert_triggered": False, "violation_id": None,
                    "violation_type": None, "matched_user": None, "actual_driver": None,
                    "vehicle_crop_base64": None, "plate_crop_base64": None, "unreadable_reason": None}
            veh = assigned.get(sid)
            if veh is None or poly is None:
                res = {**base, "occupancy_status": "vacant", "verification_status": "VACANT", "detected_plate": None,
                       "confidence": 0.0, "message": f"Slot {sname} is empty" + (f" (booked by {exp['user_name']})" if exp.get("user_name") else ""),
                       "vehicle_box": None}
                results.append(res)
                self._draw_slot(annotated, poly, sname, "VACANT", None)
                continue

            x1, y1, x2, y2 = veh["box"]
            px, py = (x2 - x1) * 0.08, (y2 - y1) * 0.08
            vcrop = frame[int(max(0, y1 - py)):int(min(fh, y2 + py)), int(max(0, x1 - px)):int(min(fw, x2 + px))]
            pr = plate_only.get(sid) or read_plate_thorough(anpr, self.identity, vcrop, thorough)
            common = {**base, "occupancy_status": "occupied", "vehicle_box": veh["box"],
                      "occupancy_source": veh.get("source"), "vehicle_class": veh.get("class_name"),
                      "vehicle_crop_base64": _b64(vcrop, max_w=640), "plate_crop_base64": pr["plate_b64"],
                      "plate_reads": pr.get("reads", 0)}
            if not pr["plate"]:
                res = {**common, "verification_status": "OCCUPIED_NO_PLATE", "detected_plate": None, "confidence": pr["confidence"],
                       "suggested_plate": pr.get("suggested_plate"), "unreadable_reason": pr["reason"], "message": f"Vehicle in {sname} - number plate not readable ({pr['reason']})"}
                results.append(res)
                self._draw_slot(annotated, poly, sname, "OCCUPIED_NO_PLATE", None, veh["box"])
                continue

            c = classify_plate(sid, pr["plate"], self.identity)
            emp = c["match"].get("employee")
            res = {**common, "verification_status": c["status"], "detected_plate": c["plate"], "raw_plate": pr["plate"],
                   "plate_match": c["match"]["match"], "confidence": round(pr["confidence"], 2),
                   "violation_type": c["violation_type"], "severity": c["severity"], "message": c["message"],
                   "actual_driver": emp.get("name") if emp else None,
                   "matched_user": _public_employee(emp, c["match"].get("reservation")),
                   "alert_triggered": bool(c["violation_type"])}

            if record and c["violation_type"] and store is not None:
                existing = store.find_recent(camera_id, sid, c["plate"])
                if existing:
                    res["violation_id"] = existing["id"]
                    res["already_logged"] = True
                else:
                    vid = f"vio_{time.strftime('%Y%m%d%H%M%S')}_{sid}_photo"
                    scene = draw_evidence(frame, poly, veh["box"], c["violation_type"], sname, c["plate"])
                    pimg = pr.get("plate_img")
                    if pimg is not None and pimg.size and pimg.shape[1] < 320:
                        s = 320.0 / pimg.shape[1]
                        pimg = cv2.resize(pimg, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
                    rec = store.create({
                        "id": vid, "camera_id": camera_id, "camera_name": camera_name, "slot_id": sid, "slot_label": sname,
                        "floor": exp.get("floor"), "violation_type": c["violation_type"], "severity": c["severity"],
                        "detected_plate": c["plate"], "raw_plate": pr["plate"], "plate_confidence": pr["confidence"],
                        "plate_match": c["match"]["match"], "plate_reads": pr.get("reads", 1),
                        "matched_user": res["matched_user"],
                        "expected": {"source": exp.get("source"), "user_id": exp.get("user_id"),
                                     "user_name": exp.get("user_name"), "vehicle_number": exp.get("vehicle_number"),
                                     "email": (exp.get("employee") or {}).get("email")},
                        "message": c["message"],
                        "snapshot_url": ViolationStore.save_image(scene, vid, "scene"),
                        "vehicle_crop_url": ViolationStore.save_image(vcrop, vid, "vehicle"),
                        "plate_crop_url": ViolationStore.save_image(pimg, vid, "plate", quality=95),
                    })
                    res["violation_id"] = rec["id"]
                    try:
                        notifier.notify_violation(rec)
                    except Exception as e:
                        print(f"[PhotoTest] notify failed: {e}")
            results.append(res)
            self._draw_slot(annotated, poly, sname, c["status"], c["plate"], veh["box"])

        for v in vehicles:
            x1, y1, x2, y2 = [int(t) for t in v["box"]]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (200, 200, 200), 1)

        occupied = sum(1 for r in results if r["occupancy_status"] == "occupied")
        violations = [r for r in results if r.get("violation_type")]
        return {
            "total_slots": len(slots), "occupied_count": occupied, "vacant_count": len(slots) - occupied,
            "violation_count": len(violations), "slots": results, "violations": violations,
            "detections": [{"class_name": d["class_name"], "confidence": d["confidence"], "box": d["box"]} for d in vehicles],
            "image_size": [fw, fh], "elapsed_ms": int((time.time() - t0) * 1000), "recorded": record,
            "annotated_frame_base64": _b64(annotated, max_w=1600, quality=82),
        }

    @staticmethod
    def _draw_slot(img, poly, sid, status, plate, box=None):
        if poly is None:
            return
        fw = img.shape[1]
        color = STATUS_COLORS.get(status, (200, 200, 200))
        p = poly.astype(np.int32)
        cv2.polylines(img, [p], True, color, max(2, fw // 500))
        if box:
            x1, y1, x2, y2 = [int(v) for v in box]
            cv2.rectangle(img, (x1, y1), (x2, y2), color, max(1, fw // 700))
        label = f"{sid}: {plate or status.replace('_', ' ')}"
        fs = max(0.5, fw / 1800)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, 2)
        cx, cy = int(p[:, 0].mean()), int(p[:, 1].mean())
        cv2.rectangle(img, (cx - tw // 2 - 4, cy - th - 6), (cx + tw // 2 + 4, cy + 6), (20, 20, 20), -1)
        cv2.putText(img, label, (cx - tw // 2, cy), cv2.FONT_HERSHEY_SIMPLEX, fs, color, 2, cv2.LINE_AA)

    def verify_parking_slot(self, image_base64: str, target_slot_id: Optional[str] = None,
                            camera_id: str = PHOTO_CAMERA_ID) -> Dict[str, Any]:
        """Single slot check: treats the whole image as the slot."""
        sid = target_slot_id or "SLOT"
        res = self.verify_multi_slots(image_base64, [{"slot_id": sid, "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}], camera_id)
        if not res.get("slots"):
            return {"occupancy_status": "error", "verification_status": "ERROR", "message": res.get("error", "Invalid image")}
        return {**res["slots"][0], "target_slot_id": target_slot_id, "detections": res["detections"]}

    # ------------------------------------------------------------------
    def suggest_hybrid_slots(self, image_base64: str, num_slots_hint: int = 2, method: str = "auto",
                             colors: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Proposes slot outlines.
          lines    - from painted floor lines (yellow / blue / white)
          vehicles - one outline around each parked vehicle
          auto     - painted lines first, parked vehicles if no lines are found
        """
        frame = _decode(image_base64)
        if frame is None:
            return {"suggested_slots": [], "count": 0, "source": "error", "message": "Failed to decode image", "lines": []}
        h, w = frame.shape[:2]
        lines: List[List[float]] = []

        if method in ("auto", "lines"):
            from services.line_slot_detector import detect_slots_from_lines
            ld = detect_slots_from_lines(frame, colors)
            lines = ld["lines"]
            if ld["slots"]:
                slots = [{"slot_id": f"P{101 + i}", "label": f"Bay {i + 1}", "points": q} for i, q in enumerate(ld["slots"])]
                return {"suggested_slots": slots, "count": len(slots), "source": "painted_lines", "lines": lines,
                        "message": f"Found {len(slots)} slots from {'/'.join(ld['colors'])} floor lines "
                                   f"({len(lines)} lines). Check the corners and slot IDs."}
            if method == "lines":
                return {"suggested_slots": [], "count": 0, "source": "painted_lines", "lines": lines,
                        "message": f"No slot pattern found in {'/'.join(ld['colors'])} lines ({len(lines)} line(s) seen). "
                                   "Try another colour, or draw the row manually."}

        from services.detection_service import DetectionService
        det = DetectionService.get_instance().detect(frame, confidence_threshold=0.25, classes_to_filter=VEHICLE_CLASSES,
                                                     enable_tracking=False, imgsz=1280 if max(w, h) >= 900 else 640)
        boxes = [d["box"] for d in det["detections"]
                 if d["class_name"] in ("car", "truck", "bus") and (d["box"][2] - d["box"][0]) > w * 0.04]
        boxes.sort(key=lambda b: ((b[1] + b[3]) / 2 // (h * 0.25), (b[0] + b[2]) / 2))
        slots = []
        for i, (x1, y1, x2, y2) in enumerate(boxes):
            bw, bh = x2 - x1, y2 - y1
            top = y1 + bh * 0.35  # slot outline follows the car's floor footprint, not its roof
            pts = [[x1 - bw * 0.06, top], [x2 + bw * 0.06, top], [x2 + bw * 0.06, y2 + bh * 0.06], [x1 - bw * 0.06, y2 + bh * 0.06]]
            pts = [[round(min(0.999, max(0.001, x / w)), 3), round(min(0.999, max(0.001, y / h)), 3)] for x, y in pts]
            slots.append({"slot_id": f"P{101 + i}", "label": f"Bay {i + 1}", "points": pts})
        if slots:
            prefix = "No painted slot lines found - " if method == "auto" else ""
            return {"suggested_slots": slots, "count": len(slots), "source": "ai_vehicles", "lines": lines,
                    "message": f"{prefix}drew a slot around each of the {len(slots)} parked vehicle(s). Adjust corners and slot IDs."}
        n = max(1, min(8, num_slots_hint or 2))
        slots = [{"slot_id": f"P{101 + i}", "label": f"Bay {i + 1}",
                  "points": [[round(0.04 + i * 0.92 / n, 3), 0.3], [round(0.04 + (i + 1) * 0.92 / n - 0.01, 3), 0.3],
                             [round(0.04 + (i + 1) * 0.92 / n - 0.01, 3), 0.92], [round(0.04 + i * 0.92 / n, 3), 0.92]]}
                 for i in range(n)]
        return {"suggested_slots": slots, "count": n, "source": "template", "lines": lines,
                "message": "No floor lines or parked vehicles found - added an evenly spaced template. Drag the corners onto the bays."}
