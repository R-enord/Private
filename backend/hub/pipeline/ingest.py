"""Ingestion: fetch -> preserve raw -> normalize -> dedupe/merge -> classify -> score -> persist.

Guarantees:
* Raw payloads are stored verbatim before normalization (one row per distinct payload version).
* Idempotent: re-running a source never creates duplicate items (identity keys + unique constraints).
* Checkpoints advance only after a successful run (at-least-once fetch, exactly-once items).
* A failing source backs off exponentially (with jitter) and reports health; other sources are unaffected.
* Works with no AI at all; AI enrichment is queued separately.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hub.config import get_settings
from hub.connectors import get_connector
from hub.connectors.base import ConnectorContext, Normalized, RawRecord
from hub.db import session_scope
from hub.models import (
    Category,
    DedupeCandidate,
    Item,
    ItemKey,
    ItemSource,
    MetricSnapshot,
    Opportunity,
    ReleaseEvent,
    Source,
    SourceCheckpoint,
    SourceRun,
)
from hub.models import (
    RawRecord as RawRow,
)
from hub.net.cost_guard import EgressDenied
from hub.net.http import HttpError, RateLimited, SafeHttpClient
from hub.net.throttle import db_throttle, persist_http_log
from hub.pipeline import classify
from hub.pipeline.canonical import canonicalize_url, identity_keys
from hub.pipeline.media import apply_media, detect_media
from hub.pipeline.scoring import ScoreInput, compute_score, growth_from_snapshots

log = logging.getLogger(__name__)
CLASS_RANK = {"official": 3, "primary": 2, "press": 1, "independent": 1, "community": 1}


@dataclass
class RunResult:
    source_key: str
    status: str
    records: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    merged: int = 0
    filtered: int = 0
    http_requests: int = 0
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    new_item_ids: list[int] = field(default_factory=list)


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _jsonable(payload: Any) -> Any:
    return json.loads(json.dumps(payload, default=str))


# ----------------------------------------------------------------------------- raw records


def store_raw(session: Session, source_id: int, run_id: int | None, raw: RawRecord, now: dt.datetime,
              is_fixture: bool = False) -> int:
    h = payload_hash(raw.payload)
    existing = session.scalar(
        select(RawRow).where(RawRow.source_id == source_id, RawRow.external_id == raw.external_id,
                             RawRow.payload_hash == h)
    )
    if existing:
        existing.last_seen_at = now
        return existing.id
    row = RawRow(source_id=source_id, run_id=run_id, external_id=raw.external_id[:500], url=raw.url or "",
                 payload=_jsonable(raw.payload), payload_hash=h, fetched_at=now, last_seen_at=now,
                 is_fixture=is_fixture)
    session.add(row)
    session.flush()
    return row.id


# ----------------------------------------------------------------------------- item upsert


def _resolve(session: Session, item_id: int) -> Item:
    item = session.get(Item, item_id)
    assert item is not None
    hops = 0
    while item.merged_into_id and hops < 10:
        nxt = session.get(Item, item.merged_into_id)
        if nxt is None:
            break
        item, hops = nxt, hops + 1
    return item


def _find_existing(session: Session, keys: list[str]) -> list[int]:
    if not keys:
        return []
    rows = session.execute(select(ItemKey.item_id).where(ItemKey.key.in_(keys))).scalars().all()
    return sorted(set(rows))


def _add_keys(session: Session, item_id: int, keys: list[str]) -> None:
    for k in keys:
        session.execute(
            text("INSERT INTO item_keys (key, item_id, created_at) VALUES (:k, :i, now()) ON CONFLICT (key) DO NOTHING"),
            {"k": k[:600], "i": item_id},
        )


def upsert_item(session: Session, source: Source, norm: Normalized, raw_id: int | None, now: dt.datetime,
                is_fixture: bool = False) -> tuple[str, Item]:
    apply_media(norm)  # links to YouTube/TikTok/Reels/X/... become media items, whatever source found them
    keys = identity_keys(norm.url, norm.identity)
    if norm.discussion_url:
        norm.links = {**norm.links, f"discussion_{source.connector_type}": norm.discussion_url}
    existing_ids = _find_existing(session, keys)
    outcome: str
    if existing_ids:
        item = _resolve(session, existing_ids[0])
        for other in existing_ids[1:]:
            o = _resolve(session, other)
            if o.id != item.id:
                _dedupe_candidate(session, item.id, o.id, 1.0, "share an identity key")
        outcome = _merge_into(session, item, source, norm, raw_id, now)
    else:
        sp = session.begin_nested()
        try:
            item = Item(
                kind=norm.kind, title=norm.title[:2000] or norm.url, url=norm.url,
                canonical_url=canonicalize_url(norm.url), summary=norm.summary, author=norm.author[:300],
                organization=norm.organization[:300], published_at=norm.published_at, discovered_at=now,
                source_updated_at=norm.source_updated_at, updated_at=now, first_source_id=source.id,
                license=norm.license[:100], language=norm.language[:50], tags=norm.tags, metrics=norm.metrics,
                links=norm.links, extra=_jsonable({**norm.extra, **(
                    {"created_at": norm.created_at.isoformat()} if norm.created_at else {})}),
                is_fixture=is_fixture,
            )
            session.add(item)
            session.flush()
            for k in keys:
                session.add(ItemKey(key=k[:600], item_id=item.id))
            session.flush()
            sp.commit()
        except IntegrityError:
            sp.rollback()  # another worker created it concurrently -> merge instead
            existing_ids = _find_existing(session, keys)
            item = _resolve(session, existing_ids[0])
            outcome = _merge_into(session, item, source, norm, raw_id, now)
        else:
            session.add(ItemSource(
                item_id=item.id, source_id=source.id, external_id=norm.external_id[:500],
                source_url=norm.discussion_url or norm.url, raw_record_id=raw_id, title_at_source=norm.title[:2000],
                published_at=norm.published_at, metrics=norm.metrics, first_seen_at=now, last_seen_at=now,
            ))
            session.flush()
            outcome = "new"
            _near_duplicates(session, item)
    _enrich(session, item, source, norm, now)
    return outcome, item


def _merge_into(session: Session, item: Item, source: Source, norm: Normalized, raw_id: int | None,
                now: dt.datetime) -> str:
    link = session.scalar(
        select(ItemSource).where(ItemSource.source_id == source.id, ItemSource.external_id == norm.external_id[:500])
    )
    outcome = "unchanged"
    if link is None:
        link = ItemSource(item_id=item.id, source_id=source.id, external_id=norm.external_id[:500],
                          source_url=norm.discussion_url or norm.url, raw_record_id=raw_id,
                          title_at_source=norm.title[:2000], published_at=norm.published_at, metrics=norm.metrics,
                          first_seen_at=now, last_seen_at=now)
        session.add(link)
        outcome = "merged"
    else:
        if link.item_id != item.id:
            link.item_id = item.id
        if link.raw_record_id != raw_id or link.metrics != norm.metrics:
            outcome = "updated"
        link.raw_record_id = raw_id
        link.metrics = norm.metrics
        link.last_seen_at = now
    _add_keys(session, item.id, identity_keys(norm.url, norm.identity))

    # Field precedence: higher-authority sources (official > primary > community) and the item's own
    # first source may update descriptive fields; others only fill gaps. History lives in raw_records.
    first = session.get(Source, item.first_source_id) if item.first_source_id else None
    my_rank = CLASS_RANK.get(source.source_class, 1)
    first_rank = CLASS_RANK.get(first.source_class, 1) if first else 0
    authoritative = source.id == item.first_source_id or my_rank > first_rank
    before = (item.title, item.summary, item.license, json.dumps(item.metrics, sort_keys=True), item.published_at)
    if authoritative:
        if norm.title:
            item.title = norm.title[:2000]
        if norm.summary:
            item.summary = norm.summary
        if my_rank > first_rank:
            item.kind = norm.kind if norm.kind not in ("article", "discussion") else item.kind
            item.url = norm.url
            item.canonical_url = canonicalize_url(norm.url)
            item.first_source_id = source.id
        if norm.license and source.source_class in ("official", "primary"):
            item.license = norm.license[:100]
        if norm.source_updated_at:
            item.source_updated_at = norm.source_updated_at
    else:
        if not item.summary and norm.summary:
            item.summary = norm.summary
        if not item.license and norm.license and source.source_class in ("official", "primary"):
            item.license = norm.license[:100]
    if norm.published_at and (item.published_at is None or norm.published_at < item.published_at):
        item.published_at = norm.published_at  # keep the earliest known publication time
    item.metrics = {**(item.metrics or {}), **norm.metrics}
    item.tags = list(dict.fromkeys([*(item.tags or []), *norm.tags]))[:40]
    item.links = {**norm.links, **(item.links or {})}
    extra = dict(item.extra or {})
    for k, v in norm.extra.items():
        extra.setdefault(k, v)
    if norm.created_at and "created_at" not in extra:
        extra["created_at"] = norm.created_at.isoformat()
    item.extra = _jsonable(extra)
    if not item.author and norm.author:
        item.author = norm.author[:300]
    if not item.organization and norm.organization:
        item.organization = norm.organization[:300]
    after = (item.title, item.summary, item.license, json.dumps(item.metrics, sort_keys=True), item.published_at)
    if outcome == "unchanged" and before != after:
        outcome = "updated"
    if outcome != "unchanged":
        item.updated_at = now
    session.flush()
    return outcome


def _dedupe_candidate(session: Session, a: int, b: int, sim: float, reason: str) -> None:
    lo, hi = sorted((a, b))
    session.execute(
        text("INSERT INTO dedupe_candidates (item_id, candidate_id, similarity, reason, status, created_at) "
             "VALUES (:a, :b, :s, :r, 'open', now()) ON CONFLICT ON CONSTRAINT uq_dedupe_pair DO NOTHING"),
        {"a": lo, "b": hi, "s": sim, "r": reason},
    )


def _near_duplicates(session: Session, item: Item) -> None:
    """Flag (never auto-merge) items with near-identical titles seen within 7 days.

    Only for kinds without a strong identity key (articles, discussions, opportunities): repos, models,
    spaces and papers are identified exactly (github:/hf:/arxiv: keys), so similar names there are
    distinct artifacts (e.g. a model and its demo Space, or quantization variants), not duplicates.
    """
    soft_kinds = ("article", "discussion", "opportunity", "other")
    if len(item.title) < 12 or item.kind not in soft_kinds:
        return
    rows = session.execute(
        text("SELECT id, similarity(title, :t) AS sim FROM items WHERE id <> :id AND merged_into_id IS NULL "
             "AND kind = ANY(:kinds) AND discovered_at > now() - interval '7 days' AND title % :t "
             "ORDER BY sim DESC LIMIT 3"),
        {"t": item.title, "id": item.id, "kinds": list(soft_kinds)},
    ).all()
    for r in rows:
        if r.sim >= 0.85:
            _dedupe_candidate(session, item.id, r.id, float(r.sim), f"title similarity {r.sim:.2f}")


# ----------------------------------------------------------------------------- enrichment


_CATEGORY_IDS: dict[str, int] = {}


def _category_ids(session: Session) -> dict[str, int]:
    if not _CATEGORY_IDS:
        _CATEGORY_IDS.update({c.slug: c.id for c in session.scalars(select(Category))})
    return _CATEGORY_IDS


def _source_stats(session: Session, item_id: int) -> tuple[list[str], list[str]]:
    rows = session.execute(
        select(Source.source_class, Source.publisher).join(ItemSource, ItemSource.source_id == Source.id)
        .where(ItemSource.item_id == item_id)
    ).all()
    classes = [r[0] for r in rows]
    publishers = sorted({r[1] for r in rows if r[1]})
    return classes, publishers


def _enrich(session: Session, item: Item, source: Source, norm: Normalized, now: dt.datetime) -> None:
    classes, publishers = _source_stats(session, item.id)
    item.source_count = len(classes)
    item.verification = classify.verification_level(classes, len(publishers))
    rel, terms = classify.ai_relevance(item.title, item.summary, item.tags)
    item.relevance = rel
    created_at = None
    if (ca := (item.extra or {}).get("created_at")):
        try:
            created_at = dt.datetime.fromisoformat(ca)
        except ValueError:
            created_at = None
    created_recently = bool(created_at and (now - created_at) < dt.timedelta(days=14))

    # categories: replace rule/source rows, keep ai/manual ones
    hits = classify.categorize(item.title, item.summary, item.kind, item.tags or [], source.categories_hint or [],
                               source.source_class, created_recently)
    cat_ids = _category_ids(session)
    session.execute(text("DELETE FROM item_categories WHERE item_id = :i AND method IN ('rule', 'source')"),
                    {"i": item.id})
    for h in hits:
        if h.slug in cat_ids:
            session.execute(
                text("INSERT INTO item_categories (item_id, category_id, method, confidence, evidence) "
                     "VALUES (:i, :c, :m, :conf, :e) ON CONFLICT (item_id, category_id) DO NOTHING"),
                {"i": item.id, "c": cat_ids[h.slug], "m": h.method, "conf": h.confidence, "e": h.evidence[:500]},
            )

    if item.label_method not in ("manual", "ai"):
        lr = classify.assign_label(item.title, item.summary, item.kind, source.source_class
                                   if item.first_source_id == source.id else "community", item.extra)
        item.label, item.label_evidence, item.label_method = lr.label, lr.evidence[:500], lr.method

    # metric snapshots (append-only; only when value changed or hourly)
    for metric, value in norm.metrics.items():
        last = session.execute(
            select(MetricSnapshot.value, MetricSnapshot.captured_at).where(
                MetricSnapshot.item_id == item.id, MetricSnapshot.metric == metric
            ).order_by(MetricSnapshot.captured_at.desc()).limit(1)
        ).first()
        if last is None or last[0] != value or (now - last[1]) > dt.timedelta(hours=1):
            session.add(MetricSnapshot(item_id=item.id, source_id=source.id, metric=metric, value=value,
                                       captured_at=now))
    session.flush()

    # release timeline (append-only; dedupe_key prevents rewriting history)
    if norm.release_event:
        ev = norm.release_event
        key = f"{source.key}:{norm.external_id}:{ev.get('event_type')}:{ev.get('version', '')}"
        session.execute(
            text("INSERT INTO release_events (item_id, event_type, version, title, event_date, source_url, confidence, "
                 "verification, dedupe_key, created_at) VALUES (:i, :t, :v, :ti, :d, :u, 'source-confirmed', :ver, :k, now()) "
                 "ON CONFLICT (dedupe_key) DO NOTHING"),
            {"i": item.id, "t": ev.get("event_type", "version"), "v": (ev.get("version") or "")[:100],
             "ti": (ev.get("title") or item.title)[:2000], "d": ev.get("event_date"), "u": norm.url,
             "ver": item.verification, "k": key[:500]},
        )

    # opportunities: only source-stated fields
    if item.label == "opportunity" or item.kind == "opportunity":
        of = classify.extract_opportunity(item.title, item.summary, item.author)
        opp = session.scalar(select(Opportunity).where(Opportunity.item_id == item.id))
        if opp is None:
            opp = Opportunity(item_id=item.id)
            session.add(opp)
        opp.opp_type = of.opp_type
        opp.organization = of.organization or opp.organization
        opp.role = item.title[:2000]
        opp.description = item.summary
        opp.remote = of.remote
        opp.compensation = of.compensation  # verbatim or empty ('not stated')
        opp.deadline = of.deadline
        opp.application_url = item.url
        opp.status = "expired" if (of.deadline and of.deadline < now) else ("open" if of.deadline else "unknown")
        opp.evidence = of.evidence
        opp.last_checked_at = now

    # search text: tags, people/org, category names
    names = [s for s in (item.author, item.organization) if s]
    item.search_text = " ".join([*(item.tags or []), *names, item.license, item.language])[:4000]
    rescore(session, item, now, terms=terms, publishers=publishers)


def rescore(session: Session, item: Item, now: dt.datetime, terms: list[str] | None = None,
            publishers: list[str] | None = None, weights: dict[str, float] | None = None) -> None:
    if terms is None:
        _, terms = classify.ai_relevance(item.title, item.summary, item.tags)
    if publishers is None:
        _, publishers = _source_stats(session, item.id)
    created_at = None
    if (ca := (item.extra or {}).get("created_at")):
        try:
            created_at = dt.datetime.fromisoformat(ca)
        except ValueError:
            created_at = None
    growth = []
    since = now - dt.timedelta(days=7)
    for metric in ("stars", "hf_likes", "hn_points", "hf_upvotes", "hf_downloads", "yt_views", "devto_reactions",
                   "masto_boosts", "bsky_likes"):
        if metric not in (item.metrics or {}):
            continue
        pts = [(r[0], r[1]) for r in session.execute(
            select(MetricSnapshot.captured_at, MetricSnapshot.value).where(
                MetricSnapshot.item_id == item.id, MetricSnapshot.metric == metric,
                MetricSnapshot.captured_at >= since).order_by(MetricSnapshot.captured_at)
        ).all()]
        base_time = created_at if metric in ("stars", "hf_likes", "hf_downloads") else item.published_at
        g = growth_from_snapshots(metric, pts, base_time, now, (item.metrics or {}).get(metric))
        if g:
            growth.append(g)
    si = ScoreInput(
        kind=item.kind, published_at=item.published_at, discovered_at=item.discovered_at, created_at=created_at,
        verification=item.verification, relevance=item.relevance, relevance_terms=terms, metrics=item.metrics or {},
        growth=growth, distinct_publishers=max(1, len(publishers)), publishers=publishers,
        has_license=bool(item.license), summary_len=len(item.summary or ""), links=item.links or {},
        extra=item.extra or {},
    )
    item.score, item.score_components = compute_score(si, now, weights)
    item.scored_at = now


# ----------------------------------------------------------------------------- run a source


def _load_checkpoints(session: Session, source_id: int) -> dict[str, Any]:
    return {c.key: c.value for c in session.scalars(select(SourceCheckpoint).where(SourceCheckpoint.source_id == source_id))}


def _save_checkpoints(session: Session, source_id: int, staged: dict[str, Any]) -> None:
    for k, v in staged.items():
        session.execute(
            text("INSERT INTO source_checkpoints (source_id, key, value, updated_at) VALUES (:s, :k, CAST(:v AS jsonb), now()) "
                 "ON CONFLICT (source_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"),
            {"s": source_id, "k": k, "v": json.dumps(v, default=str)},
        )


def build_client(source: Source, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] | None = None, use_db_throttle: bool = True) -> SafeHttpClient:
    settings = get_settings()
    auth: dict[str, dict[str, str]] = {}
    if settings.github_token and "api.github.com" in source.allowed_hosts:
        auth["api.github.com"] = {"Authorization": f"Bearer {settings.github_token.get_secret_value()}"}
    if settings.hf_token and "huggingface.co" in source.allowed_hosts:
        auth["huggingface.co"] = {"Authorization": f"Bearer {settings.hf_token.get_secret_value()}"}
    kw: dict[str, Any] = {}
    if sleep is not None:
        kw["sleep"] = sleep
    return SafeHttpClient(
        allowed_hosts=set(source.allowed_hosts), user_agent=settings.user_agent, source_id=source.id,
        auth_headers=auth, host_spacing={h: source.host_min_spacing_s for h in source.allowed_hosts},
        throttle=db_throttle(sleep or __import__("time").sleep) if use_db_throttle else None,
        transport=transport, max_bytes=settings.http_max_response_bytes, **kw,
    )


def run_source(source_id: int, *, job_id: int | None = None, transport: httpx.BaseTransport | None = None,
               sleep: Callable[[float], None] | None = None, now: dt.datetime | None = None,
               is_fixture: bool = False, force: bool = False,
               config_override: dict[str, Any] | None = None) -> RunResult:
    settings = get_settings()
    now = now or dt.datetime.now(dt.UTC)
    with session_scope() as s:
        source = s.get(Source, source_id)
        if source is None:
            raise ValueError(f"source {source_id} not found")
        if not force and (not source.enabled or source.paused):
            return RunResult(source.key, "skipped")
        run = SourceRun(source_id=source.id, job_id=job_id, started_at=now, status="running")
        s.add(run)
        source.last_attempt_at = now
        s.flush()
        run_id = run.id
        checkpoints = _load_checkpoints(s, source.id)
        s.expunge(source)

    connector = get_connector(source.connector_type)
    client = build_client(source, transport=transport, sleep=sleep, use_db_throttle=True)
    result = RunResult(source.key, "ok")

    def recent_ids(hours: int) -> list[str]:
        with session_scope() as rs:
            return list(rs.scalars(select(ItemSource.external_id).where(
                ItemSource.source_id == source.id,
                ItemSource.first_seen_at > now - dt.timedelta(hours=hours))))

    ctx = ConnectorContext(source_id=source.id, source_key=source.key,
                           config={**(source.config or {}), **(config_override or {})}, http=client,
                           checkpoints=checkpoints, now=now, max_items=settings.max_items_per_run,
                           lookup_recent_external_ids=recent_ids)
    error: Exception | None = None
    try:
        batch: list[RawRecord] = []

        def flush(batch: list[RawRecord]) -> None:
            with session_scope() as ws:
                src = ws.get(Source, source.id)
                assert src is not None
                for raw in batch:
                    raw_id = store_raw(ws, src.id, run_id, raw, now, is_fixture)
                    result.records += 1
                    norm = connector.normalize(raw, ctx)
                    if norm is None or not norm.title or not norm.url:
                        result.filtered += 1
                        continue
                    if norm.require_relevance is not None:
                        rel, _ = classify.ai_relevance(norm.title, norm.summary, norm.tags)
                        if rel < norm.require_relevance:
                            result.filtered += 1
                            continue
                    outcome, item = upsert_item(ws, src, norm, raw_id, now, is_fixture)
                    setattr(result, outcome, getattr(result, outcome) + 1)  # new|updated|unchanged|merged
                    if outcome == "new":
                        result.new_item_ids.append(item.id)

        for raw in connector.fetch(ctx):
            batch.append(raw)
            if len(batch) >= 25:
                flush(batch)
                batch = []
        if batch:
            flush(batch)
    except Exception as e:  # noqa: BLE001 - every failure must be recorded, never crash the worker
        error = e
    finally:
        client.close()

    result.http_requests = client.request_count
    result.warnings = ctx.warnings[:20]
    with session_scope() as s:
        src = s.get(Source, source.id)
        run_row = s.get(SourceRun, run_id)
        assert src is not None and run_row is not None
        run = run_row
        persist_http_log(s, client.log)
        run.finished_at = dt.datetime.now(dt.UTC)
        run.records_fetched, run.items_new, run.items_updated = result.records, result.new, result.updated
        run.items_unchanged, run.items_merged, run.items_filtered = result.unchanged, result.merged, result.filtered
        run.http_requests = result.http_requests
        src.rate_limit_remaining = client.last_rate.remaining
        src.rate_limit_reset_at = client.last_rate.reset_at
        src.updated_at = now
        if error is None and config_override:
            run.status = result.status = "ok"
            run.error = "targeted lookup: " + ",".join(f"{k}" for k in config_override)
        elif error is None:
            _save_checkpoints(s, src.id, ctx.staged)
            run.status = "not_modified" if ctx.not_modified and result.records == 0 else "ok"
            if ctx.warnings:
                run.error = "warnings: " + "; ".join(ctx.warnings[:5])
            src.last_success_at = now
            src.consecutive_failures = 0
            src.health = "degraded" if ctx.warnings else "healthy"
            src.last_error = run.error[:2000]
            src.backoff_until = None
            src.next_run_at = now + dt.timedelta(seconds=src.interval_s * random.uniform(0.95, 1.1))
            result.status = run.status
        else:
            msg = f"{type(error).__name__}: {error}"[:2000]
            run.error = msg
            result.error = msg
            src.last_error = msg
            if isinstance(error, RateLimited):
                run.status = result.status = "rate_limited"
                src.health = "rate_limited"
                src.backoff_until = error.reset_at or now + dt.timedelta(minutes=15)
                src.next_run_at = src.backoff_until
            else:
                run.status = result.status = "error"
                src.consecutive_failures += 1
                src.health = "failing" if src.consecutive_failures >= 3 else "degraded"
                if isinstance(error, EgressDenied):
                    src.health = "blocked"
                backoff = min(6 * 3600, src.interval_s * (2 ** min(src.consecutive_failures, 6)))
                src.backoff_until = now + dt.timedelta(seconds=backoff * random.uniform(0.8, 1.2))
                src.next_run_at = src.backoff_until
            if not isinstance(error, (HttpError, EgressDenied, RateLimited)):
                log.exception("source %s failed", src.key, exc_info=error)
    return result


def backfill_media(session: Session) -> int:
    """Re-classify already-collected items whose links are media (YouTube/TikTok/Reels/X/...). Idempotent."""
    now = dt.datetime.now(dt.UTC)
    n = 0
    rows = session.scalars(select(Item).where(Item.merged_into_id.is_(None), Item.kind.in_(("article", "discussion", "other")),
                                              Item.url.op("~*")(r"(youtube\.com|youtu\.be|tiktok\.com|instagram\.com|//(www\.)?x\.com|"
                                                                r"twitter\.com|bsky\.app|reddit\.com|vimeo\.com|medium\.com|"
                                                                r"substack\.com|dev\.to|hashnode)"))).all()
    for item in rows:
        m = detect_media(item.url)
        if m is None:
            continue
        extra = dict(item.extra or {})
        if extra.get("media"):
            continue
        extra["media"] = m.as_dict()
        item.extra = extra
        if m.format != "writeup":
            item.links = {**(item.links or {}), "original": item.url}
            item.kind = m.kind
            if m.canonical_url:
                item.url = m.canonical_url
                item.canonical_url = canonicalize_url(m.canonical_url)
        _add_keys(session, item.id, [k for k in [m.identity_key] if k])
        if item.label_method not in ("manual", "ai"):
            lr = classify.assign_label(item.title, item.summary, item.kind, "community", item.extra)
            item.label, item.label_evidence = lr.label, lr.evidence[:500]
        rescore(session, item, now)
        n += 1
    return n


def item_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Item).where(Item.merged_into_id.is_(None))) or 0


def reset_category_cache() -> None:
    _CATEGORY_IDS.clear()


_ = DedupeCandidate, ReleaseEvent  # imported for type references in raw SQL above
