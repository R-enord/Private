"""End-to-end ingestion tests against recorded fixtures (offline, flagged is_fixture)."""

from __future__ import annotations

import datetime as dt

import httpx
from sqlalchemy import func, select

from hub.db import session_scope
from hub.models import (
    Item,
    ItemCategory,
    ItemKey,
    ItemSource,
    MetricSnapshot,
    Opportunity,
    RawRecord,
    ReleaseEvent,
    Source,
    SourceCheckpoint,
    SourceRun,
)
from hub.pipeline.ingest import run_source
from tests.conftest import no_sleep
from tests.fixture_transport import TRANSPORT, fixture_handler

NOW = dt.datetime(2026, 11, 22, 12, 0, tzinfo=dt.UTC)


def sid(key: str) -> int:
    with session_scope() as s:
        return s.scalar(select(Source.id).where(Source.key == key))


def run(key: str, transport=TRANSPORT, **kw):
    return run_source(sid(key), transport=transport, sleep=no_sleep, now=kw.pop("now", NOW), is_fixture=True,
                      force=kw.pop("force", False), **kw)


def count(model, *where) -> int:
    with session_scope() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where)) or 0


def test_github_search_ingest_and_idempotent(db) -> None:
    r1 = run("github-new-ai-repos")
    assert r1.status == "ok", r1.error
    assert r1.records > 0 and r1.new > 0
    items_after_first = count(Item)
    raws_after_first = count(RawRecord)
    r2 = run("github-new-ai-repos")
    assert r2.status == "ok" and r2.new == 0, "second run must not create duplicates"
    assert count(Item) == items_after_first
    assert count(RawRecord) == raws_after_first, "identical payloads are not stored twice"
    with session_scope() as s:
        it = s.scalars(select(Item).where(Item.kind == "repository")).first()
        assert it.is_fixture
        assert it.canonical_url.startswith("https://github.com/")
        assert it.score_components and "adoption" in it.score_components
        assert s.scalar(select(func.count()).select_from(ItemKey).where(ItemKey.item_id == it.id)) >= 2
        src = s.scalar(select(Source).where(Source.key == "github-new-ai-repos"))
        assert src.health == "healthy" and src.last_success_at is not None and src.next_run_at > NOW
        assert src.rate_limit_remaining == 9
        cp = s.scalar(select(SourceCheckpoint).where(SourceCheckpoint.source_id == src.id,
                                                     SourceCheckpoint.key == "query_cursor"))
        assert cp is not None


def test_changed_payload_creates_new_raw_version_and_updates_item(db) -> None:
    run("github-new-ai-repos")
    import json

    from tests.conftest import load_fixture

    data = json.loads(load_fixture("github_search.json"))
    data["items"][0]["stargazers_count"] += 50

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/search/repositories":
            return httpx.Response(200, json=data)
        return fixture_handler(req)

    later = NOW + dt.timedelta(hours=2)
    r = run("github-new-ai-repos", transport=httpx.MockTransport(handler), now=later)
    assert r.updated >= 1
    name = data["items"][0]["full_name"].lower()
    with session_scope() as s:
        versions = s.scalar(select(func.count()).select_from(RawRecord).where(RawRecord.external_id == f"repo:{name}"))
        assert versions == 2, "history preserved: both payload versions kept"
        item = s.scalar(select(Item).join(ItemKey, ItemKey.item_id == Item.id).where(ItemKey.key == f"github:{name}"))
        snaps = s.scalar(select(func.count()).select_from(MetricSnapshot).where(
            MetricSnapshot.item_id == item.id, MetricSnapshot.metric == "stars"))
        assert snaps == 2
        assert "snapshots" in item.score_components["adoption"]["evidence"] or item.score_components["adoption"]["value"] > 0


def test_hn_filters_non_ai_and_merges_with_arxiv(db) -> None:
    ra = run("arxiv-ai")
    assert ra.status == "ok", ra.error
    rh = run("hn-ai-stories")
    assert rh.status == "ok", rh.error
    assert rh.filtered > 0, "non-AI stories are filtered"
    with session_scope() as s:
        titles = [t for (t,) in s.execute(select(Item.title))]
        assert not any("Sandwich" in t for t in titles)
        assert any("LLMs can read and write" in t for t in titles)
        # HN story linking to arXiv paper 2606.14068 -> kind paper with arxiv identity key
        it = s.scalar(select(Item).join(ItemKey, ItemKey.item_id == Item.id).where(ItemKey.key == "arxiv:2606.14068"))
        assert it is not None and it.kind == "paper"
        assert it.links.get("discussion_hn", "").startswith("https://news.ycombinator.com/item?id=")
        # incremental: seen ids checkpointed
        src = s.scalar(select(Source).where(Source.key == "hn-ai-stories"))
        cp = s.scalar(select(SourceCheckpoint).where(SourceCheckpoint.source_id == src.id, SourceCheckpoint.key == "seen_ids"))
        assert len(cp.value) == 30
    rh2 = run("hn-ai-stories")
    assert rh2.new == 0


