# Private — AI Intelligence Hub

A **local-first, zero-cost** AI intelligence and discovery platform. It continuously collects AI developments
from permitted public sources (GitHub, Hugging Face, Hacker News, arXiv, RSS/Atom). It preserves raw records
and provenance, deduplicates across sources, labels and ranks items with transparent reasons, and serves an AI
Radar, a searchable feed and an operations dashboard in your browser. It runs entirely on your own computer.
Local AI (Ollama) adds source-grounded summaries when available; everything else works without it.

> Product source of truth: the *AI Intelligence Hub — Product Overview & Implementation Specification v1.0*.
> Feature status lives in [ROADMAP.md](ROADMAP.md); verification history in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md).

## Guarantees

| Guarantee | How it is enforced |
|---|---|
| **No paid services** | Zero-cost mode is a hard invariant: setting `HUB_ZERO_COST_MODE=false` makes the app refuse to start. Every outbound request goes through one client that refuses paid or metered endpoints *before any network I/O* (`backend/hub/net/cost_guard.py`). |
| **Local-first** | UI, API, PostgreSQL, scheduler, workers and Ollama run on your machine. The server binds to `127.0.0.1`. Docker publishes ports on loopback only. |
| **Keeps collecting with the browser closed** | Collection runs in a separate worker process (`hub worker`), independent of the web UI. Use `hub start`, Docker Compose or the systemd/launchd units to keep it running. |
| **Useful without AI** | Ingestion, dedup, labels, ranking, keyword search, alerts and reports are deterministic. AI jobs wait in the queue while Ollama is down. |
| **Private by default** | Registration is disabled and there is a single owner. No telemetry. Private content never goes to a cloud AI; no cloud AI is enabled in this release. |
| **No fabricated data** | Real records only. Test fixtures are labeled, flagged `is_fixture` and hidden from the UI. Missing facts show as "not stated by source". |

## Quick start (native)

Requirements: Python ≥ 3.11 with [uv](https://docs.astral.sh/uv/), Node ≥ 20 with pnpm, PostgreSQL ≥ 15.
Ollama is optional ([install](https://ollama.com/download)); run it with `OLLAMA_NO_CLOUD=1`.

```bash
# 1. database (once)
createuser -P hub          # choose a password
createdb -O hub hub
cp .env.example .env       # set HUB_DATABASE_URL with that password

# 2. install, build the UI, migrate, create the owner
make setup                 # uv sync + pnpm install + UI build
make migrate               # alembic upgrade head + seed the audited source registry
make owner                 # interactive: owner email + password (min 12 chars)

# 3. run (server + worker, auto-restart)
make start                 # open http://127.0.0.1:8000
```

Optional local AI (CPU works; see [ENVIRONMENT.md](ENVIRONMENT.md) for model sizes):

```bash
OLLAMA_NO_CLOUD=1 ollama serve &
ollama pull qwen2.5:1.5b && ollama pull all-minilm
```

Check your machine and get recommendations: `make doctor`.

## Quick start (Docker Compose)

```bash
echo "HUB_DB_PASSWORD=$(openssl rand -hex 16)" >> .env
docker compose up -d db
docker compose run --rm api hub migrate
docker compose run --rm api hub create-owner
docker compose --profile ai up -d          # api + worker + db + local Ollama (OLLAMA_NO_CLOUD=1)
```

Every port is published on `127.0.0.1` only. The Dockerfile and compose file were validated
(`docker compose config`) but not built here: the build environment had no Docker daemon.

## Phone app

The hub installs on your phone as a **PWA** (Progressive Web App). Android uses "Install app"; iPhone uses Safari
Share → "Add to Home Screen". It provides a phone layout, offline reading and QR pairing with no password typed
on the phone. Phone access is **off by default**: set `HUB_NETWORK_MODE=lan` (same Wi-Fi) or use Tailscale. See
**[MOBILE.md](MOBILE.md)** for setup, security, and what is not possible (app stores, closed-app push
notifications) and why.

## Commands

| Command | Purpose |
|---|---|
| `hub migrate` | Apply migrations and seed or refresh the source registry. Owner choices are preserved. |
| `hub create-owner` | Create the single owner account. |
| `hub serve` / `hub worker` / `hub start` | Web server / background collection / both, with restarts. |
| `hub run-source KEY [--force]` | Run one connector in the foreground (debugging). |
| `hub status` | Show source health, queue and workers. |
| `hub doctor` | Audit OS, CPU, RAM, GPU, disk, runtimes, Postgres and Ollama, and recommend settings. |
| `hub backup` / `hub restore FILE --yes` | `pg_dump` backup to `data/backups/` and restore (destructive). |
| `make check` | Lint, type checks, tests and UI build. |

## What you get

- **Home**: important discoveries, trending projects (growth-based), model releases, MCP/agents, research, opportunities.
- **AI Radar**: last 5 / 15 / 30 / 60 minutes and today, by discovery or publication time, with live updates over SSE.
- **Feed and Search**: filters for category, type, label, verification and source. Search is hybrid: PostgreSQL full-text plus optional local embeddings, with deterministic natural-language hints.
- **Item pages**: original link, publication and discovery times, label with its rule, verification level, and full provenance per source with a raw-record viewer. The score is explained (measured vs. inferred), with metric history, release history, related items and grounded AI summaries.
- **Sources**: the audited registry with terms, quota, attribution and health. Enable, pause or run now; unverified terms need explicit acknowledgement.
- **Operations**: workers, scheduler, queue, failures, per-host request stats, AI provider status, zero-cost counters, database and disk usage, backups, duplicate review and the audit log.
- **Other pages**: watchlists and in-app alerts, daily/weekly reports, the opportunity tracker, release timeline, Stack Explorer (source-stated relationships only), community publishing (private-first) and settings.

## Documentation

[MOBILE](MOBILE.md) · [ARCHITECTURE](ARCHITECTURE.md) · [ROADMAP](ROADMAP.md) · [SOURCES](SOURCES.md) · [API_INTEGRATIONS](API_INTEGRATIONS.md) ·
[ENVIRONMENT](ENVIRONMENT.md) · [DATABASE](DATABASE.md) · [SECURITY](SECURITY.md) · [DEVELOPMENT_LOG](DEVELOPMENT_LOG.md)

## Repository layout

```
backend/   FastAPI app, worker, connectors, pipeline, AI layer, Alembic migrations, tests (pytest)
frontend/  Vite + React + TypeScript + Tailwind UI (built to frontend/dist, served by the backend)
deploy/    systemd user units, launchd plist
data/      backups and logs (git-ignored)
```
