"""PostgreSQL-backed durable job queue.

* ``FOR UPDATE SKIP LOCKED`` claiming: safe with any number of worker processes.
* Partial unique index (type, dedupe_key) over active jobs: the same work is never queued twice.
* Exponential backoff with jitter on failure; dead-letter after ``max_attempts``.
* ``defer`` re-queues without counting an attempt (e.g. AI provider unavailable).
* ``recover_stale`` re-queues jobs held by crashed workers (heartbeat stale + lock timeout).
"""

from __future__ import annotations

import datetime as dt
import json
import random
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from hub.db import rowcount
from hub.models import Job


def enqueue(session: Session, job_type: str, payload: dict[str, Any] | None = None, *, dedupe_key: str | None = None,
            run_after: dt.datetime | None = None, priority: int = 100, max_attempts: int = 5) -> int | None:
    row = session.execute(
        text(
            "INSERT INTO jobs (type, dedupe_key, payload, status, priority, run_after, attempts, max_attempts, last_error, created_at) "
            "VALUES (:t, :k, CAST(:p AS jsonb), 'queued', :pr, COALESCE(:ra, now()), 0, :ma, '', now()) "
            "ON CONFLICT (type, dedupe_key) WHERE status IN ('queued', 'running') AND dedupe_key IS NOT NULL "
            "DO NOTHING RETURNING id"
        ),
        {"t": job_type, "k": dedupe_key, "p": json.dumps(payload or {}, default=str), "pr": priority, "ra": run_after,
         "ma": max_attempts},
    ).first()
    return row[0] if row else None


def claim(session: Session, worker_id: str, *, exclude_types: list[str] | None = None,
          only_types: list[str] | None = None) -> Job | None:
    """Atomically claim the next runnable job (highest priority = lowest number, then oldest run_after)."""
    exclude = exclude_types or []
    row = session.execute(
        text(
            "UPDATE jobs SET status = 'running', locked_by = :w, locked_at = now(), attempts = attempts + 1 "
            "WHERE id = (SELECT id FROM jobs WHERE status = 'queued' AND run_after <= now() "
            "  AND NOT (type = ANY(CAST(:ex AS text[]))) AND (CAST(:only AS text[]) IS NULL OR type = ANY(CAST(:only AS text[]))) "
            "  ORDER BY priority, run_after, id LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id"
        ),
        {"w": worker_id, "ex": exclude, "only": only_types},
    ).first()
    if not row:
        return None
    return session.get(Job, row[0])


def complete(session: Session, job_id: int, result: dict[str, Any] | None = None) -> None:
    session.execute(
        text("UPDATE jobs SET status='succeeded', finished_at=now(), result=CAST(:r AS jsonb), locked_by=NULL "
             "WHERE id=:i"),
        {"i": job_id, "r": json.dumps(result or {}, default=str)},
    )


def retry_delay_s(attempts: int, base: float = 30.0, cap: float = 3600.0) -> float:
    return min(cap, base * (2 ** max(0, attempts - 1))) * random.uniform(0.8, 1.2)


def fail(session: Session, job_id: int, error: str, *, retry: bool = True) -> str:
    job = session.get(Job, job_id)
    if job is None:
        return "missing"
    job.last_error = error[:4000]
    job.locked_by = None
    if retry and job.attempts < job.max_attempts:
        job.status = "queued"
        job.run_after = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=retry_delay_s(job.attempts))
    else:
        job.status = "dead"
        job.finished_at = dt.datetime.now(dt.UTC)
    return job.status


def defer(session: Session, job_id: int, until: dt.datetime, reason: str) -> None:
    session.execute(
        text("UPDATE jobs SET status='queued', run_after=:u, attempts=GREATEST(attempts - 1, 0), last_error=:r, "
             "locked_by=NULL WHERE id=:i"),
        {"i": job_id, "u": until, "r": reason[:4000]},
    )


def recover_stale(session: Session, lock_timeout_s: int, heartbeat_stale_s: int = 60) -> int:
    """Re-queue running jobs whose worker stopped heart-beating (crash/kill) or that exceeded the lock timeout."""
    res = session.execute(
        text(
            "UPDATE jobs SET status='queued', locked_by=NULL, run_after=now(), "
            "last_error = 'recovered after worker loss: ' || COALESCE(locked_by, '?') "
            "WHERE status='running' AND ("
            "  locked_at < now() - make_interval(secs => :lt) OR "
            "  locked_by NOT IN (SELECT worker_id FROM worker_heartbeats "
            "                    WHERE stopped_at IS NULL AND last_heartbeat_at > now() - make_interval(secs => :hs))"
            ") RETURNING id"
        ),
        {"lt": lock_timeout_s, "hs": heartbeat_stale_s},
    ).all()
    return len(res)


def queue_counts(session: Session) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for t, st, n in session.execute(text("SELECT type, status, count(*) FROM jobs GROUP BY 1, 2")).all():
        out.setdefault(t, {})[st] = n
    return out


def prune_finished(session: Session, keep_days: int = 7) -> int:
    res = session.execute(
        text("DELETE FROM jobs WHERE status IN ('succeeded', 'cancelled') AND finished_at < now() - make_interval(days => :d)"),
        {"d": keep_days},
    )
    return rowcount(res)
