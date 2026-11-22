from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from hub.db import session_factory, session_scope
from hub.models import User
from hub.security.auth import COOKIE_NAME, can, user_from_token


def get_db() -> Iterator[Session]:
    """Request-scoped session. Write endpoints call ``db.commit()`` explicitly; anything else is rolled back."""
    s = session_factory()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = user_from_token(db, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    db.commit()  # persist last_seen_at
    db.expunge(user)
    return user


def authenticate_request(request: Request) -> User | None:
    """Short-lived auth for long-running endpoints (SSE) that must not hold a pooled connection."""
    with session_scope() as s:
        user = user_from_token(s, request.cookies.get(COOKIE_NAME))
        if user is not None:
            s.expunge(user)
        return user


def require(permission: str) -> Callable[..., User]:
    def dep(user: User = Depends(current_user)) -> User:
        if not can(user, permission):
            raise HTTPException(status_code=403, detail=f"permission '{permission}' required")
        return user

    return dep
