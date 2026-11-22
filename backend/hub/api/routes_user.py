"""AI/provider status + settings, watchlists, alerts, reports, opportunities, timeline, projects, stack explorer."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from hub.api.deps import get_db, require
from hub.api.serialize import cards
from hub.config import get_settings
from hub.models import (
    AIProviderState,
    Alert,
    Category,
    Item,
    ItemCategory,
    ItemKey,
    Opportunity,
    ReleaseEvent,
    Report,
    User,
    Watchlist,
    WatchRule,
)
from hub.net.cost_guard import VERIFIED_FREE_TIER_POLICIES
from hub.pipeline.canonical import safe_link
from hub.security.auth import can_modify_owned
from hub.security.sanitize import render_markdown
from hub.settings_store import all_settings, audit, set_setting
from hub.worker import queue

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------------------- AI & settings


@router.get("/ai/providers")
def ai_providers(user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    s = get_settings()
    rows = db.scalars(select(AIProviderState)).all()
    return {
        "providers": [{"key": p.key, "kind": p.kind, "is_local": p.is_local, "cost_class": p.cost_class,
                       "status": p.status, "detail": p.status_detail, "model": p.model, "embed_model": p.embed_model,
                       "models_available": p.models_available, "requests_today": p.requests_today,
                       "daily_request_quota": p.daily_request_quota, "avg_latency_ms": p.avg_latency_ms,
                       "last_ok_at": p.last_ok_at.isoformat() if p.last_ok_at else None, "last_error": p.last_error}
                      for p in rows],
        "policy": {
            "primary": "local Ollama (loopback only; Ollama cloud models are refused)",
            "cloud_ai_enabled": s.cloud_ai_enabled,
            "verified_free_cloud_providers": sorted(VERIFIED_FREE_TIER_POLICIES),
            "private_content_to_cloud": s.allow_private_content_to_cloud,
            "zero_cost_mode": "hard",
            "note": "No cloud provider has a verified free tier in this release, so nothing leaves this machine "
                    "for inference. Collection works without any AI.",
        },
    }


@router.post("/ai/providers/check")
def ai_check(user: User = Depends(require("ai:admin")), db: Session = Depends(get_db)) -> dict[str, Any]:
    from hub.ai.manager import get_manager

    out = get_manager().refresh_status(db)
    db.commit()
    return out


@router.post("/ai/items/{item_id}/summarize")
def ai_summarize(item_id: int, user: User = Depends(require("ai:admin")), db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.get(Item, item_id) is None:
        raise HTTPException(404, "not found")
    jid = queue.enqueue(db, "ai_enrich", {"item_id": item_id}, dedupe_key=f"item:{item_id}", priority=20, max_attempts=3)
    db.commit()
    return {"queued": jid is not None, "job_id": jid}


EDITABLE = {"ai_enabled": bool, "embeddings_enabled": bool, "ai_backfill_batch": int, "low_resource_mode": bool,
            "reports_enabled": bool, "report_hour_utc": int, "score_weights": dict}


@router.get("/settings")
def get_settings_api(user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    s = get_settings()
    st = all_settings(db)
    return {
        "editable": {k: st.get(k) for k in EDITABLE},
        "invariants": {"zero_cost_mode": True, "cloud_ai_enabled": s.cloud_ai_enabled,
                       "allow_private_content_to_cloud": s.allow_private_content_to_cloud,
                       "registration_enabled": s.registration_enabled, "bind_host": s.bind_host,
                       "telemetry": False, "github_token_configured": s.github_token is not None,
                       "hf_token_configured": s.hf_token is not None},
    }


@router.patch("/settings")
def patch_settings(body: dict[str, Any], user: User = Depends(require("settings:write")),
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    from hub.pipeline.scoring import DEFAULT_WEIGHTS

    for k, v in body.items():
        typ = EDITABLE.get(k)
        if typ is None:
            raise HTTPException(400, f"setting '{k}' is not editable here")
        if not isinstance(v, typ) or (typ is int and isinstance(v, bool)):
            raise HTTPException(400, f"setting '{k}' must be {typ.__name__}")
        if k == "report_hour_utc" and isinstance(v, int) and not 0 <= v <= 23:
            raise HTTPException(400, "report_hour_utc must be 0-23")
        if k == "ai_backfill_batch" and isinstance(v, int) and not 0 <= v <= 50:
            raise HTTPException(400, "ai_backfill_batch must be 0-50")
        if k == "score_weights" and isinstance(v, dict) and any(
                kk not in DEFAULT_WEIGHTS or not isinstance(vv, int | float) or not 0 <= vv <= 1 for kk, vv in v.items()):
            raise HTTPException(400, "score_weights keys must be ranking components with values 0..1")
        set_setting(db, k, v, user_id=user.id)
    db.commit()
    return get_settings_api(user, db)


# ----------------------------------------------------------------------------- watchlists & alerts


class RuleIn(BaseModel):
    rule_type: str = Field(pattern="^(keyword|category|source|organization|author|label|kind|identity)$")
    value: str = Field(min_length=1, max_length=300)


class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    alerts_enabled: bool = True
    min_score: float = Field(default=0, ge=0, le=100)
    rules: list[RuleIn] = Field(default_factory=list, max_length=50)


def _wl_out(w: Watchlist) -> dict[str, Any]:
    return {"id": w.id, "name": w.name, "alerts_enabled": w.alerts_enabled, "min_score": w.min_score,
            "rules": [{"rule_type": r.rule_type, "value": r.value} for r in w.rules],
            "created_at": w.created_at.isoformat()}


@router.get("/watchlists")
def list_watchlists(user: User = Depends(require("watchlists:own")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [_wl_out(w) for w in db.scalars(select(Watchlist).where(Watchlist.owner_id == user.id).order_by(Watchlist.id))]


@router.post("/watchlists")
def create_watchlist(body: WatchlistIn, user: User = Depends(require("watchlists:own")),
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    w = Watchlist(owner_id=user.id, name=body.name, alerts_enabled=body.alerts_enabled, min_score=body.min_score,
                  rules=[WatchRule(rule_type=r.rule_type, value=r.value.strip()) for r in body.rules])
    db.add(w)
    db.commit()
    return _wl_out(w)


def _own_watchlist(db: Session, wl_id: int, user: User) -> Watchlist:
    w = db.get(Watchlist, wl_id)
    if w is None or not can_modify_owned(user, w.owner_id, "watchlists:own", elevated="users:admin"):
        raise HTTPException(404, "not found")  # do not reveal other users' watchlists
    return w


@router.put("/watchlists/{wl_id}")
def update_watchlist(wl_id: int, body: WatchlistIn, user: User = Depends(require("watchlists:own")),
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    w = _own_watchlist(db, wl_id, user)
    w.name, w.alerts_enabled, w.min_score = body.name, body.alerts_enabled, body.min_score
    w.rules = [WatchRule(rule_type=r.rule_type, value=r.value.strip()) for r in body.rules]
    db.commit()
    return _wl_out(w)


@router.delete("/watchlists/{wl_id}")
def delete_watchlist(wl_id: int, user: User = Depends(require("watchlists:own")), db: Session = Depends(get_db)) -> dict[str, Any]:
    db.delete(_own_watchlist(db, wl_id, user))
    db.commit()
    return {"deleted": True}


@router.get("/watchlists/{wl_id}/matches")
def watchlist_matches(wl_id: int, user: User = Depends(require("watchlists:own")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Preview what a watchlist matches among recent items (no alerts created)."""
    from hub.alerts import _item_facts, rule_matches

    w = _own_watchlist(db, wl_id, user)
    recent = db.scalars(select(Item).where(Item.merged_into_id.is_(None), Item.is_fixture.is_(False),
                                           Item.discovered_at > dt.datetime.now(dt.UTC) - dt.timedelta(days=7))
                        .order_by(Item.score.desc()).limit(500)).all()
    hits = [i for i in recent if i.score >= w.min_score and any(
        rule_matches(r.rule_type, r.value, i, _item_facts(db, i)) for r in w.rules)][:50]
    return {"items": cards(db, hits, user.id)}


