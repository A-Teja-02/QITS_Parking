import os
import re
import threading
import cv2
import numpy as np
import base64
from typing import List, Dict, Optional, Tuple, Any
from collections import defaultdict
from models.parking_schemas import PlateRecognitionResult, PlateCandidate
from services.ocr_service import OCRService

# Supported Indian State and Union Territory RTO Codes + Bharat Series
INDIAN_STATE_CODES = {
    'AN', 'AP', 'AR', 'AS', 'BR', 'CG', 'CH', 'DD', 'DL', 'DN',
    'GA', 'GJ', 'HP', 'HR', 'JH', 'JK', 'KA', 'KL', 'LA', 'LD',
    'MH', 'ML', 'MN', 'MP', 'MZ', 'NL', 'OD', 'PB', 'PY', 'RJ',
    'SK', 'TN', 'TR', 'TS', 'TG', 'UK', 'UP', 'WB', 'BH'
}

# Standard Indian Plate Regex: e.g. DL 01 AB 1234 or TS 09 EA 9999 or DL 1C 1234
STANDARD_PLATE_REGEX = re.compile(r'^([A-Z]{2})([0-9]{1,2})([A-Z]{0,3})([0-9]{4})$')

# Bharat Series Regex: e.g. 22 BH 1234 AA
BHARAT_SERIES_REGEX = re.compile(r'^([0-9]{2})(BH)([0-9]{4})([A-Z]{1,2})$')

# Military/Defense plates (arrow prefix or distinctive format)
DEFENSE_SERIES_REGEX = re.compile(r'^[0-9]{2}[A-Z][0-9]{6}[A-Z]?$')

# Common manufacturer/dealer words printed on plate holders to discard
DEALER_NOISE_WORDS = {
    'MARUTI', 'SUZUKI', 'HYUNDAI', 'HONDA', 'TATA', 'TOYOTA', 'MAHINDRA',
    'KIA', 'VOLKSWAGEN', 'SKODA', 'RENAULT', 'NISSAN', 'FORD', 'CHEVROLET',
    'BMW', 'MERCEDES', 'AUDI', 'MOTORS', 'AUTO', 'DEALER', 'INDIA', 'IND'
}

