# Source audit

All checks were done on **2026-11-22** from primary sources, fetched directly through the build environment's
proxy (doc sources on GitHub, the HF OpenAPI spec and client source, the HN README, the arXiv docs repository,
and the hn.algolia.com page bundle). Each value below is also stored in the `sources` table (seeded from
`backend/hub/seed.py`) and shown on the Sources page. **Re-verify periodically**: terms and quotas change.

Legend: *cost* `free_public` = public API or feed, no account or key needed. A source with unverified terms
ships **disabled** with a reason and needs an explicit, audited owner acknowledgement to enable.

## Enabled by default

| Source | Access / auth | Verified quota and rate limits | Terms, storage and display | Attribution | Interval | Fallback |
|---|---|---|---|---|---|---|
| **GitHub Search** (`github-new-ai-repos`) | REST `/search/repositories`; no auth (optional free token) | Unauthenticated: 60 req/h core, **10 req/min search**; authenticated: 30/min search; ≤1,000 results/search; secondary limits ≤100 concurrent, ≤900 points/min; on 403/429 wait for `retry-after` / `x-ratelimit-reset` (≥1 min if absent). 304s free only when authenticated | ToS §H: no spamming, no selling personal info; AUP §7 same. Stores public repo metadata only (no emails). **No official Trending API** → growth measured from our own snapshots, never scraping github.com/trending | "Repository metadata from GitHub; links to github.com" | 30 min, 4 queries/run, 7 s spacing | Rotate queries; back off to reset time |
| **GitHub Releases** (`github-releases`) | REST `/repos/{o}/{r}/releases`, ETag per repo | as above | as above | Release notes excerpt + link | 60 min | Partial failures tolerated per repo |
| **HF trending models** (`hf-trending-models`) | `GET /api/models?sort=trendingScore&full=true`; anonymous | Fixed 5-min windows; anonymous/IP: **500 Hub-API**, 3,000 resolver, 100 page requests; free account 1,000 API; 429 + `RateLimit`/`RateLimit-Policy` headers (observed `"api";r=485;t=257`) | HF ToS: public repos licensed to users via the Hub; license shown only as the repo's own `license:` tag | "Model metadata from the Hugging Face Hub" | 15 min | ETag; backoff |
| **HF org models** (`hf-org-models`) | `/api/models?author=…&sort=createdAt` × 15 orgs | as above (15 req/run) | as above | as above | 30 min | per-org failures tolerated |
| **HF trending Spaces** (`hf-trending-spaces`) | `/api/spaces?sort=trendingScore` | as above | as above | as above | 30 min | — |
| **HF Daily Papers** (`hf-daily-papers`) | `/api/daily_papers` (documented in HF OpenAPI spec: `p, limit, date, week, month, submitter, sort`) | as above | HF's own `ai_summary` field deliberately **ignored**; no PDFs stored | "Curation/upvotes from HF Daily Papers; metadata from arXiv" | 60 min | ETag |
| **Hacker News** (`hn-ai-stories`) | Official Firebase API `https://hacker-news.firebaseio.com/v0/` | README: *"There is currently no rate limit."* We self-limit (0.2 s spacing, ≤120 item fetches + ≤30 refreshes/run) | Story id/title/URL/points/comments/time stored; comment bodies not stored; non-AI stories not stored | "Story and discussion from Hacker News" | 10 min | Incremental seen-ids checkpoint |
| **arXiv API** (`arxiv-ai`) | `export.arxiv.org/api/query` (Atom); + `id_list` lookups | ToU: **≤1 request / 3 s, single connection, across all your machines**; `max_results` slices ≤2,000, total ≤30,000; RSS refreshed daily at midnight ET | ToU: metadata (title, abstract, authors, ids, categories) **CC0** — may store/share; e-prints must **not** be stored or served → we link to the abstract page only; do not imply arXiv endorsement | "Metadata courtesy of arXiv (CC0). Not affiliated with or endorsed by arXiv." | 60 min, ≤3 pages × 100 | 3.1 s DB-coordinated spacing |
| **Hugging Face blog RSS** (`rss-hf-blog`) | RSS 2.0, conditional GET | HF rate limits (above) | HF ToS; title/excerpt/link only | "Headline and excerpt from Hugging Face" | 30 min | ETag |

## Implemented, disabled pending review

| Source | Reason disabled | What the owner should check before enabling |
|---|---|---|
| HN Search by Algolia (`hn-algolia-ai`) | Rate limit verified (page text: *"limiting the number of API requests from a single IP to 10,000 per hour"*), but no separate terms found | Whether Algolia's HN API terms permit this use |
| arXiv cs.AI RSS (`rss-arxiv-cs-ai`) | Terms verified; **redundant** with the arXiv API source | Nothing; enable if you prefer the daily RSS listing |
| GitHub blog AI & ML, OpenAI news, Google DeepMind blog, Google Research blog, PyTorch blog, Simon Willison's Weblog | Feeds are publicly published for syndication and returned HTTP 200 on 2026-11-22, but each site's terms were **not individually reviewed** | Site terms of use; the app stores title, link, date, author and feed excerpt only, and links back |

Enabling one of these records an audit-log entry (`source.enable`, `acknowledged_terms=true`) and the date in
the source's notes.

## Not implemented (Phase 3 candidates, not yet audited)

Reddit, Product Hunt, GitLab, Codeberg, public X content, Discord, forums, newsletters. Until audited, their
API hosts are on the zero-cost denylist (e.g. `oauth.reddit.com`, `api.producthunt.com`; the X API is paid).

## Evidence notes

- GitHub numbers come from `github/docs` (`content/rest/using-the-rest-api/rate-limits-for-the-rest-api.md`,
  `data/reusables/rest-api/*`, `best-practices…md`, `content/rest/search/search.md`) and
  `github/site-policy` (Terms §H, AUP §7).
- In the build container, GitHub requests passed through an authenticating proxy (15,000/h core observed)
  scoped to one repository. On the owner's machine, the unauthenticated limits above apply.
- HF: `huggingface/hub-docs` `docs/hub/rate-limits.md` (limits "in September '25"), `/.well-known/openapi.json`
  (daily papers), and `huggingface_hub` `hf_api.py` (sort values `created_at, downloads, last_modified, likes,
  trending_score`).
- arXiv: `arXiv/arxiv-docs` `source/help/api/tou.md`, `user-manual.md`, `rss.md`.
