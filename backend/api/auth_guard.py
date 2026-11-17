"""
Admin-only access for the CCTV / AI endpoints.

Uses the same login token as the main parking app (JWT signed with JWT_SECRET_KEY from
server/.env). The token comes from the "Authorization: Bearer ..." header, or from a
?token= query parameter for <img> live streams (browsers can't add headers to those).
The user is looked up in the shared database, so a deactivated or demoted admin loses
access immediately.

Set AI_AUTH_DISABLED=1 only for local experiments.
"""
import os
from typing import Optional

import jwt
from fastapi import HTTPException, Request

from services.identity_service import IdentityService, _read_server_env

_env = _read_server_env()
JWT_SECRET = os.environ.get("JWT_SECRET_KEY") or _env.get("JWT_SECRET_KEY") or "change-me-in-production"
JWT_ALG = os.environ.get("JWT_ALGORITHM") or _env.get("JWT_ALGORITHM") or "HS256"
ADMIN_ROLES = {"hr", "admin"}
# Same named HR users the main server treats as admins (server/routers/auth.py)
SPECIAL_ADMIN_NAMES = {"sumanth.v", "vinod. v", "srinivas.k"}


def _token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("token")


def require_admin(request: Request):
    if os.environ.get("AI_AUTH_DISABLED") == "1":
        return None
    tok = _token(request)
    if not tok:
        raise HTTPException(status_code=401, detail="Login required")
    try:
        payload = jwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired - log in again")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid login token")
    emp = IdentityService.get_instance().employee_by_id(payload.get("sub"))
    if not emp or not emp.get("is_active", 1):
        raise HTTPException(status_code=401, detail="User not found or deactivated")
    role = (emp.get("role") or "").lower()
    name = (emp.get("name") or "").strip().lower()
    if role not in ADMIN_ROLES and name not in SPECIAL_ADMIN_NAMES:
        raise HTTPException(status_code=403, detail="Admin access required")
    return emp
