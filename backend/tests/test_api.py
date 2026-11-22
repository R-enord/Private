from __future__ import annotations

import datetime as dt

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from hub.ai.manager import ProviderManager, set_manager
from hub.ai.providers import OllamaProvider
from hub.alerts import evaluate_items
from hub.api.app import create_app
from hub.db import session_scope
from hub.models import Alert, AuditLog, Item, Job, Source
from hub.pipeline.ingest import run_source
from hub.security.auth import create_user
from tests.conftest import no_sleep
from tests.fixture_transport import TRANSPORT

PW = "correct-horse-battery-staple"
H = {"X-Hub-Request": "1"}


@pytest.fixture()
def client(db):
    with session_scope() as s:
        create_user(s, "owner@example.org", PW, role="owner", handle="owner")
        create_user(s, "member@example.org", PW, role="member", handle="member")
    return TestClient(create_app())


def login(c: TestClient, email: str = "owner@example.org") -> None:
    r = c.post("/api/auth/login", json={"email": email, "password": PW}, headers=H)
    assert r.status_code == 200, r.text


def ingest_fixtures() -> None:
    with session_scope() as s:
        ids = s.scalars(select(Source.id).where(Source.key.in_(
            ["github-new-ai-repos", "hf-trending-models", "arxiv-ai", "hn-ai-stories", "hf-daily-papers",
             "github-releases"]))).all()
    for sid in ids:
        run_source(sid, transport=TRANSPORT, sleep=no_sleep, is_fixture=True)


def test_health_and_auth_required(client) -> None:
    assert client.get("/api/health").json()["zero_cost_mode"] is True
    assert client.get("/api/items").status_code == 401
    assert client.get("/api/ops/overview").status_code == 401
    st = client.get("/api/auth/status").json()
    assert st["has_owner"] is True and st["registration_enabled"] is False


def test_registration_disabled(client) -> None:
    assert client.post("/api/auth/register", headers=H, json={}).status_code == 403


def test_login_cookie_flags_and_logout(client) -> None:
    r = client.post("/api/auth/login", json={"email": "owner@example.org", "password": PW}, headers=H)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/auth/me").json()["user"]["role"] == "owner"
    client.post("/api/auth/logout", headers=H)
    assert client.get("/api/auth/me").status_code == 401


def test_login_lockout(client) -> None:
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": "owner@example.org", "password": "wrong-password-x"},
                           headers=H).status_code == 401
    r = client.post("/api/auth/login", json={"email": "owner@example.org", "password": PW}, headers=H)
    assert r.status_code == 429


def test_csrf_and_host_protection(client) -> None:
    login(client)
    assert client.post("/api/auth/logout").status_code == 403, "missing X-Hub-Request"
    assert client.post("/api/auth/logout", headers={**H, "Origin": "https://evil.example.com"}).status_code == 403
    assert client.get("/api/health", headers={"Host": "evil.example.com"}).status_code == 400, "DNS rebinding"
    r = client.get("/api/health")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


def test_feed_radar_home_search_detail(client) -> None:
    ingest_fixtures()
    login(client)
    assert client.get("/api/items").json()["total"] == 0, "fixtures are never shown as live data"
    feed = client.get("/api/items", params={"include_fixtures": True}).json()
    assert feed["total"] > 20
    card = feed["items"][0]
    for key in ("url", "published_at", "discovered_at", "label", "verification", "sources", "score_reasons"):
        assert key in card
    assert card["is_fixture"] is True
    papers = client.get("/api/items", params={"include_fixtures": True, "kind": "paper"}).json()
    assert papers["total"] > 0 and all(i["kind"] == "paper" for i in papers["items"])
    radar = client.get("/api/radar", params={"window": "60m", "include_fixtures": True}).json()
    assert set(radar["counts"]) == {"5m", "15m", "30m", "60m", "today"}
    assert radar["counts"]["5m"] == radar["counts"]["today"] > 0  # all just discovered
    home = client.get("/api/home").json()
    assert [s["key"] for s in home["sections"]][:2] == ["important", "trending_repos"]
    assert home["sections"][-1]["items"] == [] and "No community posts" in home["sections"][-1]["empty_note"]
    s = client.get("/api/search", params={"q": "LLM agents", "include_fixtures": True, "mode": "keyword"}).json()
    assert s["items"] and s["method"] in ("full-text", "fuzzy title match")
    nl = client.get("/api/search", params={"q": "papers this week about language models", "include_fixtures": True,
                                           "mode": "keyword"}).json()
    assert any("papers" in n for n in nl["interpreted_filters"]) and all(i["kind"] == "paper" for i in nl["items"])
    detail = client.get(f"/api/items/{card['id']}").json()
    assert detail["provenance"] and detail["score_components"] and detail["identity_keys"]
    raw_id = detail["provenance"][0]["raw_record_id"]
    raw = client.get(f"/api/items/{card['id']}/raw/{raw_id}").json()
    assert raw["payload"] and "Untrusted" in raw["notice"]
    cats = client.get("/api/categories").json()
    assert {c["slug"] for c in cats} >= {"llm", "mcp", "research", "opportunities"}
    tl = client.get("/api/timeline").json()
    assert isinstance(tl, list)


