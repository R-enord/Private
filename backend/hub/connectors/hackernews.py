"""Hacker News connectors.

* ``hn``         — official Firebase API (no auth; "There is currently no rate limit", verified 2026-11-22).
                   Incremental: only ids not seen before are fetched; recent tracked stories are refreshed
                   (bounded) so we can measure points velocity. Only AI-relevant stories are kept.
* ``hn_algolia`` — HN Search by Algolia (10,000 requests/hour/IP). Ships disabled pending terms review.

A story that links to a GitHub repo / arXiv paper / HF model is matched to that canonical item, so HN
becomes *corroborating community evidence* for it rather than a duplicate.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from hub.connectors.base import Connector, ConnectorContext, Normalized, RawRecord, from_epoch, html_to_text
from hub.net.http import HttpError, RateLimited
from hub.pipeline.canonical import extract_arxiv_id, github_repo_from_url, hf_repo_from_url, safe_link

FIREBASE = "https://hacker-news.firebaseio.com/v0"
ALGOLIA = "https://hn.algolia.com/api/v1"
SEEN_LIMIT = 5000


def hn_item_url(item_id: int | str) -> str:
    return f"https://news.ycombinator.com/item?id={item_id}"


def classify_link(url: str | None) -> tuple[str, list[str]]:
    """Kind + identity keys for the thing a story links to."""
    if not url:
        return "discussion", []
    if repo := github_repo_from_url(url):
        from hub.pipeline.canonical import canonicalize_url

        if canonicalize_url(url) == f"https://github.com/{repo}":
            return "repository", [f"github:{repo}"]
    if aid := extract_arxiv_id(url):
        return "paper", [f"arxiv:{aid}"]
    if hf := hf_repo_from_url(url):
        return {"model": "model", "dataset": "dataset", "space": "space"}[hf[0]], [f"hf:{hf[0]}:{hf[1]}"]
    return "article", []


def _normalize_story(external_id: str, sid: str, title: str, url: str | None, text: str, by: str, ts: Any,
                     points: Any, comments: Any, item_type: str, min_rel: float) -> Normalized | None:
    link = safe_link(url) if url else None
    kind, identity = classify_link(link)
    tags = []
    if title.lower().startswith("show hn"):
        tags.append("show-hn")
    if title.lower().startswith("ask hn"):
        tags.append("ask-hn")
    if item_type == "job":
        kind = "opportunity"
        tags.append("hn-jobs")
    metrics = {}
    if points is not None:
        metrics["hn_points"] = float(points)
    if comments is not None:
        metrics["hn_comments"] = float(comments)
    discussion = hn_item_url(sid)
    return Normalized(
        external_id=external_id,
        kind=kind,
        title=html_to_text(title, 400),
        url=link or discussion,
        summary=html_to_text(text, 600),
        author=by,
        published_at=from_epoch(ts),
        tags=tags,
        metrics=metrics,
        links={"original": link} if link else {},
        extra={"hn_id": int(sid), "hn_type": item_type},
        identity=identity,
        discussion_url=discussion,
        require_relevance=min_rel,
    )


class HackerNewsConnector(Connector):
    type = "hn"
    version = "1"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        lists: list[str] = ctx.config.get("lists", ["topstories"])
        list_limit = int(ctx.config.get("list_limit", 200))
        max_fetch = min(int(ctx.config.get("max_item_fetches", 120)), ctx.max_items)
        seen: list[int] = list(ctx.get_checkpoint("seen_ids", []) or [])
        seen_set = set(seen)
        ordered: list[int] = []
        for name in lists:
            ids = ctx.http.get(f"{FIREBASE}/{name}.json").json() or []
            for i in ids[:list_limit]:
                if i not in ordered:
                    ordered.append(i)
        fresh = [i for i in ordered if i not in seen_set][:max_fetch]
        # bounded refresh of recently tracked stories still on the lists (points velocity)
        recent = {int(x.split(":", 1)[1]) for x in ctx.lookup_recent_external_ids(
            int(ctx.config.get("refresh_recent_hours", 24))) if x.startswith("hn:")}
        refresh = [i for i in ordered if i in recent][: int(ctx.config.get("max_refresh", 30))]
        newly_seen: list[int] = []
        for i in fresh + [r for r in refresh if r not in fresh]:
            try:
                item = ctx.http.get(f"{FIREBASE}/item/{i}.json").json()
            except RateLimited:
                raise
            except HttpError as e:
                ctx.warnings.append(f"item {i}: {e}")
                continue
            if i in fresh:
                newly_seen.append(i)
            if not item or item.get("deleted") or item.get("dead") or item.get("type") not in ("story", "job", "poll"):
                continue
            item.pop("kids", None)  # comments are not stored
            yield RawRecord(external_id=f"hn:{i}", url=hn_item_url(i), payload=item)
        merged = (seen + newly_seen)[-SEEN_LIMIT:]
        ctx.set_checkpoint("seen_ids", merged)

    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:
        p = raw.payload
        return _normalize_story(
            raw.external_id, str(p["id"]), p.get("title") or "", p.get("url"), p.get("text") or "", p.get("by") or "",
            p.get("time"), p.get("score"), p.get("descendants"), p.get("type", "story"),
            float(ctx.config.get("min_relevance", 0.35)),
        )


class HNAlgoliaConnector(Connector):
    type = "hn_algolia"
    version = "1"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        since = int(ctx.get_checkpoint("created_after", int(ctx.now.timestamp()) - 86400))
        newest = since
        min_points = int(ctx.config.get("min_points", 5))
        emitted: set[str] = set()
        for q in ctx.config.get("queries", []):
            res = ctx.http.get(
                f"{ALGOLIA}/search_by_date",
                params={"query": q, "tags": "story", "numericFilters": f"created_at_i>{since},points>={min_points}",
                        "hitsPerPage": 50},
            )
            for hit in res.json().get("hits", []):
                oid = str(hit.get("objectID"))
                if oid in emitted:
                    continue
                emitted.add(oid)
                newest = max(newest, int(hit.get("created_at_i") or 0))
                hit.pop("_highlightResult", None)
                hit.pop("children", None)
                yield RawRecord(external_id=f"hn:{oid}", url=hn_item_url(oid), payload=hit)
        # look back an hour on the next run to catch late-scoring stories (dedup makes this safe)
        ctx.set_checkpoint("created_after", max(since, newest - 3600))

    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:
        p = raw.payload
        return _normalize_story(
            raw.external_id, str(p["objectID"]), p.get("title") or "", p.get("url"), p.get("story_text") or "",
            p.get("author") or "", p.get("created_at_i"), p.get("points"), p.get("num_comments"), "story",
            float(ctx.config.get("min_relevance", 0.35)),
        )
