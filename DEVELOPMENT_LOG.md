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
