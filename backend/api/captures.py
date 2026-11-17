import io
import csv
import json
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from models.schemas import CaptureEventCreate, CaptureEventRecord, UpdateOCRTextRequest
from services.storage_service import StorageService

router = APIRouter(prefix="/api/captures", tags=["Captures"])

@router.post("", response_model=CaptureEventRecord)
async def create_capture(payload: CaptureEventCreate):
    try:
        record = StorageService.create_capture(
            timestamp=payload.timestamp,
            camera_id=payload.camera_id,
            reason=payload.reason,
            labels=payload.labels,
            confidences=payload.confidences,
            image_base64=payload.image_base64,
            crop_base64=payload.crop_base64,
            ocr_text=payload.ocr_text,
            metadata=payload.metadata
        )
        return record
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("", response_model=List[CaptureEventRecord])
async def list_captures(
    label: Optional[str] = Query(None, description="Filter by detected label"),
    reason: Optional[str] = Query(None, description="Filter by capture reason"),
    search: Optional[str] = Query(None, description="Search query matching label or OCR text"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    try:
        captures = StorageService.get_captures(
            label_filter=label,
            reason_filter=reason,
            search_query=search,
            limit=limit,
            offset=offset
        )
        return captures
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{capture_id}/ocr")
async def update_capture_ocr(capture_id: str, payload: UpdateOCRTextRequest):
    success = StorageService.update_ocr_text(capture_id, payload.ocr_text)
    if not success:
        raise HTTPException(status_code=404, detail="Capture record not found")
    return {"status": "success", "capture_id": capture_id, "ocr_text": payload.ocr_text}

@router.delete("/{capture_id}")
async def delete_capture(capture_id: str):
    success = StorageService.delete_capture(capture_id)
    if not success:
        raise HTTPException(status_code=404, detail="Capture record not found")
    return {"status": "success", "deleted_id": capture_id}

@router.delete("")
async def clear_all_captures():
    deleted_count = StorageService.clear_all_captures()
    return {"status": "success", "deleted_count": deleted_count}

@router.get("/export/json")
async def export_json():
    captures = StorageService.get_captures(limit=10000)
    json_bytes = json.dumps(captures, indent=2).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(json_bytes),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=cctv_captures_export.json"}
    )

@router.get("/export/csv")
async def export_csv():
    captures = StorageService.get_captures(limit=10000)
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["ID", "Timestamp", "Camera ID", "Reason", "Labels", "Confidences", "OCR Text", "Image URL", "Crop URL"])
    for c in captures:
        writer.writerow([
            c["id"],
            c["timestamp"],
            c["camera_id"],
            c["reason"],
            ", ".join(c["labels"]),
            ", ".join(str(conf) for conf in c["confidences"]),
            c.get("ocr_text") or "",
            c.get("image_url") or "",
            c.get("crop_url") or ""
        ])
    
    csv_bytes = output.getvalue().encode("utf-8")
    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cctv_captures_export.csv"}
    )
