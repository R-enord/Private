"""Media section tests (synthetic, documented-format fixtures; platforms unreachable from the build env)."""

from __future__ import annotations

import datetime as dt
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from hub.api import routes_media
from hub.api.app import create_app
from hub.connectors.base import Normalized
from hub.connectors.social import iso_duration_s
from hub.db import session_scope
from hub.models import Bookmark, HttpRequestLog, Item, ItemKey, ItemSource, Source
from hub.pipeline.ingest import backfill_media, run_source
from hub.pipeline.media import apply_media, detect_media
from hub.security.auth import create_user
from hub.settings_store import set_setting
from tests.conftest import load_fixture, no_sleep

NOW = dt.datetime(2026, 11, 22, 12, tzinfo=dt.UTC)
PW = "correct-horse-battery-staple"
H = {"X-Hub-Request": "1"}


def handler(req: httpx.Request) -> httpx.Response:
    host, path = req.url.host, req.url.path
    if host == "www.youtube.com" and path == "/feeds/videos.xml":
        return httpx.Response(200, content=load_fixture("youtube_feed.xml"), headers={"etag": '"yt1"'})
    if host == "www.googleapis.com" and path.endswith("/search"):
        return httpx.Response(200, content=load_fixture("youtube_api_search.json"))
    if host == "www.googleapis.com" and path.endswith("/videos"):
        return httpx.Response(200, content=load_fixture("youtube_api_videos.json"))
    if host == "dev.to":
        return httpx.Response(200, content=load_fixture("devto_articles.json"))
    if host == "mastodon.social":
        return httpx.Response(200, content=load_fixture("mastodon_tag.json"))
    if host == "public.api.bsky.app":
        return httpx.Response(200, content=load_fixture("bluesky_search.json"))
    return httpx.Response(404)


T = httpx.MockTransport(handler)


def run(key: str, transport: httpx.BaseTransport = T):
    with session_scope() as s:
        sid = s.scalar(select(Source.id).where(Source.key == key))
    return run_source(sid, transport=transport, sleep=no_sleep, now=NOW, is_fixture=True, force=True)


# ----------------------------------------------------------------------------- URL detection


@pytest.mark.parametrize("url,platform,fmt,mid", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42", "youtube", "video", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?si=abc", "youtube", "video", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/shorts/dQw4w9WgXcQ", "youtube", "short", "dQw4w9WgXcQ"),
    ("https://www.tiktok.com/@ai.tips/video/7301234567890123456?lang=en", "tiktok", "short", "7301234567890123456"),
    ("https://www.instagram.com/reel/C9xYzAbCdEf/", "instagram", "short", "C9xYzAbCdEf"),
    ("https://www.instagram.com/p/C9xYzAbCdEf/", "instagram", "post", "C9xYzAbCdEf"),
    ("https://twitter.com/karpathy/status/1800000000000000000", "x", "post", "1800000000000000000"),
    ("https://bsky.app/profile/alice.bsky.social/post/3kabc", "bluesky", "post", "alice.bsky.social/3kabc"),
    ("https://www.reddit.com/r/LocalLLaMA/comments/1abcde/new_model/", "reddit", "post", "1abcde"),
    ("https://vimeo.com/123456789", "vimeo", "video", "123456789"),
    ("https://someone.substack.com/p/agents-in-production", "writeup", "writeup", None),
])
def test_detect_media(url: str, platform: str, fmt: str, mid: str | None) -> None:
    m = detect_media(url)
    assert m is not None and (m.platform, m.format, m.media_id) == (platform, fmt, mid)


@pytest.mark.parametrize("url", ["https://www.youtube.com/@channel", "https://www.tiktok.com/discover", "https://x.com/home",
                                 "https://substack.com/", "https://example.org/watch?v=dQw4w9WgXcQ", "javascript:alert(1)"])
def test_non_media_urls(url: str) -> None:
    assert detect_media(url) is None


