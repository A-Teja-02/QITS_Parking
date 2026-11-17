"""Device token registration for Firebase push (Android APK / web)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from models.schemas import User
from routers.auth import get_current_user
from services import fcm_service

router = APIRouter(prefix="/api/devices", tags=["devices"])


class RegisterDeviceRequest(BaseModel):
    token: str
    platform: str = "android"  # android | web | ios


class RegisterDeviceResponse(BaseModel):
    message: str
    platform: str


@router.post("/register", response_model=RegisterDeviceResponse)
def register_device(
    data: RegisterDeviceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    token = (data.token or "").strip()
    if not token or len(token) < 20:
        raise HTTPException(status_code=400, detail="Invalid FCM token")

    platform = (data.platform or "android").lower()
    if platform not in ("android", "web", "ios"):
        platform = "android"

    fcm_service.upsert_token(db, user.id, token, platform)
    return RegisterDeviceResponse(message="Device registered for push notifications", platform=platform)


@router.delete("/unregister")
def unregister_device(
    data: RegisterDeviceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    removed = fcm_service.remove_token(db, user.id, data.token.strip() if data.token else None)
    return {"message": "Device unregistered", "removed": removed}
