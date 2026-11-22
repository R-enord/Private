# Architecture

## Overview

```
                     ┌────────────────────────── your computer ───────────────────────────┐
 Browser (optional)  │  hub serve (FastAPI, 127.0.0.1:8000)          hub worker (separate process)
   UI ──HTTP/SSE────►│  ├─ /api/*  auth, feed, radar, search,        ├─ scheduler (advisory-lock leader)
                     │  │          sources, ops, settings, …         │    └─ enqueues due sources + periodic jobs
                     │  └─ /       built SPA (frontend/dist)         ├─ job runners (N threads, SKIP LOCKED)
                     │            │                                  │    ├─ fetch_source → connector → pipeline
                     │            ▼                                  │    ├─ arxiv_lookup, rescore, maintenance
                     │      PostgreSQL 16  ◄──────────────────────── │    ├─ ai_enrich / embed_items (AI slot)
                     │   (data + job queue + throttle + sessions)    │    └─ reports, alerts, provider checks
                     │                                               └─ heartbeat + stale-job recovery
                     │      Ollama (127.0.0.1:11434, OLLAMA_NO_CLOUD=1) ◄── AI provider manager
                     └───────────────────────────────────┬─────────────────────────────────────────┘
                                 SafeHttpClient (allowlist + zero-cost guard + SSRF rules + retries)
                                                         ▼
          api.github.com · huggingface.co · hacker-news.firebaseio.com · export.arxiv.org · feed hosts
```

## Technology choices

The overview's stack was treated as a proposal. Deviations follow its "simplest maintainable architecture"
instruction:

| Proposal | Chosen | Why |
|---|---|---|
| Next.js / React / TS / Tailwind | **Vite + React 19 + TS + Tailwind v4 SPA**, built to static files and served by FastAPI | One runtime in production and no SSR server. A local single-user app gains nothing from SSR. Revisit for public SEO pages (Phase 7). |
| FastAPI | **FastAPI** (sync endpoints, SQLAlchemy 2, psycopg 3) | As proposed. |
| PostgreSQL | **PostgreSQL 16** with `pg_trgm` | Full-text search via a generated `tsvector`; trigram for fuzzy matching and near-duplicate titles. |
| Redis + Celery/Dramatiq | **PostgreSQL job queue** (`FOR UPDATE SKIP LOCKED`, partial unique index for dedupe, advisory-lock scheduler leadership) | One fewer service and less RAM. Jobs are durable and transactional with the data they touch. Load is a few hundred jobs per hour, far below what Postgres queues handle. Redis is installable later if needed. |
| pgvector | **Float arrays + SQL cosine** (`unnest`) | Works on stock Postgres. Fine for tens of thousands of items. pgvector is an optional future optimization. |
| Ollama | **Ollama** behind a provider-neutral manager | Loopback only, cloud models refused, results cached. |
| Docker Compose | **Native and Compose both supported** | Native is the lightest (no VM on macOS/Windows). Compose binds to loopback only. |
| Polling or SSE | **SSE** with a polling fallback | Radar gets near-live updates; SSE uses short-lived DB sessions. |

## Data flow (ingestion pipeline)

1. **Schedule.** The scheduler leader enqueues `fetch_source` for each due, enabled, non-paused source not in
   backoff. A dedupe key means a source is never queued twice.
2. **Fetch.** The connector uses only `ctx.http`, a SafeHttpClient restricted to that source's declared hosts
   and throttled per host through Postgres (e.g. arXiv: one request every 3.1 s across all workers).
   Conditional requests (ETag/Last-Modified) are used where supported; retries use jittered exponential
   backoff, and Retry-After and rate-limit headers are honored.
3. **Preserve raw.** The payload is stored verbatim in `raw_records`, one row per distinct payload hash, so
   history is kept.
4. **Normalize.** Title, URL, excerpt, author/org, timestamps (published per source, discovered by hub), tags,
   metrics, source-stated license and links.
