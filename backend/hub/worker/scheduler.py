"""Scheduler: decides *what* is due and enqueues it. Exactly one leader at a time (Postgres advisory lock).

Runs inside the worker process, so collection continues while the browser is closed.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Any

from sqlalchemy import Connection, or_, select, text
from sqlalchemy.orm import Session

from hub.models import Item, Source
from hub.settings_store import get_setting, set_setting
from hub.worker import queue

LEADER_LOCK_ID = 727_001  # arbitrary app-wide advisory lock id

PERIODIC: dict[str, dict[str, Any]] = {
    "rescore": {"every_s": 900, "payload": {}, "priority": 120},
    "maintenance": {"every_s": 3600, "payload": {}, "priority": 200},
    "check_providers": {"every_s": 300, "payload": {}, "priority": 90},
    "ai_backfill": {"every_s": 600},
    "embed_items": {"every_s": 1800, "payload": {"limit": 50}, "priority": 190},
    "reports": {"every_s": 1800},
}


def try_acquire_leadership(conn: Connection) -> bool:
    return bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LEADER_LOCK_ID}).scalar())


def release_leadership(conn: Connection) -> None:
    conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LEADER_LOCK_ID})


def enqueue_due_sources(session: Session, now: dt.datetime) -> list[str]:
    due = session.scalars(
        select(Source).where(
            Source.enabled.is_(True), Source.paused.is_(False),
            or_(Source.next_run_at.is_(None), Source.next_run_at <= now),
            or_(Source.backoff_until.is_(None), Source.backoff_until <= now),
        ).order_by(Source.next_run_at.asc().nulls_first())
    ).all()
    queued = []
    for src in due:
        jid = queue.enqueue(session, "fetch_source", {"source_id": src.id}, dedupe_key=f"source:{src.id}",
                            priority=50, max_attempts=2)
        # provisional next run; the run itself sets the real next_run_at (or backoff)
        src.next_run_at = now + dt.timedelta(seconds=max(src.min_interval_s, src.interval_s) * random.uniform(0.95, 1.1))
        if jid:
            queued.append(src.key)
    return queued


def _due(state: dict[str, str], key: str, every_s: int, now: dt.datetime) -> bool:
    last = state.get(key)
    if not last:
        return True
    return (now - dt.datetime.fromisoformat(last)).total_seconds() >= every_s


def tick(session: Session, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or dt.datetime.now(dt.UTC)
    if get_setting(session, "global_pause", False):
        return {"paused": True}
    out: dict[str, Any] = {"sources": enqueue_due_sources(session, now), "periodic": []}
    state: dict[str, str] = dict(get_setting(session, "scheduler_state", {}) or {})
    low_resource = bool(get_setting(session, "low_resource_mode", False))
    for key, spec in PERIODIC.items():
        every = spec["every_s"] * (3 if low_resource and key in ("ai_backfill", "embed_items", "rescore") else 1)
        if not _due(state, key, every, now):
            continue
        state[key] = now.isoformat()
        if key == "ai_backfill":
            if get_setting(session, "ai_enabled", True):
                batch = int(get_setting(session, "ai_backfill_batch", 5))
                ids = session.scalars(
                    select(Item.id).where(Item.ai_summary.is_(None), Item.merged_into_id.is_(None))
                    .order_by(Item.score.desc()).limit(batch)
                ).all()
                for iid in ids:
                    queue.enqueue(session, "ai_enrich", {"item_id": iid}, dedupe_key=f"item:{iid}", priority=180,
                                  max_attempts=3)
                out["periodic"].append(f"ai_backfill:{len(ids)}")
            continue
        if key == "embed_items" and (low_resource or not get_setting(session, "embeddings_enabled", True)):
            continue
        if key == "reports":
            if get_setting(session, "reports_enabled", True):
                out["periodic"].extend(_enqueue_reports(session, now))
            continue
        queue.enqueue(session, key, spec.get("payload", {}), dedupe_key=key, priority=spec.get("priority", 100))
        out["periodic"].append(key)
    set_setting(session, "scheduler_state", state, audit=False)
    return out


def _enqueue_reports(session: Session, now: dt.datetime) -> list[str]:
    hour = int(get_setting(session, "report_hour_utc", 6))
    queued = []
    if now.hour >= hour:
        day_start = dt.datetime(now.year, now.month, now.day, tzinfo=dt.UTC) - dt.timedelta(days=1)
        exists = session.execute(text("SELECT 1 FROM reports WHERE period='daily' AND period_start=:p AND owner_id IS NULL"),
                                 {"p": day_start}).first()
        if not exists:
            queue.enqueue(session, "generate_report", {"period": "daily", "period_start": day_start.isoformat()},
                          dedupe_key=f"report:daily:{day_start.date()}", priority=150)
            queued.append(f"report:daily:{day_start.date()}")
        if now.weekday() == 0:  # Monday: previous ISO week
            week_start = day_start - dt.timedelta(days=6)
            exists = session.execute(text("SELECT 1 FROM reports WHERE period='weekly' AND period_start=:p AND owner_id IS NULL"),
                                     {"p": week_start}).first()
            if not exists:
                queue.enqueue(session, "generate_report", {"period": "weekly", "period_start": week_start.isoformat()},
                              dedupe_key=f"report:weekly:{week_start.date()}", priority=150)
                queued.append(f"report:weekly:{week_start.date()}")
    return queued
