"""Media section: Shorts / News / Videos / Posts / Write-ups, saved links and privacy-preserving thumbnails.

Privacy defaults:
* Thumbnails are fetched by the hub (not the phone) and only when ``media_previews_enabled`` is on (default off).
* Saved links are never fetched. Optional metadata comes only from official oEmbed endpoints
  (YouTube/TikTok/X) and only when ``media_oembed_enabled`` is on (default off).
* In-app playback (YouTube only, privacy-enhanced youtube-nocookie player) starts only when you tap Play.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.orm import Session

from hub.api.deps import get_db, require
from hub.api.serialize import cards
from hub.config import get_settings
from hub.connectors.base import Normalized, RawRecord, html_to_text
from hub.models import Bookmark, Item, ItemSource, Source, User
from hub.net.http import HttpError, SafeHttpClient
from hub.net.throttle import persist_http_log
from hub.pipeline.canonical import safe_link
from hub.pipeline.ingest import store_raw, upsert_item
from hub.pipeline.media import detect_media
from hub.settings_store import audit, get_setting, set_setting

router = APIRouter(prefix="/api/media")

TABS = ("shorts", "news", "videos", "posts", "writeups")
NEWS_LABELS = ("announcement", "update", "release", "deprecation", "rumor", "preview", "beta", "unlabeled")
THUMB_HOSTS = {"i.ytimg.com", "media2.dev.to", "dev-to-uploads.s3.amazonaws.com", "files.mastodon.social", "cdn.bsky.app"}
OEMBED = {"youtube": ("www.youtube.com", "https://www.youtube.com/oembed"),
          "tiktok": ("www.tiktok.com", "https://www.tiktok.com/oembed"),
          "x": ("publish.twitter.com", "https://publish.twitter.com/oembed")}
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
MEDIA_SOURCE_TYPES = ("youtube", "devto", "mastodon", "bluesky", "manual")


def _tab_query(tab: str, include_fixtures: bool = False) -> Select[Any]:
    q = select(Item).where(Item.merged_into_id.is_(None))
    if not include_fixtures:
        q = q.where(Item.is_fixture.is_(False))
    if tab == "shorts":
        return q.where(Item.kind == "short")
    if tab == "videos":
        return q.where(Item.kind == "video")
    if tab == "posts":
        return q.where(or_(Item.kind == "post", Item.kind == "discussion"))
    if tab == "writeups":
        return q.where(Item.kind == "article", or_(Item.extra["media"]["format"].astext == "writeup",
                                                   Item.id.in_(select(ItemSource.item_id).join(Source).where(
                                                       Source.source_class == "independent"))))
    # news: articles from official/press/community sources that are not write-ups, newest first
    return q.where(Item.kind.in_(("article", "release")), Item.label.in_(NEWS_LABELS),
                   or_(Item.extra["media"].is_(None), Item.extra["media"]["format"].astext != "writeup"))


@router.get("")
def media_list(tab: str = Query("shorts", pattern="^(shorts|news|videos|posts|writeups)$"), q: str | None = None,
               platform: str | None = None, sort: str = Query("new", pattern="^(new|top)$"),
               page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100), include_fixtures: bool = False,
               user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    query = _tab_query(tab, include_fixtures)
    if platform:
        query = query.where(Item.extra["media"]["platform"].astext == platform)
    if q:
        tsq = func.websearch_to_tsquery("english", q)
        query = query.where(or_(Item.search_vector.op("@@")(tsq), Item.title.ilike(f"%{q[:100]}%")))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    order = (Item.discovered_at.desc(), Item.id.desc()) if sort == "new" else (Item.score.desc(), Item.id.desc())
    rows = db.scalars(query.order_by(*order).limit(page_size).offset((page - 1) * page_size)).all()
    counts = {t: db.scalar(select(func.count()).select_from(_tab_query(t, include_fixtures).subquery())) or 0 for t in TABS}
    return {"tab": tab, "items": cards(db, list(rows), user.id), "total": total, "page": page, "counts": counts,
            "previews_enabled": bool(get_setting(db, "media_previews_enabled", False)),
            "oembed_enabled": bool(get_setting(db, "media_oembed_enabled", False))}


@router.get("/sources")
def media_sources(user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.scalars(select(Source).where(or_(Source.connector_type.in_(MEDIA_SOURCE_TYPES),
                                               Source.key.like("news-%"), Source.key.like("reddit-%"),
                                               Source.key.like("medium-%"))).order_by(Source.name)).all()
    return {
        "sources": [{"id": s.id, "key": s.key, "name": s.name, "enabled": s.enabled, "health": s.health,
                     "terms_status": s.terms_status, "terms_url": s.terms_url, "disabled_reason": s.disabled_reason,
                     "quota_notes": s.quota_notes} for s in rows],
        "settings": {"media_previews_enabled": bool(get_setting(db, "media_previews_enabled", False)),
                     "media_oembed_enabled": bool(get_setting(db, "media_oembed_enabled", False))},
        "not_available": [
            {"platform": "TikTok", "why": "No free public API for discovering videos by topic (official research access "
             "is limited to approved researchers); scraping is not permitted. TikTok links shared on HN, Mastodon, "
             "Bluesky or saved by you still appear here."},
            {"platform": "Instagram Reels", "why": "Hashtag search needs an approved Meta business app; scraping is not "
             "permitted. Reels links shared elsewhere or saved by you still appear (opened on Instagram)."},
            {"platform": "X (Twitter)", "why": "Reading the X API is a paid service (zero-cost mode). X posts shared "
             "elsewhere or saved by you still appear."},
        ],
    }


class MediaSettings(BaseModel):
    media_previews_enabled: bool | None = None
    media_oembed_enabled: bool | None = None


@router.patch("/settings")
def media_settings(body: MediaSettings, user: User = Depends(require("settings:write")),
                   db: Session = Depends(get_db)) -> dict[str, Any]:
    for k, v in body.model_dump(exclude_none=True).items():
        set_setting(db, k, v, user_id=user.id)
    db.commit()
    return {k: bool(get_setting(db, k, False)) for k in ("media_previews_enabled", "media_oembed_enabled")}


# ----------------------------------------------------------------------------- save a link


class SaveLink(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    title: str = Field(default="", max_length=300)
    note: str = Field(default="", max_length=2000)


def _oembed(platform: str, url: str) -> dict[str, Any] | None:
    host, endpoint = OEMBED[platform]
    client = SafeHttpClient(allowed_hosts={host}, user_agent=get_settings().user_agent, purpose="media",
                            max_retries=1, timeout_s=10)
    try:
        data = client.get(endpoint, params={"url": url, "format": "json"}).json()
        return {k: data.get(k) for k in ("title", "author_name", "author_url", "thumbnail_url", "provider_name")}
    except (HttpError, ValueError):
        return None
    finally:
        from hub.db import session_scope

        with session_scope() as s:
            persist_http_log(s, client.log)
        client.close()


@router.post("/links")
def save_link(body: SaveLink, user: User = Depends(require("bookmarks:own")), db: Session = Depends(get_db)) -> dict[str, Any]:
    url = safe_link(body.url.strip())
    if url is None:
        raise HTTPException(400, "only http(s) links can be saved")
    src = db.scalar(select(Source).where(Source.key == "saved-links"))
    if src is None:
        raise HTTPException(500, "saved-links source missing; run `hub migrate`")
    media = detect_media(url)
    meta = None
    if media and media.platform in OEMBED and get_setting(db, "media_oembed_enabled", False):
        meta = _oembed(media.platform, media.canonical_url or url)
    title = body.title.strip() or html_to_text((meta or {}).get("title") or "", 300) or (
        f"{media.platform.title()} {media.format}" if media else url)
    now = dt.datetime.now(dt.UTC)
    ext_id = "saved:" + hashlib.sha256(url.encode()).hexdigest()[:32]
    raw = RawRecord(external_id=ext_id, url=url, payload={"url": url, "title": body.title, "note": body.note,
                                                          "saved_by": user.id, "oembed": meta})
    extra: dict[str, Any] = {"saved_note": body.note[:2000], "saved_by": user.id}
    if media:
        md = media.as_dict()
        if meta and safe_link(meta.get("thumbnail_url")) and not md.get("thumbnail"):
            md["thumbnail"] = meta["thumbnail_url"]
        if meta and meta.get("author_name"):
            md["channel"] = html_to_text(meta["author_name"], 200)
        extra["media"] = md
    norm = Normalized(external_id=ext_id, kind=media.kind if media else "article", title=title, url=url,
                      summary=html_to_text(body.note, 500), author=(extra.get("media") or {}).get("channel", ""),
                      tags=["saved"], links={"original": url}, extra=extra,
                      identity=[media.identity_key] if media and media.identity_key else [])
    raw_id = store_raw(db, src.id, None, raw, now)
    outcome, item = upsert_item(db, src, norm, raw_id, now)
    db.execute(text("INSERT INTO bookmarks (user_id, item_id, note, created_at) VALUES (:u, :i, :n, now()) "
                    "ON CONFLICT (user_id, item_id) DO NOTHING"), {"u": user.id, "i": item.id, "n": body.note[:500]})
    audit(db, "media.link_saved", user_id=user.id, target_type="item", target_id=item.id, platform=media.platform if media else "web")
    db.commit()
    return {"outcome": outcome, "item": cards(db, [item], user.id)[0]}


# ----------------------------------------------------------------------------- thumbnails (hub-side proxy)


def _cache_dir() -> Path:
    d = get_settings().data_dir / "media-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/thumb/{item_id}")
def thumbnail(item_id: int, user: User = Depends(require("items:read")), db: Session = Depends(get_db)) -> FileResponse:
    if not get_setting(db, "media_previews_enabled", False):
        raise HTTPException(404, "previews disabled")
    item = db.get(Item, item_id)
    turl = safe_link(((item.extra or {}).get("media") or {}).get("thumbnail")) if item else None
    if not turl:
        raise HTTPException(404, "no thumbnail")
    from urllib.parse import urlsplit

    host = (urlsplit(turl).hostname or "").lower()
    if host not in THUMB_HOSTS:
        raise HTTPException(404, "thumbnail host not allowed")
    key = hashlib.sha256(turl.encode()).hexdigest()
    for ext in IMAGE_TYPES.values():
        p = _cache_dir() / f"{key}{ext}"
        if p.exists() and (dt.datetime.now().timestamp() - p.stat().st_mtime) < 7 * 86400:
            return FileResponse(p, headers={"Cache-Control": "private, max-age=86400"})
    client = SafeHttpClient(allowed_hosts=THUMB_HOSTS, user_agent=get_settings().user_agent, purpose="media",
                            max_retries=1, timeout_s=10, max_bytes=2 * 1024 * 1024)
    try:
        res = client.get(turl)
    except HttpError as e:
        raise HTTPException(404, "thumbnail unavailable") from e
    finally:
        persist_http_log(db, client.log)
        db.commit()
        client.close()
    ctype = res.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype not in IMAGE_TYPES:
        raise HTTPException(404, "not an image")
    p = _cache_dir() / f"{key}{IMAGE_TYPES[ctype]}"
    p.write_bytes(res.content)
    return FileResponse(p, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})


_ = Bookmark
