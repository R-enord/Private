from __future__ import annotations

import datetime as dt

import pytest

from hub.connectors.base import html_to_text
from hub.pipeline import classify
from hub.pipeline.canonical import canonicalize_url, extract_arxiv_id, identity_keys, safe_link
from hub.pipeline.scoring import Growth, ScoreInput, compute_score, growth_from_snapshots


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("http://www.Example.com/Post/?utm_source=x&b=2&a=1#frag", "https://example.com/Post?a=1&b=2"),
        ("https://github.com/Ollama/Ollama", "https://github.com/ollama/ollama"),
        ("https://github.com/ollama/ollama/tree/main/docs", "https://github.com/ollama/ollama"),
        ("https://github.com/ollama/ollama.git", "https://github.com/ollama/ollama"),
        ("https://github.com/ollama/ollama/releases/tag/v1", "https://github.com/ollama/ollama/releases/tag/v1"),
        ("http://arxiv.org/abs/2611.26232v2", "https://arxiv.org/abs/2611.26232"),
        ("https://arxiv.org/pdf/2611.26232v1", "https://arxiv.org/abs/2611.26232"),
        ("https://news.ycombinator.com/item?id=123&utm_x=1", "https://news.ycombinator.com/item?id=123"),
        ("https://example.com:443/a//b/", "https://example.com/a/b"),
    ],
)
def test_canonicalize(raw: str, expected: str) -> None:
    assert canonicalize_url(raw) == expected


def test_identity_keys_cross_source() -> None:
    assert "github:ollama/ollama" in identity_keys("https://github.com/Ollama/ollama/")
    assert "arxiv:2611.26232" in identity_keys("https://arxiv.org/abs/2611.26232v3")
    assert "hf:model:qwen/qwen3-8b" in identity_keys("https://huggingface.co/Qwen/Qwen3-8B")
    assert "hf:space:a/b" in identity_keys("https://huggingface.co/spaces/a/b")
    assert not any(k.startswith("github:") for k in identity_keys("https://github.com/o/r/issues/5"))
    assert not any(k.startswith("hf:") for k in identity_keys("https://huggingface.co/blog/some-post"))


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "//x", "", None,
                                 "https://u:p@example.com/"])
def test_safe_link_rejects(bad) -> None:
    assert safe_link(bad) is None


def test_extract_arxiv() -> None:
    assert extract_arxiv_id("see arXiv:2401.01234 for details") == "2401.01234"
    assert extract_arxiv_id("https://arxiv.org/html/2401.01234v2") == "2401.01234"
    assert extract_arxiv_id("version 2401.01234 of something") is None


def test_html_to_text_strips_scripts_and_markup() -> None:
    s = html_to_text('<p>Hello <b>world</b></p><script>steal()</script><img src=x onerror=alert(1)> &amp; more')
    assert s == "Hello world & more"


def test_relevance() -> None:
    hi, terms = classify.ai_relevance("LLMs can read and write executables byte by byte")
    lo, _ = classify.ai_relevance("Sandwich Simulator")
    assert hi >= 0.9 and "llms" in terms
    assert lo == 0
    # domain names do not count
    assert classify.ai_relevance("Upload2Shell (CVE-2026-125396)", "https://pwn.ai/upload2shell")[0] < 0.35


@pytest.mark.parametrize(
    "title,kind,cls,expected",
    [
        ("The legacy embeddings endpoint is deprecated", "article", "official", "deprecation"),
        ("Acme (YC W24) is hiring ML engineers", "opportunity", "community", "opportunity"),
        ("Reportedly, a new model is coming", "article", "community", "rumor"),
        ("Introducing our new agent SDK in beta", "article", "official", "beta"),
        ("Join the waitlist for early access", "article", "official", "preview"),
        ("A study of attention", "paper", "primary", "research"),
        ("Qwen/Qwen3-8B", "model", "primary", "release"),
        ("Introducing Gemini 9", "article", "official", "announcement"),
        ("Show HN: My MCP server", "repository", "community", "release"),
        ("What's new in v2.3", "article", "independent", "update"),
    ],
)
def test_labels(title, kind, cls, expected) -> None:
    assert classify.assign_label(title, "", kind, cls).label == expected


