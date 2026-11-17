import socket
from fastapi import APIRouter
from models.schemas import SystemStatus
from services.detection_service import DetectionService
from services.ocr_service import OCRService
from services.storage_service import StorageService

router = APIRouter(prefix="/api/health", tags=["Health"])

def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

@router.get("", response_model=SystemStatus)
async def check_health():
    det_service = DetectionService.get_instance()
    ocr_service = OCRService.get_instance()
    
    classes_dict = det_service.get_class_names()
    total_captures = StorageService.get_total_count()

    return SystemStatus(
        status="healthy",
        model_loaded=det_service._model is not None,
        model_name="YOLOv8s (Fast & Accurate)",
        classes_count=len(classes_dict),
        device=det_service._device.upper(),
        ocr_available=ocr_service._reader_initialized,
        ocr_engine="EasyOCR (Deep Learning)",
        total_captures=total_captures,
        host_ip=get_lan_ip(),
    )
