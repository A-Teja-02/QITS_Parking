import os
import json
import time
import uuid
import base64
import threading
from collections import deque, defaultdict
import cv2
import numpy as np
from typing import Dict, List, Optional, Tuple, Generator, Any
from datetime import datetime
from models.parking_schemas import (
    CameraConfig, CameraConfigCreate, CameraTestRequest, ParkingEvent, VehicleDetection
)
from services.detection_service import DetectionService
from services.anpr_service import ANPRService

DATA_DIR = os.environ.get("CCTV_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)
CAMERAS_FILE = os.path.join(DATA_DIR, "cameras.json")
EVENTS_FILE = os.path.join(DATA_DIR, "parking_events.json")
MASK = "******"

# Manufacturer RTSP URL templates
BRAND_TEMPLATES = {
    "hikvision": "rtsp://{username}:{password}@{host}:{port}/Streaming/Channels/{channel}0{stream_profile}",
    "dahua": "rtsp://{username}:{password}@{host}:{port}/cam/realmonitor?channel={channel}&subtype={stream_profile}",
    "cpplus": "rtsp://{username}:{password}@{host}:{port}/cam/realmonitor?channel={channel}&subtype={stream_profile}",
    "tapo": "rtsp://{username}:{password}@{host}:{port}/stream{stream_profile}",
    "reolink": "rtsp://{username}:{password}@{host}:{port}/h264Preview_0{channel}_{stream_name}",
    "axis": "rtsp://{username}:{password}@{host}:{port}/axis-media/media.amp",
    "uniview": "rtsp://{username}:{password}@{host}:{port}/unicast/c{channel}/s{stream_index}/live",
    "generic": "rtsp://{username}:{password}@{host}:{port}/{stream_path}",
}
STREAM_FIELDS = {"source_type", "rtsp_url", "brand", "host", "port", "username", "password",
                 "stream_path", "transport", "stream_profile", "channel"}


def build_stream_url(cfg) -> Any:
    """Returns the OpenCV source for a camera config: RTSP/HTTP URL, file path, or USB index."""
    src_type = (getattr(cfg, "source_type", "rtsp") or "rtsp").lower()
    url = (getattr(cfg, "rtsp_url", None) or "").strip()
    if src_type in ("usb", "webcam") or (url.isdigit() and len(url) <= 2):
        return int(url) if url.isdigit() else 0
    if url:
        return url
    brand = (getattr(cfg, "brand", None) or "generic").lower()
    tpl = BRAND_TEMPLATES.get(brand, BRAND_TEMPLATES["generic"])
    main = (getattr(cfg, "stream_profile", "main") or "main") == "main"
    channel = str(getattr(cfg, "channel", 1) or 1)
    if brand in ("dahua", "cpplus"):
        profile = "0" if main else "1"
    elif brand == "tapo":
        profile = "1" if main else "2"
    else:
        profile = "1" if main else "2"
    from urllib.parse import quote
    return tpl.format(
        username=quote(getattr(cfg, "username", None) or "admin", safe=""),
        password=quote(getattr(cfg, "password", None) or "", safe=""),
        host=getattr(cfg, "host", None) or "127.0.0.1",
        port=getattr(cfg, "port", None) or 554,
        channel=channel,
        stream_profile=profile,
        stream_name="main" if main else "sub",
        stream_index="0" if main else "1",
        stream_path=(getattr(cfg, "stream_path", None) or "").lstrip("/"),
    )


def redact_url(url: Any) -> str:
    url = str(url)
    if "://" in url and "@" in url:
        proto, rest = url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{proto}://{user}:{MASK}@{host}"
    return url


def open_capture(source: Any, transport: str = "tcp") -> cv2.VideoCapture:
    """Opens a capture with sensible network options (RTSP over TCP, timeouts, small buffer)."""
    if isinstance(source, int):
        cap = cv2.VideoCapture(source, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(source)
        if not cap.isOpened():
            cap = cv2.VideoCapture(source)
        return cap
    if str(source).lower().startswith("rtsp"):
        # TCP avoids the grey/smeared frames UDP produces on busy networks
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            f"rtsp_transport;{'udp' if transport == 'udp' else 'tcp'}|stimeout;8000000|max_delay;500000")
    try:
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG,
                               [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000])
    except Exception:
        cap = cv2.VideoCapture(source)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    return cap


