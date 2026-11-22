from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, and_, func, or_, select, text
from sqlalchemy.orm import Session

from hub.api.deps import authenticate_request, get_db, require
from hub.api.serialize import cards, safe_links, top_reasons
from hub.db import session_scope
from hub.models import (
    Bookmark,
    Category,
    DedupeCandidate,
    Item,
    ItemCategory,
    ItemKey,
    ItemSource,
    MetricSnapshot,
    Opportunity,
    RawRecord,
    ReleaseEvent,
    Source,
    User,
)
from hub.pipeline.canonical import safe_link
from hub.security.auth import can

router = APIRouter(prefix="/api")

WINDOWS = {"5m": 5, "15m": 15, "30m": 30, "60m": 60}
SORTS = {"score", "discovered", "published"}


def _apply_filters(q: Select[Any], *, category: str | None, source: str | None, kind: str | None, label: str | None,
                   verification: str | None, since: dt.datetime | None, until: dt.datetime | None,
                   min_score: float | None, license_: str | None = None, basis: str = "discovered",
                   include_fixtures: bool = False) -> Select[Any]:
    q = q.where(Item.merged_into_id.is_(None))
    if not include_fixtures:
        q = q.where(Item.is_fixture.is_(False))
    if category:
        q = q.where(Item.id.in_(select(ItemCategory.item_id).join(Category, Category.id == ItemCategory.category_id)
                                .where(Category.slug.in_(category.split(",")))))
    if source:
        q = q.where(Item.id.in_(select(ItemSource.item_id).join(Source, Source.id == ItemSource.source_id)
                                .where(Source.key.in_(source.split(",")))))
    if kind:
        q = q.where(Item.kind.in_(kind.split(",")))
    if label:
        q = q.where(Item.label.in_(label.split(",")))
    if verification:
        q = q.where(Item.verification.in_(verification.split(",")))
    if license_:
        q = q.where(func.lower(Item.license) == license_.lower())
    col = Item.published_at if basis == "published" else Item.discovered_at
    if since:
        q = q.where(col >= since)
    if until:
        q = q.where(col < until)
    if min_score is not None:
        q = q.where(Item.score >= min_score)
    return q


def _order(q: Select[Any], sort: str) -> Select[Any]:
    if sort == "discovered":
        return q.order_by(Item.discovered_at.desc(), Item.id.desc())
    if sort == "published":
        return q.order_by(Item.published_at.desc().nulls_last(), Item.id.desc())
    return q.order_by(Item.score.desc(), Item.id.desc())


class FeedParams:
    def __init__(self, category: str | None = None, source: str | None = None, kind: str | None = None,
                 label: str | None = None, verification: str | None = None, since: dt.datetime | None = None,
                 until: dt.datetime | None = None, min_score: float | None = None, license: str | None = None,
                 basis: str = Query("discovered", pattern="^(discovered|published)$"),
                 include_fixtures: bool = False):
        self.kw: dict[str, Any] = dict(category=category, source=source, kind=kind, label=label, verification=verification,
                       since=since, until=until, min_score=min_score, license_=license, basis=basis,
                       include_fixtures=include_fixtures)


