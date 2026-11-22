"""Phone pairing and device sessions.

Flow: a logged-in user (e.g. on the computer) creates a one-time pairing code -> the UI shows it as a QR code
for the phone URL -> the phone opens /pair?code=... and exchanges the code for its own session cookie.
Codes: 10 random characters (31-symbol alphabet, ~49 bits), valid 10 minutes, single use, stored hashed,
attempts rate-limited per IP. Every device session can be listed and revoked.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from hub.api.deps import current_user, get_db
from hub.config import get_settings
from hub.models import PairingCode, User, UserSession
from hub.net.lanaddr import phone_urls
from hub.security.auth import COOKIE_NAME, create_session, session_id_for_token
from hub.settings_store import audit

router = APIRouter(prefix="/api")
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 10
CODE_TTL = dt.timedelta(minutes=10)


def _hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().upper().encode()).hexdigest()


def _client(request: Request) -> str:
    return request.client.host if request.client else ""


@router.get("/devices/phone-access")
def phone_access(user: User = Depends(current_user)) -> dict[str, Any]:
    s = get_settings()
    urls = phone_urls(s.network_mode, s.extra_hosts, s.scheme, s.port)
    return {
        "network_mode": s.network_mode,
        "enabled": s.network_mode != "local" or bool(urls),
        "urls": urls,
        "https": s.scheme == "https" or any(u.startswith("https://") for u in urls),
        "instructions": None if (s.network_mode != "local" or urls) else
        "Phone access is off. See MOBILE.md: set HUB_NETWORK_MODE=lan (same Wi-Fi) or use Tailscale, then restart.",
    }


@router.post("/devices/pairing-codes")
def create_pairing_code(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    # one active code per user: older unused codes are invalidated
    db.execute(text("UPDATE pairing_codes SET expires_at = now() WHERE user_id = :u AND used_at IS NULL AND expires_at > now()"),
               {"u": user.id})
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    exp = dt.datetime.now(dt.UTC) + CODE_TTL
    db.add(PairingCode(code_hash=_hash(code), user_id=user.id, expires_at=exp, created_ip=_client(request)))
    audit(db, "device.pairing_code_created", user_id=user.id, target_type="user", target_id=user.id)
    db.commit()
    return {"code": f"{code[:5]}-{code[5:]}", "expires_at": exp.isoformat(), "ttl_seconds": int(CODE_TTL.total_seconds())}


class PairIn(BaseModel):
    code: str = Field(min_length=8, max_length=20)
    device_name: str = Field(default="Phone", max_length=100)


@router.post("/auth/pair")
def pair(body: PairIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC)
    pc = db.scalar(select(PairingCode).where(PairingCode.code_hash == _hash(body.code)).with_for_update())
    if pc is None or pc.used_at is not None or pc.expires_at < now:
        audit(db, "device.pairing_failed", actor="anonymous", target_type="ip", target_id=_client(request))
        db.commit()
        raise HTTPException(401, "invalid or expired pairing code")
    user = db.get(User, pc.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "invalid or expired pairing code")
    s = get_settings()
    token = create_session(db, user, request.headers.get("user-agent", ""), device_name=body.device_name.strip() or "Phone",
                           created_via="pairing", client_ip=_client(request), days=s.device_session_days)
    db.flush()
    pc.used_at, pc.used_ip = now, _client(request)
    pc.used_session_id = session_id_for_token(db, token)
    audit(db, "device.paired", user_id=user.id, target_type="session", target_id=pc.used_session_id or "",
          device=body.device_name[:100], ip=_client(request))
    db.commit()
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=request.url.scheme == "https",
                        max_age=s.device_session_days * 86400, path="/")
    return {"user": {"id": user.id, "email": user.email, "handle": user.handle, "display_name": user.display_name,
                     "role": user.role}}


@router.get("/devices")
def list_devices(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    current = session_id_for_token(db, request.cookies.get(COOKIE_NAME))
    now = dt.datetime.now(dt.UTC)
    rows = db.scalars(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked.is_(False),
                                                UserSession.expires_at > now).order_by(UserSession.last_seen_at.desc()))
    return [{"id": r.id, "device_name": r.device_name or ("This browser" if r.id == current else "Browser"),
             "created_via": r.created_via, "user_agent": r.user_agent[:120], "client_ip": r.client_ip,
             "created_at": r.created_at.isoformat(), "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
             "expires_at": r.expires_at.isoformat(), "current": r.id == current} for r in rows]


@router.post("/devices/{session_id}/revoke")
def revoke_device(session_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    us = db.get(UserSession, session_id)
    if us is None or us.user_id != user.id:
        raise HTTPException(404, "not found")
    us.revoked = True
    audit(db, "device.revoked", user_id=user.id, target_type="session", target_id=session_id)
    db.commit()
    return {"revoked": True}


@router.post("/devices/revoke-others")
def revoke_others(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    current = session_id_for_token(db, request.cookies.get(COOKIE_NAME))
    res = db.execute(text("UPDATE user_sessions SET revoked = true WHERE user_id = :u AND id <> :c AND NOT revoked"),
                     {"u": user.id, "c": current or -1})
    audit(db, "device.revoke_others", user_id=user.id, target_type="user", target_id=user.id)
    db.commit()
    return {"revoked": getattr(res, "rowcount", 0)}
