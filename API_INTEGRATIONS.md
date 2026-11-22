# API integrations, AI providers and zero-cost enforcement

## Connector contract

`backend/hub/connectors/base.py`:

```python
class Connector:
    type: ClassVar[str]          # registry key used by sources.connector_type
    version: ClassVar[str]       # bump when normalization changes
    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]: ...
    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None: ...
```

- Use **only** `ctx.http` (SafeHttpClient). It is restricted to the source's `allowed_hosts`, throttled per host
  across processes, retried with jittered exponential backoff, rate-limit aware, and size-capped.
- Yield `RawRecord(external_id, url, payload)`. The payload must be JSON-able and is stored as received; bulky
  unused fields may be dropped and are documented in each connector.
- Stage incremental state with `ctx.set_checkpoint(key, value)`. It is committed only if the run succeeds.
  Use ETag/Last-Modified via `ctx.http.get(..., etag=, last_modified=)` and set `ctx.not_modified` on a 304.
- Report non-fatal problems in `ctx.warnings`; the source health becomes `degraded`. Raise on fatal problems
  (health then goes degraded or failing with backoff). `RateLimited` sets `backoff_until` to the reset time.
- `normalize` returns a `Normalized` item (kind, title, url, summary/excerpt, author, organization,
  `published_at`, `created_at`, tags, metrics, links, source-stated license, extra, identity keys,
  `discussion_url`, an optional `require_relevance` filter and an optional `release_event`), or `None` to
  filter the record out.
- Never execute, install or follow instructions from fetched content. Treat all of it as untrusted text.

Adding a source means writing a connector (or reusing `rss`), adding a `seed.py` entry with every audit field,
recording the evidence in SOURCES.md, and adding fixture-based tests.

## Implemented connectors

| Type | Module | Modes |
|---|---|---|
| `github` | `connectors/github.py` | `search` (rotating topic queries, created ≤14 d, ≥10 stars, forks skipped), `releases` (watched repos, ETag) |
| `huggingface` | `connectors/huggingface.py` | `list` (trending models/spaces), `orgs` (newest per org), `daily_papers` |
| `hn` | `connectors/hackernews.py` | Firebase lists + items, incremental, AI filter, bounded refresh |
| `hn_algolia` | `connectors/hackernews.py` | `search_by_date` keyword queries (disabled by default) |
| `arxiv` | `connectors/arxiv.py` | query with newest-published checkpoint; `id_list` lookups |
| `rss` | `connectors/rss.py` | any RSS/Atom feed, conditional GET, unsafe links dropped |

Optional free credentials (never required): `HUB_GITHUB_TOKEN` (fine-grained PAT, read-only public data) and
`HUB_HF_TOKEN`. They are sent only to their own host, never logged, and never stored in the database.

## AI providers

| Provider | Status | Verified facts (2026-11-22) |
|---|---|---|
| **Ollama (local)** | **enabled, primary** | MIT license. Binds 127.0.0.1:11434 by default. Local API `/api/tags`, `/api/version`, `/api/chat` (structured output via a JSON-schema `format`), `/api/embed`. *Cloud models* run on ollama.com and are **pay-per-token** (ollama.com/pricing: "Pay as you go … Pay per token for every cloud model"). Cloud features can be disabled with `OLLAMA_NO_CLOUD=1` or `disable_ollama_cloud` in `~/.ollama/server.json`; the log then shows `Ollama cloud disabled: true` (verified). |
| OpenAI-compatible free tiers (OpenRouter `:free`, Gemini free tier, Groq, GitHub Models, Mistral, Cloudflare Workers AI) | **blocked / unverified** | Their official docs hosts were not reachable from the build environment, so no free tier or quota could be verified. Per policy they are not usable. `OpenAICompatibleProvider` refuses to construct without an entry in `cost_guard.VERIFIED_FREE_TIER_POLICIES`, which is empty. |

### How a cloud free tier could be added later (owner decision)

1. Verify official terms: an ongoing free tier (not a trial), quotas, data-use and training policy, and how
   billing could start.
2. Add a `FreeTierPolicy` (hosts, `verified_on`, terms URL, daily cap, and a model regex that stays on the free
   tier, e.g. `.*:free$`) to `VERIFIED_FREE_TIER_POLICIES` in code review, with tests.
3. Configure the key via environment, set `HUB_CLOUD_AI_ENABLED=true`, and keep
   `HUB_ALLOW_PRIVATE_CONTENT_TO_CLOUD=false`.

Private content (drafts, watchlists, notes) is classified `private` and is never sent to a cloud provider unless
that separate opt-in is set.

### Fallback order

Local providers first. If a provider is blocked, private-only, circuit-open or out of quota, the next allowed
provider is tried. If none remain, the job defers (it is re-queued in 15 minutes without consuming an attempt).
Results are cached, so the same input is never inferred twice.

## Zero-cost enforcement (code, not UI)

1. `Settings.zero_cost_mode` validator: `false` raises, so the app refuses to start.
2. `SafeHttpClient.check_url` calls `cost_guard.check_request` **before** any socket is opened. A paid host
   (OpenAI, Anthropic, Google GenAI and Vertex, Cohere, Mistral, Together, Replicate, Perplexity, Groq,
   OpenRouter, Fireworks, DeepSeek, xAI, DeepInfra, Cerebras, SambaNova, GitHub Models, Azure OpenAI, AWS
   Bedrock/SageMaker runtime, HF Inference/router/endpoints, ollama.com, Cloudflare API, X API, and
   not-yet-audited Reddit/Product Hunt APIs) raises `CostPolicyViolation`. It is logged with outcome `blocked`
   and counted on the Ops page.
3. The cost guard wins even if a paid host is added to an allowlist (tested).
4. The Ollama adapter rejects non-loopback hosts (unless LAN- or container-trusted; `ollama.com` can never be
   trusted) and rejects `cloud`-tagged or remote models.
5. Enabling a source via the API requires `cost_status` of `free_public` or `free_tier`.

Tests: `tests/test_http_and_cost_guard.py`, `tests/test_ai.py`. Live checks are recorded in DEVELOPMENT_LOG.md.