def test_bookmarks_and_role_authorization(client) -> None:
    ingest_fixtures()
    with session_scope() as s:
        iid = s.scalar(select(Item.id).limit(1))
        src = s.scalar(select(Source).where(Source.key == "rss-openai-news"))
        src_id = src.id
    login(client, "member@example.org")
    assert client.post(f"/api/items/{iid}/bookmark", headers=H).json()["bookmarked"] is True
    marked = client.get("/api/items", params={"bookmarked": True, "include_fixtures": True}).json()
    assert [i["id"] for i in marked["items"]] == [iid]
    assert client.post(f"/api/sources/{src_id}/enable", headers=H, json={}).status_code == 403
    assert client.get("/api/ops/overview").status_code == 403
    assert client.patch("/api/settings", headers=H, json={"ai_enabled": False}).status_code == 403
    client.delete(f"/api/items/{iid}/bookmark", headers=H)
    assert client.get("/api/items", params={"bookmarked": True, "include_fixtures": True}).json()["total"] == 0


def test_source_admin_terms_ack_and_audit(client) -> None:
    login(client)
    with session_scope() as s:
        sid = s.scalar(select(Source.id).where(Source.key == "rss-openai-news"))
    r = client.post(f"/api/sources/{sid}/enable", headers=H, json={})
    assert r.status_code == 409, "unverified terms need explicit acknowledgement"
    r = client.post(f"/api/sources/{sid}/enable", headers=H, json={"acknowledge_terms": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert client.patch(f"/api/sources/{sid}", headers=H, json={"interval_s": 60}).status_code == 400, "below minimum"
    assert client.patch(f"/api/sources/{sid}", headers=H, json={"interval_s": 3600}).json()["interval_s"] == 3600
    client.post(f"/api/sources/{sid}/run-now", headers=H)
    client.post(f"/api/sources/{sid}/disable", headers=H)
    with session_scope() as s:
        actions = [a.action for a in s.scalars(select(AuditLog).order_by(AuditLog.id))]
        assert {"source.enable", "source.update", "source.run-now", "source.disable"} <= set(actions)
        assert s.scalar(select(func.count()).select_from(Job).where(Job.type == "fetch_source")) == 1
        # restore seed default for other tests
        s.get(Source, sid).interval_s = 1800


def test_ops_overview_and_pause(client) -> None:
    ingest_fixtures()
    login(client)
    ov = client.get("/api/ops/overview").json()
    assert ov["zero_cost"]["mode"].startswith("hard")
    assert ov["ai"]["verified_free_cloud_providers"] == []
    assert ov["database"]["size_bytes"] > 0 and ov["http_24h"]
    r = client.post("/api/ops/pause", headers=H, json={"paused": True})
    assert r.json()["global_pause"] is True
    client.post("/api/ops/pause", headers=H, json={"paused": False})
    assert client.post("/api/ops/pause", headers=H, json={"paused": True, "job_type": "nope"}).status_code == 400


def test_settings_validation(client) -> None:
    login(client)
    assert client.patch("/api/settings", headers=H, json={"zero_cost_mode": False}).status_code == 400
    assert client.patch("/api/settings", headers=H, json={"score_weights": {"stars": 1}}).status_code == 400
    r = client.patch("/api/settings", headers=H, json={"low_resource_mode": True, "score_weights": {"recency": 0.3}})
    assert r.status_code == 200 and r.json()["editable"]["low_resource_mode"] is True
    assert r.json()["invariants"]["zero_cost_mode"] is True
    client.patch("/api/settings", headers=H, json={"low_resource_mode": False, "score_weights": {}})


def test_watchlist_alerts_no_duplicates_and_ownership(client) -> None:
    ingest_fixtures()
    login(client)
    wl = client.post("/api/watchlists", headers=H, json={"name": "Papers", "rules": [{"rule_type": "kind", "value": "paper"}]}).json()
    with session_scope() as s:
        ids = s.scalars(select(Item.id)).all()
    first = evaluate_items(list(ids))["alerts"]
    second = evaluate_items(list(ids))["alerts"]
    assert first > 0 and second == 0
    alerts = client.get("/api/alerts").json()
    assert alerts["unread"] == first
    client.post("/api/alerts/read", headers=H, json={})
    assert client.get("/api/alerts").json()["unread"] == 0
    other = TestClient(create_app())
    login(other, "member@example.org")
    assert other.get(f"/api/watchlists/{wl['id']}/matches").status_code == 404
    assert other.delete(f"/api/watchlists/{wl['id']}", headers=H).status_code == 404
    assert other.get("/api/alerts").json()["unread"] == 0
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Alert)) == first