@router.get("/alerts")
def list_alerts(unread: bool = False, user: User = Depends(require("watchlists:own")), db: Session = Depends(get_db)) -> dict[str, Any]:
    q = select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc()).limit(200)
    if unread:
        q = q.where(Alert.read_at.is_(None))
    alerts = db.scalars(q).all()
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_([a.item_id for a in alerts])))}
    cmap = {c["id"]: c for c in cards(db, list(items.values()), user.id)}
    unread_n = db.scalar(select(func.count(Alert.id)).where(Alert.user_id == user.id, Alert.read_at.is_(None)))
    return {"unread": unread_n, "alerts": [{"id": a.id, "reason": a.reason, "created_at": a.created_at.isoformat(),
                                            "read": a.read_at is not None, "item": cmap.get(a.item_id)} for a in alerts]}


@router.post("/alerts/read")
def mark_alerts_read(body: dict[str, Any], user: User = Depends(require("watchlists:own")), db: Session = Depends(get_db)) -> dict[str, Any]:
    ids = body.get("ids")
    if ids is None:
        db.execute(text("UPDATE alerts SET read_at = now() WHERE user_id = :u AND read_at IS NULL"), {"u": user.id})
    else:
        db.execute(text("UPDATE alerts SET read_at = now() WHERE user_id = :u AND id = ANY(:ids)"),
                   {"u": user.id, "ids": [int(i) for i in ids]})
    db.commit()
    return {"ok": True}