class CameraStreamWorker:
    """
    Background reader for one camera. Reads frames as fast as the camera sends them
    (so the network buffer never backs up and frames stay live), keeps only the newest
    frame, and reconnects with backoff when the stream drops.
    Video files loop in real time, which is handy for testing without a camera.
    """

    def __init__(self, config: CameraConfig, on_frame_callback=None):
        self.config = config
        self.on_frame_callback = on_frame_callback
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.frame_queue: deque = deque(maxlen=1)
        self._frame_lock = threading.Lock()
        self._latest: Optional[np.ndarray] = None
        self._latest_t: Optional[float] = None
        self.current_fps = 0.0
        self.resolution = "0x0"
        self.last_frame_time: Optional[float] = None
        self.reconnect_count = 0
        self.status = "disconnected"
        self.last_error: Optional[str] = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.status = "connecting"
        self.thread = threading.Thread(target=self._run, name=f"stream-{self.config.id}", daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3.0)
        self.status = "disconnected"

    def latest_frame(self) -> Tuple[Optional[np.ndarray], Optional[float]]:
        with self._frame_lock:
            if self._latest is None:
                return None, None
            return self._latest, self._latest_t

    def _run(self):
        backoff = 2.0
        while self.running:
            source = build_stream_url(self.config)
            is_file = isinstance(source, str) and os.path.isfile(source)
            cap = None
            try:
                cap = open_capture(source, self.config.transport)
                if not cap.isOpened():
                    self.status = "error"
                    self.last_error = "Could not open stream. Check the URL, credentials and that the camera is reachable."
                    raise ConnectionError(self.last_error)

                self.status = "connected"
                self.last_error = None
                backoff = 2.0
                w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                self.resolution = f"{w}x{h}"
                file_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
                times: deque = deque(maxlen=30)
                failures = 0

                while self.running:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        if is_file:
                            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            continue
                        failures += 1
                        if failures >= 5:
                            self.last_error = "Stream interrupted (no frames)"
                            break
                        time.sleep(0.05)
                        continue
                    failures = 0
                    now = time.time()
                    times.append(now)
                    if len(times) > 1:
                        self.current_fps = round((len(times) - 1) / max(1e-6, times[-1] - times[0]), 1)
                    if frame.shape[1] != w:
                        w, h = frame.shape[1], frame.shape[0]
                        self.resolution = f"{w}x{h}"
                    with self._frame_lock:
                        self._latest, self._latest_t = frame, now
                    self.frame_queue.append(frame)
                    self.last_frame_time = now
                    if self.on_frame_callback:
                        self.on_frame_callback(self.config.id, frame)
                    if is_file:
                        time.sleep(1.0 / max(1.0, file_fps))
            except Exception as e:
                self.status = "error"
                self.last_error = self.last_error or str(e)
            finally:
                if cap is not None:
                    cap.release()

            if self.running:
                self.status = "reconnecting"
                self.reconnect_count += 1
                time.sleep(backoff)
                backoff = min(30.0, backoff * 1.5)


