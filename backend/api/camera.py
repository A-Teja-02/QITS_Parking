from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import cv2

router = APIRouter(prefix="/api/camera", tags=["Camera"])

class RTSPTestRequest(BaseModel):
    rtsp_url: str
    camera_name: str = "CCTV Camera"

@router.get("/status")
async def get_camera_status():
    return {
        "status": "ready",
        "supported_inputs": ["Webcam (Browser MediaDevices)", "Mobile Camera (PWA)", "RTSP Network Stream"],
        "future_extensions": ["Multi-camera concurrent pipeline", "ANPR / License plate recognition"]
    }

@router.post("/rtsp/test")
async def test_rtsp_stream(payload: RTSPTestRequest):
    url = payload.rtsp_url.strip()
    if not (url.startswith("rtsp://") or url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Invalid stream URL format. Must start with rtsp://, http://, or https://")

    # Attempt short connection test with OpenCV
    try:
        cap = cv2.VideoCapture(url)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 2500)
        
        is_opened = cap.isOpened()
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if is_opened else 0
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if is_opened else 0
        fps = round(cap.get(cv2.CAP_PROP_FPS), 1) if is_opened else 0
        cap.release()

        return {
            "connected": is_opened,
            "camera_name": payload.camera_name,
            "stream_url": url,
            "resolution": f"{width}x{height}" if is_opened else "Unavailable",
            "fps": fps,
            "message": "Stream connected successfully" if is_opened else "Could not connect to RTSP stream. Verify stream availability and network credentials."
        }
    except Exception as e:
        return {
            "connected": False,
            "camera_name": payload.camera_name,
            "stream_url": url,
            "resolution": "Unavailable",
            "fps": 0,
            "message": f"Connection error: {str(e)}"
        }
