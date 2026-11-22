"""Passwords (scrypt), server-side sessions, roles and a central authorization policy.

* Passwords: hashlib.scrypt (n=2**15, r=8, p=1, 32-byte salt) — memory-hard, stdlib only.
* Sessions: random 256-bit tokens; only the SHA-256 hash is stored; HttpOnly + SameSite=Strict cookie.
* Authorization: role ranks + ownership checks in one place, so multi-user rules are consistent from day one.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hub.config import get_settings
from hub.models import ROLES, LoginAttempt, User, UserSession

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
COOKIE_NAME = "hub_session"

ROLE_RANK = {"viewer": 0, "member": 1, "moderator": 2, "admin": 3, "owner": 4}
PERMISSIONS: dict[str, str] = {
    "items:read": "viewer",
    "bookmarks:own": "member",
    "watchlists:own": "member",
    "articles:own": "member",
    "comments:own": "member",
    "moderation:act": "moderator",
    "sources:admin": "admin",
    "ops:read": "admin",
    "ops:write": "admin",
    "ai:admin": "admin",
    "settings:write": "owner",
    "users:admin": "owner",
    "backup:run": "owner",
}


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    salt = secrets.token_bytes(32)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=128 * 1024 * 1024,
                        dklen=64)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=int(n), r=int(r), p=int(p),
                            maxmem=128 * 1024 * 1024, dklen=len(base64.b64decode(dk_b64)))
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(session: Session, user: User, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = dt.datetime.now(dt.UTC)
    session.add(UserSession(user_id=user.id, token_hash=_token_hash(token), created_at=now, last_seen_at=now,
                            expires_at=now + dt.timedelta(days=get_settings().session_days), user_agent=user_agent[:300]))
    user.last_login_at = now
    return token


def user_from_token(session: Session, token: str | None) -> User | None:
    if not token:
        return None
    us = session.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    now = dt.datetime.now(dt.UTC)
    if us is None or us.revoked or us.expires_at < now:
        return None
    user = session.get(User, us.user_id)
    if user is None or not user.is_active:
        return None
    if us.last_seen_at is None or (now - us.last_seen_at) > dt.timedelta(minutes=5):
        us.last_seen_at = now
    return user


def revoke_session(session: Session, token: str | None) -> None:
    if not token:
        return
    us = session.scalar(select(UserSession).where(UserSession.token_hash == _token_hash(token)))
    if us:
        us.revoked = True


def is_locked_out(session: Session, email: str) -> bool:
    s = get_settings()
    since = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=s.login_lockout_minutes)
    failures = session.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.email == email.lower(), LoginAttempt.success.is_(False), LoginAttempt.created_at > since)) or 0
    return failures >= s.login_max_failures


def record_attempt(session: Session, email: str, success: bool, client: str = "") -> None:
    session.add(LoginAttempt(email=email.lower()[:320], success=success, client=client[:100]))


def can(user: User | None, permission: str) -> bool:
    if user is None or not user.is_active:
        return False
    needed = PERMISSIONS.get(permission)
    if needed is None:
        return False  # unknown permissions are denied
    return ROLE_RANK.get(user.role, -1) >= ROLE_RANK[needed]


def can_modify_owned(user: User | None, owner_id: int | None, permission: str, elevated: str = "moderation:act") -> bool:
    """Owner of the resource with `permission`, or a user holding the elevated permission."""
    if user is None:
        return False
    if owner_id is not None and owner_id == user.id and can(user, permission):
        return True
    return can(user, elevated)


def create_user(session: Session, email: str, password: str, *, role: str = "member", handle: str = "",
                display_name: str = "") -> User:
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    email = email.strip().lower()
    if "@" not in email or len(email) > 320:
        raise ValueError("invalid email")
    handle = (handle or email.split("@")[0]).lower()
    user = User(email=email, handle=handle[:64], display_name=display_name or handle, password_hash=hash_password(password),
                role=role, is_active=True)
    session.add(user)
    session.flush()
    return user
