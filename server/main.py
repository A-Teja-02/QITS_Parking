from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from dotenv import load_dotenv
load_dotenv()

from routers import reservations, slots, auth, users, devices

# Import models so tables are registered with Base
from db.models import Employee, OTPVerification, PasswordReset, Notification, DeviceToken  # noqa: F401
from db import engine, Base

app = FastAPI(
    title="Parking Reservation System",
    description="Internal company parking reservation API",
    version="1.0.0",
)

# Create database tables on startup
@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
    
    # Auto-migrate: ensure profile_picture_url column exists in the database
    from sqlalchemy.sql import text
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE employees ADD COLUMN profile_picture_url VARCHAR(500) DEFAULT NULL;"))
            conn.commit()
    except Exception:
        pass

    # Daily day-refresh push (00:05 Asia/Kolkata)
    try:
        from services.daily_notify import start_daily_notify_scheduler
        start_daily_notify_scheduler()
    except Exception as e:
        print(f"[DAILY_NOTIFY] scheduler start failed: {e}")

# CORS — allow Vite dev server and production domain
allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
env_origins = os.getenv("ALLOWED_ORIGINS")
if env_origins:
    allowed_origins = [o.strip() for o in env_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if env_origins else ["*"],
    allow_credentials=True if (env_origins or not os.getenv("RENDER")) else False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(reservations.router)
app.include_router(slots.router)
app.include_router(devices.router)

# Mount uploads static directory
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "Parking Reservation API"}


@app.post("/api/admin/trigger-day-refresh")
def trigger_day_refresh():
    """Manual test endpoint for daily refresh push (DEV / ops)."""
    from services.daily_notify import send_day_refresh_notification, STATE_FILE
    if os.getenv("DEV_MODE", "true").lower() in ("true", "1", "yes"):
        try:
            if STATE_FILE.is_file():
                STATE_FILE.unlink()
        except Exception:
            pass
    sent = send_day_refresh_notification()
    return {"status": "ok", "sent": sent}
