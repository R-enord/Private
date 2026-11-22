"""Job handlers. Each handler gets (payload, job_id) and returns a JSON-able result.

Handlers raise ``Defer`` to re-queue without counting a failed attempt (e.g. local AI not running).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import select, text

from hub.db import rowcount, session_scope
from hub.models import Item
from hub.settings_store import get_setting
from hub.worker import queue


class Defer(Exception):  # noqa: N818 - control-flow signal, not an error
    def __init__(self, seconds: float, reason: str):
        super().__init__(reason)
        self.seconds = seconds
        self.reason = reason


_TRANSPORT_OVERRIDE: httpx.BaseTransport | None = None
_SLEEP_OVERRIDE: Callable[[float], None] | None = None


def set_test_overrides(transport: httpx.BaseTransport | None, sleep: Callable[[float], None] | None = None) -> None:
    """Test hook only: route connector traffic to recorded fixtures."""
    global _TRANSPORT_OVERRIDE, _SLEEP_OVERRIDE
    _TRANSPORT_OVERRIDE, _SLEEP_OVERRIDE = transport, sleep


def fetch_source(payload: dict[str, Any], job_id: int) -> dict[str, Any]:
    from hub.pipeline.ingest import run_source

    r = run_source(int(payload["source_id"]), job_id=job_id, transport=_TRANSPORT_OVERRIDE, sleep=_SLEEP_OVERRIDE,
                   is_fixture=_TRANSPORT_OVERRIDE is not None)
    with session_scope() as s:
        if r.new_item_ids:
            queue.enqueue(s, "evaluate_alerts", {"item_ids": r.new_item_ids}, priority=60)
            enqueue_arxiv_lookups(s, r.new_item_ids)
            if get_setting(s, "ai_enabled", True):
                # Enrich only the most promising new items right away; the backfill handles the rest.
                top = s.scalars(select(Item.id).where(Item.id.in_(r.new_item_ids)).order_by(Item.score.desc())
                                .limit(10)).all()
                for iid in top:
                    queue.enqueue(s, "ai_enrich", {"item_id": iid}, dedupe_key=f"item:{iid}", priority=150,
                                  max_attempts=3)
    return {"source": r.source_key, "status": r.status, "records": r.records, "new": r.new, "updated": r.updated,
            "merged": r.merged, "filtered": r.filtered, "http_requests": r.http_requests, "error": r.error[:500]}


def arxiv_lookup(payload: dict[str, Any], job_id: int) -> dict[str, Any]:
    from hub.models import Source
    from hub.pipeline.ingest import run_source

    with session_scope() as s:
        src = s.scalar(select(Source).where(Source.connector_type == "arxiv", Source.enabled.is_(True)))
        if src is None:
            return {"skipped": "arXiv source disabled"}
        sid = src.id
    r = run_source(sid, job_id=job_id, transport=_TRANSPORT_OVERRIDE, sleep=_SLEEP_OVERRIDE,
                   is_fixture=_TRANSPORT_OVERRIDE is not None, config_override={"id_list": payload["ids"]})
    return {"status": r.status, "records": r.records, "merged": r.merged, "updated": r.updated, "error": r.error[:300]}


def enqueue_arxiv_lookups(session: Any, item_ids: list[int]) -> int:
    """Papers first seen via HN/HF without an arXiv record get their official metadata fetched (batched)."""
    rows = session.execute(text(
        "SELECT DISTINCT substring(k.key from 7) FROM item_keys k JOIN items i ON i.id = k.item_id "
        "WHERE k.item_id = ANY(:ids) AND k.key LIKE 'arxiv:%' AND NOT EXISTS ("
        "  SELECT 1 FROM item_sources s JOIN sources src ON src.id = s.source_id "
        "  WHERE s.item_id = i.id AND src.connector_type = 'arxiv')"), {"ids": item_ids}).scalars().all()
    ids = sorted(r for r in rows if r)
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        queue.enqueue(session, "arxiv_lookup", {"ids": chunk}, dedupe_key="arxiv:" + ",".join(chunk)[:280],
                      priority=70, max_attempts=3)
    return len(ids)


def ai_enrich(payload: dict[str, Any], job_id: int) -> dict[str, Any]:
    from hub.ai.manager import AIUnavailable
    from hub.ai.tasks import enrich_item

    with session_scope() as s:
        if not get_setting(s, "ai_enabled", True):
            raise Defer(3600, "AI enrichment disabled in settings")
    try:
        return enrich_item(int(payload["item_id"]))
    except AIUnavailable as e:
        raise Defer(e.retry_after_s, f"AI unavailable: {e}") from e


def embed_items(payload: dict[str, Any], job_id: int) -> dict[str, Any]:
    from hub.ai.manager import AIUnavailable
    from hub.ai.tasks import embed_pending

    try:
        return embed_pending(int(payload.get("limit", 50)))
    except AIUnavailable as e:
        raise Defer(e.retry_after_s, f"embeddings unavailable: {e}") from e


def rescore(payload: dict[str, Any], job_id: int) -> dict[str, Any]:
    from hub.pipeline.ingest import rescore as rescore_item

    now = dt.datetime.now(dt.UTC)
    n = 0
    with session_scope() as s:
        weights = get_setting(s, "score_weights", {}) or {}
        ids = s.scalars(select(Item.id).where(Item.merged_into_id.is_(None),
                                              Item.discovered_at > now - dt.timedelta(days=14))).all()
    for chunk_start in range(0, len(ids), 200):
        with session_scope() as s:
            for iid in ids[chunk_start:chunk_start + 200]:
                item = s.get(Item, iid)
                if item:
                    rescore_item(s, item, now, weights=weights)
                    n += 1
    return {"rescored": n}


def maintenance(payload: dict[str, Any], job_id: int) -> dict[str, Any]:
    from hub.config import get_settings

    settings = get_settings()
    with session_scope() as s:
        http_pruned = rowcount(s.execute(
            text("DELETE FROM http_request_log WHERE created_at < now() - make_interval(days => :d)"),
            {"d": settings.http_log_retention_days},
        ))
        jobs_pruned = queue.prune_finished(s)
        s.execute(text("DELETE FROM login_attempts WHERE created_at < now() - interval '30 days'"))
        s.execute(text("DELETE FROM user_sessions WHERE expires_at < now() OR revoked"))
        expired = rowcount(s.execute(text(
            "UPDATE opportunities SET status='expired', last_checked_at=now() WHERE deadline IS NOT NULL "
            "AND deadline < now() AND status <> 'expired'")))
        s.execute(text("DELETE FROM worker_heartbeats WHERE last_heartbeat_at < now() - interval '7 days'"))
    return {"http_log_pruned": http_pruned, "jobs_pruned": jobs_pruned, "opportunities_expired": expired}


def check_providers(payload: dict[str, Any], job_id: int) -> dict[str, Any]:
    from hub.ai.manager import get_manager

    with session_scope() as s:
        return get_manager().refresh_status(s)


def generate_report(payload: dict[str, Any], job_id: int) -> dict[str, Any]:
    from hub.reports import generate_report as gen

    start = dt.datetime.fromisoformat(payload["period_start"])
    return gen(payload["period"], start)


def evaluate_alerts(payload: dict[str, Any], job_id: int) -> dict[str, Any]:
    from hub.alerts import evaluate_items

    return evaluate_items([int(i) for i in payload.get("item_ids", [])])


HANDLERS: dict[str, Callable[[dict[str, Any], int], dict[str, Any]]] = {
    "fetch_source": fetch_source,
    "arxiv_lookup": arxiv_lookup,
    "ai_enrich": ai_enrich,
    "embed_items": embed_items,
    "rescore": rescore,
    "maintenance": maintenance,
    "check_providers": check_providers,
    "generate_report": generate_report,
    "evaluate_alerts": evaluate_alerts,
}
AI_JOB_TYPES = ["ai_enrich", "embed_items"]