def test_apply_media_canonicalizes_and_keys() -> None:
    n = Normalized(external_id="hn:1", kind="article", title="Great LLM talk", url="https://youtu.be/dQw4w9WgXcQ?t=10")
    apply_media(n)
    assert n.kind == "video" and n.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert "youtube:dQw4w9WgXcQ" in n.identity and n.extra["media"]["embed"] == "youtube"
    paper = Normalized(external_id="x", kind="paper", title="p", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    apply_media(paper)
    assert paper.kind == "paper", "primary kinds are never overridden"


def test_iso_duration() -> None:
    assert iso_duration_s("PT45S") == 45 and iso_duration_s("PT1H12M3S") == 4323 and iso_duration_s("P1DT1S") == 86401
    assert iso_duration_s("bad") is None


# ----------------------------------------------------------------------------- connectors


def test_youtube_feed_shorts_videos_and_filter(db) -> None:
    r = run("youtube-ai-channels")
    assert r.status == "ok", r.error
    assert r.filtered >= 1, "non-AI vlog filtered by relevance"
    with session_scope() as s:
        short = s.scalar(select(Item).join(ItemKey).where(ItemKey.key == "youtube:AAAAAAAAAA1"))
        video = s.scalar(select(Item).join(ItemKey).where(ItemKey.key == "youtube:BBBBBBBBBB2"))
        assert short.kind == "short" and short.url == "https://www.youtube.com/shorts/AAAAAAAAAA1"
        assert short.extra["media"]["thumbnail"] == "https://i.ytimg.com/vi/AAAAAAAAAA1/hqdefault.jpg"
        assert short.metrics["yt_views"] == 12000
        assert video.kind == "video" and video.extra["media"]["vertical"] is False
    assert run("youtube-ai-channels").new == 0, "idempotent"


def test_youtube_api_needs_key_then_uses_header_and_budget(db, monkeypatch) -> None:
    r = run("youtube-api-search")
    assert r.status == "error" and "HUB_YOUTUBE_API_KEY" in r.error
    from hub.config import reset_settings_cache

    monkeypatch.setenv("HUB_YOUTUBE_API_KEY", "TEST-KEY-123")
    reset_settings_cache()
    seen: list[httpx.Request] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    try:
        with session_scope() as s:
            src = s.scalar(select(Source).where(Source.key == "youtube-api-search"))
            src.config = {**src.config, "daily_unit_budget": 150}
        r = run("youtube-api-search", httpx.MockTransport(h))
        assert r.status == "ok", r.error
        searches = [q for q in seen if q.url.path.endswith("/search")]
        assert len(searches) == 1, "budget of 150 units allows exactly one search (100 units)"
        assert all(q.headers.get("x-goog-api-key") == "TEST-KEY-123" for q in seen)
        assert all("key=" not in str(q.url) for q in seen), "key sent as header, not in URL"
        with session_scope() as s:
            logged = " ".join(f"{h.path} {h.error}" for h in s.scalars(select(HttpRequestLog)))
            assert "TEST-KEY-123" not in logged
            d = s.scalar(select(Item).join(ItemKey).where(ItemKey.key == "youtube:DDDDDDDDDD4"))
            e = s.scalar(select(Item).join(ItemKey).where(ItemKey.key == "youtube:EEEEEEEEEE5"))
            assert d.kind == "short" and d.extra["media"]["duration_s"] == 45, "45 s video is a Short"
            assert e.kind == "video"
    finally:
        monkeypatch.delenv("HUB_YOUTUBE_API_KEY")
        reset_settings_cache()


def test_devto_writeups(db) -> None:
    r = run("devto-ai")
    assert r.status == "ok" and r.filtered == 1, "CSS article filtered (not AI)"
    with session_scope() as s:
        it = s.scalar(select(Item).where(Item.title.contains("RAG pipeline")))
        assert it.kind == "article" and it.extra["media"]["format"] == "writeup" and it.metrics["devto_reactions"] == 87


def test_mastodon_posts_skip_sensitive_and_link_videos(db) -> None:
    r = run("mastodon-ai")
    assert r.status == "ok"
    assert r.records == 2, "sensitive + reblog skipped"
    with session_scope() as s:
        post = s.scalar(select(Item).where(Item.kind == "post"))
        assert "llama.cpp" in post.summary and post.label == "discussion"
        vid = s.scalar(select(Item).join(ItemKey).where(ItemKey.key == "youtube:AAAAAAAAAA1"))
        assert vid.kind == "short" and vid.links["discussion_mastodon"].startswith("https://mastodon.social/@bob/")


def test_same_video_from_two_sources_is_one_item(db) -> None:
    run("youtube-ai-channels")
    r = run("mastodon-ai")
    assert r.merged == 1
    with session_scope() as s:
        it = s.scalar(select(Item).join(ItemKey).where(ItemKey.key == "youtube:AAAAAAAAAA1"))
        assert s.scalar(select(func.count()).select_from(ItemSource).where(ItemSource.item_id == it.id)) == 2
        assert it.verification == "corroborated"


def test_bluesky_skips_adult_and_detects_tiktok(db) -> None:
    r = run("bluesky-ai")
    assert r.status == "ok" and r.records == 2
    with session_scope() as s:
        tt = s.scalar(select(Item).join(ItemKey).where(ItemKey.key == "tiktok:7300000000000000001"))
        assert tt.kind == "short" and tt.extra["media"]["platform"] == "tiktok" and tt.extra["media"]["embed"] is None
        assert s.scalar(select(func.count()).select_from(Item).where(Item.title.contains("LLM content"))) == 0


def test_backfill_reclassifies_existing_links(db) -> None:
    with session_scope() as s:
        s.add(Item(kind="article", title="Talk: LLM agents in production", url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                   canonical_url="https://youtube.com/watch?v=dQw4w9WgXcQ", is_fixture=True))
    with session_scope() as s:
        assert backfill_media(s) == 1
    with session_scope() as s:
        it = s.scalar(select(Item).where(Item.title.contains("Talk: LLM")))
        assert it.kind == "video" and it.extra["media"]["id"] == "dQw4w9WgXcQ"
        assert backfill_media(s) == 0, "idempotent"


def test_news_from_press_is_reported_not_official(db) -> None:
    from hub.pipeline import classify

    assert classify.verification_level(["press"], 1) == "reported"
    assert classify.verification_level(["press", "official"], 2) == "official"


# ----------------------------------------------------------------------------- API


@pytest.fixture()
def client(db):
    with session_scope() as s:
        create_user(s, "owner@example.org", PW, role="owner", handle="owner")
    c = TestClient(create_app())
    c.post("/api/auth/login", json={"email": "owner@example.org", "password": PW}, headers=H)
    return c


def _unfixture() -> None:
    with session_scope() as s:
        s.execute(Item.__table__.update().values(is_fixture=False))


def test_media_tabs_and_search(client) -> None:
    run("youtube-ai-channels")
    run("devto-ai")
    run("mastodon-ai")
    _unfixture()
    shorts = client.get("/api/media", params={"tab": "shorts"}).json()
    assert shorts["counts"]["shorts"] == 1 and shorts["items"][0]["media"]["platform"] == "youtube"
    assert client.get("/api/media", params={"tab": "videos"}).json()["total"] == 1
    assert client.get("/api/media", params={"tab": "posts"}).json()["total"] >= 1
    assert client.get("/api/media", params={"tab": "writeups"}).json()["total"] == 1
    found = client.get("/api/media", params={"tab": "videos", "q": "fine-tuning"}).json()
    assert found["total"] == 1
    srcs = client.get("/api/media/sources").json()
    assert {n["platform"] for n in srcs["not_available"]} == {"TikTok", "Instagram Reels", "X (Twitter)"}
    assert srcs["settings"] == {"media_previews_enabled": False, "media_oembed_enabled": False}


def test_save_link_never_fetches_and_bookmarks(client, monkeypatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(routes_media, "_oembed", lambda platform, url: called.append(url) or None)
    r = client.post("/api/media/links", headers=H, json={"url": "https://www.tiktok.com/@ai.tips/video/7301234567890123456",
                                                          "note": "agents explainer"}).json()
    assert r["item"]["kind"] == "short" and r["item"]["media"]["platform"] == "tiktok" and r["item"]["bookmarked"]
    assert called == [], "oEmbed is off by default: nothing is fetched"
    assert client.post("/api/media/links", headers=H, json={"url": "javascript:alert(1)"}).status_code == 422 or \
        client.post("/api/media/links", headers=H, json={"url": "javascript:alert(1)xx"}).status_code == 400
    again = client.post("/api/media/links", headers=H, json={"url": "https://www.tiktok.com/@ai.tips/video/7301234567890123456"}).json()
    assert again["item"]["id"] == r["item"]["id"], "saving twice does not duplicate"
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Bookmark)) == 1