# ----------------------------------------------------------------------------- reports


@router.get("/reports")
def list_reports(user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [{"id": r.id, "period": r.period, "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
             "generator": r.generator, "items": len(r.item_ids or []), "created_at": r.created_at.isoformat()}
            for r in db.scalars(select(Report).where((Report.owner_id.is_(None)) | (Report.owner_id == user.id))
                                .order_by(Report.period_start.desc()).limit(100))]


@router.get("/reports/{report_id}")
def get_report(report_id: int, user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    r = db.get(Report, report_id)
    if r is None or (r.owner_id is not None and r.owner_id != user.id):
        raise HTTPException(404, "not found")
    return {"id": r.id, "period": r.period, "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
            "generator": r.generator, "content_md": r.content_md, "content_html": render_markdown(r.content_md),
            "created_at": r.created_at.isoformat()}


class ReportIn(BaseModel):
    period: str = Field(pattern="^(daily|weekly)$")
    start: dt.date | None = None


@router.post("/reports/generate")
def generate_report_api(body: ReportIn, user: User = Depends(require("ops:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    from hub.reports import generate_report

    today = dt.datetime.now(dt.UTC).date()
    start = body.start or (today if body.period == "daily" else today - dt.timedelta(days=6))
    res = generate_report(body.period, dt.datetime(start.year, start.month, start.day, tzinfo=dt.UTC))
    rid = db.scalar(select(Report.id).where(Report.period == body.period, Report.owner_id.is_(None),
                                            Report.period_start == dt.datetime(start.year, start.month, start.day, tzinfo=dt.UTC)))
    audit(db, "report.generate", user_id=user.id, target_type="report", target_id=rid or "")
    db.commit()
    return {**res, "id": rid}


# ----------------------------------------------------------------------------- opportunities & timeline


@router.get("/opportunities")
def opportunities(status: str | None = None, opp_type: str | None = None, user: User = Depends(require("items:read")),
                  db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    q = (select(Opportunity, Item).join(Item, Item.id == Opportunity.item_id).where(Item.merged_into_id.is_(None),
                                                                                     Item.is_fixture.is_(False))
         .order_by(Opportunity.deadline.asc().nulls_last(), Item.discovered_at.desc()).limit(200))
    if status:
        q = q.where(Opportunity.status == status)
    if opp_type:
        q = q.where(Opportunity.opp_type == opp_type)
    rows = db.execute(q).all()
    cmap = {c["id"]: c for c in cards(db, [it for _, it in rows], user.id)}
    return [{"item": cmap[it.id], "type": o.opp_type, "organization": o.organization or None, "role": o.role,
             "remote": o.remote, "compensation": o.compensation or None, "location": o.location or None,
             "deadline": o.deadline.isoformat() if o.deadline else None, "status": o.status,
             "application_url": safe_link(o.application_url), "evidence": o.evidence,
             "last_checked_at": o.last_checked_at.isoformat() if o.last_checked_at else None} for o, it in rows]


@router.get("/timeline")
def timeline(event_type: str | None = None, q: str | None = None, days: int = 30, user: User = Depends(require("items:read")),
             db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    since = dt.datetime.now(dt.UTC) - dt.timedelta(days=min(days, 365))
    stmt = (select(ReleaseEvent, Item).join(Item, Item.id == ReleaseEvent.item_id)
            .where(Item.is_fixture.is_(False), ReleaseEvent.event_date >= since)
            .order_by(ReleaseEvent.event_date.desc().nulls_last()).limit(300))
    if event_type:
        stmt = stmt.where(ReleaseEvent.event_type.in_(event_type.split(",")))
    if q:
        stmt = stmt.where(Item.title.ilike(f"%{q}%"))
    return [{"id": e.id, "event_type": e.event_type, "version": e.version, "title": e.title,
             "event_date": e.event_date.isoformat() if e.event_date else None, "source_url": safe_link(e.source_url),
             "confidence": e.confidence, "verification": e.verification, "item_id": it.id, "item_title": it.title,
             "kind": it.kind, "recorded_at": e.created_at.isoformat()} for e, it in db.execute(stmt)]


# ----------------------------------------------------------------------------- stack explorer


@router.get("/stack")
def stack_explorer(limit: int = 150, user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Relationship graph derived ONLY from source-stated links (each edge carries its evidence label).

    Edges: release -> repository (GitHub release of repo, source-confirmed), paper -> repository (code link in
    the paper's arXiv/HF metadata, source-stated), model -> paper (model card arxiv: tag, source-stated),
    model -> base model (model card base_model: tag, source-stated).
    """
    nodes: dict[int, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node(i: Item) -> None:
        nodes.setdefault(i.id, {"id": i.id, "title": i.title, "kind": i.kind, "score": round(i.score, 1),
                                "organization": i.organization or i.author})

    key_to_item = {k: iid for k, iid in db.execute(select(ItemKey.key, ItemKey.item_id).where(
        ItemKey.key.like("github:%") | ItemKey.key.like("arxiv:%") | ItemKey.key.like("hf:model:%")))}
    candidates = db.scalars(select(Item).where(Item.merged_into_id.is_(None), Item.is_fixture.is_(False))
                            .order_by(Item.score.desc()).limit(3000)).all()
    by_id = {i.id: i for i in candidates}
    for it in candidates:
        ex, links = it.extra or {}, it.links or {}
        targets: list[tuple[str, str, str]] = []
        if it.kind == "release" and ex.get("repo"):
            targets.append((f"github:{ex['repo'].lower()}", "release_of", "source-confirmed"))
        if code := links.get("code"):
            from hub.pipeline.canonical import github_repo_from_url

            if repo := github_repo_from_url(code):
                targets.append((f"github:{repo}", "implemented_in", "source-stated"))
        for aid in ex.get("arxiv_refs") or []:
            targets.append((f"arxiv:{aid}", "cites_paper", "source-stated"))
        for bm in ex.get("base_models") or []:
            targets.append((f"hf:model:{bm.lower()}", "built_on", "source-stated"))
        for key, rel, conf in targets:
            tid = key_to_item.get(key)
            if tid and tid != it.id:
                tgt = by_id.get(tid) or db.get(Item, tid)
                if tgt is None:
                    continue
                node(it)
                node(tgt)
                edges.append({"from": it.id, "to": tid, "relation": rel, "confidence": conf})
        if len(edges) >= limit:
            break
    return {"nodes": list(nodes.values()), "edges": edges,
            "legend": {"source-confirmed": "stated by the publishing platform's own metadata",
                       "source-stated": "stated in the item's metadata (e.g. model card tag, paper code link)",
                       "inferred": "not used in this graph"}}


@router.get("/categories/{slug}/items")
def category_items(slug: str, user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    cat = db.scalar(select(Category).where(Category.slug == slug))
    if cat is None:
        raise HTTPException(404, "not found")
    rows = db.scalars(select(Item).join(ItemCategory, ItemCategory.item_id == Item.id).where(
        ItemCategory.category_id == cat.id, Item.merged_into_id.is_(None), Item.is_fixture.is_(False))
        .order_by(Item.score.desc()).limit(60)).all()
    return {"category": {"slug": cat.slug, "name": cat.name, "description": cat.description}, "items": cards(db, list(rows), user.id)}