class CCTVManager:
    _instance: Optional['CCTVManager'] = None

    def __init__(self):
        self.cameras: Dict[str, CameraConfig] = {}
        self.workers: Dict[str, CameraStreamWorker] = {}
        self.monitors: Dict[str, Any] = {}
        self.events: List[ParkingEvent] = []
        self.detection_service = DetectionService.get_instance()
        self.anpr_service = ANPRService.get_instance()
        self.track_positions: Dict[int, deque] = defaultdict(lambda: deque(maxlen=10))
        self.crossed_tracks: set = set()
        self._save_lock = threading.Lock()
        self._load_cameras()
        self._load_events()

    @classmethod
    def get_instance(cls) -> 'CCTVManager':
        if cls._instance is None:
            cls._instance = CCTVManager()
        return cls._instance

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    # Camera settings live in the parking database (table cctv_cameras), so they survive
    # reinstalling the AI server and any server connected to the database sees them.
    # cameras.json is only kept as a fallback / one-time import from the earlier version.
    _CAM_TABLE = "cctv_cameras"

    def _cam_table_ready(self) -> bool:
        from sqlalchemy import inspect, text
        from services.identity_service import ENGINE
        try:
            if self._CAM_TABLE not in inspect(ENGINE).get_table_names():
                with ENGINE.begin() as conn:
                    conn.execute(text(f"CREATE TABLE {self._CAM_TABLE} (id VARCHAR(64) NOT NULL PRIMARY KEY, "
                                      f"config TEXT NOT NULL, updated_at VARCHAR(32))"))
            return True
        except Exception as e:
            print(f"[CCTV] camera table not available in the parking database ({e}) - using cameras.json")
            return False

    def _load_cameras(self):
        from sqlalchemy import text
        from services.identity_service import ENGINE
        self._db_cameras = self._cam_table_ready()
        if self._db_cameras:
            try:
                with ENGINE.connect() as conn:
                    rows = conn.execute(text(f"SELECT config FROM {self._CAM_TABLE}")).fetchall()
                for (cfg,) in rows:
                    cam = CameraConfig(**json.loads(cfg))
                    self.cameras[cam.id] = cam
            except Exception as e:
                print(f"Error loading cameras from database: {e}")
        if os.path.exists(CAMERAS_FILE):
            try:
                with open(CAMERAS_FILE, "r", encoding="utf-8") as f:
                    file_cams = [CameraConfig(**item) for item in json.load(f)]
                new = [c for c in file_cams if c.id not in self.cameras]
                for cam in new:
                    self.cameras[cam.id] = cam
                if self._db_cameras and new:
                    self._save_cameras()
                    os.replace(CAMERAS_FILE, CAMERAS_FILE + ".imported")
                    print(f"[CCTV] moved {len(new)} camera(s) from cameras.json into the parking database")
            except Exception as e:
                print(f"Error loading cameras.json: {e}")

    def _save_cameras(self):
        from sqlalchemy import text
        from services.identity_service import ENGINE
        with self._save_lock:
            if getattr(self, "_db_cameras", False):
                try:
                    now = datetime.now().isoformat(timespec="seconds")
                    with ENGINE.begin() as conn:
                        ids = [r[0] for r in conn.execute(text(f"SELECT id FROM {self._CAM_TABLE}"))]
                        for cid in ids:
                            if cid not in self.cameras:
                                conn.execute(text(f"DELETE FROM {self._CAM_TABLE} WHERE id = :i"), {"i": cid})
                        for cam in self.cameras.values():
                            cfg = json.dumps(cam.model_dump())
                            if cam.id in ids:
                                conn.execute(text(f"UPDATE {self._CAM_TABLE} SET config = :c, updated_at = :t WHERE id = :i"),
                                             {"c": cfg, "t": now, "i": cam.id})
                            else:
                                conn.execute(text(f"INSERT INTO {self._CAM_TABLE} (id, config, updated_at) VALUES (:i, :c, :t)"),
                                             {"c": cfg, "t": now, "i": cam.id})
                    return
                except Exception as e:
                    print(f"Error saving cameras to database: {e} - writing cameras.json instead")
            try:
                tmp = CAMERAS_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump([cam.model_dump() for cam in self.cameras.values()], f, indent=2)
                os.replace(tmp, CAMERAS_FILE)
            except Exception as e:
                print(f"Error saving cameras: {e}")

    def _load_events(self):
        if os.path.exists(EVENTS_FILE):
            try:
                with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                    self.events = [ParkingEvent(**item) for item in json.load(f)]
            except Exception as e:
                print(f"Error loading parking events: {e}")

    def _save_events(self):
        try:
            with open(EVENTS_FILE, "w", encoding="utf-8") as f:
                json.dump([ev.model_dump() for ev in self.events[:500]], f, indent=2)
        except Exception as e:
            print(f"Error saving parking events: {e}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start_all(self):
        """Called at backend startup: reconnect every active camera and start slot monitoring."""
        for cam in self.cameras.values():
            if cam.is_active:
                self.start_camera(cam.id)

    def shutdown(self):
        for m in list(self.monitors.values()):
            m.stop()
        for w in list(self.workers.values()):
            w.stop()

    def start_camera(self, cam_id: str) -> bool:
        cam = self.cameras.get(cam_id)
        if not cam:
            return False
        if cam_id in self.workers:
            self.workers[cam_id].stop()
        worker = CameraStreamWorker(cam)
        self.workers[cam_id] = worker
        worker.start()
        self._ensure_monitor(cam_id)
        if not cam.is_active:
            cam.is_active = True
            self._save_cameras()
        return True

    def stop_camera(self, cam_id: str, persist: bool = True) -> bool:
        mon = self.monitors.pop(cam_id, None)
        if mon:
            mon.stop()
        worker = self.workers.pop(cam_id, None)
        if worker:
            worker.stop()
        cam = self.cameras.get(cam_id)
        if cam and persist and cam.is_active:
            cam.is_active = False
            self._save_cameras()
        return worker is not None

    def _ensure_monitor(self, cam_id: str):
        from services.slot_monitor import SlotMonitor
        mon = self.monitors.get(cam_id)
        if mon is None:
            mon = SlotMonitor(self, cam_id)
            self.monitors[cam_id] = mon
        mon.start()

    # ------------------------------------------------------------------
    # Camera CRUD
    # ------------------------------------------------------------------
    def public_camera(self, cam: CameraConfig) -> Dict[str, Any]:
        d = cam.model_dump()
        if d.get("password"):
            d["password"] = MASK
        if d.get("rtsp_url"):
            d["rtsp_url"] = redact_url(d["rtsp_url"])
        w = self.workers.get(cam.id)
        if w:
            d["status"] = w.status
            d["last_error"] = w.last_error
            d["resolution"] = w.resolution
            d["fps"] = w.current_fps
            d["reconnect_count"] = w.reconnect_count
            if w.last_frame_time:
                d["last_frame_time"] = datetime.fromtimestamp(w.last_frame_time).isoformat(timespec="seconds")
        else:
            d["status"] = "stopped"
        mon = self.monitors.get(cam.id)
        d["monitoring"] = bool(mon and mon.running)
        d["stream_source"] = redact_url(build_stream_url(cam))
        return d

    def get_cameras(self) -> List[Dict[str, Any]]:
        return [self.public_camera(c) for c in self.cameras.values()]

    def add_camera(self, create_req: CameraConfigCreate) -> Dict[str, Any]:
        cam_id = f"cam_{uuid.uuid4().hex[:8]}"
        now = datetime.now().isoformat(timespec="seconds")
        cam = CameraConfig(id=cam_id, created_at=now, updated_at=now, **create_req.model_dump())
        self.cameras[cam_id] = cam
        self._save_cameras()
        self.start_camera(cam_id)
        return self.public_camera(cam)

    def update_camera(self, cam_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        cam = self.cameras.get(cam_id)
        if not cam:
            return None
        restart = False
        for k, v in fields.items():
            if v is None or not hasattr(cam, k) or k in ("id", "created_at"):
                continue
            if k == "password" and v == MASK:
                continue
            if k == "rtsp_url" and MASK in str(v):
                continue  # unchanged redacted URL coming back from the UI
            if getattr(cam, k) != v:
                setattr(cam, k, v)
                restart = restart or k in STREAM_FIELDS
        cam.updated_at = datetime.now().isoformat(timespec="seconds")
        self._save_cameras()
        if restart and cam.is_active:
            self.start_camera(cam_id)
        return self.public_camera(cam)

    def delete_camera(self, cam_id: str) -> bool:
        self.stop_camera(cam_id, persist=False)
        if cam_id in self.cameras:
            del self.cameras[cam_id]
            self._save_cameras()
            return True
        return False

    def test_connection(self, req: CameraTestRequest) -> Dict:
        """Opens the stream once, grabs a frame and returns a preview so the admin can see the view."""
        saved = self.cameras.get(req.camera_id) if req.camera_id else None
        if saved:
            if req.rtsp_url and MASK in req.rtsp_url:
                req.rtsp_url = saved.rtsp_url
            if req.password == MASK:
                req.password = saved.password
        source = build_stream_url(req)
        t0 = time.time()
        cap = None
        try:
            cap = open_capture(source, req.transport)
            opened = cap.isOpened()
            frame = None
            if opened:
                for _ in range(15):  # first frames after connect can be incomplete
                    ok, f = cap.read()
                    if ok and f is not None:
                        frame = f
                        break
            latency = round((time.time() - t0) * 1000, 1)
            preview = None
            if frame is not None:
                h, w = frame.shape[:2]
                small = cv2.resize(frame, (640, int(640 * h / w))) if w > 640 else frame
                _, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                preview = "data:image/jpeg;base64," + base64.b64encode(buf).decode()
            connected = frame is not None
            return {
                "connected": connected,
                "success": connected,
                "latency_ms": latency,
                "resolution": f"{frame.shape[1]}x{frame.shape[0]}" if connected else "Unavailable",
                "fps": round(cap.get(cv2.CAP_PROP_FPS) or 0, 1) if opened else 0,
                "stream_url": redact_url(source),
                "preview_base64": preview,
                "message": ("Connected - frame received" if connected else
                            "Stream opened but no video frame arrived" if opened else
                            "Could not connect. Check IP, port, credentials and stream path."),
            }
        except Exception as e:
            return {"connected": False, "success": False, "latency_ms": round((time.time() - t0) * 1000, 1),
                    "resolution": "Unavailable", "fps": 0, "stream_url": redact_url(source),
                    "message": f"Connection error: {e}"}
        finally:
            if cap is not None:
                cap.release()

    # ------------------------------------------------------------------
    # Frames & streams
    # ------------------------------------------------------------------
    def latest_frame(self, cam_id: str) -> Optional[np.ndarray]:
        w = self.workers.get(cam_id)
        if not w:
            return None
        frame, _ = w.latest_frame()
        return frame

    @staticmethod
    def _placeholder(text: str) -> bytes:
        blank = np.full((360, 640, 3), 24, dtype=np.uint8)
        cv2.putText(blank, text, (30, 185), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (220, 220, 220), 2, cv2.LINE_AA)
        _, buf = cv2.imencode('.jpg', blank)
        return buf.tobytes()

    def generate_mjpeg_stream(self, cam_id: str, annotated: bool = True, max_width: int = 1280,
                              fps: float = 8.0) -> Generator[bytes, None, None]:
        """multipart/x-mixed-replace stream for an <img> tag, with live slot overlays."""
        boundary = b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
        last_t = None
        idle = 0
        while True:
            worker = self.workers.get(cam_id)
            if worker is None:
                yield boundary + self._placeholder("Camera stopped") + b'\r\n'
                return
            frame, t = worker.latest_frame()
            if frame is None or t == last_t:
                idle += 1
                if frame is None and idle % 20 == 1:
                    yield boundary + self._placeholder(f"Connecting... ({worker.status})") + b'\r\n'
                time.sleep(0.05)
                continue
            idle = 0
            last_t = t
            mon = self.monitors.get(cam_id)
            if annotated and mon is not None:
                frame = mon.draw_overlay(frame)
            h, w = frame.shape[:2]
            if w > max_width:
                frame = cv2.resize(frame, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if ok:
                yield boundary + buf.tobytes() + b'\r\n'
            time.sleep(1.0 / fps)

    # ------------------------------------------------------------------
    # Browser webcam ANPR (unchanged behaviour)
    # ------------------------------------------------------------------
    def process_vehicle_frame(self, frame_b64: str, camera_id: str = "default_camera") -> List[VehicleDetection]:
        cam = self.cameras.get(camera_id)
        virtual_line = cam.virtual_line if cam else None
        img_bytes = base64.b64decode(frame_b64.split(",")[-1])
        frame_cv = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        detections: List[VehicleDetection] = []
        if frame_cv is None:
            return detections

        det_response = self.detection_service.detect(
            image=frame_cv, confidence_threshold=cam.min_confidence if cam else 0.35,
            classes_to_filter=['car', 'motorcycle', 'bus', 'truck'], enable_tracking=True)
        fh, fw = frame_cv.shape[:2]

        for det in det_response["detections"]:
            x1, y1, x2, y2 = [int(v) for v in det["box"]]
            pad_x, pad_y = int((x2 - x1) * 0.05), int((y2 - y1) * 0.05)
            x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
            x2, y2 = min(fw, x2 + pad_x), min(fh, y2 + pad_y)
            vehicle_crop = frame_cv[y1:y2, x1:x2]
            track_id = det.get("track_id")
            plate_res = self.anpr_service.recognize_plate(vehicle_crop, track_id=track_id) if vehicle_crop.size else None

            crossing_event = None
            if track_id is not None and virtual_line and virtual_line.enabled:
                cy = (y1 + y2) / 2.0 / fh
                hist = self.track_positions[track_id]
                hist.append(((x1 + x2) / 2.0 / fw, cy, time.time()))
                if len(hist) >= 2 and track_id not in self.crossed_tracks:
                    prev_cy = hist[0][1]
                    line_y = (virtual_line.y1 + virtual_line.y2) / 2.0
                    if prev_cy < line_y <= cy:
                        crossing_event = "ENTRY"
                    elif prev_cy > line_y >= cy:
                        crossing_event = "EXIT"
                    if crossing_event:
                        self.crossed_tracks.add(track_id)
                        self._record_parking_event(camera_id, det["class_name"], track_id, plate_res, vehicle_crop,
                                                   frame_cv, f"VEHICLE_{crossing_event}")

            if plate_res and plate_res.status == "CONFIRMED" and track_id and f"plate_{track_id}" not in self.crossed_tracks:
                self.crossed_tracks.add(f"plate_{track_id}")
                self._record_parking_event(camera_id, det["class_name"], track_id, plate_res, vehicle_crop, frame_cv,
                                           "PLATE_CAPTURED")

            detections.append(VehicleDetection(track_id=track_id, class_name=det["class_name"],
                                               confidence=det["confidence"], box=det["box"], plate=plate_res,
                                               crossing_event=crossing_event))
        return detections

    def _record_parking_event(self, camera_id: str, vehicle_type: str, track_id: Optional[int], plate_res: Optional[Any],
                              vehicle_crop: np.ndarray, full_frame: np.ndarray, event_type: str):
        event_id = f"evt_{uuid.uuid4().hex[:10]}"
        cam = self.cameras.get(camera_id)
        captures_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "captures")
        os.makedirs(captures_dir, exist_ok=True)
        full_filename, veh_filename = f"{event_id}_full.jpg", f"{event_id}_veh.jpg"
        cv2.imwrite(os.path.join(captures_dir, full_filename), full_frame)
        cv2.imwrite(os.path.join(captures_dir, veh_filename), vehicle_crop)
        is_valid = plate_res.is_valid_format if plate_res else False
        event = ParkingEvent(
            id=event_id, camera_id=camera_id, camera_name=cam.name if cam else "Browser camera",
            zone_id=cam.zone_id if cam else "zone_1", timestamp=datetime.now().isoformat(),
            event_type=event_type, vehicle_type=vehicle_type, track_id=track_id,
            plate_number=plate_res.plate_text if plate_res and plate_res.plate_text else "UNREADABLE",
            plate_status=plate_res.status if plate_res else "UNREADABLE",
            confidence=plate_res.confidence if plate_res else 0.0, is_valid_format=is_valid,
            image_url=f"/captures/{full_filename}", vehicle_crop_url=f"/captures/{veh_filename}",
            candidates=plate_res.candidates if plate_res else [],
            review_status="auto_approved" if is_valid else "pending_review")
        self.events.insert(0, event)
        self._save_events()

    def update_event_correction(self, event_id: str, corrected_plate: str, notes: Optional[str] = None) -> Optional[ParkingEvent]:
        for ev in self.events:
            if ev.id == event_id:
                ev.manual_corrected_plate = corrected_plate.strip().upper()
                ev.plate_number = corrected_plate.strip().upper()
                ev.review_status = "audited"
                ev.plate_status = "CONFIRMED"
                ev.audit_notes = notes
                self._save_events()
                return ev
        return None
