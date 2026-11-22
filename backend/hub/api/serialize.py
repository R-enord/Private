"""Response shaping. Only safe (http/https) links leave the API; all text is plain (the UI never injects HTML
from sources). Raw payloads are returned as JSON data for display in an escaped viewer."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.models import Bookmark, Category, Item, ItemCategory, ItemSource, Source
from hub.pipeline.canonical import safe_link


def _iso(d: Any) -> str | None:
    return d.isoformat() if d else None


def safe_links(links: dict[str, Any] | None) -> dict[str, str]:
    out = {}
    for k, v in (links or {}).items():
        if isinstance(v, str) and (u := safe_link(v)):
            out[k] = u
    return out


def batch_context(db: Session, item_ids: list[int], user_id: int | None) -> dict[str, Any]:
    cats: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for iid, slug, name, method, conf in db.execute(
        select(ItemCategory.item_id, Category.slug, Category.name, ItemCategory.method, ItemCategory.confidence)
        .join(Category, Category.id == ItemCategory.category_id).where(ItemCategory.item_id.in_(item_ids))
    ):
        cats[iid].append({"slug": slug, "name": name, "method": method, "confidence": conf})
    srcs: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for iid, key, name, publisher, cls, url in db.execute(
        select(ItemSource.item_id, Source.key, Source.name, Source.publisher, Source.source_class, ItemSource.source_url)
        .join(Source, Source.id == ItemSource.source_id).where(ItemSource.item_id.in_(item_ids))
    ):
        srcs[iid].append({"key": key, "name": name, "publisher": publisher, "class": cls, "url": safe_link(url)})
    marks: set[int] = set()
    if user_id is not None and item_ids:
        marks = {i for (i,) in db.execute(select(Bookmark.item_id).where(Bookmark.user_id == user_id,
                                                                          Bookmark.item_id.in_(item_ids)))}
    return {"cats": cats, "srcs": srcs, "marks": marks}


def top_reasons(components: dict[str, Any], n: int = 3) -> list[dict[str, Any]]:
    comps = [{"component": k, **v} for k, v in (components or {}).items()]
    comps.sort(key=lambda c: c.get("contribution", 0), reverse=True)
    return [{"component": c["component"], "contribution": c.get("contribution", 0), "kind": c.get("kind"),
             "evidence": c.get("evidence", "")} for c in comps[:n]]


def item_card(item: Item, ctx: dict[str, Any]) -> dict[str, Any]:
    ai = item.ai_summary or {}
    return {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "url": safe_link(item.url),
        "canonical_url": safe_link(item.canonical_url),
        "summary": (item.summary or "")[:400],
        "author": item.author,
        "organization": item.organization,
        "published_at": _iso(item.published_at),
        "discovered_at": _iso(item.discovered_at),
        "updated_at": _iso(item.updated_at),
        "label": item.label,
        "label_evidence": item.label_evidence,
        "verification": item.verification,
        "license": item.license or None,
        "tags": (item.tags or [])[:12],
        "metrics": item.metrics or {},
        "links": safe_links(item.links),
        "score": round(item.score, 1),
        "score_reasons": top_reasons(item.score_components),
        "relevance": round(item.relevance, 2),
        "categories": ctx["cats"].get(item.id, []),
        "sources": ctx["srcs"].get(item.id, []),
        "source_count": item.source_count,
        "ai_summary": {"summary": ai.get("summary"), "model": ai.get("model"), "status": ai.get("status")}
        if ai.get("status") == "ok" else None,
        "bookmarked": item.id in ctx["marks"],
        "is_fixture": item.is_fixture,
    }


def cards(db: Session, items: list[Item], user_id: int | None) -> list[dict[str, Any]]:
    ctx = batch_context(db, [i.id for i in items], user_id)
    return [item_card(i, ctx) for i in items]
