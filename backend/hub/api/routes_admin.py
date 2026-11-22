"""Auth, source registry administration and the operations dashboard."""

from __future__ import annotations

import datetime as dt
import shutil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from hub.api.deps import current_user, get_db, require
from hub.config import get_settings
from hub.models import (
    AIProviderState,
    AIUsage,
    AuditLog,
    DedupeCandidate,
    HttpRequestLog,
    Item,
    ItemKey,
    ItemSource,
    Job,
    ModerationReport,
    RawRecord,
    Source,
    SourceCheckpoint,
    SourceRun,
    User,
    WorkerHeartbeat,
)
from hub.net.cost_guard import VERIFIED_FREE_TIER_POLICIES
from hub.security.auth import (
    COOKIE_NAME,
    create_session,
    is_locked_out,
    record_attempt,
    revoke_session,
    verify_password,
)
from hub.settings_store import all_settings, audit, set_setting
from hub.worker import queue

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------------------- auth


class LoginBody(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=500)


def _user_out(u: User) -> dict[str, Any]:
    return {"id": u.id, "email": u.email, "handle": u.handle, "display_name": u.display_name, "role": u.role}


@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    email = body.email.strip().lower()
    client = request.client.host if request.client else ""
    if is_locked_out(db, email):
        raise HTTPException(429, "too many failed attempts; try again later")
    user = db.scalar(select(User).where(User.email == email))
    ok = user is not None and user.is_active and verify_password(body.password, user.password_hash)
    record_attempt(db, email, ok, client)
    if not ok or user is None:
        db.commit()
        raise HTTPException(401, "invalid email or password")
    token = create_session(db, user, request.headers.get("user-agent", ""), client_ip=client)
    audit(db, "auth.login", user_id=user.id, target_type="user", target_id=user.id, client=client)
    db.commit()
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=request.url.scheme == "https",
                        max_age=get_settings().session_days * 86400, path="/")
    return {"user": _user_out(user)}


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    revoke_session(db, request.cookies.get(COOKIE_NAME))
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)) -> dict[str, Any]:
    return {"user": _user_out(user)}


@router.get("/auth/status")
def auth_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    s = get_settings()
    has_owner = bool(db.scalar(select(func.count()).select_from(User).where(User.role == "owner")))
    return {"has_owner": has_owner, "registration_enabled": s.registration_enabled and s.public_access_approved,
            "setup_hint": None if has_owner else "Create the owner account from a terminal: `hub create-owner`"}


@router.post("/auth/register")
def register() -> None:
    s = get_settings()
    if not (s.registration_enabled and s.public_access_approved):
        raise HTTPException(403, "registration is disabled (private single-owner installation)")
    raise HTTPException(501, "public registration is not implemented in this release")


# ----------------------------------------------------------------------------- sources


def _source_out(s: Source, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": s.id, "key": s.key, "name": s.name, "connector_type": s.connector_type,
        "connector_version": s.connector_version, "endpoint": s.endpoint, "homepage": s.homepage,
        "publisher": s.publisher, "source_class": s.source_class, "access_method": s.access_method,
        "auth_method": s.auth_method, "cost_status": s.cost_status, "quota_notes": s.quota_notes,
        "terms_url": s.terms_url, "terms_status": s.terms_status,
        "terms_checked_at": s.terms_checked_at.isoformat() if s.terms_checked_at else None,
        "attribution": s.attribution, "storage_notes": s.storage_notes, "allowed_hosts": s.allowed_hosts,
        "min_interval_s": s.min_interval_s, "interval_s": s.interval_s, "host_min_spacing_s": s.host_min_spacing_s,
        "enabled": s.enabled, "paused": s.paused, "disabled_reason": s.disabled_reason, "health": s.health,
        "last_attempt_at": s.last_attempt_at.isoformat() if s.last_attempt_at else None,
        "last_success_at": s.last_success_at.isoformat() if s.last_success_at else None,
        "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
        "backoff_until": s.backoff_until.isoformat() if s.backoff_until else None,
        "consecutive_failures": s.consecutive_failures, "last_error": s.last_error,
        "rate_limit_remaining": s.rate_limit_remaining,
        "rate_limit_reset_at": s.rate_limit_reset_at.isoformat() if s.rate_limit_reset_at else None,
        "config": s.config, "stats": stats or {},
    }