5. **Identity and dedupe.** Canonical URLs plus identity keys (`github:owner/repo`, `arxiv:ID`,
   `hf:model:id`, `url:…`). A shared key merges into one item and adds a provenance row (cross-source
   corroboration). Field precedence: official > primary > community. Similar titles without a strong key go
   to human review and are never auto-merged.
6. **Classify.** Rule-based categories, label and verification, each recording its evidence. AI suggestions
   are shown separately and never change the deterministic categories.
7. **Snapshots and timeline.** Append-only metric snapshots (for growth) and release events.
8. **Score.** A transparent weighted score with measured vs. inferred components (see below).
9. **Follow-ups.** Alerts, arXiv lookups for papers first seen elsewhere, and AI enrichment for the most
   promising new items. Checkpoints advance only after the run succeeds; failures set health and backoff.

## Ranking

`score = 100 · Σ wᵢ·vᵢ / Σ wᵢ`. Each component is in [0, 1] and records evidence:

| Component | Basis |
|---|---|
| Recency | Measured: age since publication (or discovery), with a half-life per kind. |
| Credibility | Inferred from the verification level. |
| Relevance | Inferred from AI-term evidence. |
| Novelty | Measured from creation date when known, otherwise inferred. |
| Adoption | Measured **velocity** (stars/likes/points per day from our snapshots, or since creation), not totals. Stars accrued with zero forks are discounted, with an explanation. |
| Utility | Inferred: license, description, links, artifacts. |
| Corroboration | Measured: number of independent publishers. |

Weights are editable in Settings. Scores are recomputed every 15 minutes as recency decays.

## Connector contract

See [API_INTEGRATIONS.md](API_INTEGRATIONS.md#connector-contract). In short, a connector declares
`type`/`version`, implements `fetch(ctx) → RawRecord*` and `normalize(raw, ctx) → Normalized | None`, uses only
`ctx.http`, and stages checkpoints with `ctx.set_checkpoint`. It never executes fetched content.

## AI provider manager and fallback

- **Order of use.** Providers are sorted local-first. A provider is used only if all of these hold:
  - it passes the zero-cost policy (a cloud provider needs an in-code verified free-tier policy, and none
    exist);
  - privacy allows it (private content stays local);
  - its circuit is closed (3 consecutive errors open it for 10 minutes);
  - its daily quota is not exhausted.
- **Cache.** Results are cached by (task, prompt version, provider, model, input hash).
- **Fallback.** If no provider is available, `AIUnavailable` makes the job **defer**: it is re-queued without
  consuming an attempt. Ingestion is never blocked.
- **Output validation.** Output must match a JSON schema. It is then post-validated: sentences containing
  numbers, versions, sizes, prices or licenses absent from the source text are removed, as are URLs and
  commands; categories and maturity are restricted to closed vocabularies. The result is labeled as
  AI-generated, with model and time.

## Cost and privacy controls

- Hard zero-cost mode (the config validator refuses `false`), with paid-host and pattern denylists checked
  before I/O.
- An empty verified-free registry; Ollama cloud models (`*-cloud`, remote entries) are refused, and remote
  Ollama hosts are refused unless explicitly LAN- or container-trusted.
- A per-source host allowlist. The only egress is the enabled sources plus local Ollama, with no telemetry.
- Resource controls: `HUB_WORKER_CONCURRENCY`, AI concurrency of 1, `ai_num_ctx`, and a low-resource mode
  (single thread, slower backfill, no embeddings). Pause/resume applies globally, per job type and per
  source. HTTP logs are pruned after 14 days.

## Scaling and multi-user path

- **Data model.** Ownership columns (`owner_id`/`author_id`/`user_id`), roles
  (viewer < member < moderator < admin < owner) and central permission checks (`hub/security/auth.py`)
  already exist.
- **Opening to users.** Becoming multi-user means enabling registration (gated on
  `HUB_PUBLIC_ACCESS_APPROVED`), adding email verification and rate limiting, and putting a reverse proxy
  with TLS in front. See the [SECURITY.md](SECURITY.md) checklist.
- **Scaling out.** Workers scale horizontally (SKIP LOCKED, advisory-lock leader). The SPA can move behind a
  CDN.
