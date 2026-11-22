"""arXiv connector (legacy arXiv API, Atom).

ToU (verified 2026-11-22): <= 1 request / 3 s, one connection at a time; metadata is CC0; e-prints must
not be stored or served (we only link to the abstract page). Spacing is enforced by the DB host throttle
(host_min_spacing_s=3.1) so it holds across worker processes.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from typing import Any

import feedparser

from hub.connectors.base import Connector, ConnectorContext, Normalized, RawRecord, html_to_text
from hub.pipeline.canonical import extract_arxiv_id

API = "https://export.arxiv.org/api/query"
_GH_IN_TEXT = re.compile(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def _ts(struct: Any) -> dt.datetime | None:
    if not struct:
        return None
    return dt.datetime(struct[0], struct[1], struct[2], struct[3], struct[4], struct[5], tzinfo=dt.UTC)


def _iso(d: dt.datetime | None) -> str | None:
    return d.isoformat() if d else None


def parse_feed(content: bytes) -> list[dict[str, Any]]:
    parsed = feedparser.parse(content)
    out = []
    for e in parsed.entries:
        aid = extract_arxiv_id(e.get("id", "")) or extract_arxiv_id(e.get("link", ""))
        if not aid:
            continue
        version = None
        if m := re.search(r"v(\d+)$", e.get("id", "")):
            version = int(m.group(1))
        out.append({
            "arxiv_id": aid,
            "version": version,
            "title": e.get("title", ""),
            "summary": e.get("summary", ""),
            "authors": [a.get("name", "") for a in e.get("authors", [])],
            "published": e.get("published", ""),
            "updated": e.get("updated", ""),
            "published_ts": _iso(_ts(e.get("published_parsed"))),
            "updated_ts": _iso(_ts(e.get("updated_parsed"))),
            "categories": [t.get("term") for t in e.get("tags", []) if t.get("term")],
            "primary_category": (e.get("arxiv_primary_category") or {}).get("term", ""),
            "comment": e.get("arxiv_comment", ""),
            "journal_ref": e.get("arxiv_journal_ref", ""),
            "doi": e.get("arxiv_doi", ""),
        })
    return out


class ArxivConnector(Connector):
    type = "arxiv"
    version = "1"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        query = ctx.config.get("search_query", "cat:cs.AI")
        page_size = min(2000, int(ctx.config.get("page_size", 100)))
        max_pages = int(ctx.config.get("max_pages", 3))
        last_seen = ctx.get_checkpoint("newest_published")  # ISO timestamp
        newest = last_seen
        emitted = 0
        for page in range(max_pages):
            res = ctx.http.get(API, params={"search_query": query, "sortBy": "submittedDate",
                                            "sortOrder": "descending", "start": page * page_size,
                                            "max_results": page_size})
            entries = parse_feed(res.content)
            if not entries:
                break
            reached_old = False
            for e in entries:
                pub = e.get("published_ts")
                if pub and (newest is None or pub > newest):
                    newest = pub
                if last_seen and pub and pub <= last_seen:
                    reached_old = True
                    continue
                yield RawRecord(external_id=f"arxiv:{e['arxiv_id']}", url=f"https://arxiv.org/abs/{e['arxiv_id']}",
                                payload=e)
                emitted += 1
                if emitted >= ctx.max_items:
                    reached_old = True
                    break
            if reached_old or len(entries) < page_size:
                break
        if newest:
            ctx.set_checkpoint("newest_published", newest)

    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:
        p = raw.payload
        aid = p["arxiv_id"]
        authors = [a for a in p.get("authors", []) if a]
        author = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        links = {"arxiv": f"https://arxiv.org/abs/{aid}", "pdf": f"https://arxiv.org/pdf/{aid}"}
        code = _GH_IN_TEXT.search(f"{p.get('comment', '')} {p.get('summary', '')}")
        if code:
            links["code"] = code.group(0).rstrip(".")
        return Normalized(
            external_id=raw.external_id,
            kind="paper",
            title=html_to_text(p.get("title", ""), 400),
            url=f"https://arxiv.org/abs/{aid}",
            summary=html_to_text(p.get("summary", ""), 2000),
            author=author,
            published_at=dt.datetime.fromisoformat(p["published_ts"]) if p.get("published_ts") else None,
            source_updated_at=dt.datetime.fromisoformat(p["updated_ts"]) if p.get("updated_ts") else None,
            tags=[c for c in p.get("categories", []) if c][:10],
            links=links,
            extra={"arxiv_id": aid, "version": p.get("version"), "primary_category": p.get("primary_category", ""),
                   "comment": html_to_text(p.get("comment", ""), 300), "journal_ref": p.get("journal_ref", ""),
                   "doi": p.get("doi", ""), "authors": authors[:50]},
            identity=[f"arxiv:{aid}"],
            release_event={"event_type": "paper", "version": f"v{p['version']}" if p.get("version") else "",
                           "event_date": dt.datetime.fromisoformat(p["published_ts"]) if p.get("published_ts") else None},
        )
