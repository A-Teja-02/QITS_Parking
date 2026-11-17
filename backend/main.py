import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.detection import router as detection_router
from api.ocr import router as ocr_router
from api.captures import router as captures_router
from api.camera import router as camera_router
from api.health import router as health_router
from api.parking import router as parking_router

from services.detection_service import DetectionService
from services.ocr_service import OCRService
from services.anpr_service import ANPRService
from services.cctv_manager import CCTVManager
from services.violation_store import ViolationStore

CAPTURES_DIR = os.path.join(os.path.dirname(__file__), "captures")
os.makedirs(CAPTURES_DIR, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 60)
    print("Starting AI Vision & Parking ANPR Backend Engine...")
    print("Preloading YOLOv8 vehicle detection model...")
    DetectionService.get_instance()
    print("Initializing EasyOCR text extraction engine...")
    OCRService.get_instance()
    print("Initializing ANPR license plate recognition service...")
    anpr = ANPRService.get_instance()
    try:  # warm up OCR + plate path so the first real photo check is fast
        import numpy as _np, cv2 as _cv2
        _img = _np.full((120, 360, 3), 255, _np.uint8)
        _cv2.putText(_img, "MH 12 AB 1234", (12, 75), _cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3)
        anpr.recognize_plate(_img, max_ocr_calls=2)
        DetectionService.get_instance().detect(_np.zeros((640, 640, 3), _np.uint8), imgsz=1280, augment=True, enable_tracking=False)
        print("OCR and detector warmed up")
    except Exception as e:
        print(f"Warm-up skipped: {e}")
    print("Opening violation store...")
    ViolationStore.get_instance()
    print("Initializing CCTV Manager & reconnecting saved cameras...")
    mgr = CCTVManager.get_instance()
    mgr.start_all()
    print(f"AI Parking Surveillance ready - {len(mgr.cameras)} camera(s) configured")
    print("=" * 60)
    yield
    print("Shutting down AI Vision Backend Engine...")
    mgr.shutdown()

app = FastAPI(
    title="Real-Time AI Vision, Detection & Smart Photo Capture API",
    description="Production-grade AI vision backend powered by YOLOv8, EasyOCR, and SQLite persistence",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for local web development and mobile devices on local network
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static folder for captured images
app.mount("/captures", StaticFiles(directory=CAPTURES_DIR), name="captures")

# Register API Routers
app.include_router(detection_router)
app.include_router(ocr_router)
app.include_router(captures_router)
app.include_router(camera_router)
app.include_router(health_router)
app.include_router(parking_router)

@app.get("/")
async def root():
    return {
        "app": "Real-Time AI Vision API",
        "version": "1.0.0",
        "status": "online",
        "docs_url": "/docs",
        "health_url": "/api/health"
    }

if __name__ == "__main__":
    import uvicorn
    import sys
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
