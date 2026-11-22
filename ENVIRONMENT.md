# Environment and resources

## Where this was built

The build and verification ran in a **cloud development container, not on the owner's computer**. No owner
machine was attached to the session, so its real hardware is unknown. Run `make doctor` (`hub doctor`) on your
machine: it prints OS, CPU, RAM, GPU/VRAM, disk, runtimes, Postgres and Ollama status, plus model and
concurrency recommendations.

| Resource | Build container (2026-11-22) |
|---|---|
| OS | Ubuntu 24.04.5 LTS, x86_64 |
| CPU | 4 vCPU Intel Xeon @ 2.30 GHz |
| RAM | 15 GiB, no swap |
| GPU / VRAM | none |
| Disk | ~61 GB free |
| Runtimes | Python 3.13.18 + uv 0.11, Node 22.23 + pnpm 10.28, PostgreSQL 16.15, Redis 7.0 (unused), Docker CLI (no daemon) |
| Ollama | 0.40.17, installed to `/opt/ollama` (1.5 GB download, 2.4 GB on disk incl. GPU libraries), CPU-only, `OLLAMA_NO_CLOUD=1` |
| Models | `qwen2.5:1.5b` (986 MB), `all-minilm` (45 MB) |

## Requirements

- Python ≥ 3.11 (uv recommended), Node ≥ 20 + pnpm (build time only), PostgreSQL ≥ 15 (NULLS NOT DISTINCT is
  used) with `pg_trgm` (a trusted extension; the DB owner can create it).
- Optional: Ollama (any OS), with `OLLAMA_NO_CLOUD=1`.
- Disk: app about 0.4 GB (venv + node_modules), database about 25 MB per 1,000 items including raw records,
  Ollama 2.4 GB plus models.

## Measured resource usage (build container, steady state)

| Process | RSS |
|---|---|
| `hub serve` (FastAPI/uvicorn) | ~94 MB |
| `hub worker` (scheduler + 2 job threads) | ~92 MB |
| PostgreSQL (all backends) | ~200–300 MB, mostly shared buffers |
| Ollama runner with qwen2.5:1.5b loaded | ~1.44 GB (unloads after 5 min idle, `keep_alive=5m`) |

On 4 CPU cores, a grounded summary with qwen2.5:1.5b took **~20 s**; cache hits take ~10 ms. Load average stayed
below 1 while collecting. The database was 23 MB after the first ~840 items.

## Model recommendations (what `hub doctor` prints)

| Memory available to models | Chat model | Embeddings | Notes |
|---|---|---|---|
| < 3 GB | qwen2.5:0.5b / 1.5b | all-minilm | `HUB_LOW_RESOURCE_MODE=true` |
| 3–6 GB | qwen2.5:1.5b or llama3.2:3b (Q4) | all-minilm | default concurrency 2 |
| 6–12 GB (or 8 GB VRAM) | qwen2.5:7b / llama3.1:8b (Q4, ~5 GB) | nomic-embed-text | |
| ≥ 12 GB | qwen2.5:14b (Q4, ~9 GB), or 7–8B for speed | nomic-embed-text | concurrency 3 |

Apple Silicon uses unified memory; budget about 60% of RAM. If `HUB_OLLAMA_MODEL` is empty, the app picks an
installed local chat model by preference (qwen3 → qwen2.5 → llama3.2 → gemma3 → …), and an embedding model
likewise.

## Configuration (`.env`, prefix `HUB_`)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://hub:hub@127.0.0.1:5432/hub` | PostgreSQL DSN |
| `BIND_HOST` / `PORT` | `127.0.0.1` / `8000` | non-loopback requires `PUBLIC_ACCESS_APPROVED=true` |
| `ALLOWED_HOSTS` | `["127.0.0.1","localhost"]` | Host header allowlist (DNS-rebinding defense) |
| `ZERO_COST_MODE` | `true` | **cannot be false** |
| `REGISTRATION_ENABLED` / `PUBLIC_ACCESS_APPROVED` | `false` / `false` | both needed before any public use |
| `GITHUB_TOKEN`, `HF_TOKEN` | empty | optional free tokens |
| `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_EMBED_MODEL` | `http://127.0.0.1:11434`, auto, auto | local AI |
| `OLLAMA_ALLOW_LAN`, `OLLAMA_TRUSTED_HOSTS` | `false`, `[]` | private-LAN or container hostnames for Ollama |
| `CLOUD_AI_ENABLED`, `ALLOW_PRIVATE_CONTENT_TO_CLOUD` | `false`, `false` | no effect until a verified free tier exists |
| `WORKER_CONCURRENCY`, `AI_MAX_CONCURRENCY`, `AI_NUM_CTX`, `LOW_RESOURCE_MODE` | `2`, `1`, `4096`, `false` | resource controls |
| `MAX_ITEMS_PER_RUN`, `HTTP_LOG_RETENTION_DAYS`, `SESSION_DAYS` | `200`, `14`, `14` | |
| `USER_AGENT` | generic, no personal data | sent to public APIs |
| `NETWORK_MODE` | `local` | `local` (this computer only), `lan` (same Wi-Fi; private IPs only), `tailnet` (Tailscale IPs). See MOBILE.md |
| `EXTRA_HOSTS`, `ALLOWED_CLIENT_NETWORKS` | `[]`, `[]` | extra Host names (e.g. `pc.tailnet.ts.net`), extra client CIDRs |
| `TLS_CERT_FILE`, `TLS_KEY_FILE` | empty | serve https directly (needed for PWA install/offline over LAN) |
| `DEVICE_SESSION_DAYS`, `RATE_LIMIT_AUTH_PER_MIN`, `RATE_LIMIT_API_PER_MIN` | `30`, `10`, `600` | phone sessions and per-IP limits |

Runtime settings (AI on/off, embeddings, backfill batch, report hour, ranking weights, low-resource mode,
pauses) are edited in the UI and stored in the `settings` table with an audit trail.

## Keeping collection running

| Platform | How |
|---|---|
| Any | `make start` (supervises server + worker; restarts crashed children) |
| Linux | `deploy/systemd/hub-*.service` user units; `loginctl enable-linger $USER` keeps them running without a login session |
| macOS | `deploy/launchd/local.ai-hub.worker.plist` (edit paths) |
| Windows | Task Scheduler: "At log on" → `uv run hub worker` in `backend\`; or Docker Desktop with `restart: unless-stopped` |
| Docker | `docker compose up -d` (restart policies included) |

Collection stops when the computer sleeps or shuts down; on wake, due sources run on the next scheduler tick.

## Build-environment network allowlist (cloud sessions only)

The cloud session ran in a personal environment ("Private (AI Intelligence Hub)") whose allowlist covers the
source hosts (huggingface.co, hacker-news.firebaseio.com, hn.algolia.com, export.arxiv.org, rss.arxiv.org,
feed hosts), Ollama download and registry hosts, and GitHub release hosts. This applies only to cloud
development sessions; your own computer uses its normal network.
