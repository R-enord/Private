# Database

PostgreSQL ≥ 15 (developed on 16.15). Schema is defined in `backend/hub/models.py` and migrated with Alembic
(`backend/migrations/versions`). `alembic check` reports no drift between models and migrations.

## Migrations

| Revision | Content |
|---|---|
| `0001` | Initial schema (39 tables) plus `CREATE EXTENSION IF NOT EXISTS pg_trgm` |
| `0002` | `uq_report_period` becomes `UNIQUE NULLS NOT DISTINCT` (global reports have `owner_id NULL`) |

Commands: `hub migrate` (upgrade head, then seed the registry and categories), or
`cd backend && uv run alembic upgrade head | downgrade -1 | history`. Every test run builds `hub_test` from
zero with `upgrade head`. `upgrade → downgrade base → upgrade` was verified on the dev DB. Rule: never edit a
released migration; add a new revision.

## Model map

| Area | Tables | Notes |
|---|---|---|
| Identity & access | `users`, `user_sessions`, `login_attempts`, `audit_log`, `settings` | Roles viewer < member < moderator < admin < owner; sessions store a SHA-256 of the token only |
| Source registry | `sources`, `source_checkpoints`, `source_runs`, `http_request_log`, `host_throttle` | All audit fields (access, auth, cost, quota, terms, attribution, storage notes, intervals, health, backoff, rate limits) |
| Provenance | `raw_records` | Verbatim payload (JSONB) + SHA-256 hash; unique per (source, external id, hash), so every distinct version is kept; `is_fixture` flag |
| Canonical discoveries | `items`, `item_keys`, `item_sources`, `dedupe_candidates` | `items.search_vector` is a generated, weighted tsvector (GIN) plus a trigram index on title. `item_keys` holds identity keys for dedup/merge. `item_sources` holds per-source provenance (first/last seen, metrics, raw record) |
| Classification | `categories`, `item_categories` | Method `rule`/`source`/`manual` + confidence + evidence |
| History | `metric_snapshots`, `release_events` | Append-only; release events dedupe on a key, so history is never rewritten |
| Entities | `entities`, `entity_relations` | Schema for canonical projects/orgs and source-confirmed/community/inferred relations (Phase 4 population pending) |
| Opportunities | `opportunities` | Source-stated fields only, with an `evidence` JSON |
| AI | `ai_providers`, `ai_cache`, `ai_usage`, `item_embeddings` | Cache key = hash(task, prompt version, provider, model, input); embeddings are unit-normalized float arrays |
| Queue | `jobs`, `worker_heartbeats` | Partial unique index `(type, dedupe_key) WHERE status IN ('queued','running')` |
| Personal | `bookmarks`, `watchlists`, `watch_rules`, `alerts`, `reports` | `alerts` unique per (user, item) |
| Community | `articles`, `comments`, `reactions`, `follows`, `moderation_reports` | Ownership columns; sanitized HTML stored alongside Markdown |

### Key decisions

- **Raw first.** Payloads are stored before normalization, so normalization bugs can be fixed and re-run
  without re-fetching.
- **Identity keys rather than one URL column**, because the same thing appears under many URLs (repo page,
  HN link, arXiv abs/pdf/html). A shared key means the same item.
- **No auto-merge on fuzzy similarity.** Similar titles go to `dedupe_candidates` for review. Merging keeps the
  dropped item as a tombstone (`merged_into_id`).
- **Published vs discovered.** `published_at` is kept as reported (the earliest known value wins);
  `discovered_at` is set once.
- **Job queue in Postgres**: see ARCHITECTURE.md.

## Retention

The `maintenance` job runs hourly. It prunes:
- `http_request_log` after 14 days;
- succeeded and cancelled jobs after 7 days;
- login attempts after 30 days;
- expired sessions;
- stale worker rows.

It also marks opportunities past their deadline as expired. Raw records, items, snapshots and events are kept
indefinitely.

## Backup and restore

- **Backup:** `hub backup` (or the "Create backup" button in Operations). It runs `pg_dump -Fc --no-owner` to
  `data/backups/hub-YYYYMMDD-HHMMSS.dump` and keeps the newest 14. The password is passed via `PGPASSWORD`,
  never on the command line.
- **Restore:** `hub restore FILE --yes`. It runs `pg_restore --clean --if-exists --single-transaction`, skipping
  extension entries (they may be superuser-owned) after ensuring `pg_trgm` exists. It is destructive, so test
  restores go into a scratch database first:
  ```bash
  createdb -O hub hub_restore_check
  cd backend && uv run python -c "from pathlib import Path; from hub.backup import restore_backup; \
    restore_backup(Path('../data/backups/<file>.dump'), 'postgresql+psycopg://hub:PASS@127.0.0.1:5432/hub_restore_check')"
  ```
- **Verified** (2026-11-22): a backup of the live DB was restored twice into a scratch DB, and the counts
  matched exactly (838 items, 1,249 raw records, alembic 0002). `tests/test_backup.py` round-trips on every
  test run.
- **Off-machine copies** are up to you (e.g. an encrypted external drive). No cloud upload is built in.
