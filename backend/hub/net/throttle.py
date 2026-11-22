"""Cross-process request spacing per host, coordinated through PostgreSQL.

A worker *reserves* the next slot inside a short transaction (row lock), then sleeps outside the lock.
This keeps e.g. arXiv's "no more than one request every three seconds, single connection" rule even when
several worker processes run connectors that share a host.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from hub.db import session_scope
from hub.models import HttpRequestLog


def reserve_slot(session: Session, host: str, spacing_s: float) -> float:
    """Reserve the next request slot for host; return seconds to wait before sending."""
    session.execute(
        text("INSERT INTO host_throttle (host, next_allowed_at) VALUES (:h, now()) ON CONFLICT (host) DO NOTHING"),
        {"h": host},
    )
    row = session.execute(
        text("SELECT next_allowed_at, now() AS now FROM host_throttle WHERE host = :h FOR UPDATE"), {"h": host}
    ).one()
    start = max(row.next_allowed_at, row.now)
    session.execute(
        text("UPDATE host_throttle SET next_allowed_at = :n WHERE host = :h"),
        {"h": host, "n": start + dt.timedelta(seconds=spacing_s)},
    )
    return max(0.0, (start - row.now).total_seconds())


def db_throttle(sleep: Callable[[float], None] = time.sleep) -> Callable[[str, float], None]:
    def _throttle(host: str, spacing_s: float) -> None:
        with session_scope() as s:
            wait = reserve_slot(s, host, spacing_s)
        if wait > 0:
            sleep(wait)

    return _throttle


def persist_http_log(session: Session, entries: list[dict[str, Any]]) -> None:
    for e in entries:
        session.add(
            HttpRequestLog(
                source_id=e.get("source_id"),
                purpose=e.get("purpose", "connector"),
                method=e.get("method", "GET"),
                host=e.get("host", "?")[:255],
                path=(e.get("path") or "")[:2000],
                status=e.get("status"),
                outcome=e.get("outcome", "error"),
                attempt=e.get("attempt", 1),
                duration_ms=e.get("duration_ms", 0),
                bytes=e.get("bytes", 0),
                rate_limit_remaining=e.get("rate_limit_remaining"),
                error=(e.get("error") or "")[:1000],
                created_at=e.get("created_at") or dt.datetime.now(dt.UTC),
            )
        )
    entries.clear()