@router.get("/sources")
def list_sources(user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    day = dt.datetime.now(dt.UTC) - dt.timedelta(hours=24)
    items = dict(db.execute(select(ItemSource.source_id, func.count()).group_by(ItemSource.source_id)).all())
    reqs = {sid: (n, errs) for sid, n, errs in db.execute(text(
        "SELECT source_id, count(*), count(*) FILTER (WHERE outcome IN ('error','rate_limited','blocked')) "
        "FROM http_request_log WHERE created_at > :d GROUP BY 1"), {"d": day})}
    out = []
    for s in db.scalars(select(Source).order_by(Source.enabled.desc(), Source.name)):
        n, errs = reqs.get(s.id, (0, 0))
        out.append(_source_out(s, {"items": items.get(s.id, 0), "requests_24h": n, "request_errors_24h": errs}))
    return out


@router.get("/sources/{source_id}")
def source_detail(source_id: int, user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    s = db.get(Source, source_id)
    if s is None:
        raise HTTPException(404, "not found")
    runs = [{"id": r.id, "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
             "status": r.status, "records": r.records_fetched, "new": r.items_new, "updated": r.items_updated,
             "merged": r.items_merged, "filtered": r.items_filtered, "unchanged": r.items_unchanged,
             "http_requests": r.http_requests, "error": r.error}
            for r in db.scalars(select(SourceRun).where(SourceRun.source_id == s.id).order_by(SourceRun.started_at.desc()).limit(50))]
    checkpoints = {c.key: (c.value if not isinstance(c.value, list) else f"[{len(c.value)} entries]")
                   for c in db.scalars(select(SourceCheckpoint).where(SourceCheckpoint.source_id == s.id))}
    raw_n = db.scalar(select(func.count(RawRecord.id)).where(RawRecord.source_id == s.id))
    return {**_source_out(s), "runs": runs, "checkpoints": checkpoints, "raw_records": raw_n}


class SourcePatch(BaseModel):
    interval_s: int | None = Field(default=None, ge=60, le=7 * 86400)
    config: dict[str, Any] | None = None


@router.patch("/sources/{source_id}")
def patch_source(source_id: int, body: SourcePatch, user: User = Depends(require("sources:admin")),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    s = db.get(Source, source_id)
    if s is None:
        raise HTTPException(404, "not found")
    changes: dict[str, Any] = {}
    if body.interval_s is not None:
        if body.interval_s < s.min_interval_s:
            raise HTTPException(400, f"interval below this source's allowed minimum ({s.min_interval_s} s)")
        changes["interval_s"] = (s.interval_s, body.interval_s)
        s.interval_s = body.interval_s
    if body.config is not None:
        if set(body.config) - set(s.config or {}):
            raise HTTPException(400, "unknown config keys")
        changes["config"] = "updated"
        s.config = {**(s.config or {}), **body.config}
    audit(db, "source.update", user_id=user.id, target_type="source", target_id=s.key, changes=str(changes))
    db.commit()
    return _source_out(s)


class EnableBody(BaseModel):
    acknowledge_terms: bool = False


@router.post("/sources/{source_id}/{action}")
def source_action(source_id: int, action: str, body: EnableBody | None = None,
                  user: User = Depends(require("sources:admin")), db: Session = Depends(get_db)) -> dict[str, Any]:
    s = db.get(Source, source_id)
    if s is None:
        raise HTTPException(404, "not found")
    if s.cost_status not in ("free_public", "free_tier"):
        raise HTTPException(400, "zero-cost mode: only free sources can be enabled")
    if action == "enable":
        if s.terms_status not in ("verified", "n/a") and not (body and body.acknowledge_terms):
            raise HTTPException(409, "terms not verified for this source; review them and confirm acknowledge_terms")
        s.enabled, s.paused, s.next_run_at = True, False, None
        if s.terms_status != "verified":
            s.disabled_reason = f"enabled by owner after terms review on {dt.date.today().isoformat()}"
    elif action == "disable":
        s.enabled = False
    elif action == "pause":
        s.paused = True
    elif action == "resume":
        s.paused, s.backoff_until, s.next_run_at = False, None, None
    elif action == "run-now":
        if not s.enabled:
            raise HTTPException(400, "source is disabled")
        s.backoff_until, s.next_run_at = None, None
        queue.enqueue(db, "fetch_source", {"source_id": s.id}, dedupe_key=f"source:{s.id}", priority=10, max_attempts=2)
    else:
        raise HTTPException(404, "unknown action")
    audit(db, f"source.{action}", user_id=user.id, target_type="source", target_id=s.key,
          acknowledged_terms=bool(body and body.acknowledge_terms))
    db.commit()
    return _source_out(s)


# ----------------------------------------------------------------------------- operations


@router.get("/ops/overview")
def ops_overview(user: User = Depends(require("ops:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    day = now - dt.timedelta(hours=24)
    workers = [{"worker_id": w.worker_id, "hostname": w.hostname, "pid": w.pid, "roles": w.roles,
                "leader": w.is_scheduler_leader, "started_at": w.started_at.isoformat(),
                "last_heartbeat_at": w.last_heartbeat_at.isoformat(),
                "alive": w.stopped_at is None and (now - w.last_heartbeat_at).total_seconds() < 60,
                "stopped_at": w.stopped_at.isoformat() if w.stopped_at else None,
                "jobs_done": w.jobs_done, "jobs_failed": w.jobs_failed, "current_job_id": w.current_job_id}
               for w in db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_heartbeat_at.desc()).limit(20))]
    http = db.execute(text(
        "SELECT host, count(*) AS n, count(*) FILTER (WHERE outcome='retry') AS retries, "
        "count(*) FILTER (WHERE outcome='error') AS errors, count(*) FILTER (WHERE outcome='rate_limited') AS rate_limited, "
        "count(*) FILTER (WHERE outcome='blocked') AS blocked, count(*) FILTER (WHERE outcome='not_modified') AS not_modified, "
        "round(avg(duration_ms)) AS avg_ms FROM http_request_log WHERE created_at > :d GROUP BY host ORDER BY n DESC"),
        {"d": day}).mappings().all()
    blocked_cost = db.scalar(select(func.count(HttpRequestLog.id)).where(
        HttpRequestLog.created_at > day, HttpRequestLog.outcome == "blocked", HttpRequestLog.error.like("cost_policy%")))
    failures = [{"id": j.id, "type": j.type, "status": j.status, "attempts": j.attempts, "last_error": j.last_error[:500],
                 "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                 "run_after": j.run_after.isoformat()}
                for j in db.scalars(select(Job).where(or_(Job.status == "dead", Job.last_error != "")).order_by(Job.id.desc()).limit(20))]
    ai = [{"key": p.key, "kind": p.kind, "is_local": p.is_local, "cost_class": p.cost_class, "enabled": p.enabled,
           "status": p.status, "detail": p.status_detail, "model": p.model, "embed_model": p.embed_model,
           "models_available": p.models_available, "requests_today": p.requests_today,
           "daily_request_quota": p.daily_request_quota, "avg_latency_ms": p.avg_latency_ms,
           "last_ok_at": p.last_ok_at.isoformat() if p.last_ok_at else None, "last_error": p.last_error,
           "circuit_open_until": p.circuit_open_until.isoformat() if p.circuit_open_until else None}
          for p in db.scalars(select(AIProviderState))]
    ai_usage = {f"{prov}:{st}": n for prov, st, n in db.execute(
        select(AIUsage.provider, AIUsage.status, func.count()).where(AIUsage.created_at > day).group_by(AIUsage.provider, AIUsage.status))}
    pending_ai = db.scalar(select(func.count(Item.id)).where(Item.ai_summary.is_(None), Item.merged_into_id.is_(None)))
    db_size = db.scalar(text("SELECT pg_database_size(current_database())"))
    tables = {t: n for t, n in db.execute(text(
        "SELECT relname, n_live_tup FROM pg_stat_user_tables WHERE relname IN "
        "('items','raw_records','item_sources','jobs','http_request_log','metric_snapshots','release_events','ai_cache') "
        "ORDER BY relname"))}
    disk = shutil.disk_usage(settings.data_dir if settings.data_dir.exists() else "/")
    from hub.backup import list_backups

    st = all_settings(db)
    return {
        "now": now.isoformat(),
        "workers": workers,
        "scheduler_leader": next((w["worker_id"] for w in workers if w["leader"] and w["alive"]), None),
        "queue": queue.queue_counts(db),
        "recent_failures": failures,
        "http_24h": [dict(r) for r in http],
        "sources": {h: n for h, n in db.execute(select(Source.health, func.count()).where(Source.enabled.is_(True)).group_by(Source.health))},
        "duplicates_open": db.scalar(select(func.count(DedupeCandidate.id)).where(DedupeCandidate.status == "open")),
        "moderation_open": db.scalar(select(func.count(ModerationReport.id)).where(ModerationReport.status == "open")),
        "ai": {"providers": ai, "usage_24h": ai_usage, "items_pending_summary": pending_ai,
               "verified_free_cloud_providers": sorted(VERIFIED_FREE_TIER_POLICIES),
               "cloud_ai_enabled": settings.cloud_ai_enabled,
               "private_content_to_cloud": settings.allow_private_content_to_cloud},
        "zero_cost": {"mode": "hard (cannot be disabled)", "blocked_paid_requests_24h": blocked_cost,
                      "telemetry": "none", "external_transfer": "only enabled sources and local Ollama"},
        "database": {"size_bytes": db_size, "tables": tables},
        "disk": {"total": disk.total, "free": disk.free},
        "backups": list_backups(),
        "settings": {k: st[k] for k in ("global_pause", "paused_job_types", "ai_enabled", "embeddings_enabled",
                                        "low_resource_mode", "reports_enabled")},
        "exposure": {"bind_host": settings.bind_host, "registration_enabled": settings.registration_enabled,
                     "public_access_approved": settings.public_access_approved},
    }


@router.get("/ops/jobs")
def ops_jobs(status: str | None = None, type: str | None = None, limit: int = 100,  # noqa: A002
             user: User = Depends(require("ops:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    q = select(Job).order_by(Job.id.desc()).limit(min(limit, 500))
    if status:
        q = q.where(Job.status == status)
    if type:
        q = q.where(Job.type == type)
    return [{"id": j.id, "type": j.type, "status": j.status, "priority": j.priority, "attempts": j.attempts,
             "max_attempts": j.max_attempts, "payload": j.payload, "result": j.result, "last_error": j.last_error[:800],
             "created_at": j.created_at.isoformat(), "run_after": j.run_after.isoformat(),
             "finished_at": j.finished_at.isoformat() if j.finished_at else None, "locked_by": j.locked_by}
            for j in db.scalars(q)]


@router.post("/ops/jobs/{job_id}/{action}")
def ops_job_action(job_id: int, action: str, user: User = Depends(require("ops:write")),
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "not found")
    if action == "retry" and j.status in ("dead", "failed", "cancelled"):
        j.status, j.attempts, j.run_after, j.finished_at = "queued", 0, dt.datetime.now(dt.UTC), None
    elif action == "cancel" and j.status == "queued":
        j.status, j.finished_at = "cancelled", dt.datetime.now(dt.UTC)
    else:
        raise HTTPException(400, f"cannot {action} a {j.status} job")
    audit(db, f"job.{action}", user_id=user.id, target_type="job", target_id=job_id)
    db.commit()
    return {"id": j.id, "status": j.status}


class PauseBody(BaseModel):
    paused: bool
    job_type: str | None = None


@router.post("/ops/pause")
def ops_pause(body: PauseBody, user: User = Depends(require("ops:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    from hub.worker.jobs import HANDLERS

    if body.job_type:
        if body.job_type not in HANDLERS:
            raise HTTPException(400, "unknown job type")
        st = set(all_settings(db)["paused_job_types"] or [])
        (st.add if body.paused else st.discard)(body.job_type)
        set_setting(db, "paused_job_types", sorted(st), user_id=user.id)
    else:
        set_setting(db, "global_pause", body.paused, user_id=user.id)
    db.commit()
    s = all_settings(db)
    return {"global_pause": s["global_pause"], "paused_job_types": s["paused_job_types"]}


@router.get("/ops/duplicates")
def ops_duplicates(user: User = Depends(require("ops:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    out = []
    for d in db.scalars(select(DedupeCandidate).where(DedupeCandidate.status == "open").order_by(DedupeCandidate.similarity.desc()).limit(100)):
        a, b = db.get(Item, d.item_id), db.get(Item, d.candidate_id)
        if a and b:
            out.append({"id": d.id, "similarity": d.similarity, "reason": d.reason,
                        "a": {"id": a.id, "title": a.title, "url": a.url, "kind": a.kind, "discovered_at": a.discovered_at.isoformat()},
                        "b": {"id": b.id, "title": b.title, "url": b.url, "kind": b.kind, "discovered_at": b.discovered_at.isoformat()}})
    return out


class DupeAction(BaseModel):
    action: str = Field(pattern="^(merge|dismiss)$")
    keep_id: int | None = None


@router.post("/ops/duplicates/{cand_id}")
def ops_duplicate_action(cand_id: int, body: DupeAction, user: User = Depends(require("ops:write")),
                         db: Session = Depends(get_db)) -> dict[str, Any]:
    d = db.get(DedupeCandidate, cand_id)
    if d is None or d.status != "open":
        raise HTTPException(404, "not found")
    now = dt.datetime.now(dt.UTC)
    if body.action == "merge":
        keep = body.keep_id if body.keep_id in (d.item_id, d.candidate_id) else d.item_id
        drop = d.candidate_id if keep == d.item_id else d.item_id
        # Move provenance + identity keys; the dropped item stays as a tombstone pointing at the kept one.
        db.execute(text("UPDATE item_sources SET item_id = :k WHERE item_id = :d "
                        "AND (source_id, external_id) NOT IN (SELECT source_id, external_id FROM item_sources WHERE item_id = :k)"),
                   {"k": keep, "d": drop})
        db.execute(text("UPDATE item_keys SET item_id = :k WHERE item_id = :d"), {"k": keep, "d": drop})
        dropped = db.get(Item, drop)
        assert dropped is not None
        dropped.merged_into_id = keep
        kept = db.get(Item, keep)
        assert kept is not None
        kept.source_count = db.scalar(select(func.count()).select_from(ItemSource).where(ItemSource.item_id == keep)) or 1
        d.status = "merged"
    else:
        d.status = "dismissed"
    d.resolved_at, d.resolved_by = now, user.id
    audit(db, f"duplicate.{body.action}", user_id=user.id, target_type="dedupe_candidate", target_id=cand_id)
    db.commit()
    return {"id": d.id, "status": d.status}


@router.get("/ops/audit")
def ops_audit(limit: int = 100, user: User = Depends(require("ops:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [{"id": a.id, "actor_user_id": a.actor_user_id, "actor": a.actor_label, "action": a.action,
             "target_type": a.target_type, "target_id": a.target_id, "details": a.details, "created_at": a.created_at.isoformat()}
            for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500)))]


@router.get("/ops/http-log")
def ops_http_log(limit: int = 100, outcome: str | None = None, user: User = Depends(require("ops:read")),
                 db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    q = select(HttpRequestLog, Source.key).join(Source, Source.id == HttpRequestLog.source_id, isouter=True) \
        .order_by(HttpRequestLog.id.desc()).limit(min(limit, 500))
    if outcome:
        q = q.where(HttpRequestLog.outcome == outcome)
    return [{"id": h.id, "source": key, "purpose": h.purpose, "method": h.method, "host": h.host, "path": h.path[:300],
             "status": h.status, "outcome": h.outcome, "attempt": h.attempt, "duration_ms": h.duration_ms,
             "bytes": h.bytes, "rate_limit_remaining": h.rate_limit_remaining, "error": h.error[:300],
             "created_at": h.created_at.isoformat()} for h, key in db.execute(q)]


@router.post("/ops/backup")
def ops_backup(user: User = Depends(require("backup:run")), db: Session = Depends(get_db)) -> dict[str, Any]:
    from hub.backup import create_backup

    path = create_backup()
    audit(db, "backup.create", user_id=user.id, target_type="backup", target_id=path.name)
    db.commit()
    return {"file": path.name, "size": path.stat().st_size}


_ = ItemKey