def test_prerelease_is_preview() -> None:
    assert classify.assign_label("repo v1.0-rc1", "", "release", "official", {"prerelease": True}).label == "preview"


def test_categories_have_evidence() -> None:
    hits = {h.slug: h for h in classify.categorize("An MCP server for Postgres", "Model Context Protocol server",
                                                   "repository", ["mcp", "llm"], ["open-source"], "primary")}
    assert "mcp" in hits and "open-source" in hits
    assert "matched" in hits["mcp"].evidence


def test_verification_levels() -> None:
    assert classify.verification_level(["official", "community"], 2) == "official"
    assert classify.verification_level(["primary", "community"], 2) == "corroborated"
    assert classify.verification_level(["primary"], 1) == "primary"
    assert classify.verification_level(["community"], 1) == "community"


def test_opportunity_extraction_never_invents() -> None:
    o = classify.extract_opportunity("Acme (YC W24) is hiring an ML engineer", "Remote. Apply by March 3, 2027.")
    assert o.organization == "Acme"
    assert o.remote == "yes"
    assert o.deadline == dt.datetime(2027, 3, 3, tzinfo=dt.UTC)
    assert o.compensation == ""  # not stated -> empty, never guessed
    o2 = classify.extract_opportunity("Paid AI evaluator role", "Pay: $40/hour, remote")
    assert o2.compensation == "$40/hour"


def test_score_is_explained_and_growth_beats_totals() -> None:
    now = dt.datetime(2026, 11, 22, tzinfo=dt.UTC)
    base = dict(kind="repository", published_at=now - dt.timedelta(days=2), discovered_at=now - dt.timedelta(hours=1),
                created_at=now - dt.timedelta(days=2), verification="primary", relevance=0.9, relevance_terms=["llm"],
                distinct_publishers=1, publishers=["GitHub"], has_license=True, summary_len=80,
                links={"repository": "x"})
    fast = ScoreInput(**base, metrics={"stars": 300}, growth=[Growth("stars", 150, "since_creation", 48)])
    old_big = ScoreInput(**{**base, "created_at": now - dt.timedelta(days=900), "published_at": now - dt.timedelta(days=900)},
                         metrics={"stars": 90000}, growth=[])
    s_fast, comps = compute_score(fast, now)
    s_big, _ = compute_score(old_big, now)
    assert s_fast > s_big, "a fast-growing new repo outranks an old repo with huge total stars"
    assert comps["adoption"]["kind"] == "measured" and "stars/day" in comps["adoption"]["evidence"]
    assert comps["relevance"]["kind"] == "inferred"
    assert set(comps) == {"recency", "credibility", "relevance", "novelty", "adoption", "utility", "corroboration"}


def test_growth_from_snapshots() -> None:
    now = dt.datetime(2026, 11, 22, 12, tzinfo=dt.UTC)
    g = growth_from_snapshots("stars", [(now - dt.timedelta(hours=12), 100), (now, 160)], None, now, 160)
    assert g and g.method == "snapshots" and g.per_day == pytest.approx(120)


def test_stars_without_forks_are_discounted_and_explained() -> None:
    now = dt.datetime(2026, 11, 22, tzinfo=dt.UTC)
    base = dict(kind="repository", published_at=now - dt.timedelta(days=1), discovered_at=now,
                created_at=now - dt.timedelta(days=1), verification="primary", relevance=0.9, relevance_terms=["llm"],
                distinct_publishers=1, publishers=["GitHub"], has_license=True, summary_len=80, links={},
                growth=[Growth("stars", 300, "since_creation", 24)])
    organic = compute_score(ScoreInput(**base, metrics={"stars": 300, "forks": 25}), now)[1]["adoption"]
    farmed = compute_score(ScoreInput(**base, metrics={"stars": 300, "forks": 0}), now)[1]["adoption"]
    assert farmed["value"] == pytest.approx(organic["value"] / 2)
    assert "0 forks" in farmed["evidence"] and "inferred" in farmed["evidence"]
