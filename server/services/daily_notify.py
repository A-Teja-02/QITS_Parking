"""
Daily day-refresh push: runs once per calendar day after midnight (Asia/Kolkata).
Notifies registered devices that a new booking day is open.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "last_daily_notify.txt"


def _today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def _seconds_until_next_run() -> float:
    """Next run at 00:05 IST (a few minutes after day change)."""
    now = datetime.now(IST)
    target = now.replace(hour=0, minute=5, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return max(5.0, (target - now).total_seconds())


def _already_sent_today() -> bool:
    try:
        if STATE_FILE.is_file():
            return STATE_FILE.read_text(encoding="utf-8").strip() == _today_ist()
    except Exception:
        pass
    return False


def _mark_sent_today() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(_today_ist(), encoding="utf-8")
    except Exception as e:
        print(f"[DAILY_NOTIFY] could not write state: {e}")


def send_day_refresh_notification() -> int:
    """Send 'new day' push to all registered devices. Returns send count."""
    from db import SessionLocal
    from services import fcm_service
    from services.reservation_service import get_tomorrow_date

    if _already_sent_today():
        print("[DAILY_NOTIFY] already sent today — skip")
        return 0

    tomorrow = get_tomorrow_date()
    parts = tomorrow.split("-")
    display = f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else tomorrow

    title = "Parking Day Refreshed"
    body = (
        f"A new booking day is open. Reserve your parking slot for {display}."
    )

    db = SessionLocal()
    try:
        sent = fcm_service.send_to_all_registered(
            db,
            title=title,
            body=body,
            data={"type": "day_refresh", "date": tomorrow},
        )
        _mark_sent_today()
        print(f"[DAILY_NOTIFY] sent={sent} for booking date {tomorrow}")
        return sent
    except Exception as e:
        print(f"[DAILY_NOTIFY] failed: {e}")
        return 0
    finally:
        db.close()


def _loop() -> None:
    # On startup: if past 00:05 IST and not yet sent today, send once
    try:
        now = datetime.now(IST)
        if now.hour > 0 or (now.hour == 0 and now.minute >= 5):
            send_day_refresh_notification()
    except Exception as e:
        print(f"[DAILY_NOTIFY] startup check failed: {e}")

    while True:
        wait = _seconds_until_next_run()
        print(f"[DAILY_NOTIFY] next run in {int(wait)}s (00:05 IST)")
        time.sleep(wait)
        send_day_refresh_notification()


def start_daily_notify_scheduler() -> None:
    if os.getenv("DISABLE_DAILY_NOTIFY", "").lower() in ("1", "true", "yes"):
        print("[DAILY_NOTIFY] disabled by env")
        return
    t = threading.Thread(target=_loop, name="daily-parking-notify", daemon=True)
    t.start()
    print("[DAILY_NOTIFY] scheduler started (Asia/Kolkata 00:05)")
