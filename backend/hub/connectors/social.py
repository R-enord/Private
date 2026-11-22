"""Video, social and write-up connectors (official/public endpoints only; nothing is scraped).

* ``youtube``  mode ``feed``: public per-channel Atom feeds (``/feeds/videos.xml?channel_id=``), no key.
               mode ``api_search``: YouTube Data API v3 topic search; needs the owner's free API key
               (``HUB_YOUTUBE_API_KEY``) and enforces a local daily quota-unit budget. Shorts are detected from
               ``/shorts/`` links, ``#shorts`` tags, or duration <= 180 s (API mode).
* ``devto``    public Forem API (``/api/articles?tag=``).
* ``mastodon`` public hashtag timelines (``/api/v1/timelines/tag/{tag}``) of one instance; sensitive posts skipped.
* ``bluesky``  public AppView search (``app.bsky.feed.searchPosts``); adult-labelled posts skipped.

TikTok, Instagram and X have no free public discovery API that may be used this way, so they are NOT
connectors: their links still appear when a permitted source (HN, Mastodon, Bluesky, RSS) or the owner shares them.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterator
from typing import Any

import feedparser

from hub.connectors.base import Connector, ConnectorContext, Normalized, RawRecord, html_to_text, parse_iso
from hub.net.http import HttpError, RateLimited
from hub.pipeline.canonical import safe_link
from hub.pipeline.media import detect_media, youtube_thumbnail

_ISO_DUR = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
SHORT_MAX_S = 180
ADULT_LABELS = {"porn", "sexual", "nudity", "graphic-media", "gore"}


def iso_duration_s(value: str | None) -> int | None:
    if not value:
        return None
    m = _ISO_DUR.match(value)
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _ts(struct: Any) -> str | None:
    if not struct:
        return None
    return dt.datetime(struct[0], struct[1], struct[2], struct[3], struct[4], struct[5], tzinfo=dt.UTC).isoformat()


class YouTubeConnector(Connector):
    type = "youtube"
    version = "1"
    FEED = "https://www.youtube.com/feeds/videos.xml"
    API = "https://www.googleapis.com/youtube/v3"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        if ctx.config.get("mode", "feed") == "api_search":
            yield from self._api(ctx)
        else:
            yield from self._feeds(ctx)

    def _feeds(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        failures: list[str] = []
        ok = 0
        etags: dict[str, str] = dict(ctx.get_checkpoint("etags", {}) or {})
        for ch in ctx.config.get("channels", []):
            cid = ch["id"] if isinstance(ch, dict) else str(ch)
            param = {"playlist_id": cid} if cid.startswith("PL") else {"channel_id": cid}
            try:
                res = ctx.http.get(self.FEED, params=param, etag=etags.get(cid))
            except RateLimited:
                raise
            except HttpError as e:
                failures.append(f"{cid}: {e}")
                continue
            ok += 1
            if res.not_modified:
                continue
            if res.etag:
                etags[cid] = res.etag
            parsed = feedparser.parse(res.content)
            for entry in parsed.entries[: ctx.max_items]:
                vid = entry.get("yt_videoid")
                if not vid:
                    continue
                stats = entry.get("media_statistics") or {}
                rating = entry.get("media_starrating") or {}
                yield RawRecord(external_id=f"yt:{vid}", url=entry.get("link", ""), payload={
                    "video_id": vid, "channel_id": entry.get("yt_channelid", ""), "title": entry.get("title", ""),
                    "link": entry.get("link", ""), "channel": (entry.get("author_detail") or {}).get("name", entry.get("author", "")),
                    "channel_url": (entry.get("author_detail") or {}).get("href", ""),
                    "description": (entry.get("summary") or "")[:5000],
                    "published_ts": _ts(entry.get("published_parsed")), "updated_ts": _ts(entry.get("updated_parsed")),
                    "views": stats.get("views"), "rating_count": rating.get("count"), "source": "feed",
                })
        ctx.set_checkpoint("etags", etags)
        if failures:
            ctx.warnings.extend(failures)
            if ok == 0:
                raise HttpError("all YouTube channel feeds failed: " + "; ".join(failures[:3]))

    def _api(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        from hub.config import get_settings

        key = get_settings().youtube_api_key
        if key is None:
            raise HttpError("YouTube Data API mode needs HUB_YOUTUBE_API_KEY (free key from a Google Cloud project)")
        headers = {"X-Goog-Api-Key": key.get_secret_value()}
        budget = int(ctx.config.get("daily_unit_budget", 8000))
        day = ctx.now.date().isoformat()
        used = ctx.get_checkpoint("units", {}) or {}
        units = int(used.get(day, 0))
        since = (ctx.now - dt.timedelta(days=int(ctx.config.get("published_within_days", 3)))).strftime("%Y-%m-%dT%H:%M:%SZ")
        ids: list[str] = []
        snippets: dict[str, dict[str, Any]] = {}
        for q in ctx.config.get("queries", []):
            if units + 100 > budget:  # search.list costs 100 units
                ctx.warnings.append(f"daily YouTube quota budget reached ({units}/{budget} units); remaining queries skipped")
                break
            res = ctx.http.get(f"{self.API}/search", headers=headers, params={
                "part": "snippet", "q": q, "type": "video", "order": ctx.config.get("order", "date"),
                "publishedAfter": since, "maxResults": int(ctx.config.get("max_results", 25)),
                "relevanceLanguage": ctx.config.get("language", "en"), "safeSearch": "strict"})
            units += 100
            for it in res.json().get("items", []):
                vid = (it.get("id") or {}).get("videoId")
                if vid and vid not in snippets:
                    ids.append(vid)
                    snippets[vid] = it.get("snippet") or {}
        for start in range(0, len(ids), 50):  # videos.list: 1 unit per call
            chunk = ids[start:start + 50]
            res = ctx.http.get(f"{self.API}/videos", headers=headers,
                               params={"part": "contentDetails,statistics", "id": ",".join(chunk)})
            units += 1
            details = {v["id"]: v for v in res.json().get("items", [])}
            for vid in chunk:
                sn, det = snippets[vid], details.get(vid, {})
                yield RawRecord(external_id=f"yt:{vid}", url=f"https://www.youtube.com/watch?v={vid}", payload={
                    "video_id": vid, "channel_id": sn.get("channelId", ""), "title": sn.get("title", ""),
                    "channel": sn.get("channelTitle", ""), "description": sn.get("description", ""),
                    "published_ts": sn.get("publishedAt"), "duration": (det.get("contentDetails") or {}).get("duration"),
                    "views": (det.get("statistics") or {}).get("viewCount"),
                    "likes": (det.get("statistics") or {}).get("likeCount"), "source": "api"})
        ctx.set_checkpoint("units", {day: units})

    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:
        p = raw.payload
        vid = p["video_id"]
        dur = iso_duration_s(p.get("duration"))
        text = f"{p.get('title', '')} {p.get('description', '')}".lower()
        is_short = "/shorts/" in (p.get("link") or "") or "#shorts" in text or (dur is not None and dur <= SHORT_MAX_S)
        url = f"https://www.youtube.com/shorts/{vid}" if is_short else f"https://www.youtube.com/watch?v={vid}"
        metrics: dict[str, float] = {}
        if p.get("views") not in (None, ""):
            metrics["yt_views"] = float(p["views"])
        if p.get("likes") not in (None, ""):
            metrics["yt_likes"] = float(p["likes"])
        channel = html_to_text(p.get("channel", ""), 200)
        return Normalized(
            external_id=raw.external_id, kind="short" if is_short else "video",
            title=html_to_text(p.get("title", ""), 300), url=url,
            summary=html_to_text(p.get("description", ""), 500), author=channel, organization=channel,
            published_at=parse_iso(p.get("published_ts")), tags=["youtube", "short" if is_short else "video"],
            metrics=metrics,
            links={"youtube": url} | ({"channel": p["channel_url"]} if safe_link(p.get("channel_url")) else {}),
            extra={"media": {"platform": "youtube", "format": "short" if is_short else "video", "id": vid,
                             "embed": "youtube", "thumbnail": youtube_thumbnail(vid), "duration_s": dur,
                             "channel": channel, "vertical": is_short}},
            identity=[f"youtube:{vid}"],
            require_relevance=float(ctx.config["min_relevance"]) if ctx.config.get("min_relevance") is not None else None,
        )


class DevToConnector(Connector):
    type = "devto"
    version = "1"
    API = "https://dev.to/api/articles"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        seen: set[int] = set()
        for tag in ctx.config.get("tags", []):
            res = ctx.http.get(self.API, params={"tag": tag, "per_page": int(ctx.config.get("per_page", 30))})
            for a in res.json():
                if a.get("id") in seen:
                    continue
                seen.add(a["id"])
                a.pop("body_html", None)
                a.pop("body_markdown", None)
                yield RawRecord(external_id=f"devto:{a['id']}", url=a.get("url", ""), payload=a)

    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:
        a = raw.payload
        url = safe_link(a.get("url"))
        if not url:
            return None
        raw_tags = a.get("tag_list")
        tags: list[str] = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else str(a.get("tags", "")).split(", ")
        user = a.get("user") or {}
        cover = safe_link(a.get("cover_image") or a.get("social_image"))
        metrics = {"devto_reactions": float(a.get("public_reactions_count") or a.get("positive_reactions_count") or 0),
                   "devto_comments": float(a.get("comments_count") or 0)}
        return Normalized(
            external_id=raw.external_id, kind="article", title=html_to_text(a.get("title", ""), 300), url=url,
            summary=html_to_text(a.get("description", ""), 500), author=user.get("name") or user.get("username", ""),
            organization=(a.get("organization") or {}).get("name", ""), published_at=parse_iso(a.get("published_at")),
            tags=[t for t in tags if t][:10], metrics=metrics, links={"original": url},
            extra={"media": {"platform": "devto", "format": "writeup", "thumbnail": cover,
                             "reading_minutes": a.get("reading_time_minutes")}},
            require_relevance=float(ctx.config.get("min_relevance", 0.35)),
        )


class MastodonConnector(Connector):
    type = "mastodon"
    version = "1"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        instance = ctx.config["instance"].rstrip("/")
        since = dict(ctx.get_checkpoint("since_id", {}) or {})
        seen: set[str] = set()  # the same post often carries several of our hashtags
        for tag in ctx.config.get("tags", []):
            params: dict[str, Any] = {"limit": 40}
            if since.get(tag):
                params["since_id"] = since[tag]
            res = ctx.http.get(f"{instance}/api/v1/timelines/tag/{tag}", params=params)
            statuses = res.json()
            if statuses:
                since[tag] = max((s["id"] for s in statuses), key=lambda x: int(x) if str(x).isdigit() else 0)
            for st in statuses:
                if st.get("reblog") or st.get("sensitive") or st.get("spoiler_text") or str(st["id"]) in seen:
                    continue
                seen.add(str(st["id"]))
                yield RawRecord(external_id=f"masto:{st['id']}", url=st.get("url") or st.get("uri", ""), payload=st)
        ctx.set_checkpoint("since_id", since)

    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:
        st = raw.payload
        post_url = safe_link(st.get("url") or st.get("uri"))
        if not post_url:
            return None
        acct = st.get("account") or {}
        text = html_to_text(st.get("content", ""), 500)
        card = st.get("card") or {}
        card_url = safe_link(card.get("url"))
        media = detect_media(card_url) if card_url else None
        thumb = next((safe_link(m.get("preview_url")) for m in st.get("media_attachments", [])
                      if m.get("type") in ("image", "video", "gifv")), None) or safe_link(card.get("image"))
        metrics = {"masto_boosts": float(st.get("reblogs_count") or 0), "masto_favs": float(st.get("favourites_count") or 0),
                   "masto_replies": float(st.get("replies_count") or 0)}
        if media and media.format in ("short", "video"):
            # The post shares a video: the video becomes the item; the post is its discussion/provenance.
            return Normalized(
                external_id=raw.external_id, kind=media.kind, title=html_to_text(card.get("title") or text, 300),
                url=card_url or post_url, summary=html_to_text(card.get("description") or text, 500),
                author=html_to_text(card.get("author_name", "") or "", 200), published_at=parse_iso(st.get("created_at")),
                tags=[t.get("name", "") for t in st.get("tags", [])][:10], metrics=metrics,
                links={"original": card_url or post_url}, extra={"media": media.as_dict()},
                identity=[media.identity_key] if media.identity_key else [], discussion_url=post_url,
                require_relevance=float(ctx.config.get("min_relevance", 0.35)),
            )
        title = text[:140] + ("…" if len(text) > 140 else "")
        return Normalized(
            external_id=raw.external_id, kind="post", title=title or "(post)", url=post_url, summary=text,
            author=acct.get("display_name") or acct.get("acct", ""), published_at=parse_iso(st.get("created_at")),
            tags=[t.get("name", "") for t in st.get("tags", [])][:10], metrics=metrics,
            links={"original": post_url} | ({"shared_link": card_url} if card_url else {}),
            extra={"media": {"platform": "mastodon", "format": "post", "id": str(st.get("id")), "thumbnail": thumb,
                             "channel": "@" + acct.get("acct", ""), "bot": bool(acct.get("bot"))}},
            identity=[f"mastodon:{st.get('uri') or post_url}"],
            require_relevance=float(ctx.config.get("min_relevance", 0.35)),
        )


class BlueskyConnector(Connector):
    type = "bluesky"
    version = "1"
    API = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        seen: set[str] = set()
        for q in ctx.config.get("queries", []):
            res = ctx.http.get(self.API, params={"q": q, "sort": "latest", "limit": int(ctx.config.get("limit", 25))})
            for post in res.json().get("posts", []):
                uri = post.get("uri", "")
                labels = {lab.get("val", "") for lab in post.get("labels", [])}
                if uri in seen or labels & ADULT_LABELS:
                    continue
                seen.add(uri)
                yield RawRecord(external_id=f"bsky:{uri}", url=uri, payload=post)

    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:
        p = raw.payload
        author = p.get("author") or {}
        rkey = (p.get("uri") or "").rsplit("/", 1)[-1]
        handle = author.get("handle", "")
        if not handle or not rkey:
            return None
        post_url = f"https://bsky.app/profile/{handle}/post/{rkey}"
        rec = p.get("record") or {}
        text = html_to_text(rec.get("text", ""), 500)
        ext = ((p.get("embed") or {}).get("external") or {})
        ext_url = safe_link(ext.get("uri"))
        media = detect_media(ext_url) if ext_url else None
        metrics = {"bsky_likes": float(p.get("likeCount") or 0), "bsky_reposts": float(p.get("repostCount") or 0),
                   "bsky_replies": float(p.get("replyCount") or 0)}
        if media and media.format in ("short", "video"):
            return Normalized(
                external_id=raw.external_id, kind=media.kind, title=html_to_text(ext.get("title") or text, 300),
                url=ext_url or post_url, summary=html_to_text(ext.get("description") or text, 500),
                published_at=parse_iso(rec.get("createdAt")), metrics=metrics, links={"original": ext_url or post_url},
                extra={"media": media.as_dict()}, identity=[media.identity_key] if media.identity_key else [],
                discussion_url=post_url, require_relevance=float(ctx.config.get("min_relevance", 0.35)),
            )
        title = text[:140] + ("…" if len(text) > 140 else "")
        return Normalized(
            external_id=raw.external_id, kind="post", title=title or "(post)", url=post_url, summary=text,
            author=author.get("displayName") or handle, published_at=parse_iso(rec.get("createdAt")), metrics=metrics,
            links={"original": post_url} | ({"shared_link": ext_url} if ext_url else {}),
            extra={"media": {"platform": "bluesky", "format": "post", "id": f"{handle}/{rkey}",
                             "thumbnail": safe_link(ext.get("thumb")), "channel": "@" + handle}},
            identity=[f"bluesky:{handle}/{rkey}"], require_relevance=float(ctx.config.get("min_relevance", 0.35)),
        )


class ManualConnector(Connector):
    """Holds links the owner saves by hand. Never fetches anything."""

    type = "manual"
    version = "1"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        return iter(())

    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:  # pragma: no cover
        return None