def test_reports_generate_and_sanitize(client) -> None:
    ingest_fixtures()
    with session_scope() as s:
        s.execute(Item.__table__.update().values(is_fixture=False))  # reports exclude fixtures; treat as data here
    login(client)
    today = dt.datetime.now(dt.UTC).date().isoformat()
    r = client.post("/api/reports/generate", headers=H, json={"period": "daily", "start": today}).json()
    assert r["items"] > 0 and r["id"]
    rep = client.get(f"/api/reports/{r['id']}").json()
    assert "verification" in rep["content_md"] and "<script" not in rep["content_html"]
    assert 'rel="noopener noreferrer nofollow"' in rep["content_html"]
    again = client.post("/api/reports/generate", headers=H, json={"period": "daily", "start": today}).json()
    assert again["id"] == r["id"], "one report per period (no duplicates)"


def test_sse_stream_delivers_new_items(client) -> None:
    login(client)
    ingest_fixtures()
    with session_scope() as s:
        s.execute(Item.__table__.update().values(is_fixture=False))
        first = s.scalar(select(func.min(Item.id)))
    with client.stream("GET", "/api/stream", params={"last_id": first, "once": True}) as r:
        body = "".join(r.iter_text())
    assert "event: hello" in body and "event: item" in body


def test_semantic_and_hybrid_search_with_local_embeddings(client) -> None:
    ingest_fixtures()

    def handler(req: httpx.Request) -> httpx.Response:
        import json as _j

        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "all-minilm:latest"}, {"name": "qwen2.5:1.5b"}]})
        if req.url.path == "/api/embed":
            inp = _j.loads(req.content)["input"]
            return httpx.Response(200, json={"embeddings": [[1.0 if "agent" in t.lower() else 0.1, 0.5, 0.2] for t in inp]})
        return httpx.Response(404)

    set_manager(ProviderManager([OllamaProvider("http://127.0.0.1:11434", transport=httpx.MockTransport(handler))]))
    try:
        from hub.ai.tasks import embed_pending

        assert embed_pending(500)["embedded"] > 0
        login(client)
        r = client.get("/api/search", params={"q": "agent frameworks", "mode": "hybrid", "include_fixtures": True}).json()
        assert r["semantic"].startswith("semantic: local embeddings") and r["items"]
    finally:
        set_manager(None)


def test_semantic_search_degrades_without_ollama(client) -> None:
    ingest_fixtures()

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    set_manager(ProviderManager([OllamaProvider("http://127.0.0.1:11434", transport=httpx.MockTransport(down))]))
    try:
        login(client)
        r = client.get("/api/search", params={"q": "LLM", "mode": "hybrid", "include_fixtures": True}).json()
        assert "unavailable" in r["semantic"] and r["items"], "keyword results still returned"
    finally:
        set_manager(None)