@router.get("/items")
def list_items(fp: FeedParams = Depends(), sort: str = "score", page: int = Query(1, ge=1),
               page_size: int = Query(30, ge=1, le=100), bookmarked: bool = False,
               user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    if sort not in SORTS:
        raise HTTPException(400, "invalid sort")
    q = _apply_filters(select(Item), **fp.kw)
    if bookmarked:
        q = q.where(Item.id.in_(select(Bookmark.item_id).where(Bookmark.user_id == user.id)))
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(_order(q, sort).limit(page_size).offset((page - 1) * page_size)).all()
    return {"items": cards(db, list(rows), user.id), "total": total, "page": page, "page_size": page_size}


def _today_start(tz_offset_min: int, now: dt.datetime) -> dt.datetime:
    local = now - dt.timedelta(minutes=tz_offset_min)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local + dt.timedelta(minutes=tz_offset_min)


@router.get("/radar")
def radar(window: str = Query("60m", pattern="^(5m|15m|30m|60m|today)$"), tz_offset: int = 0,
          fp: FeedParams = Depends(), limit: int = Query(100, ge=1, le=300),
          user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC)
    basis = fp.kw["basis"]
    col = Item.published_at if basis == "published" else Item.discovered_at
    bounds = {k: now - dt.timedelta(minutes=m) for k, m in WINDOWS.items()}
    bounds["today"] = _today_start(tz_offset, now)
    kw = {**fp.kw, "since": bounds[window]}
    q = _apply_filters(select(Item), **kw).order_by(col.desc().nulls_last(), Item.id.desc()).limit(limit)
    rows = db.scalars(q).all()
    counts = {}
    for k, since in bounds.items():
        cq = _apply_filters(select(func.count(Item.id)), **{**fp.kw, "since": since})
        counts[k] = db.scalar(cq) or 0
    last = db.scalar(select(func.max(Item.discovered_at)))
    return {"window": window, "basis": basis, "since": bounds[window].isoformat(), "now": now.isoformat(),
            "counts": counts, "items": cards(db, list(rows), user.id),
            "last_discovery_at": last.isoformat() if last else None}


@router.get("/home")
def home(user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC)
    day = now - dt.timedelta(hours=24)
    week = now - dt.timedelta(days=7)

    def top(q: Select[Any], n: int = 6) -> list[dict[str, Any]]:
        q = q.where(Item.merged_into_id.is_(None), Item.is_fixture.is_(False))
        return cards(db, list(db.scalars(q.order_by(Item.score.desc()).limit(n)).all()), user.id)

    def in_cats(*slugs: str) -> Any:
        return Item.id.in_(select(ItemCategory.item_id).join(Category).where(Category.slug.in_(slugs)))

    trending = db.execute(text(
        "SELECT id FROM items WHERE kind='repository' AND merged_into_id IS NULL AND NOT is_fixture "
        "AND discovered_at > :w ORDER BY (score_components->'adoption'->>'value')::float DESC NULLS LAST, score DESC "
        "LIMIT 6"), {"w": week}).scalars().all()
    trending_items = cards(db, [it for i in trending if (it := db.get(Item, i)) is not None], user.id)
    stats = {
        "items_total": db.scalar(select(func.count(Item.id)).where(Item.merged_into_id.is_(None))) or 0,
        "items_24h": db.scalar(select(func.count(Item.id)).where(Item.discovered_at > day)) or 0,
        "sources_enabled": db.scalar(select(func.count(Source.id)).where(Source.enabled.is_(True))) or 0,
        "sources_healthy": db.scalar(select(func.count(Source.id)).where(Source.enabled.is_(True),
                                                                         Source.health == "healthy")) or 0,
        "last_discovery_at": (lambda d: d.isoformat() if d else None)(db.scalar(select(func.max(Item.discovered_at)))),
    }
    return {
        "stats": stats,
        "sections": [
            {"key": "important", "title": "Important discoveries (24 h)", "items": top(select(Item).where(Item.discovered_at > day), 8)},
            {"key": "media", "title": "Shorts & videos", "items": top(select(Item).where(Item.kind.in_(("short", "video")), Item.discovered_at > week), 4)},
            {"key": "trending_repos", "title": "Trending projects (growth, 7 d)", "items": trending_items},
            {"key": "model_releases", "title": "Model releases", "items": top(select(Item).where(Item.kind == "model", Item.discovered_at > week))},
            {"key": "mcp_agents", "title": "MCP servers & agents", "items": top(select(Item).where(in_cats("mcp", "agents"), Item.discovered_at > week))},
            {"key": "research", "title": "Research", "items": top(select(Item).where(Item.kind == "paper", Item.discovered_at > week))},
            {"key": "opportunities", "title": "Opportunities", "items": top(select(Item).where(or_(Item.label == "opportunity", Item.kind == "opportunity"), Item.discovered_at > now - dt.timedelta(days=30)))},
            {"key": "community", "title": "Community contributions", "items": [],
             "empty_note": "No community posts yet. Publishing is private and single-user in this release."},
        ],
    }


# ----------------------------------------------------------------------------- search

_TIME_HINTS = [
    (re.compile(r"\b(today)\b", re.I), dt.timedelta(hours=24)),
    (re.compile(r"\b(this week|past week|last week|last 7 days)\b", re.I), dt.timedelta(days=7)),
    (re.compile(r"\b(this month|last 30 days|past month)\b", re.I), dt.timedelta(days=30)),
    (re.compile(r"\b(last 24 hours|past 24 hours|last day)\b", re.I), dt.timedelta(hours=24)),
]
_KIND_HINTS = [
    (re.compile(r"\b(papers?|research)\b", re.I), "paper"),
    (re.compile(r"\b(repos?|repositories|projects? on github)\b", re.I), "repository"),
    (re.compile(r"\b(models?|checkpoints?)\b", re.I), "model"),
    (re.compile(r"\b(jobs?|opportunit(?:y|ies)|internships?|hackathons?|grants?)\b", re.I), "opportunity"),
]
_CAT_HINTS = [(re.compile(r"\b(mcp|model context protocol)\b", re.I), "mcp"),
              (re.compile(r"\bagents?\b", re.I), "agents")]


def interpret_query(q: str) -> tuple[str, dict[str, Any], list[str]]:
    """Deterministic natural-language hints -> filters (shown to the user as 'interpreted as')."""
    notes: list[str] = []
    filters: dict[str, Any] = {}
    text_q = q
    for rx, delta in _TIME_HINTS:
        if rx.search(text_q):
            filters["since"] = dt.datetime.now(dt.UTC) - delta
            notes.append(f"time: {rx.search(text_q).group(0)}")  # type: ignore[union-attr]
            text_q = rx.sub(" ", text_q)
            break
    for rx, kind in _KIND_HINTS:
        m = rx.search(text_q)
        if m and kind in ("paper", "opportunity"):
            filters["kind"] = kind if kind != "opportunity" else None
            if kind == "opportunity":
                filters["label"] = "opportunity"
            notes.append(f"type: {m.group(0)}")
            text_q = rx.sub(" ", text_q)
            break
    text_q = re.sub(r"\b(show me|find|latest|new|recent|what are|what's|the|about|on|for|any)\b", " ", text_q, flags=re.I)
    text_q = re.sub(r"\s+", " ", text_q).strip()
    return text_q or q, {k: v for k, v in filters.items() if v is not None}, notes


@router.get("/search")
def search(q: str = Query(..., min_length=1, max_length=300), mode: str = Query("hybrid", pattern="^(keyword|semantic|hybrid)$"),
           fp: FeedParams = Depends(), limit: int = Query(40, ge=1, le=100), interpret: bool = True,
           user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    text_q, hints, notes = interpret_query(q) if interpret else (q, {}, [])
    kw = {**fp.kw, **{k: v for k, v in hints.items() if fp.kw.get(k if k != "license" else "license_") in (None, "")}}
    base = _apply_filters(select(Item.id), **kw).subquery()
    # Full-text query built with SQLAlchemy so every filter stays parameterized.
    tsq = func.websearch_to_tsquery("english", text_q)
    fts = (select(Item.id, func.ts_rank_cd(Item.search_vector, tsq).label("rank"))
           .where(Item.search_vector.op("@@")(tsq), Item.id.in_(select(base.c.id)))
           .order_by(text("rank DESC"), Item.score.desc()).limit(limit))
    rows = db.execute(fts).all()
    keyword_ids = [r[0] for r in rows]
    method = "full-text"
    if not keyword_ids:
        trig = (select(Item.id).where(Item.id.in_(select(base.c.id)),
                                      or_(Item.title.op("%")(text_q), Item.title.ilike(f"%{text_q}%")))
                .order_by(func.similarity(Item.title, text_q).desc()).limit(limit))
        keyword_ids = list(db.scalars(trig).all())
        method = "fuzzy title match"
    semantic_ids: list[int] = []
    semantic_note = None
    if mode in ("semantic", "hybrid"):
        from hub.ai.manager import AIUnavailable
        from hub.ai.tasks import semantic_search

        try:
            allowed = set(db.scalars(select(base.c.id)).all())
            hits, model = semantic_search(db, text_q, limit=limit * 3)
            semantic_ids = [i for i, sim in hits if i in allowed and sim > 0.2][:limit]
            semantic_note = f"semantic: local embeddings ({model})"
            if not hits:
                semantic_note = "semantic: no embeddings stored yet"
        except AIUnavailable as e:
            semantic_note = f"semantic search unavailable ({str(e)[:120]}); keyword results shown"
        db.commit()
    if mode == "semantic" and semantic_ids:
        ordered = semantic_ids
    elif mode == "hybrid" and semantic_ids:
        scores: dict[int, float] = {}
        for rank, i in enumerate(keyword_ids):
            scores[i] = scores.get(i, 0) + 1 / (60 + rank)
        for rank, i in enumerate(semantic_ids):
            scores[i] = scores.get(i, 0) + 1 / (60 + rank)
        ordered = [i for i, _ in sorted(scores.items(), key=lambda x: -x[1])][:limit]
        method = "hybrid (reciprocal rank fusion of full-text + semantic)"
    else:
        ordered = keyword_ids
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_(ordered)))}
    return {"query": q, "interpreted_query": text_q, "interpreted_filters": notes, "method": method,
            "semantic": semantic_note, "items": cards(db, [items[i] for i in ordered if i in items], user.id)}