class ANPRService:
    _instance: Optional['ANPRService'] = None

    def __init__(self):
        self.ocr_service = OCRService.get_instance()
        # Track-based candidate cache: track_id -> List[Dict]
        self.track_candidates: Dict[int, List[Dict]] = defaultdict(list)
        # Store confirmed plates for tracks to prevent duplicate alerts
        self.confirmed_tracks: Dict[int, str] = {}
        # Optional dedicated licence-plate detector (YOLO weights trained on plates).
        self.plate_model = None
        self._plate_lock = threading.Lock()
        self._load_plate_model()

    def _load_plate_model(self):
        """
        Loads a licence-plate YOLO model if one is available. This is the single biggest
        accuracy upgrade for ANPR: drop any plate-detection weights file at
        backend/license_plate_detector.pt (or set ANPR_PLATE_MODEL=path).
        Without it, plates are located with OCR text detection + edge heuristics.
        """
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [os.environ.get("ANPR_PLATE_MODEL", "")] + [
            os.path.join(base, n) for n in (
                "license_plate_detector.pt", "plate_detector.pt", "anpr_plate.pt",
                os.path.join("weights", "license_plate_detector.pt"),
            )
        ]
        for path in candidates:
            if path and os.path.isfile(path):
                try:
                    from ultralytics import YOLO
                    self.plate_model = YOLO(path)
                    print(f"[ANPRService] Plate detector loaded: {path}")
                    return
                except Exception as e:
                    print(f"[ANPRService] Could not load plate detector {path}: {e}")
        print("[ANPRService] No plate detector weights found - using OCR text-region localisation.")

    @classmethod
    def get_instance(cls) -> 'ANPRService':
        if cls._instance is None:
            cls._instance = ANPRService()
        return cls._instance

    def assess_quality(self, crop: np.ndarray) -> Tuple[float, Dict[str, Any]]:
        """Evaluate plate crop quality: resolution, sharpness, and contrast."""
        if crop is None or crop.size == 0:
            return 0.0, {"sharpness": 0, "contrast": 0, "resolution": "0x0"}

        h, w = crop.shape[:2]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop

        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        contrast = float(np.std(gray))

        size_score = min(1.0, (w * h) / (120 * 35))
        sharp_score = min(1.0, sharpness / 100.0)
        contrast_score = min(1.0, contrast / 45.0)

        quality = round((size_score * 0.3) + (sharp_score * 0.4) + (contrast_score * 0.3), 3)

        return quality, {
            "sharpness": round(sharpness, 1),
            "contrast": round(contrast, 1),
            "resolution": f"{w}x{h}",
            "quality_score": quality
        }

    def preprocess_plate(self, plate_crop: np.ndarray) -> np.ndarray:
        """Applies gentle LAB CLAHE contrast enhancement and bilateral filtering."""
        if plate_crop is None or plate_crop.size == 0:
            return plate_crop

        h, w = plate_crop.shape[:2]
        if w < 160 or h < 45:
            scale = max(160.0 / max(1, w), 45.0 / max(1, h))
            plate_crop = cv2.resize(plate_crop, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)

        lab = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        lab_enhanced = cv2.merge([clahe.apply(l), a, b])
        return cv2.cvtColor(lab_enhanced, cv2.COLOR_LAB2BGR)

    # ------------------------------------------------------------------
    # Plate localisation
    # ------------------------------------------------------------------
    def _plate_model_boxes(self, img: np.ndarray, max_boxes: int = 2) -> List[List[int]]:
        if self.plate_model is None or img is None or img.size == 0:
            return []
        try:
            with self._plate_lock:
                res = self.plate_model.predict(img, conf=0.25, imgsz=640, verbose=False, max_det=5)
            boxes = []
            if res and res[0].boxes is not None:
                for bx in res[0].boxes:
                    x1, y1, x2, y2 = [int(v) for v in bx.xyxy[0].tolist()]
                    boxes.append((float(bx.conf[0].item()), [x1, y1, x2, y2]))
            boxes.sort(key=lambda t: -t[0])
            return [b for _, b in boxes[:max_boxes]]
        except Exception as e:
            print(f"[ANPRService] plate model inference error: {e}")
            return []

    def _contour_plate_boxes(self, vehicle_crop: np.ndarray, max_boxes: int = 2) -> List[List[int]]:
        """Finds plate-shaped regions with dense vertical character strokes (lower 70% of vehicle)."""
        vh, vw = vehicle_crop.shape[:2]
        if vh < 20 or vw < 40:
            return []
        roi_y = int(vh * 0.30)
        gray = cv2.cvtColor(vehicle_crop[roi_y:vh], cv2.COLOR_BGR2GRAY)
        grad_x = cv2.convertScaleAbs(cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3))
        blurred = cv2.GaussianBlur(grad_x, (5, 5), 0)
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kw = max(9, int(vw * 0.04)) | 1
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 3)))
        closed = cv2.morphologyEx(closed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        roi_area = float(vw * (vh - roi_y))
        scored = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            ar = w / max(1.0, float(h))
            area_ratio = (w * h) / max(1.0, roi_area)
            # Single-line plates are ~4.5:1, two-line plates ~1.6:1
            if 1.4 <= ar <= 7.0 and 0.004 <= area_ratio <= 0.25 and w >= 25:
                fill = cv2.contourArea(cnt) / max(1.0, float(w * h))
                score = area_ratio * fill * (1.0 - min(0.8, abs(ar - 4.0) * 0.1))
                px, py = int(w * 0.08), int(h * 0.15)
                scored.append((score, [max(0, x - px), max(0, y + roi_y - py),
                                       min(vw, x + w + px), min(vh, y + roi_y + h + py)]))
        scored.sort(key=lambda t: -t[0])
        return [b for _, b in scored[:max_boxes]]

    def locate_candidate_plates(self, vehicle_crop: np.ndarray) -> List[Tuple[np.ndarray, List[int], str]]:
        """
        Returns plate proposals (crop, [x1, y1, x2, y2] in vehicle_crop coords, source),
        most promising first.
        """
        if vehicle_crop is None or vehicle_crop.size == 0:
            return []
        vh, vw = vehicle_crop.shape[:2]
        out: List[Tuple[np.ndarray, List[int], str]] = []

        def add(box, src):
            x1, y1, x2, y2 = [int(v) for v in box]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(vw, x2), min(vh, y2)
            if x2 - x1 >= 12 and y2 - y1 >= 6:
                out.append((vehicle_crop[y1:y2, x1:x2], [x1, y1, x2, y2], src))

        for b in self._plate_model_boxes(vehicle_crop):
            w, h = b[2] - b[0], b[3] - b[1]
            add([b[0] - w * 0.06, b[1] - h * 0.12, b[2] + w * 0.06, b[3] + h * 0.12], "plate_model")
        for b in self._contour_plate_boxes(vehicle_crop):
            add(b, "contour_sobel")
        # Whole vehicle: the OCR text detector (CRAFT) finds the plate region itself
        add([0, 0, vw, vh], "whole_vehicle")
        # Lower part of the vehicle (bumper) at a higher effective resolution
        add([0, vh * 0.45, vw, vh], "lower_half")
        return out

    # ------------------------------------------------------------------
    # Turning OCR text boxes into plate candidates
    # ------------------------------------------------------------------
    @staticmethod
    def _group_text_boxes(boxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Builds plate-text candidates from OCR boxes: each box alone, horizontally
        adjacent boxes on one line (e.g. "TS 09" + "EA 9999"), and two stacked lines
        (two-line plates). Each candidate keeps the union box of its parts.
        """
        if not boxes:
            return []
        items = sorted(boxes, key=lambda b: ((b["box"][1] + b["box"][3]) / 2.0, b["box"][0]))
        cands: List[Dict[str, Any]] = []

        def make(parts):
            txt = "".join(p["text"] for p in parts)
            lens = [max(1, len(p["text"])) for p in parts]
            conf = sum(p["confidence"] * l for p, l in zip(parts, lens)) / float(sum(lens))
            x1 = min(p["box"][0] for p in parts)
            y1 = min(p["box"][1] for p in parts)
            x2 = max(p["box"][2] for p in parts)
            y2 = max(p["box"][3] for p in parts)
            return {"text": txt, "confidence": conf, "box": [x1, y1, x2, y2], "parts": len(parts)}

        for b in items:
            cands.append(make([b]))

        def h(b):
            return max(1, b["box"][3] - b["box"][1])

        def vov(a, b):
            top = max(a["box"][1], b["box"][1])
            bot = min(a["box"][3], b["box"][3])
            return max(0, bot - top) / float(min(h(a), h(b)))

        def hov(a, b):
            left = max(a["box"][0], b["box"][0])
            right = min(a["box"][2], b["box"][2])
            narrow = max(1, min(a["box"][2] - a["box"][0], b["box"][2] - b["box"][0]))
            return max(0, right - left) / float(narrow)

        # Same-line chains, left to right
        by_x = sorted(items, key=lambda b: b["box"][0])
        for i, start in enumerate(by_x):
            chain = [start]
            for b in by_x[i + 1:]:
                last = chain[-1]
                gap = b["box"][0] - last["box"][2]
                if vov(last, b) > 0.5 and gap < 1.8 * h(last) and abs(h(b) - h(last)) < 0.6 * h(last):
                    chain.append(b)
                    cands.append(make(list(chain)))
                if len(chain) >= 4:
                    break

        # Two-line plates: an upper line directly above a lower line of similar size
        for a in items:
            for b in items:
                if a is b:
                    continue
                below = b["box"][1] > a["box"][1] + 0.6 * h(a)
                close = (b["box"][1] - a["box"][3]) < 0.8 * h(a)
                if below and close and hov(a, b) > 0.4 and abs(h(a) - h(b)) < 0.6 * max(h(a), h(b)):
                    cands.append(make([a, b]))

        # De-duplicate by text, keep highest confidence
        best: Dict[str, Dict[str, Any]] = {}
        for c in cands:
            if c["text"] not in best or c["confidence"] > best[c["text"]]["confidence"]:
                best[c["text"]] = c
        return list(best.values())

    def disambiguate_indian_plate(self, cleaned: str) -> str:
        """
        Applies character disambiguation matrix according to Indian RTO syntax:
        - Positions 0, 1: State code (MUST be letters: 0->O, 1->I, 8->B, 5->S, etc.)
        - Positions 2, 3: District RTO (MUST be digits: O->0, I->1, Z->2, B->8, Q->9, etc.)
        - Middle positions: Series (MUST be letters)
        - Last 4 positions: Vehicle unique number (MUST be digits: O->0, I->1, S->5, B->8, Q->9, etc.)
        """
        if len(cleaned) < 7 or len(cleaned) > 11:
            return cleaned

        to_letter = {'0': 'O', '1': 'I', '2': 'Z', '4': 'A', '5': 'S', '6': 'G', '8': 'B', '7': 'T'}
        to_digit = {'O': '0', 'D': '0', 'Q': '9', 'I': '1', 'L': '1', 'Z': '2', 'A': '4', 'S': '5', 'G': '6', 'B': '8', 'T': '7'}

        chars = list(cleaned)
        n = len(chars)

        # 1. State letters (first 2 chars)
        for i in (0, 1):
            if chars[i] in to_letter:
                chars[i] = to_letter[chars[i]]

        # 2. District digits (chars 2 and 3)
        for i in (2, 3):
            if i < n and chars[i] in to_digit:
                chars[i] = to_digit[chars[i]]

        # Special case: If district starts with 0 and second is Q, it's 09
        if n >= 4 and chars[2] == '0' and chars[3] == 'Q':
            chars[3] = '9'

        # 3. Last 4 characters are always digits in Indian registrations
        for i in range(max(4, n - 4), n):
            if chars[i] in to_digit:
                chars[i] = to_digit[chars[i]]

        # 4. Characters between index 4 and (n-4) are the series (letters)
        for i in range(4, n - 4):
            if chars[i] in to_letter:
                chars[i] = to_letter[chars[i]]

        return "".join(chars)

    def validate_indian_plate(self, raw_text: str) -> Tuple[bool, str, Optional[str]]:
        """
        Validates whether raw OCR text conforms to Indian registration formats.
        Robustly extracts the registration number by stripping IND prefix and dealer logos.
        Returns: (is_valid, normalized_text, state_code)
        """
        if not raw_text:
            return False, "", None

        # Clean raw text: uppercase and remove newlines/tabs
        raw_upper = raw_text.upper().replace('\n', ' ').replace('\r', ' ')

        # Strip known dealer branding tokens
        tokens = [t for t in re.split(r'[\s\-_\.:/|\\]+', raw_upper) if t]
        clean_tokens = [t for t in tokens if t not in DEALER_NOISE_WORDS]
        combined = "".join(clean_tokens)

        # Remove leading IND / INDIA if present
        if combined.startswith("IND") and len(combined) >= 10:
            combined = combined[3:]
        elif combined.startswith("INDIA") and len(combined) >= 12:
            combined = combined[5:]

        # Also strip trailing IND / INDIA
        if combined.endswith("IND") and len(combined) >= 10:
            combined = combined[:-3]
        elif combined.endswith("INDIA") and len(combined) >= 12:
            combined = combined[:-5]

        cleaned = re.sub(r'[^A-Z0-9]', '', combined)
        if not cleaned:
            return False, "", None

        candidates_to_try = [cleaned]

        # Apply positional disambiguation
        disambiguated = self.disambiguate_indian_plate(cleaned)
        if disambiguated != cleaned:
            candidates_to_try.append(disambiguated)

        for cand in candidates_to_try:
            # 1. Bharat Series Check: e.g. 22 BH 1234 AA
            bh_match = BHARAT_SERIES_REGEX.search(cand)
            if bh_match:
                year, bh, digits, series = bh_match.groups()
                formatted = f"{year} {bh} {digits} {series}"
                return True, formatted, "BH"

            # 2. Standard State/UT format: e.g. TS 09 EA 9999 or MH 12 CD 1234
            std_match = STANDARD_PLATE_REGEX.search(cand)
            if std_match:
                st, dist, series, num = std_match.groups()
                if st in INDIAN_STATE_CODES:
                    dist_str = dist.zfill(2)
                    series_part = f" {series}" if series else ""
                    formatted = f"{st} {dist_str}{series_part} {num}".strip()
                    return True, formatted, st

            # 3. Defense format check
            def_match = DEFENSE_SERIES_REGEX.search(cand)
            if def_match:
                return True, def_match.group(0), "DEFENSE"

        # 4. Partial check: Scan for 2-letter state code anywhere in string followed by digits
        for cand in candidates_to_try:
            for st in INDIAN_STATE_CODES:
                if st in cand:
                    idx = cand.index(st)
                    sub = cand[idx:]
                    if len(sub) >= 7:
                        disamb_sub = self.disambiguate_indian_plate(sub[:10])
                        std_m = STANDARD_PLATE_REGEX.search(disamb_sub)
                        if std_m:
                            s_st, s_dist, s_series, s_num = std_m.groups()
                            s_dist_str = s_dist.zfill(2)
                            s_ser = f" {s_series}" if s_series else ""
                            return True, f"{s_st} {s_dist_str}{s_ser} {s_num}".strip(), s_st

        # 5. Fallback for custom / short vehicle identifiers (at least 6 alphanumeric chars with digits)
        best = candidates_to_try[-1]
        has_digits = bool(re.search(r'\d', best))
        has_letters = bool(re.search(r'[A-Z]', best))
        if 6 <= len(best) <= 11 and has_digits and has_letters:
            return False, best, best[:2] if best[:2] in INDIAN_STATE_CODES else None

        # Never return random 1-4 character OCR noise (e.g. "M", "3", "F") as a plate number!
        return False, "", None

    def diagnose_unreadable_reason(self, vehicle_crop: np.ndarray, best_crop: Optional[np.ndarray] = None) -> str:
        """
        Diagnoses why OCR could not read the plate:
        - Low light in basement
        - High distance / low camera resolution
        - Headlight glare / reflection
        - Severe tilt or obstruction
        """
        target = best_crop if (best_crop is not None and best_crop.size > 0) else vehicle_crop
        if target is None or target.size == 0:
            return "No vehicle image"

        gray = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY) if len(target.shape) == 3 else target
        mean_val = float(np.mean(gray))
        std_val = float(np.std(gray))
        th, tw = target.shape[:2]

        # 1. Overexposed / Headlight Glare
        saturated_pixels = np.sum(gray >= 250)
        total_pixels = max(1, gray.size)
        if (saturated_pixels / total_pixels) > 0.10 or (mean_val > 195 and std_val > 55):
            return "Headlight Glare / Reflection"

        # 2. Dim / Under-exposed lighting
        if mean_val < 45.0:
            return "Low Basement Light"

        # 3. Low Resolution / Distance
        if tw < 80 or th < 25:
            return "Camera Distance / Low Resolution"

        # 4. Low contrast / blur
        laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        if laplacian_var < 45.0:
            return "Motion Blur / Low Focus"

        return "Plate Obscured or Angled"

    def recognize_plate(self, vehicle_crop: np.ndarray, track_id: Optional[int] = None,
                        max_ocr_calls: int = 7) -> PlateRecognitionResult:
        """
        Locates the licence plate inside a vehicle crop and reads it.
        Tries plate proposals x image variants, keeps the best Indian-format reading,
        and stops early once a confident valid plate is found. OCR calls are capped
        (CPU budget); the slot monitor accumulates readings across frames instead.
        """
        if vehicle_crop is None or vehicle_crop.size == 0:
            return PlateRecognitionResult(plate_text="", confidence=0.0, status="UNREADABLE",
                                          is_valid_format=False, candidates=[], unreadable_reason="Empty crop")

        proposals = self.locate_candidate_plates(vehicle_crop)
        variant_plan = {
            "plate_model": ["base", "clahe", "sharpened", "inverted"],
            "contour_sobel": ["base", "clahe", "sharpened"],
            "whole_vehicle": ["base", "clahe"],
            "lower_half": ["base", "sharpened"],
        }
        best: Optional[Dict[str, Any]] = None
        best_any_crop: Optional[np.ndarray] = None
        all_valid: Dict[str, Dict[str, Any]] = {}
        calls = 0

        for crop, pbox, source in proposals:
            if crop is None or crop.size == 0:
                continue
            if best_any_crop is None and source in ("plate_model", "contour_sobel"):
                best_any_crop = crop
            for variant in variant_plan.get(source, ["base"]):
                if calls >= max_ocr_calls:
                    break
                calls += 1
                boxes = self.ocr_service.read_text_boxes(crop, [variant])
                if not boxes:
                    continue
                sx = crop.shape[1] / float(max(1, boxes[0]["img_w"]))
                sy = crop.shape[0] / float(max(1, boxes[0]["img_h"]))
                for cand in self._group_text_boxes(boxes):
                    is_valid, norm, state = self.validate_indian_plate(cand["text"])
                    if not norm or len(norm.replace(" ", "")) < 6:
                        continue
                    # Map candidate box back into crop pixel coordinates, pad a little
                    bx1, by1 = int(cand["box"][0] * sx), int(cand["box"][1] * sy)
                    bx2, by2 = int(cand["box"][2] * sx), int(cand["box"][3] * sy)
                    bw, bh = bx2 - bx1, by2 - by1
                    px1, py1 = max(0, bx1 - int(bw * 0.08)), max(0, by1 - int(bh * 0.25))
                    px2 = min(crop.shape[1], bx2 + int(bw * 0.08))
                    py2 = min(crop.shape[0], by2 + int(bh * 0.25))
                    plate_crop = crop[py1:py2, px1:px2] if (px2 > px1 and py2 > py1) else crop
                    quality, _ = self.assess_quality(plate_crop)
                    score = cand["confidence"] * 0.75 + quality * 0.25 + (1.0 if is_valid else 0.0)
                    entry = {
                        "normalized_text": norm, "is_valid": is_valid, "confidence": cand["confidence"],
                        "state_code": state, "quality_score": quality, "score": score, "source": source,
                        "plate_box": [pbox[0] + px1, pbox[1] + py1, pbox[0] + px2, pbox[1] + py2],
                        "crop": plate_crop,
                    }
                    if is_valid:
                        key = norm.replace(" ", "")
                        if key not in all_valid or all_valid[key]["score"] < score:
                            all_valid[key] = entry
                    if best is None or score > best["score"]:
                        best = entry
                if best and best["is_valid"] and best["confidence"] >= 0.65:
                    break
            if best and best["is_valid"] and best["confidence"] >= 0.5:
                break
            if calls >= max_ocr_calls:
                break

        if not best or not best["is_valid"]:
            reason = self.diagnose_unreadable_reason(vehicle_crop, best["crop"] if best else best_any_crop)
            return PlateRecognitionResult(
                plate_text="", confidence=best["confidence"] if best else 0.0, status="UNREADABLE",
                is_valid_format=False, candidates=[],
                plate_crop_base64=self._encode_crop(best["crop"]) if best else None,
                unreadable_reason=reason)

        final_text = best["normalized_text"]
        final_conf = best["confidence"]
        state_code = best["state_code"]
        candidates = [PlateCandidate(plate_text=e["normalized_text"], confidence=round(e["confidence"], 2),
                                     is_valid_format=True, state_code=e["state_code"])
                      for e in sorted(all_valid.values(), key=lambda e: -e["score"])[:5]]
        final_status = "CONFIRMED" if final_conf >= 0.55 else "REVIEW_REQUIRED"
        frames_count = 1

        # Multi-frame voting for tracked vehicles (live webcam ANPR endpoint)
        if track_id is not None:
            hist = self.track_candidates[track_id]
            hist.append({"text": final_text, "conf": final_conf, "state": state_code})
            del hist[:-15]
            frames_count = len(hist)
            tally: Dict[str, float] = defaultdict(float)
            for c in hist:
                tally[c["text"]] += 0.5 + c["conf"]
            winner = max(tally.items(), key=lambda kv: kv[1])[0]
            same = [c for c in hist if c["text"] == winner]
            final_text = winner
            final_conf = round(max(c["conf"] for c in same), 2)
            state_code = same[0]["state"]
            final_status = "CONFIRMED" if (len(same) >= 2 or final_conf >= 0.7) else "REVIEW_REQUIRED"
            if final_status == "CONFIRMED":
                self.confirmed_tracks[track_id] = final_text

        return PlateRecognitionResult(
            plate_text=final_text, confidence=round(final_conf, 2), status=final_status, is_valid_format=True,
            state_code=state_code, plate_box=best["plate_box"], quality_score=best["quality_score"],
            plate_crop_base64=self._encode_crop(best["crop"]), candidates=candidates, frames_aggregated=frames_count)

    @staticmethod
    def _encode_crop(crop: Optional[np.ndarray]) -> Optional[str]:
        if crop is None or crop.size == 0:
            return None
        h, w = crop.shape[:2]
        if w < 200:
            s = 200.0 / max(1, w)
            crop = cv2.resize(crop, (int(w * s), int(h * s)), interpolation=cv2.INTER_CUBIC)
        ok, buf = cv2.imencode('.jpg', crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        return ("data:image/jpeg;base64," + base64.b64encode(buf).decode('utf-8')) if ok else None
