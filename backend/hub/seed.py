"""Seed data: coverage categories and the audited source registry.

Every source records how it is accessed, what it costs, the quota/terms evidence and the date checked
(2026-11-22). Sources whose terms could not be verified ship DISABLED with a reason, pending owner review.
Re-seeding updates descriptive/audit fields only; owner choices (enabled, paused, interval, config) are kept.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.models import Category, Source

CHECKED = dt.datetime(2026, 11, 22, tzinfo=dt.UTC)

CATEGORIES: list[tuple[str, str, str]] = [
    ("llm", "LLMs & foundation models", "Large language and foundation models, chat/instruct models."),
    ("model-releases", "Model releases, fine-tunes & benchmarks", "New checkpoints, fine-tunes, quantizations, evals."),
    ("mcp", "MCP servers, tools & integrations", "Model Context Protocol servers, clients and integrations."),
    ("agents", "Agents, frameworks & orchestration", "Agent frameworks, multi-agent systems, orchestration."),
    ("open-source", "Open-source projects", "GitHub and other open-source repositories."),
    ("devtools-infra", "APIs, inference & dev tools", "Inference engines, serving, SDKs, developer tooling, infra."),
    ("creative-ai", "Creative AI", "Image, video, audio, music, voice, animation and 3D generation."),
    ("research", "Research & papers", "AI/ML research, preprints and papers."),
    ("industry", "Company & lab announcements", "Announcements from companies, labs and the industry."),
    ("workflows", "Workflows, tutorials & use cases", "How-tos, tutorials, practical use cases."),
    ("opportunities", "Opportunities", "AI roles, evaluation/annotation work, internships, grants, hackathons, betas."),
    ("emerging", "Emerging tech & independent devs", "Early-stage projects and independent developers."),
]

UA_NOTE = "Requests identify the client with a generic User-Agent (no personal data)."

GITHUB_TERMS = "https://docs.github.com/en/site-policy/github-terms/github-terms-of-service#h-api-terms"
GITHUB_QUOTA = (
    "Verified 2026-11-22 (docs.github.com rate-limits page): unauthenticated 60 req/hour per IP; search API "
    "10 req/min unauthenticated, 30 req/min authenticated, max 1,000 results per search. Secondary limits: "
    "<=100 concurrent, <=900 points/min per endpoint; on 403/429 wait for retry-after or x-ratelimit-reset "
    "(>=1 min if absent). Conditional 304 responses are free only when authenticated. Optional free personal "
    "access token raises limits; never required."
)
GITHUB_STORAGE = (
    "Terms H/AUP s7: no spamming or selling users' personal information. We store public repo metadata "
    "(name, description, topics, stars, license as stated) and link back to github.com. No owner emails stored."
)
HF_TERMS = "https://huggingface.co/terms-of-service"
HF_QUOTA = (
    "Verified 2026-11-22 (huggingface.co/docs/hub/rate-limits): fixed 5-minute windows; anonymous per IP: "
    "500 Hub API requests, 3,000 resolver, 100 page requests; free account 1,000 API. HTTP 429 when exceeded; "
    "headers RateLimit / RateLimit-Policy (observed: '\"api\";r=485;t=257', '\"fixed window\";\"api\";q=500;w=300')."
)
HF_STORAGE = (
    "Public repository content is licensed to users via the Hub (ToS 'Your Content'); repo licenses are "
    "shown only as the repo states them. We store metadata (id, tags, likes, downloads, dates) and link back."
)
ARXIV_TERMS = "https://info.arxiv.org/help/api/tou.html"
ARXIV_QUOTA = (
    "Verified 2026-11-22 (arXiv API ToU): legacy APIs (arXiv API, RSS, OAI-PMH) — no more than one request "
    "every three seconds, single connection at a time, across all machines you control. max_results slices "
    "<= 2,000, total <= 30,000. RSS updated daily at midnight Eastern."
)
ARXIV_STORAGE = (
    "Descriptive metadata (title, abstract, authors, ids, categories) is CC0 and may be stored and shared. "
    "E-prints (PDF/source) must NOT be stored or served — we link to the arxiv.org abstract page only. "
    "Do not represent the project as endorsed by arXiv."
)
ARXIV_ATTRIBUTION = "Metadata courtesy of arXiv (CC0). Not affiliated with or endorsed by arXiv."
HN_TERMS = "https://github.com/HackerNews/API"
HN_QUOTA = (
    "Verified 2026-11-22 (official README): 'There is currently no rate limit.' Public, no auth. We still "
    "self-limit (spacing 0.2 s, capped item fetches per run)."
)

AI_TOPIC_QUERIES = [
    "topic:llm",
    "topic:large-language-models",
    "topic:mcp-server",
    "topic:model-context-protocol",
    "topic:ai-agents",
    "topic:agents topic:llm",
    "topic:generative-ai",
    "topic:rag",
    "topic:text-to-image",
    "topic:text-to-speech",
    "topic:local-llm",
    "topic:inference",
]

WATCHED_REPOS = [
    "ollama/ollama",
    "ggml-org/llama.cpp",
    "vllm-project/vllm",
    "huggingface/transformers",
    "modelcontextprotocol/servers",
    "modelcontextprotocol/python-sdk",
    "langchain-ai/langgraph",
    "crewAIInc/crewAI",
    "microsoft/autogen",
    "comfyanonymous/ComfyUI",
    "open-webui/open-webui",
    "unslothai/unsloth",
]

HF_ORGS = [
    "meta-llama", "mistralai", "Qwen", "google", "microsoft", "deepseek-ai", "openai", "nvidia",
    "ibm-granite", "allenai", "HuggingFaceTB", "black-forest-labs", "stabilityai", "moonshotai", "zai-org",
]


def _rss(
    key: str, name: str, url: str, homepage: str, publisher: str, source_class: str, *, enabled: bool,
    terms_status: str, terms_url: str = "", disabled_reason: str = "", categories: list[str] | None = None,
    interval_s: int = 1800, quota: str = "", storage: str = "", ai_filter: bool = False,
) -> dict[str, Any]:
    host = url.split("/")[2]
    return {
        "key": key, "name": name, "connector_type": "rss", "endpoint": url, "homepage": homepage,
        "publisher": publisher, "source_class": source_class, "access_method": "RSS/Atom feed (HTTP GET, conditional)",
        "auth_method": "none", "cost_status": "free_public",
        "quota_notes": quota or "No published quota. Polled at most every 30 min with ETag/Last-Modified.",
        "terms_url": terms_url, "terms_status": terms_status, "terms_checked_at": CHECKED,
        "attribution": f"Headline and excerpt from {publisher}; full text at the original link.",
        "storage_notes": storage or "Store title, link, date, author and the feed-provided excerpt only; "
        "no full-text republication; always link to the original.",
        "allowed_hosts": [host], "min_interval_s": 600, "interval_s": interval_s, "host_min_spacing_s": 2.0,
        "config": {"feed_url": url, "ai_filter": ai_filter}, "categories_hint": categories or [],
        "enabled": enabled, "disabled_reason": disabled_reason,
    }


PENDING_REVIEW = (
    "Pending owner review: the feed is publicly published for syndication, but this site's terms were not "
    "individually reviewed (2026-11-22). Enable in Sources after review."
)

SOURCES: list[dict[str, Any]] = [
    {
        "key": "github-new-ai-repos", "name": "GitHub — new AI repositories (Search API)",
        "connector_type": "github", "endpoint": "https://api.github.com/search/repositories",
        "homepage": "https://github.com", "publisher": "GitHub", "source_class": "primary",
        "access_method": "GitHub REST Search API", "auth_method": "none (optional free token)",
        "cost_status": "free_public", "quota_notes": GITHUB_QUOTA, "terms_url": GITHUB_TERMS,
        "terms_status": "verified", "terms_checked_at": CHECKED,
        "attribution": "Repository metadata from GitHub; links to github.com.", "storage_notes": GITHUB_STORAGE,
        "allowed_hosts": ["api.github.com"], "min_interval_s": 600, "interval_s": 1800, "host_min_spacing_s": 7.0,
        "config": {"mode": "search", "queries": AI_TOPIC_QUERIES, "queries_per_run": 4, "created_within_days": 14,
                   "min_stars": 10, "per_page": 30},
        "categories_hint": ["open-source"], "enabled": True,
    },
    {
        "key": "github-releases", "name": "GitHub — releases of watched AI projects",
        "connector_type": "github", "endpoint": "https://api.github.com/repos/{owner}/{repo}/releases",
        "homepage": "https://github.com", "publisher": "GitHub (repository owners)", "source_class": "official",
        "access_method": "GitHub REST API (list releases)", "auth_method": "none (optional free token)",
        "cost_status": "free_public", "quota_notes": GITHUB_QUOTA, "terms_url": GITHUB_TERMS,
        "terms_status": "verified", "terms_checked_at": CHECKED,
        "attribution": "Release notes excerpt from the repository's GitHub releases.", "storage_notes": GITHUB_STORAGE,
        "allowed_hosts": ["api.github.com"], "min_interval_s": 1800, "interval_s": 3600, "host_min_spacing_s": 2.0,
        "config": {"mode": "releases", "repos": WATCHED_REPOS, "per_page": 5},
        "categories_hint": ["open-source", "devtools-infra"], "enabled": True,
    },
    {
        "key": "hf-trending-models", "name": "Hugging Face — trending models",
        "connector_type": "huggingface", "endpoint": "https://huggingface.co/api/models",
        "homepage": "https://huggingface.co/models", "publisher": "Hugging Face Hub", "source_class": "primary",
        "access_method": "Hugging Face Hub REST API", "auth_method": "none (optional free token)",
        "cost_status": "free_public", "quota_notes": HF_QUOTA, "terms_url": HF_TERMS, "terms_status": "verified",
        "terms_checked_at": CHECKED, "attribution": "Model metadata from the Hugging Face Hub.",
        "storage_notes": HF_STORAGE, "allowed_hosts": ["huggingface.co"], "min_interval_s": 300, "interval_s": 900,
        "host_min_spacing_s": 1.0,
        "config": {"mode": "list", "repo_type": "models", "params": {"sort": "trendingScore", "limit": 50, "full": "true"}},
        "categories_hint": ["model-releases"], "enabled": True,
    },
    {
        "key": "hf-org-models", "name": "Hugging Face — new models from major AI orgs",
        "connector_type": "huggingface", "endpoint": "https://huggingface.co/api/models?author={org}",
        "homepage": "https://huggingface.co/models", "publisher": "Hugging Face Hub (org accounts)",
        "source_class": "official", "access_method": "Hugging Face Hub REST API", "auth_method": "none",
        "cost_status": "free_public", "quota_notes": HF_QUOTA, "terms_url": HF_TERMS, "terms_status": "verified",
        "terms_checked_at": CHECKED, "attribution": "Model metadata from the Hugging Face Hub.",
        "storage_notes": HF_STORAGE, "allowed_hosts": ["huggingface.co"], "min_interval_s": 900, "interval_s": 1800,
        "host_min_spacing_s": 1.0,
        "config": {"mode": "orgs", "repo_type": "models", "orgs": HF_ORGS,
                   "params": {"sort": "createdAt", "direction": "-1", "limit": 10, "full": "true"}},
        "categories_hint": ["model-releases", "industry"], "enabled": True,
    },
    {
        "key": "hf-trending-spaces", "name": "Hugging Face — trending Spaces (demos)",
        "connector_type": "huggingface", "endpoint": "https://huggingface.co/api/spaces",
        "homepage": "https://huggingface.co/spaces", "publisher": "Hugging Face Hub", "source_class": "primary",
        "access_method": "Hugging Face Hub REST API", "auth_method": "none", "cost_status": "free_public",
        "quota_notes": HF_QUOTA, "terms_url": HF_TERMS, "terms_status": "verified", "terms_checked_at": CHECKED,
        "attribution": "Space metadata from the Hugging Face Hub.", "storage_notes": HF_STORAGE,
        "allowed_hosts": ["huggingface.co"], "min_interval_s": 600, "interval_s": 1800, "host_min_spacing_s": 1.0,
        "config": {"mode": "list", "repo_type": "spaces", "params": {"sort": "trendingScore", "limit": 30, "full": "true"}},
        "categories_hint": ["emerging"], "enabled": True,
    },
    {
        "key": "hf-daily-papers", "name": "Hugging Face — Daily Papers",
        "connector_type": "huggingface", "endpoint": "https://huggingface.co/api/daily_papers",
        "homepage": "https://huggingface.co/papers", "publisher": "Hugging Face (community-curated)",
        "source_class": "community", "access_method": "Hugging Face Hub REST API (documented in OpenAPI spec)",
        "auth_method": "none", "cost_status": "free_public", "quota_notes": HF_QUOTA, "terms_url": HF_TERMS,
        "terms_status": "verified", "terms_checked_at": CHECKED,
        "attribution": "Paper curation and upvotes from Hugging Face Daily Papers; metadata from arXiv.",
        "storage_notes": HF_STORAGE + " Paper PDFs are never stored.", "allowed_hosts": ["huggingface.co"],
        "min_interval_s": 900, "interval_s": 3600, "host_min_spacing_s": 1.0,
        "config": {"mode": "daily_papers", "params": {"limit": 50, "sort": "publishedAt"}},
        "categories_hint": ["research"], "enabled": True,
    },
    {
        "key": "hn-ai-stories", "name": "Hacker News — AI stories (top, new & Show HN)",
        "connector_type": "hn", "endpoint": "https://hacker-news.firebaseio.com/v0/",
        "homepage": "https://news.ycombinator.com", "publisher": "Hacker News (Y Combinator)",
        "source_class": "community", "access_method": "Official HN Firebase API", "auth_method": "none",
        "cost_status": "free_public", "quota_notes": HN_QUOTA, "terms_url": HN_TERMS, "terms_status": "verified",
        "terms_checked_at": CHECKED, "attribution": "Story and discussion from Hacker News (news.ycombinator.com).",
        "storage_notes": "Store story id, title, URL, points, comment count, time. Comments are not stored.",
        "allowed_hosts": ["hacker-news.firebaseio.com"], "min_interval_s": 300, "interval_s": 600,
        "host_min_spacing_s": 0.2,
        "config": {"lists": ["topstories", "newstories", "showstories"], "list_limit": 200, "max_item_fetches": 120,
                   "refresh_recent_hours": 24, "max_refresh": 30, "min_relevance": 0.35},
        "categories_hint": [], "enabled": True,
    },
    {
        "key": "hn-algolia-ai", "name": "Hacker News search (Algolia) — AI keyword stories",
        "connector_type": "hn_algolia", "endpoint": "https://hn.algolia.com/api/v1/search_by_date",
        "homepage": "https://hn.algolia.com", "publisher": "Algolia (HN Search)", "source_class": "community",
        "access_method": "HN Search API by Algolia", "auth_method": "none", "cost_status": "free_public",
        "quota_notes": "Verified 2026-11-22 (hn.algolia.com/api page): 'We are limiting the number of API "
        "requests from a single IP to 10,000 per hour.'",
        "terms_url": "https://hn.algolia.com/api", "terms_status": "unverified", "terms_checked_at": CHECKED,
        "attribution": "Search results from HN Search powered by Algolia; stories from Hacker News.",
        "storage_notes": "Same as the HN connector.", "allowed_hosts": ["hn.algolia.com"], "min_interval_s": 300,
        "interval_s": 900, "host_min_spacing_s": 1.0,
        "config": {"queries": ["LLM", "MCP server", "AI agent", "open-source model", "Ollama", "fine-tuning",
                               "diffusion model", "AI hiring", "AI hackathon"], "min_points": 5, "min_relevance": 0.35},
        "categories_hint": [], "enabled": False,
        "disabled_reason": "Pending owner review: rate limit is published, but no separate terms of use for the "
        "Algolia HN Search API were found (2026-11-22). The official HN API source covers HN without it.",
    },
    {
        "key": "arxiv-ai", "name": "arXiv — new AI/ML papers (API)",
        "connector_type": "arxiv", "endpoint": "https://export.arxiv.org/api/query",
        "homepage": "https://arxiv.org", "publisher": "arXiv (authors' preprints)", "source_class": "primary",
        "access_method": "arXiv API (Atom)", "auth_method": "none", "cost_status": "free_public",
        "quota_notes": ARXIV_QUOTA, "terms_url": ARXIV_TERMS, "terms_status": "verified",
        "terms_checked_at": CHECKED, "attribution": ARXIV_ATTRIBUTION, "storage_notes": ARXIV_STORAGE,
        "allowed_hosts": ["export.arxiv.org"], "min_interval_s": 1800, "interval_s": 3600,
        "host_min_spacing_s": 3.1,
        "config": {"search_query": "cat:cs.AI OR cat:cs.CL OR cat:cs.LG OR cat:cs.CV OR cat:cs.MA OR cat:cs.IR",
                   "page_size": 100, "max_pages": 3},
        "categories_hint": ["research"], "enabled": True,
    },
    _rss("rss-hf-blog", "Hugging Face blog", "https://huggingface.co/blog/feed.xml", "https://huggingface.co/blog",
         "Hugging Face", "official", enabled=True, terms_status="verified", terms_url=HF_TERMS,
         categories=["industry", "workflows"], quota=HF_QUOTA),
    _rss("rss-arxiv-cs-ai", "arXiv cs.AI daily RSS", "https://rss.arxiv.org/rss/cs.AI", "https://arxiv.org/list/cs.AI/new",
         "arXiv", "primary", enabled=False, terms_status="verified", terms_url=ARXIV_TERMS, categories=["research"],
         interval_s=21600, quota=ARXIV_QUOTA, storage=ARXIV_STORAGE,
         disabled_reason="Disabled by default: redundant with the arXiv API source (same papers). "
         "Terms verified; enable if you prefer the daily RSS listing."),
    _rss("rss-github-blog-ai", "GitHub blog — AI & ML", "https://github.blog/ai-and-ml/feed/",
         "https://github.blog/ai-and-ml/", "GitHub", "official", enabled=False, terms_status="unverified",
         disabled_reason=PENDING_REVIEW, categories=["industry", "devtools-infra"]),
    _rss("rss-openai-news", "OpenAI news", "https://openai.com/news/rss.xml", "https://openai.com/news/",
         "OpenAI", "official", enabled=False, terms_status="unverified", disabled_reason=PENDING_REVIEW,
         categories=["industry", "llm"]),
    _rss("rss-deepmind", "Google DeepMind blog", "https://deepmind.google/blog/rss.xml", "https://deepmind.google/discover/blog/",
         "Google DeepMind", "official", enabled=False, terms_status="unverified", disabled_reason=PENDING_REVIEW,
         categories=["industry", "research"]),
    _rss("rss-google-research", "Google Research blog", "https://research.google/blog/rss/", "https://research.google/blog/",
         "Google Research", "official", enabled=False, terms_status="unverified", disabled_reason=PENDING_REVIEW,
         categories=["research", "industry"]),
    _rss("rss-pytorch", "PyTorch blog", "https://pytorch.org/feed/", "https://pytorch.org/blog/",
         "PyTorch Foundation", "official", enabled=False, terms_status="unverified", disabled_reason=PENDING_REVIEW,
         categories=["devtools-infra", "open-source"]),
    _rss("rss-simonwillison", "Simon Willison's Weblog", "https://simonwillison.net/atom/everything/",
         "https://simonwillison.net/", "Simon Willison (independent)", "independent", enabled=False,
         terms_status="unverified", disabled_reason=PENDING_REVIEW, categories=["workflows", "emerging"],
         ai_filter=True),
]


def seed_categories(session: Session) -> None:
    existing = {c.slug for c in session.scalars(select(Category))}
    for i, (slug, name, desc) in enumerate(CATEGORIES):
        if slug not in existing:
            session.add(Category(slug=slug, name=name, description=desc, sort_order=i))


_OWNER_FIELDS = {"enabled", "paused", "interval_s", "config", "disabled_reason"}


def seed_sources(session: Session) -> None:
    by_key = {s.key: s for s in session.scalars(select(Source))}
    for spec in SOURCES:
        row = by_key.get(spec["key"])
        if row is None:
            session.add(Source(**spec))
            continue
        for field, value in spec.items():
            if field in _OWNER_FIELDS:
                continue
            setattr(row, field, value)


def seed_all(session: Session) -> None:
    seed_categories(session)
    seed_sources(session)
    session.flush()
