from fastapi import APIRouter, HTTPException
from models.schemas import DetectionRequest, DetectionResponse
from services.detection_service import DetectionService

router = APIRouter(prefix="/api/detect", tags=["Detection"])

@router.post("", response_model=DetectionResponse)
async def detect_objects(payload: DetectionRequest):
    try:
        service = DetectionService.get_instance()
        image = service.decode_base64_image(payload.image_base64)
        result = service.detect(
            image=image,
            confidence_threshold=payload.confidence_threshold,
            classes_to_filter=payload.classes_to_filter,
            enable_tracking=payload.enable_tracking
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/classes")
async def get_available_classes():
    try:
        service = DetectionService.get_instance()
        classes = service.get_class_names()
        return {
            "classes": list(classes.values()),
            "total": len(classes)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
