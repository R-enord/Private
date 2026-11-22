"""Generic RSS/Atom connector (one registry row per feed).

Conditional GET (ETag / Last-Modified). Stores title, link, date, author and a feed-provided excerpt only —
never full-text republication. Links that are not http(s) (e.g. ``javascript:``) are dropped.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from collections.abc import Iterator
from typing import Any

import feedparser

from hub.connectors.base import Connector, ConnectorContext, Normalized, RawRecord, html_to_text
from hub.pipeline.canonical import safe_link


def _ts(struct: Any) -> str | None:
    if not struct:
        return None
    return dt.datetime(*struct[:6], tzinfo=dt.UTC).isoformat()


class RSSConnector(Connector):
    type = "rss"
    version = "1"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        url = ctx.config["feed_url"]
        res = ctx.http.get(url, etag=ctx.get_checkpoint("etag"), last_modified=ctx.get_checkpoint("last_modified"),
                           headers={"Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"})
        if res.not_modified:
            ctx.not_modified = True
            return
        if res.etag:
            ctx.set_checkpoint("etag", res.etag)
        if res.last_modified:
            ctx.set_checkpoint("last_modified", res.last_modified)
        parsed = feedparser.parse(res.content)
        if parsed.bozo and not parsed.entries:
            raise ValueError(f"feed could not be parsed: {type(parsed.bozo_exception).__name__}")
        feed_title = html_to_text(parsed.feed.get("title", ""), 200)
        for e in parsed.entries[: ctx.max_items]:
            ident = e.get("id") or e.get("link") or e.get("title")
            if not ident:
                continue
            ext = "rss:" + hashlib.sha256(ident.encode("utf-8", "replace")).hexdigest()[:32]
            payload = {
                "id": e.get("id", ""),
                "title": e.get("title", ""),
                "link": e.get("link", ""),
                "author": e.get("author", ""),
                "summary": (e.get("summary") or "")[:20000],
                "published": e.get("published", ""),
                "updated": e.get("updated", ""),
                "published_ts": _ts(e.get("published_parsed")),
                "updated_ts": _ts(e.get("updated_parsed")),
                "tags": [t.get("term") for t in e.get("tags", []) if t.get("term")][:20],
                "feed_title": feed_title,
            }
            yield RawRecord(external_id=ext, url=e.get("link", ""), payload=payload)

    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:
        p = raw.payload
        link = safe_link(p.get("link"))
        if link is None:
            ctx.warnings.append(f"dropped entry with unsafe/missing link: {str(p.get('link'))[:60]!r}")
            return None
        published = p.get("published_ts") or p.get("updated_ts")
        return Normalized(
            external_id=raw.external_id,
            kind="article",
            title=html_to_text(p.get("title", ""), 400) or "(untitled)",
            url=link,
            summary=html_to_text(p.get("summary", ""), 600),
            author=html_to_text(p.get("author", ""), 200),
            published_at=dt.datetime.fromisoformat(published) if published else None,
            source_updated_at=dt.datetime.fromisoformat(p["updated_ts"]) if p.get("updated_ts") else None,
            tags=[html_to_text(t, 60) for t in p.get("tags", []) if t][:15],
            links={"original": link},
            extra={"feed_title": p.get("feed_title", "")},
            require_relevance=0.35 if ctx.config.get("ai_filter") else None,
        )
