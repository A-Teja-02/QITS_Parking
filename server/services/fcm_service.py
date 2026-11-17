"""
Firebase Cloud Messaging helper for Android / web push.
Requires FIREBASE_CREDENTIALS_PATH pointing to service account JSON.
"""
from __future__ import annotations

import os
from typing import List, Optional

from sqlalchemy.orm import Session

from db.models import DeviceToken

_firebase_ready = False
_init_attempted = False


def _ensure_firebase() -> bool:
    global _firebase_ready, _init_attempted
    if _firebase_ready:
        return True
    if _init_attempted:
        return False
    _init_attempted = True

    cred_path = os.getenv("FIREBASE_CREDENTIALS_PATH", "").strip()
    if not cred_path or not os.path.isfile(cred_path):
        print("[FCM] FIREBASE_CREDENTIALS_PATH not set or file missing — push disabled")
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        _firebase_ready = True
        print("[FCM] Firebase Admin initialized")
        return True
    except Exception as e:
        print(f"[FCM] Init failed: {e}")
        return False


def upsert_token(db: Session, user_id: str, token: str, platform: str = "android") -> DeviceToken:
    existing = db.query(DeviceToken).filter(DeviceToken.token == token).first()
    if existing:
        existing.user_id = user_id
        existing.platform = platform or existing.platform
        db.commit()
        db.refresh(existing)
        return existing

    row = DeviceToken(user_id=user_id, token=token, platform=platform or "android")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def remove_token(db: Session, user_id: str, token: Optional[str] = None) -> int:
    q = db.query(DeviceToken).filter(DeviceToken.user_id == user_id)
    if token:
        q = q.filter(DeviceToken.token == token)
    count = q.delete(synchronize_session=False)
    db.commit()
    return count


def send_to_user(db: Session, user_id: str, title: str, body: str, data: Optional[dict] = None) -> int:
    """Send FCM to all devices for a user. Returns number of successful sends."""
    tokens: List[str] = [
        t.token for t in db.query(DeviceToken).filter(DeviceToken.user_id == user_id).all()
    ]
    if not tokens:
        return 0
    return send_to_tokens(db, tokens, title, body, data)


def send_to_all_registered(
    db: Session,
    title: str,
    body: str,
    data: Optional[dict] = None,
    exclude_user_ids: Optional[List[str]] = None,
) -> int:
    """Send FCM to every stored device token (optionally skipping some users)."""
    q = db.query(DeviceToken)
    exclude = set(exclude_user_ids or [])
    tokens = [t.token for t in q.all() if t.user_id not in exclude]
    # de-dupe tokens
    tokens = list(dict.fromkeys(tokens))
    return send_to_tokens(db, tokens, title, body, data)


def send_to_tokens(
    db: Session,
    tokens: List[str],
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> int:
    if not tokens or not _ensure_firebase():
        return 0

    from firebase_admin import messaging

    sent = 0
    stale: List[str] = []
    payload_data = {k: str(v) for k, v in (data or {}).items()}

    for token in tokens:
        try:
            message = messaging.Message(
                token=token,
                notification=messaging.Notification(title=title, body=body),
                data=payload_data or None,
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        channel_id="parking_alerts",
                        sound="default",
                    ),
                ),
            )
            messaging.send(message)
            sent += 1
        except Exception as e:
            err = str(e).lower()
            print(f"[FCM] send failed: {e}")
            if "not-found" in err or "unregistered" in err or "invalid" in err:
                stale.append(token)

    if stale:
        db.query(DeviceToken).filter(DeviceToken.token.in_(stale)).delete(synchronize_session=False)
        db.commit()

    return sent
