"""
Violation alerts:
- in-app notification for every HR admin (server/parking.db notifications table)
- optional email with the evidence images, using the SMTP settings in server/.env
  (only sent when SMTP_HOST and SMTP_USER are configured).
"""
import os
import smtplib
import threading
import uuid
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List

from services.identity_service import SERVER_DIR, IdentityService, db_execute

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TYPE_LABELS = {
    "WRONG_SLOT": "Wrong slot",
    "NO_BOOKING": "Parked without booking",
    "UNKNOWN_VEHICLE": "Unknown vehicle",
    "UNREADABLE_PLATE": "Plate unreadable (review)",
}


def _server_env() -> Dict[str, str]:
    env: Dict[str, str] = {}
    path = os.path.join(SERVER_DIR, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith("SMTP_")})
    return env


def _in_app(violation: Dict[str, Any]):
    ids = IdentityService.get_instance().hr_user_ids()
    if not ids:
        return
    label = TYPE_LABELS.get(violation.get("violation_type"), violation.get("violation_type"))
    msg = (f"[Parking violation - {label}] Slot {violation.get('slot_label') or violation.get('slot_id')}: "
           f"{violation.get('message') or ''}")
    # Same format SQLAlchemy writes for DateTime columns (works on SQLite and MySQL)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    for uid in ids:
        db_execute("INSERT INTO notifications (id, user_id, message, is_read, created_at) "
                   "VALUES (:id, :uid, :msg, 0, :created)",
                   {"id": str(uuid.uuid4()), "uid": uid, "msg": msg, "created": created})


def _email(violation: Dict[str, Any]):
    env = _server_env()
    host, user = env.get("SMTP_HOST"), env.get("SMTP_USER")
    if not host or not user:
        return
    recipients: List[str] = IdentityService.get_instance().hr_emails()
    if not recipients:
        return
    label = TYPE_LABELS.get(violation.get("violation_type"), violation.get("violation_type"))
    mu = violation.get("matched_user") or {}
    ex = violation.get("expected") or {}
    body = "\n".join([
        f"Parking violation detected: {label}",
        "",
        f"Slot:            {(violation.get('slot_label') or violation.get('slot_id'))} ({violation.get('floor') or '-'})",
        f"Camera:          {violation.get('camera_name')}",
        f"Time:            {violation.get('created_at')}",
        f"Detected plate:  {violation.get('detected_plate') or 'unreadable'} "
        f"(confidence {round((violation.get('plate_confidence') or 0) * 100)}%, match: {violation.get('plate_match') or '-'})",
        f"Vehicle owner:   {mu.get('name') or 'Not a registered company vehicle'}"
        + (f" - {mu.get('email')}, {mu.get('department') or ''}, booked slot today: {mu.get('reserved_slot_today') or 'none'}" if mu else ""),
        f"Slot booked for: {ex.get('user_name') or 'nobody'} ({ex.get('vehicle_number') or '-'})",
        "",
        violation.get("message") or "",
        "",
        "Evidence images are attached. Review in Admin Dashboard > CCTV & AI > Violations.",
    ])

    def send():
        try:
            msg = MIMEMultipart()
            msg["From"] = env.get("SMTP_FROM_EMAIL") or user
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = f"Parking violation: {label} at slot {(violation.get('slot_label') or violation.get('slot_id'))}"
            msg.attach(MIMEText(body, "plain"))
            from services.violation_store import ViolationStore
            for kind in ("scene", "plate", "vehicle"):
                data = ViolationStore.get_instance().get_image(violation["id"], kind)
                if data:
                    msg.attach(MIMEImage(data, name=f"{violation['id']}_{kind}.jpg"))
            with smtplib.SMTP(host, int(env.get("SMTP_PORT") or 587), timeout=20) as s:
                s.starttls()
                s.login(user, env.get("SMTP_PASSWORD", ""))
                s.send_message(msg)
            print(f"[notifier] violation email sent to {recipients}")
        except Exception as e:
            print(f"[notifier] violation email failed: {e}")

    threading.Thread(target=send, daemon=True).start()


def notify_violation(violation: Dict[str, Any]):
    _in_app(violation)
    _email(violation)
