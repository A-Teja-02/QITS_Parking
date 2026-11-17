import pytest
import numpy as np
import cv2
import base64
from fastapi.testclient import TestClient
from main import app
from services.detection_service import DetectionService
from services.ocr_service import OCRService
from services.storage_service import StorageService

client = TestClient(app)

def create_synthetic_image(text: str = "AI VISION 2026"):
    # Create 320x240 image with text and rectangle
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    img[:] = (30, 30, 30) # dark background
    cv2.rectangle(img, (20, 20), (300, 220), (0, 255, 0), 2)
    cv2.putText(img, text, (35, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    
    _, buffer = cv2.imencode(".jpg", img)
    b64_str = base64.b64encode(buffer).decode("utf-8")
    return f"data:image/jpeg;base64,{b64_str}"

def test_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"

def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["model_loaded"] is True
    assert data["classes_count"] >= 80

def test_available_classes():
    response = client.get("/api/detect/classes")
    assert response.status_code == 200
    data = response.json()
    assert "person" in data["classes"]
    assert "car" in data["classes"]
    assert "cell phone" in data["classes"]

def test_detection_inference():
    img_b64 = create_synthetic_image()
    payload = {
        "image_base64": img_b64,
        "confidence_threshold": 0.25,
        "classes_to_filter": None,
        "enable_tracking": False
    }
    response = client.post("/api/detect", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "detections" in data
    assert "inference_time_ms" in data
    assert data["frame_width"] == 320
    assert data["frame_height"] == 240

def test_ocr_inference():
    img_b64 = create_synthetic_image("ABC 123")
    payload = {
        "image_base64": img_b64,
        "box": [10, 10, 310, 230],
        "enhance_image": True
    }
    response = client.post("/api/ocr", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "text" in data
    assert "processing_time_ms" in data

def test_captures_crud():
    img_b64 = create_synthetic_image()
    # 1. Create capture
    create_payload = {
        "timestamp": "2026-10-23T10:00:00.000Z",
        "camera_id": "test_cam_1",
        "reason": "person_detected",
        "labels": ["person", "backpack"],
        "confidences": [0.88, 0.72],
        "ocr_text": "SAMPLE OCR",
        "image_base64": img_b64,
        "crop_base64": None,
        "metadata": {"test": True}
    }
    create_res = client.post("/api/captures", json=create_payload)
    assert create_res.status_code == 200
    created = create_res.json()           
    cap_id = created["id"]
    assert cap_id.startswith("cap_")

    # 2. List captures
    list_res = client.get("/api/captures?label=person")
    assert list_res.status_code == 200
    items = list_res.json()
    assert any(item["id"] == cap_id for item in items)

    # 3. Update OCR text
    update_res = client.put(f"/api/captures/{cap_id}/ocr", json={"ocr_text": "CORRECTED TEXT"})
    assert update_res.status_code == 200
    assert update_res.json()["ocr_text"] == "CORRECTED TEXT"

    # 4. Delete capture
    del_res = client.delete(f"/api/captures/{cap_id}")
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "success"

if __name__ == "__main__":
    pytest.main(["-v", __file__])
