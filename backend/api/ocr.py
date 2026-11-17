from fastapi import APIRouter, HTTPException
from models.schemas import OCRRequest, OCRResponse
from services.ocr_service import OCRService

router = APIRouter(prefix="/api/ocr", tags=["OCR"])

@router.post("", response_model=OCRResponse)
async def extract_text(payload: OCRRequest):
    try:
        service = OCRService.get_instance()
        image = service.decode_base64_image(payload.image_base64)
        result = service.extract_text(
            image=image,
            box=payload.box,
            enhance=payload.enhance_image
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
