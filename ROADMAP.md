# Roadmap and feature status

Status legend: **complete** (implemented *and* covered by tests or verified live, as stated) · **partial** ·
**blocked** · **not started**. A feature is marked complete only after testing. Last updated 2026-11-22.

## Definition of success (overview §Definition of success)

| Criterion | Status | Evidence |
|---|---|---|
| Useful first release runs locally | complete | `hub start`; UI at 127.0.0.1:8000, verified with Playwright screenshots |
| Background collection continues with the browser closed | complete | Separate worker process. `test_worker_collects_with_no_web_server_and_survives_restart`; live counts grew with no browser attached (DEVELOPMENT_LOG) |
| Real records arrive from permitted initial sources | complete | Live runs: GitHub search, HF ×4, HN, arXiv, HF blog RSS (DEVELOPMENT_LOG). GitHub releases is verified with fixtures only (blocked in the build container) |
| Provenance and deduplication work | complete | Raw versions, `item_sources`, identity keys, cross-source merge tests; arXiv↔HF Daily Papers and HN↔arXiv merges seen live |
| AI Radar and searchable feed | complete | Radar windows, SSE and search tested (`test_api.py`) |
| No paid service required | complete | Hard zero-cost mode; paid endpoints blocked before I/O (tests + live check) |
| Local AI is default | complete | Ollama loopback provider; live summary with qwen2.5:1.5b; cloud constructs nothing |
| Core collection useful without AI | complete | Verified live with Ollama stopped: ingestion OK, AI jobs deferred |
| Secure, ready for multi-user expansion | complete for single-owner scope | Auth, roles, ownership checks, CSRF/Host/CSP, audit log, tests. Public deployment intentionally not done |

## Phases

### Phase 0 — audit (complete)
Workspace, hardware, runtime and network audit (cloud build container; `hub doctor` for the owner's machine).
Source and terms audit is in [SOURCES.md](SOURCES.md); environment assessment is in [ENVIRONMENT.md](ENVIRONMENT.md).

### Phase 1 — core (complete)

| Feature | Status | Notes |
|---|---|---|
| Backend API, DB, migrations | complete | Alembic 0001–0002; clean upgrade on every test run |
| Scheduler and background workers | complete | Postgres queue, leader lock, heartbeats, crash recovery (tested and killed live with SIGKILL) |
| Source registry with audit fields | complete | 17 sources: 9 enabled, 8 disabled pending review |
| GitHub connector | complete (search) / partial (releases) | Search live-verified. Releases tested against a synthetic fixture: per-repo endpoints are blocked in the build container; expected to work on the owner's machine |
| Hugging Face (trending models, org models, spaces, daily papers) | complete | Live |
| Hacker News (official API) | complete | Live; AI-relevance filter |
| HN Algolia search | partial | Implemented and documented; disabled pending terms review |
| arXiv API (+ id lookups) | complete | Live; 3.1 s spacing enforced across processes |
| RSS/Atom | complete | HF blog live. Other official feeds implemented but disabled pending owner terms review |
| Raw preservation, normalization, canonical URLs, provenance, dedup, checkpoints, retries/backoff, rate limits, health | complete | Tests |
| Categories, labels, verification levels | complete | Rule-based with evidence |
| Home, Radar, Feed, Item page, Sources, Ops UI | complete | Typecheck, lint, unit tests and build; Playwright screenshots |

### Phase 2 — AI and ranking (complete)

| Feature | Status |
|---|---|
| Provider abstraction, Ollama primary, cache, circuit breaker, quotas, usage tracking | complete |
| Optional verified free cloud providers | **blocked**: no free tier could be verified (official docs unreachable from the build environment). The adapter exists but cannot be constructed; see API_INTEGRATIONS.md |
| Source-grounded summaries + grounding validator | complete (live with qwen2.5:1.5b) |
| Transparent ranking (measured vs inferred, growth not totals) | complete |
| Keyword search (FTS + trigram) | complete |
| Semantic search (local embeddings, SQL cosine), hybrid RRF | complete (tested; live embeddings computed with all-minilm) |
| AI classification/metadata extraction | partial: AI category and maturity suggestions are shown labeled but are not used for filtering; deterministic extraction is used for facts |

### Phase 3 — expanded sources and opportunities

| Feature | Status |
|---|---|
| Opportunities tracker (source-stated fields only; expiry) | complete for HN jobs/labels; dedicated job-board sources not started |
| Reddit, Product Hunt, GitLab, Codeberg, X, Discord, forums, newsletters | not started. Each needs a terms/API audit first. Reddit, Product Hunt and X APIs are on the blocked-host list until audited; X API is paid |
| More official lab feeds (OpenAI, DeepMind, Google Research, GitHub blog, PyTorch, Simon Willison) | implemented, **disabled pending owner terms review** (one click in Sources with acknowledgement) |

### Phase 4 — project pages, timeline, stack

| Feature | Status |
|---|---|
| Item/project pages with provenance, related items, release history | complete |
| Release timeline (append-only) | complete |
| Stack Explorer (source-stated relationships only) | complete (basic list view; no graph canvas) |
| Cross-source corroboration | complete |
| Side-by-side comparisons | not started |
| Canonical entity table (multi-type entities, verified-facts records) | partial: schema exists; items act as canonical records via identity keys |

### Phase 5 — community publishing (partial)

| Feature | Status |
|---|---|
| Articles (Markdown → sanitized HTML, drafts, published, private/public, tags, cover image URL) | complete |
| Comments, likes, author pages | complete |
| Reporting and moderation (hide/remove/restore; authors cannot override) | complete |
| Follows, project submissions, rich-text editor, image upload, video embeds | not started (embeds intentionally rendered as links only) |

### Phase 6 — personalization (partial)

| Feature | Status |
|---|---|
| Watchlists and in-app alerts (no duplicates) | complete |
| Daily/weekly reports (deterministic, local) | complete |
| Email/Discord/Telegram delivery | not started (design: optional, free, opt-in webhooks; no paid delivery) |
| Personalized ranking | not started |

### Phase 7 — security review and public readiness (partial)

| Feature | Status |
|---|---|
| Ownership/authorization tests | complete for current endpoints |
| Backup/restore | complete (tested round-trip) |
| Security review of current code | done in-session (see SECURITY.md); an external review is recommended before any public deployment |
| Public deployment | **not started by design**: requires explicit owner approval |

## Next steps (proposed)

1. Owner: run `make doctor`, pick a model size, then review and enable the official lab feeds you want.
2. Add a free GitHub token (optional) to raise GitHub limits from 60/h to 5,000/h.
3. Phase 3 audits: Reddit (API terms changed in 2023; likely needs OAuth and has commercial limits), Product
   Hunt (API v2 requires a token; terms need review), GitLab/Codeberg (public APIs).
4. Entity table population and comparison views.