# ----------------------------------------------------------------------------- detail


@router.get("/items/{item_id}")
def item_detail(item_id: int, user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(404, "not found")
    if item.merged_into_id:
        return {"redirect_to": item.merged_into_id}
    card = cards(db, [item], user.id)[0]
    provenance = []
    for link, src in db.execute(select(ItemSource, Source).join(Source, Source.id == ItemSource.source_id)
                                .where(ItemSource.item_id == item.id).order_by(ItemSource.first_seen_at)):
        provenance.append({
            "source_key": src.key, "source_name": src.name, "publisher": src.publisher, "source_class": src.source_class,
            "attribution": src.attribution, "external_id": link.external_id, "source_url": safe_link(link.source_url),
            "title_at_source": link.title_at_source, "published_at": link.published_at.isoformat() if link.published_at else None,
            "first_seen_at": link.first_seen_at.isoformat(), "last_seen_at": link.last_seen_at.isoformat(),
            "metrics": link.metrics, "raw_record_id": link.raw_record_id,
            "raw_versions": db.scalar(select(func.count(RawRecord.id)).where(RawRecord.source_id == src.id,
                                                                              RawRecord.external_id == link.external_id)),
        })
    cats = [{"slug": c.slug, "name": c.name, "method": ic.method, "confidence": ic.confidence, "evidence": ic.evidence}
            for ic, c in db.execute(select(ItemCategory, Category).join(Category).where(ItemCategory.item_id == item.id))]
    history: dict[str, list[list[Any]]] = {}
    for m, v, t in db.execute(select(MetricSnapshot.metric, MetricSnapshot.value, MetricSnapshot.captured_at)
                              .where(MetricSnapshot.item_id == item.id).order_by(MetricSnapshot.captured_at).limit(500)):
        history.setdefault(m, []).append([t.isoformat(), v])
    events = [{"event_type": e.event_type, "version": e.version, "title": e.title,
               "event_date": e.event_date.isoformat() if e.event_date else None, "source_url": safe_link(e.source_url),
               "confidence": e.confidence, "verification": e.verification, "recorded_at": e.created_at.isoformat()}
              for e in db.scalars(select(ReleaseEvent).where(ReleaseEvent.item_id == item.id).order_by(ReleaseEvent.event_date))]
    opp = db.scalar(select(Opportunity).where(Opportunity.item_id == item.id))
    keys = [k for (k,) in db.execute(select(ItemKey.key).where(ItemKey.item_id == item.id))]
    return {
        **card,
        "summary": item.summary,
        "license_note": "as stated by the source" if item.license else "not stated by source",
        "extra": item.extra,
        "links": safe_links(item.links),
        "identity_keys": keys,
        "score_components": item.score_components,
        "score_reasons": top_reasons(item.score_components, 7),
        "scored_at": item.scored_at.isoformat() if item.scored_at else None,
        "categories_detail": cats,
        "provenance": provenance,
        "metric_history": history,
        "release_events": events,
        "opportunity": None if opp is None else {
            "type": opp.opp_type, "organization": opp.organization or None, "role": opp.role,
            "remote": opp.remote, "compensation": opp.compensation or None, "deadline": opp.deadline.isoformat() if opp.deadline else None,
            "status": opp.status, "application_url": safe_link(opp.application_url), "evidence": opp.evidence,
            "last_checked_at": opp.last_checked_at.isoformat() if opp.last_checked_at else None},
        "ai_summary_full": item.ai_summary,
        "related": related_items(db, item, user.id),
        "dedupe_candidates": [
            {"id": d.id, "other_id": d.candidate_id if d.item_id == item.id else d.item_id, "similarity": d.similarity,
             "reason": d.reason, "status": d.status}
            for d in db.scalars(select(DedupeCandidate).where(or_(DedupeCandidate.item_id == item.id,
                                                                  DedupeCandidate.candidate_id == item.id)))],
    }


def related_items(db: Session, item: Item, user_id: int) -> list[dict[str, Any]]:
    ids: list[tuple[int, str]] = []
    extra = item.extra or {}
    repo = None
    for k in db.scalars(select(ItemKey.key).where(ItemKey.item_id == item.id)):
        if k.startswith("github:"):
            repo = k.split(":", 1)[1]
    if repo:  # releases of this repo, papers whose code link is this repo
        for (i,) in db.execute(select(Item.id).where(Item.extra["repo"].astext.ilike(repo), Item.id != item.id).limit(10)):
            ids.append((i, "release of this repository"))
        for (i,) in db.execute(select(Item.id).where(Item.links["code"].astext.ilike(f"%github.com/{repo}%"),
                                                     Item.id != item.id).limit(5)):
            ids.append((i, "paper links to this repository (source-stated)"))
    if extra.get("repo"):
        for (i,) in db.execute(select(ItemKey.item_id).where(ItemKey.key == f"github:{extra['repo'].lower()}")):
            ids.append((i, "repository of this release"))
    if item.kind == "paper" and (aid := extra.get("arxiv_id") or next(
            (k.split(":", 1)[1] for k in db.scalars(select(ItemKey.key).where(ItemKey.item_id == item.id)) if k.startswith("arxiv:")), None)):
        for (i,) in db.execute(select(Item.id).where(Item.extra["arxiv_refs"].contains([aid]), Item.id != item.id).limit(8)):
            ids.append((i, "model card cites this paper (source-stated tag)"))
    for (i,) in db.execute(select(Item.id).where(Item.id != item.id, Item.merged_into_id.is_(None),
                                                 Item.title.op("%")(item.title))
                           .order_by(func.similarity(Item.title, item.title).desc()).limit(4)):
        ids.append((i, "similar title (inferred)"))
    seen: dict[int, str] = {}
    for i, why in ids:
        seen.setdefault(i, why)
    objs = {o.id: o for o in db.scalars(select(Item).where(Item.id.in_(list(seen))))}
    out = []
    for c in cards(db, [objs[i] for i in seen if i in objs], user_id):
        c["relation"] = seen[c["id"]]
        out.append(c)
    return out[:12]


@router.get("/items/{item_id}/raw/{raw_id}")
def raw_record(item_id: int, raw_id: int, user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    ok = db.scalar(select(func.count()).select_from(ItemSource).where(ItemSource.item_id == item_id))
    raw = db.get(RawRecord, raw_id)
    if not ok or raw is None:
        raise HTTPException(404, "not found")
    versions = db.execute(select(RawRecord.id, RawRecord.fetched_at, RawRecord.payload_hash).where(
        RawRecord.source_id == raw.source_id, RawRecord.external_id == raw.external_id).order_by(RawRecord.fetched_at)).all()
    return {"id": raw.id, "external_id": raw.external_id, "url": safe_link(raw.url), "fetched_at": raw.fetched_at.isoformat(),
            "last_seen_at": raw.last_seen_at.isoformat(), "payload_hash": raw.payload_hash, "is_fixture": raw.is_fixture,
            "payload": raw.payload, "notice": "Untrusted data exactly as received from the source.",
            "versions": [{"id": v[0], "fetched_at": v[1].isoformat(), "hash": v[2][:12]} for v in versions]}


@router.post("/items/{item_id}/bookmark")
def add_bookmark(item_id: int, user: User = Depends(require("bookmarks:own")), db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(Item, item_id) is None:
        raise HTTPException(404, "not found")
    db.execute(text("INSERT INTO bookmarks (user_id, item_id, note, created_at) VALUES (:u, :i, '', now()) "
                    "ON CONFLICT DO NOTHING"), {"u": user.id, "i": item_id})
    db.commit()
    return {"bookmarked": True}


@router.delete("/items/{item_id}/bookmark")
def remove_bookmark(item_id: int, user: User = Depends(require("bookmarks:own")), db: Session = Depends(get_db)) -> dict[str, Any]:
    db.execute(text("DELETE FROM bookmarks WHERE user_id = :u AND item_id = :i"), {"u": user.id, "i": item_id})
    db.commit()
    return {"bookmarked": False}


@router.get("/categories")
def categories(user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    counts = dict(db.execute(select(ItemCategory.category_id, func.count()).join(Item, Item.id == ItemCategory.item_id)
                             .where(Item.merged_into_id.is_(None), Item.is_fixture.is_(False))
                             .group_by(ItemCategory.category_id)).all())
    return [{"slug": c.slug, "name": c.name, "description": c.description, "count": counts.get(c.id, 0)}
            for c in db.scalars(select(Category).order_by(Category.sort_order))]


@router.get("/facets")
def facets(user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    def grp(col: Any) -> dict[str, int]:
        return {k: n for k, n in db.execute(select(col, func.count()).where(Item.merged_into_id.is_(None),
                                                                              Item.is_fixture.is_(False)).group_by(col)).all() if k}
    sources = [{"key": s.key, "name": s.name, "enabled": s.enabled} for s in db.scalars(select(Source).order_by(Source.name))]
    return {"kinds": grp(Item.kind), "labels": grp(Item.label), "verification": grp(Item.verification),
            "licenses": dict(sorted(grp(Item.license).items(), key=lambda x: -x[1])[:20]), "sources": sources}


# ----------------------------------------------------------------------------- SSE


def _new_since(last_id: int, user_id: int) -> tuple[list[dict[str, Any]], int]:
    with session_scope() as s:
        if last_id <= 0:
            return [], s.scalar(select(func.coalesce(func.max(Item.id), 0))) or 0
        rows = s.scalars(select(Item).where(Item.id > last_id, Item.merged_into_id.is_(None), Item.is_fixture.is_(False))
                         .order_by(Item.id).limit(50)).all()
        return cards(s, list(rows), user_id), (rows[-1].id if rows else last_id)


@router.get("/stream")
async def stream(request: Request, last_id: int = 0, once: bool = False) -> StreamingResponse:
    user = await run_in_threadpool(authenticate_request, request)
    if user is None or not can(user, "items:read"):
        raise HTTPException(401, "authentication required")
    header_id = request.headers.get("last-event-id")
    cursor = int(header_id) if header_id and header_id.isdigit() else last_id

    async def gen() -> Any:
        nonlocal cursor
        if cursor <= 0:
            _, cursor = await run_in_threadpool(_new_since, 0, user.id)
        yield f"event: hello\ndata: {json.dumps({'cursor': cursor})}\n\n"
        idle = 0
        while True:
            items, cursor = await run_in_threadpool(_new_since, cursor, user.id)
            for c in items:
                yield f"id: {c['id']}\nevent: item\ndata: {json.dumps(c)}\n\n"
            if once:
                return
            idle += 1
            if idle % 3 == 0:
                yield ": keepalive\n\n"
            if await request.is_disconnected():
                return
            await asyncio.sleep(5)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


_ = and_  # keep import for future filter composition
