# Test fixtures — NOT live data

These files are **recorded fixtures** used only by the automated tests. They are never loaded into the
application database at runtime, and any record created from them in tests is flagged `is_fixture=true`.

| File | Recorded from | Date | Notes |
|---|---|---|---|
| github_search.json | `GET https://api.github.com/search/repositories?q=topic:llm+created:>2026-11-08&sort=stars&per_page=4` | 2026-11-22 | real response, unmodified |
| github_releases.json | **synthetic** — hand-written in the shape documented for `GET /repos/{owner}/{repo}/releases` | 2026-11-22 | per-repo endpoints are blocked by the cloud build environment's proxy |
| hf_models_trending.json | `GET https://huggingface.co/api/models?sort=trendingScore&limit=4&full=true` | 2026-11-22 | real |
| hf_org_models.json | `GET https://huggingface.co/api/models?author=Qwen&sort=createdAt&direction=-1&limit=3&full=true` | 2026-11-22 | real |
| hf_spaces.json | `GET https://huggingface.co/api/spaces?sort=trendingScore&limit=3&full=true` | 2026-11-22 | real |
| hf_daily_papers.json | `GET https://huggingface.co/api/daily_papers?limit=3` | 2026-11-22 | real |
| hn_topstories.json | `GET https://hacker-news.firebaseio.com/v0/topstories.json` (first 30 ids) | 2026-11-22 | real, truncated |
| hn_items.json | `GET .../v0/item/{id}.json` for those ids | 2026-11-22 | real; `kids` (comment ids) removed |
| arxiv_query.xml | `GET https://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=4` | 2026-11-22 | real (metadata is CC0) |
| rss_hf_blog.xml | `GET https://huggingface.co/blog/feed.xml` | 2026-11-22 | real, truncated to 3 items |
| atom_simonwillison.xml | `GET https://simonwillison.net/atom/everything/` | 2026-11-22 | real, truncated to 3 entries (Atom parsing test only) |
| youtube_feed.xml | **synthetic** — shaped per YouTube's channel Atom feed (`yt:videoId`, `media:group`) | 2026-11-22 | www.youtube.com blocked in the build env |
| youtube_api_search.json, youtube_api_videos.json | **synthetic** — shaped per YouTube Data API v3 `search.list` / `videos.list` | 2026-11-22 | no API key / host blocked |
| devto_articles.json | **synthetic** — shaped per the Forem `/api/articles` response | 2026-11-22 | dev.to blocked |
| mastodon_tag.json | **synthetic** — shaped per Mastodon `Status` entities | 2026-11-22 | mastodon.social blocked |
| bluesky_search.json | **synthetic** — shaped per `app.bsky.feed.searchPosts` | 2026-11-22 | public.api.bsky.app blocked |
| malicious_feed.xml | **synthetic** — adversarial feed (script tags, javascript: links, prompt-injection text) | 2026-11-22 | security tests |
