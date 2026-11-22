# Development log

Chronological, append-only. Each entry: what changed, how it was verified (exact commands and real
results), limitations and the next step. Dates are UTC.

---

## 2026-11-22 — Phase 0: audit

**Workspace.** Repo `R-enord/Private`, branch `ccr-49257046-ypc0g7`. One commit with a one-line
`README.md` (`# Private`). Clean tree, no manifests, env files or services. Nothing to migrate; README
expanded rather than replaced.

**Machine (cloud build container, NOT the owner's computer).** Ubuntu 24.04.5 x86_64, 4 vCPU Xeon
@ 2.3 GHz, 15 GiB RAM, no swap, no GPU, ~61 GB free disk. Python 3.13.18 + uv, Node 22.23 + pnpm,
PostgreSQL 16.15 (cluster stopped; `pg_trgm` available, pgvector not installed), Redis 7.0.15
(installed, unused), Docker CLI without a daemon, Ollama not installed. No computer of the owner's was
attached, so `hub doctor` exists to run the same audit on the real machine.

**Network.** The initial egress policy blocked HF, HN, arXiv, Ollama and the RSS hosts (proxy 403). The
owner approved a personal environment with exactly those hosts. In this container `api.github.com` is
proxied with authentication (15,000/h core, 30/min search) and is scoped to this repo: `/repos/*` for
other repositories returns 403. Search works. On the owner's machine GitHub is unauthenticated
(60/h, search 10/min) unless a free token is configured.

**Research.** Official terms and limits were read from primary sources fetched through the proxy:
github/docs and github/site-policy sources, HF hub-docs, the HF OpenAPI spec and `huggingface_hub`
client, the HackerNews/API README, the hn.algolia.com/api page bundle, arXiv/arxiv-docs ToU and RSS
docs, and ollama/ollama docs plus ollama.com/pricing. The research agents' own fetch tool could not reach
the docs hosts, so they were fetched directly. Results are in SOURCES.md and API_INTEGRATIONS.md.

Key findings:
- Ollama cloud models are pay-per-token. The app blocks them in code; Ollama runs with `OLLAMA_NO_CLOUD=1`.
- No cloud LLM free tier could be verified from here (their docs hosts were unreachable), so the verified
  free-tier registry is empty and all cloud AI stays blocked.

**Ollama.** Installed v0.40.17 to `/opt/ollama` (1.5 GB download, 2.4 GB on disk, CPU-only), bound to
127.0.0.1 with `OLLAMA_NO_CLOUD=1`; the log confirms `Ollama cloud disabled: true`. Pulled `qwen2.5:1.5b`
(986 MB) and `all-minilm` (45 MB).

## 2026-11-22 — Phase 1 increment 1: core, egress guard, connectors, pipeline

**Built**
- `hub/config.py`: zero-cost mode is a validator that refuses `false`; exposure check (non-loopback
  bind or registration requires explicit approval).
- `hub/models.py` + Alembic `0001`: 39 tables, provenance-first, append-only history, ownership columns.
- `hub/net/`:
  - `SafeHttpClient`: allowlist, cost guard before I/O, SSRF and scheme rules, per-hop redirect
    checks, jittered backoff, Retry-After and rate-limit header handling, size cap, request log.
  - `cost_guard`: paid-host denylist and patterns, Ollama cloud-model block, empty verified-free registry.
  - DB-coordinated host throttle.
- Connectors: GitHub (search, releases), Hugging Face (list, orgs, daily papers), HN (Firebase), HN
  Algolia (disabled pending review), arXiv (API, 3.1 s spacing), RSS/Atom (conditional GET).
- Pipeline:
  - Raw versioning; canonical URLs and identity keys; cross-source merge with field precedence.
  - Near-duplicate review queue (never auto-merged).
  - Rule-based categories and labels with evidence; verification levels; opportunities (stated facts only).
  - Append-only metric snapshots and release events; transparent scoring (measured vs inferred).

**Verified**
- `cd backend && uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head`
  → OK, 39 tables.
- `cd backend && uv run pytest -q` → **88 passed**. Tests use a fresh `hub_test` DB migrated from zero
  and recorded fixtures listed in `tests/fixtures/README.md`.
- Live run of every enabled source into the dev DB:

  | Source | Result |
  |---|---|
  | github-new-ai-repos | 66 new |
  | github-releases | error: HTTP 403 from this container's GitHub proxy scoping (expected here) |
  | hf-trending-models | 50 new |
  | hf-org-models | 146 new, 4 merged |
  | hf-trending-spaces | 30 new |
  | hf-daily-papers | 50 new |
  | hn-ai-stories | 22 new, 1 merged, 97 non-AI filtered |
  | arxiv-ai | 178 new, 22 merged (with HF Daily Papers), 2 requests |
  | rss-hf-blog | 200 new |

  Total: 742 items, 866 raw records, 0 fixture-flagged items in the live DB.

**Fixed during verification**
- Domains such as `pwn.ai` counted as AI terms → URLs and domains are now stripped before matching.
- The GitHub connector mutated raw payloads (`_query`) → payloads are stored verbatim, and repos
  repeated within a run are skipped.
- Near-duplicate review flagged distinct HF artifacts → limited to articles, discussions and
  opportunities (kinds without strong identity keys).

**Limitations**
- Star-farmed or spam repositories can rank high. Stars are not proof of quality or security; the
  credibility score reflects source class only.
- Per-repo GitHub endpoints can't be verified live from this container.

**Next:** Postgres job queue, scheduler and worker; then API, UI and AI.

## 2026-11-22 — Phase 1–2 increment 2: queue, worker, AI, API

**Built**
- Postgres job queue (SKIP LOCKED, dedupe, backoff, dead-letter, defer, stale recovery).
- Advisory-lock scheduler with periodic jobs; worker process with heartbeats and graceful stop.
- AI provider manager: Ollama loopback-only, cloud models refused, cache, circuit breaker, privacy gating.
- Grounding validator; local embeddings and SQL-cosine semantic search.
- Deterministic alerts and reports.
- FastAPI: auth (scrypt, server sessions, lockout), CSRF header, Origin and Host checks, CSP; all read and
  admin endpoints; SSE.
- CLI.

**Verified**
- `uv run pytest -q` → 120 passed.
- `ruff check` → clean; `mypy hub` → clean.
- Live grounded summary with qwen2.5:1.5b in **20.4 s**; re-run from cache in **0.01 s**.

**Fixed during verification**
- Provider error state was rolled back with the failed task → state is now committed before re-raising.
- Final worker stats were not written on stop → `stop()` now writes them.
- Global reports could duplicate because NULL owner values count as distinct → migration 0002
  (NULLS NOT DISTINCT).

## 2026-11-22 — increment 3: web UI, arXiv lookups, inorganic-star discount, community

**Built**
- Vite/React/TS/Tailwind UI covering every page. Charts follow the data-viz method: validated colors,
  table views, hover.
- arXiv `id_list` lookups for papers first seen on HN/HF.
- Adoption discount for stars accrued with 0 forks (seen live: a new repo with 337 stars and 0 forks ranked #1).
- Community slice: articles, comments, likes, author pages, moderation.
- Makefile, Dockerfile, docker-compose (loopback-only), systemd and launchd units.
- `HUB_OLLAMA_TRUSTED_HOSTS` for container networks.

**Fixed during verification**
- pnpm installed TypeScript 7, which typescript-eslint does not support → pinned TS 6.
- `@vitejs/plugin-react@6` needs Vite 8 → upgraded Vite.
- ESLint (react-hooks) flagged a component defined during render → hoisted.
- The `hub` console script was missing its editable `.pth` → reinstalled the package.

## 2026-11-22 — end-to-end verification (live system, real data)

All of the following ran against the dev database with real sources, using `hub serve` + `hub worker` as
separate processes.

| Scenario | Command / method | Result |
|---|---|---|
| Browser closed | No UI client after the 12:22 Playwright session; only the worker running | Items **811 → 851** (12:27 → 12:37 UTC), AI summaries 18 → 21; scheduler-driven runs at 12:35 (hf-trending-models) and 12:37 (hn-ai-stories) |
| UI renders with live data | Playwright login + 11 pages screenshotted | All pages rendered; only console error was the expected pre-login 401 on `/api/auth/me` |
| Ollama stopped | `pkill -f "ollama serve"`, then `hub run-source hn-ai-stories --force` | Ingestion OK (**27 new**, 93 non-AI filtered); provider status `unavailable`; queued `ai_enrich` job **deferred** (status queued, attempts 0, reason recorded); Ollama restarted afterwards |
| Internet unavailable | `HTTPS_PROXY=http://127.0.0.1:9 hub run-source hf-trending-spaces --force` | `error` after 4 attempts (1 + 3 retries); health `degraded`, backoff set, no crash; local full-text search still returned 21 results |
| Worker crash | `run-now` on hf-org-models, `kill -9` the worker mid-job, start a new worker | Job 49 re-queued after the heartbeat went stale (`recovered after worker loss: vm:6169:…`) and **succeeded** |
| Zero-cost | `HUB_ZERO_COST_MODE=false hub status`; `HUB_OLLAMA_URL=https://ollama.com hub doctor`; `HUB_CLOUD_AI_ENABLED=true` + direct calls to api.openai.com and openrouter.ai | Refused to start (validation error); Ollama URL blocked; no cloud provider constructed; both paid calls **blocked before any network I/O** (log outcome `blocked`) |
| Backup/restore | `hub backup` (1.2 MB dump), restored twice into a scratch DB | First attempt **failed** (pg_restore tried to drop the superuser-owned `pg_trgm`). Fixed: skip extension TOC entries and surface stderr. Rerun: counts identical (838 items, 1,249 raw, 1 user, alembic 0002). Added `tests/test_backup.py` |
| Compose | `HUB_DB_PASSWORD=x docker compose --profile ai config` | Valid; host ports bound only to `127.0.0.1` (api, db, ollama). Image build **not run**: no Docker daemon in the build container |

**Final checks** (see the end of this log for exact output).

**Limitations**
- GitHub releases is unverified live here (container proxy scoping).
- Cloud AI providers are unverified, so they stay blocked.
- Lab feeds await owner terms review.
- The Docker image is unbuilt here.
- Phase 3 sources and Phase 5–6 extras (follows, submissions, notifications delivery) are not started.

### Final check output (2026-11-22)

```
backend:  uv run pytest -q              → 131 passed, 1 warning (starlette httpx deprecation)
          uv run ruff check hub tests   → All checks passed!
          uv run mypy hub               → Success: no issues found in 48 source files
          uv run alembic check          → No new upgrade operations detected.
frontend: pnpm typecheck / pnpm lint    → ok / ok
          pnpm test                     → 4 passed
          pnpm build                    → dist/assets/index-*.js 335.74 kB (gzip 102.08 kB)
```

**Next:** owner runs `make doctor` on the real machine and reviews and enables lab feeds; optional GitHub
token; then Phase 3 source audits (Reddit, Product Hunt, GitLab, Codeberg).