def test_hf_daily_papers_corroborate_arxiv(db) -> None:
    import json

    from tests.conftest import load_fixture

    papers = json.loads(load_fixture("hf_daily_papers.json"))
    pid = papers[0]["paper"]["id"]
    # arXiv first (primary), then HF Daily Papers (community) for the same paper id
    xml = load_fixture("arxiv_query.xml").decode()
    first_entry_id = xml.split("<id>http://arxiv.org/abs/")[1].split("v")[0] if "<id>http://arxiv.org/abs/" in xml else None
    assert first_entry_id
    run("arxiv-ai")
    run("hf-daily-papers")
    with session_scope() as s:
        it = s.scalar(select(Item).join(ItemKey, ItemKey.item_id == Item.id).where(ItemKey.key == f"arxiv:{pid}"))
        assert it is not None and it.kind == "paper"
        assert it.url == f"https://arxiv.org/abs/{pid}"
        assert "hf_upvotes" in it.metrics


def test_cross_source_corroboration_same_paper(db) -> None:
    """Same paper from arXiv (primary) and HF papers (community): one item, two sources, corroborated."""
    import json

    from tests.conftest import load_fixture

    papers = json.loads(load_fixture("hf_daily_papers.json"))
    pid = papers[0]["paper"]["id"]
    entry = f"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>http://arxiv.org/abs/{pid}v1</id><published>2026-11-19T10:00:00Z</published>
      <updated>2026-11-19T10:00:00Z</updated><title>{papers[0]['paper']['title']}</title>
      <summary>Abstract text.</summary><author><name>A. Author</name></author>
      <category term="cs.LG"/></entry></feed>"""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "export.arxiv.org":
            return httpx.Response(200, content=entry.encode()) if req.url.params.get("start") == "0" else \
                httpx.Response(200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        return fixture_handler(req)

    run("arxiv-ai", transport=httpx.MockTransport(handler))
    r = run("hf-daily-papers")
    assert r.merged >= 1
    with session_scope() as s:
        it = s.scalar(select(Item).join(ItemKey, ItemKey.item_id == Item.id).where(ItemKey.key == f"arxiv:{pid}"))
        n_sources = s.scalar(select(func.count()).select_from(ItemSource).where(ItemSource.item_id == it.id))
        assert n_sources == 2
        assert it.verification == "corroborated"
        assert it.source_count == 2
        assert it.score_components["corroboration"]["value"] > 0


def test_huggingface_models_and_license_from_tags(db) -> None:
    r = run("hf-trending-models")
    assert r.status == "ok" and r.new > 0
    with session_scope() as s:
        m = s.scalar(select(Item).where(Item.kind == "model").order_by(Item.id))
        assert m.url.startswith("https://huggingface.co/")
        assert m.label == "release"
        cats = s.scalar(select(func.count()).select_from(ItemCategory).where(ItemCategory.item_id == m.id))
        assert cats >= 1


def test_github_releases_timeline_append_only(db) -> None:
    r = run("github-releases")
    assert r.status == "ok", r.error
    with session_scope() as s:
        evs = s.scalars(select(ReleaseEvent)).all()
        assert {e.event_type for e in evs} >= {"version", "preview"}
        n = len(evs)
        pre = s.scalar(select(Item).where(Item.title.contains("Release Candidate")))
        assert pre.label == "preview"
    run("github-releases", now=NOW + dt.timedelta(hours=1))
    assert count(ReleaseEvent) == n, "timeline is never duplicated or rewritten"


def test_rss_feed_and_conditional_get(db) -> None:
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.headers))
        if req.headers.get("if-none-match") == '"feed1"':
            return httpx.Response(304)
        return fixture_handler(req)

    t = httpx.MockTransport(handler)
    r1 = run("rss-hf-blog", transport=t)
    assert r1.status == "ok" and r1.new >= 1
    r2 = run("rss-hf-blog", transport=t)
    assert r2.status == "not_modified" and r2.records == 0
    assert seen[-1].get("if-none-match") == '"feed1"'


def test_malicious_feed_is_neutralized(db) -> None:
    with session_scope() as s:
        src = Source(key="test-adversarial-feed", name="Adversarial (test)", connector_type="rss",
                     endpoint="https://feeds.example.org/rss", access_method="RSS", cost_status="free_public",
                     allowed_hosts=["feeds.example.org"], config={"feed_url": "https://feeds.example.org/rss"},
                     enabled=True, source_class="independent", publisher="Test", host_min_spacing_s=0)
        s.add(src)
    try:
        r = run("test-adversarial-feed")
        assert r.status == "ok"
        assert r.filtered == 1, "javascript: link entry dropped"
        with session_scope() as s:
            items = s.scalars(select(Item).join(ItemSource).join(Source).where(Source.key == "test-adversarial-feed")).all()
            assert len(items) == 1
            it = items[0]
            assert "utm_" not in it.canonical_url
            assert "<script" not in it.title and "<script" not in it.summary
            # raw record preserved verbatim (untrusted, never rendered as HTML)
            raw = s.scalars(select(RawRecord).where(RawRecord.source_id == src.id)).all()
            assert any("javascript:" in str(x.payload) for x in raw)
    finally:
        with session_scope() as s:
            s.execute(Source.__table__.delete().where(Source.key == "test-adversarial-feed"))


def test_failure_backoff_and_health(db) -> None:
    t = httpx.MockTransport(lambda r: httpx.Response(500))
    r1 = run("hf-trending-models", transport=t)
    assert r1.status == "error"
    with session_scope() as s:
        src = s.scalar(select(Source).where(Source.key == "hf-trending-models"))
        assert src.consecutive_failures == 1 and src.health == "degraded"
        assert src.backoff_until > NOW
        assert "500" in src.last_error
        first_backoff = src.backoff_until - NOW
    run("hf-trending-models", transport=t)
    r3 = run("hf-trending-models", transport=t)
    with session_scope() as s:
        src = s.scalar(select(Source).where(Source.key == "hf-trending-models"))
        assert src.consecutive_failures == 3 and src.health == "failing"
        assert (src.backoff_until - NOW) > first_backoff
        runs = s.scalars(select(SourceRun).where(SourceRun.source_id == src.id)).all()
        assert [x.status for x in runs] == ["error"] * 3
    assert r3.http_requests >= 3  # retries happened inside each run
    # recovery resets health
    r4 = run("hf-trending-models")
    assert r4.status == "ok"
    with session_scope() as s:
        src = s.scalar(select(Source).where(Source.key == "hf-trending-models"))
        assert src.consecutive_failures == 0 and src.health == "healthy"


def test_rate_limit_defers_source(db) -> None:
    reset = int((NOW + dt.timedelta(minutes=40)).timestamp())
    t = httpx.MockTransport(lambda r: httpx.Response(
        403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)}, json={"message": "API rate limit exceeded"}))
    r = run("github-new-ai-repos", transport=t)
    assert r.status == "rate_limited"
    with session_scope() as s:
        src = s.scalar(select(Source).where(Source.key == "github-new-ai-repos"))
        assert src.health == "rate_limited"
        assert abs(src.backoff_until.timestamp() - reset) < 5
        assert src.consecutive_failures == 0  # rate limiting is not a failure


def test_disabled_source_is_skipped_unless_forced(db) -> None:
    r = run("hn-algolia-ai")
    assert r.status == "skipped"


def test_offline_network_errors_do_not_crash(db) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unreachable", request=req)

    r = run("arxiv-ai", transport=httpx.MockTransport(handler))
    assert r.status == "error" and "network" in r.error.lower()


def test_opportunity_from_hn_job(db) -> None:
    import json

    job = {"id": 999001, "type": "job", "by": "acme", "time": int(NOW.timestamp()) - 600,
           "title": "Acme AI (YC W25) is hiring ML engineers for LLM evaluation (Remote)",
           "url": "https://jobs.example.org/acme-ml"}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("stories.json"):
            return httpx.Response(200, json=[999001])
        if req.url.path == "/v0/item/999001.json":
            return httpx.Response(200, content=json.dumps(job).encode())
        return fixture_handler(req)

    r = run("hn-ai-stories", transport=httpx.MockTransport(handler))
    assert r.new == 1
    with session_scope() as s:
        opp = s.scalars(select(Opportunity)).one()
        assert opp.organization == "Acme AI"
        assert opp.remote == "yes"
        assert opp.compensation == "" and opp.deadline is None and opp.status == "unknown"


def test_near_duplicate_articles_flagged_not_merged(db) -> None:
    from hub.models import DedupeCandidate

    feed = """<rss version="2.0"><channel><title>t</title>
    <item><title>Announcing the Open Agent Toolkit 2.0 for LLM developers</title><link>https://a.example.org/p1</link><guid>1</guid></item>
    <item><title>Announcing the Open Agent Toolkit 2.0 for LLM developers!</title><link>https://b.example.org/mirror</link><guid>2</guid></item>
    </channel></rss>"""
    with session_scope() as s:
        s.add(Source(key="test-dupe-feed", name="Dupe (test)", connector_type="rss", endpoint="https://feeds.example.org/d",
                     access_method="RSS", cost_status="free_public", allowed_hosts=["feeds.example.org"],
                     config={"feed_url": "https://feeds.example.org/d"}, enabled=True, host_min_spacing_s=0))
    try:
        r = run("test-dupe-feed", transport=httpx.MockTransport(lambda req: httpx.Response(200, content=feed.encode())))
        assert r.new == 2, "different URLs stay separate items"
        with session_scope() as s:
            cands = s.scalars(select(DedupeCandidate)).all()
            assert len(cands) == 1 and cands[0].status == "open" and cands[0].similarity >= 0.85
    finally:
        with session_scope() as s:
            s.execute(Source.__table__.delete().where(Source.key == "test-dupe-feed"))
