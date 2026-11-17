import cv2
import numpy as np
import base64
import time
import threading
from typing import Optional, List, Dict, Any, Tuple

# Characters that can appear on Indian registration plates
PLATE_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

class OCRService:
    _instance = None
    _reader = None
    _reader_initialized = False
    _init_error: Optional[str] = None
    # EasyOCR (torch) is not safe to call from several threads at once
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._init_reader()

    def _init_reader(self):
        try:
            import easyocr
            import torch
            use_gpu = torch.cuda.is_available()
            print(f"[OCRService] Initializing EasyOCR (gpu={use_gpu})...")
            self._reader = easyocr.Reader(
                ['en'],
                gpu=use_gpu,
                verbose=False,
                model_storage_directory=None,
                quantize=True,
            )
            self._reader_initialized = True
            print("[OCRService] EasyOCR initialized successfully!")
        except Exception as e:
            self._init_error = str(e)
            print(f"[OCRService] EasyOCR init note: {e}")

    def decode_base64_image(self, b64_str: str) -> np.ndarray:
        if "," in b64_str:
            b64_str = b64_str.split(",", 1)[1]
        img_bytes = base64.b64decode(b64_str)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Failed to decode image data for OCR")
        return img

    def prepare_variants(self, roi: np.ndarray, enhance: bool = True) -> List[Tuple[str, np.ndarray]]:
        """
        Creates multiple high-quality image variants for ensemble OCR recognition.
        Enhanced for parking bay photos (angled, dim, low-res CCTV conditions).
        """
        h, w = roi.shape[:2]
        variants = []

        # --- Step 1: Deskew / Perspective correction for angled plates ---
        try:
            deskewed = self._deskew_if_needed(roi)
        except Exception:
            deskewed = roi

        # --- Step 2: Aggressive upscale — aim for 320px wide min ---
        min_target_w = 320
        min_target_h = 80
        if w < min_target_w or h < min_target_h:
            scale = max(min_target_w / max(1, w), min_target_h / max(1, h))
            scale = min(scale, 6.0)  # cap at 6x to avoid memory blow-up
            base_img = cv2.resize(deskewed, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LANCZOS4)
        else:
            base_img = deskewed.copy()

        variants.append(("base", base_img))

        if not enhance:
            return variants

        # Variant 2: Strong LAB CLAHE (clipLimit 3 to handle dim basement/underground lighting)
        try:
            lab = cv2.cvtColor(base_img, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
            lab_norm = cv2.merge([clahe.apply(l), a, b])
            clahe_bgr = cv2.cvtColor(lab_norm, cv2.COLOR_LAB2BGR)
            variants.append(("clahe", clahe_bgr))
        except Exception:
            pass

        # Variant 3: Inverted polarity (white text on blue/dark HSRP plates)
        try:
            gray = cv2.cvtColor(base_img, cv2.COLOR_BGR2GRAY) if len(base_img.shape) == 3 else base_img
            inverted = cv2.bitwise_not(gray)
            inv_bgr = cv2.cvtColor(inverted, cv2.COLOR_GRAY2BGR)
            variants.append(("inverted", inv_bgr))
        except Exception:
            pass

        # Variant 4: Bilateral sharpen + unsharp mask (recovers JPEG/compression blur)
        try:
            gray = cv2.cvtColor(base_img, cv2.COLOR_BGR2GRAY) if len(base_img.shape) == 3 else base_img
            denoised = cv2.bilateralFilter(gray, 9, 55, 55)
            gaussian = cv2.GaussianBlur(denoised, (0, 0), 1.5)
            sharpened = cv2.addWeighted(denoised, 1.6, gaussian, -0.6, 0)
            sharp_bgr = cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)
            variants.append(("sharpened", sharp_bgr))
        except Exception:
            pass

        # Variant 5: Otsu binarised (clean black/white — works best for well-lit plates)
        try:
            gray = cv2.cvtColor(base_img, cv2.COLOR_BGR2GRAY) if len(base_img.shape) == 3 else base_img
            _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            otsu_bgr = cv2.cvtColor(otsu, cv2.COLOR_GRAY2BGR)
            variants.append(("otsu", otsu_bgr))
        except Exception:
            pass

        # Variant 6: Morphological closing + dilate (joins broken strokes from low-res)
        try:
            gray = cv2.cvtColor(base_img, cv2.COLOR_BGR2GRAY) if len(base_img.shape) == 3 else base_img
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
            closed = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
            clahe2 = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(6, 6))
            enhanced_closed = clahe2.apply(closed)
            morph_bgr = cv2.cvtColor(enhanced_closed, cv2.COLOR_GRAY2BGR)
            variants.append(("morph_close", morph_bgr))
        except Exception:
            pass

        return variants

    def _deskew_if_needed(self, img: np.ndarray, max_angle_deg: float = 15.0) -> np.ndarray:
        """
        Detects dominant tilt angle using Hough lines and corrects plate skew up to max_angle_deg.
        Preserves aspect ratio and fills background with white.
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img.copy()
        edges = cv2.Canny(gray, 50, 150)
        lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=max(30, min(gray.shape) // 4))
        if lines is None:
            return img

        angles = []
        for line in lines[:20]:
            rho, theta = line[0]
            angle_deg = np.degrees(theta) - 90.0
            if abs(angle_deg) <= max_angle_deg:
                angles.append(angle_deg)

        if not angles:
            return img

        median_angle = float(np.median(angles))
        if abs(median_angle) < 1.0:  # < 1° — not worth correcting
            return img

        h, w = img.shape[:2]
        cx, cy = w / 2.0, h / 2.0
        M = cv2.getRotationMatrix2D((cx, cy), median_angle, 1.0)
        rotated = cv2.warpAffine(img, M, (w, h),
                                  flags=cv2.INTER_LANCZOS4,
                                  borderMode=cv2.BORDER_CONSTANT,
                                  borderValue=(255, 255, 255))
        return rotated

    def extract_text(
        self,
        image: np.ndarray,
        box: Optional[List[int]] = None,
        enhance: bool = True
    ) -> Dict[str, Any]:
        t0 = time.perf_counter()
        h, w = image.shape[:2]

        # Crop Region of Interest (ROI) if box is provided
        if box and len(box) == 4:
            x1, y1, x2, y2 = box
            x1 = max(0, min(int(x1), w - 1))
            y1 = max(0, min(int(y1), h - 1))
            x2 = max(x1 + 1, min(int(x2), w))
            y2 = max(y1 + 1, min(int(y2), h))

            # Small 6% padding around ROI for context
            pad_x = int((x2 - x1) * 0.06)
            pad_y = int((y2 - y1) * 0.06)
            x1 = max(0, x1 - pad_x)
            y1 = max(0, y1 - pad_y)
            x2 = min(w, x2 + pad_x)
            y2 = min(h, y2 + pad_y)

            roi = image[y1:y2, x1:x2]
        else:
            roi = image

        if roi is None or roi.size == 0:
            return {
                "text": "",
                "confidence": 0.0,
                "words": [],
                "processing_time_ms": 0.0,
                "status": "error: empty ROI region",
                "detected_language": "en"
            }

        if not self._reader_initialized:
            self._init_reader()

        if self._reader is None:
            return {
                "text": "",
                "confidence": 0.0,
                "words": [],
                "processing_time_ms": round((time.perf_counter() - t0) * 1000, 2),
                "status": f"OCR engine initializing or unavailable ({self._init_error or 'downloading weights'})",
                "detected_language": "en"
            }

        variants = self.prepare_variants(roi, enhance=enhance)

        best_extracted = {
            "text": "",
            "confidence": 0.0,
            "words": [],
        }
        best_score = -1.0

        for variant_name, img_variant in variants:
            try:
                # Optimized parameters for scene text & license plates
                with self._lock:
                  results = self._reader.readtext(
                    img_variant,
                    paragraph=False,
                    width_ths=0.5,
                    low_text=0.25,
                    text_threshold=0.35,
                    link_threshold=0.25,
                    mag_ratio=1.5,
                  )

                if not results:
                    continue

                words = []
                extracted_texts = []
                confidences = []

                # Sort results top-to-bottom, left-to-right
                sorted_results = sorted(
                    results,
                    key=lambda r: (round(min(pt[1] for pt in r[0]) / 20.0), min(pt[0] for pt in r[0]))
                )

                for item in sorted_results:
                    bbox, raw_txt, prob = item
                    clean_txt = raw_txt.strip()
                    if clean_txt:
                        extracted_texts.append(clean_txt)
                        confidences.append(float(prob))
                        bbox_list = [[int(pt[0]), int(pt[1])] for pt in bbox]
                        words.append({
                            "text": clean_txt,
                            "confidence": round(float(prob), 3),
                            "bbox": bbox_list
                        })

                full_text = " ".join(extracted_texts).strip()
                avg_conf = round(float(np.mean(confidences)), 3) if confidences else 0.0
                
                # Combined score: favors longer, higher-confidence detection
                score = (len(full_text) ** 0.5) * (avg_conf + 0.1)

                if score > best_score:
                    best_score = score
                    best_extracted = {
                        "text": full_text,
                        "confidence": avg_conf,
                        "words": words,
                    }

                # If we achieved high confidence and meaningful text, stop early for speed
                if avg_conf >= 0.88 and len(full_text) >= 6:
                    break

            except Exception as e:
                continue

        elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        final_text = best_extracted["text"]

        return {
            "text": final_text,
            "confidence": best_extracted["confidence"],
            "words": best_extracted["words"],
            "processing_time_ms": elapsed_ms,
            "status": "success" if final_text else "no_text_detected",
            "detected_language": "en"
        }

    # ------------------------------------------------------------------
    # Plate-focused OCR
    # ------------------------------------------------------------------
    def read_text_boxes(self, image: np.ndarray, variant_names: Optional[List[str]] = None,
                        min_width: int = 320) -> List[Dict[str, Any]]:
        """
        Runs OCR restricted to plate characters and returns every text box found,
        per image variant: [{"variant", "text", "confidence", "box": [x1,y1,x2,y2]}].
        Boxes are kept separate so the caller can pick the ones that form a plate
        (instead of gluing dealer stickers, stickers and plate text together).
        """
        if image is None or image.size == 0:
            return []
        if not self._reader_initialized:
            self._init_reader()
        if self._reader is None:
            return []

        variants = self.prepare_variants(image, enhance=True)
        if variant_names:
            order = {n: i for i, n in enumerate(variant_names)}
            variants = sorted([v for v in variants if v[0] in order], key=lambda v: order[v[0]])

        out: List[Dict[str, Any]] = []
        for name, img in variants:
            try:
                with self._lock:
                    results = self._reader.readtext(
                        img,
                        allowlist=PLATE_ALLOWLIST,
                        paragraph=False,
                        decoder="beamsearch",
                        beamWidth=5,
                        width_ths=0.7,
                        low_text=0.3,
                        text_threshold=0.5,
                        link_threshold=0.3,
                        mag_ratio=1.0 if img.shape[1] >= min_width else 1.5,
                    )
            except Exception:
                continue
            for bbox, txt, prob in results or []:
                txt = (txt or "").strip().upper()
                if not txt:
                    continue
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                out.append({
                    "variant": name,
                    "text": txt,
                    "confidence": float(prob),
                    "box": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
                    "img_w": img.shape[1],
                    "img_h": img.shape[0],
                })
        return out