def test_save_link_with_oembed_enabled(client, monkeypatch) -> None:
    monkeypatch.setattr(routes_media, "_oembed", lambda platform, url: {
        "title": "How agents call tools", "author_name": "Dev Shorts", "thumbnail_url": "https://i.ytimg.com/vi/x/hq.jpg"})
    with session_scope() as s:
        set_setting(s, "media_oembed_enabled", True)
    r = client.post("/api/media/links", headers=H, json={"url": "https://youtube.com/shorts/dQw4w9WgXcQ"}).json()
    assert r["item"]["title"] == "How agents call tools" and r["item"]["media"]["channel"] == "Dev Shorts"
    assert r["item"]["url"] == "https://www.youtube.com/shorts/dQw4w9WgXcQ"


def test_thumbnail_proxy_privacy_and_validation(client, monkeypatch, tmp_path) -> None:
    run("youtube-ai-channels")
    with session_scope() as s:
        iid = s.scalar(select(Item.id).join(ItemKey).where(ItemKey.key == "youtube:AAAAAAAAAA1"))
    assert client.get(f"/api/media/thumb/{iid}").status_code == 404, "previews off by default"
    with session_scope() as s:
        set_setting(s, "media_previews_enabled", True)
    from hub.config import reset_settings_cache

    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    reset_settings_cache()
    real = routes_media.SafeHttpClient
    responses = {"ctype": "image/jpeg"}
    monkeypatch.setattr(routes_media, "SafeHttpClient", lambda **kw: real(**kw, transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"\xff\xd8jpegdata", headers={"content-type": responses["ctype"]}))))
    try:
        r = client.get(f"/api/media/thumb/{iid}")
        assert r.status_code == 200 and r.content.startswith(b"\xff\xd8") and r.headers["content-type"] == "image/jpeg"
        assert client.get(f"/api/media/thumb/{iid}").status_code == 200, "served from cache"
        with session_scope() as s:
            it = s.get(Item, iid)
            it.extra = {**it.extra, "media": {**it.extra["media"], "thumbnail": "https://evil.example.com/x.jpg"}}
        assert client.get(f"/api/media/thumb/{iid}").status_code == 404, "only allowlisted image hosts"
        with session_scope() as s:
            it = s.get(Item, iid)
            it.extra = {**it.extra, "media": {**it.extra["media"], "thumbnail": "https://i.ytimg.com/vi/other/hq.jpg"}}
        responses["ctype"] = "text/html"
        assert client.get(f"/api/media/thumb/{iid}").status_code == 404, "non-images refused"
    finally:
        monkeypatch.delenv("HUB_DATA_DIR")
        reset_settings_cache()


def test_csp_allows_only_privacy_youtube_player(client) -> None:
    csp = client.get("/api/health").headers["content-security-policy"]
    assert "frame-src https://www.youtube-nocookie.com;" in csp and "tiktok" not in csp


_ = json
