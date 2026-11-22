"""GitHub connector (REST API, unauthenticated by default; optional free token).

Modes:
* ``search``   — newly created AI repositories with traction (rotating topic queries, created within N days).
                 GitHub has no official "Trending" API (verified 2026-11-22), so trending is approximated by
                 star *growth* measured from our own snapshots, never by scraping github.com/trending.
* ``releases`` — releases of watched repositories (conditional requests with ETag per repo).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from hub.connectors.base import (
    Connector,
    ConnectorContext,
    Normalized,
    RawRecord,
    markdown_to_text,
    parse_iso,
)
from hub.net.http import HttpError, RateLimited
from hub.pipeline.canonical import safe_link

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


class GitHubConnector(Connector):
    type = "github"
    version = "1"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        mode = ctx.config.get("mode", "search")
        if mode == "search":
            yield from self._search(ctx)
        elif mode == "releases":
            yield from self._releases(ctx)
        else:
            raise ValueError(f"unknown github mode {mode!r}")

    # ------------------------------------------------------------------ search
    def _search(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        queries: list[str] = ctx.config.get("queries", [])
        if not queries:
            return
        per_run = max(1, int(ctx.config.get("queries_per_run", 3)))
        cursor = int(ctx.get_checkpoint("query_cursor", 0)) % len(queries)
        since = (ctx.now - dt.timedelta(days=int(ctx.config.get("created_within_days", 14)))).date().isoformat()
        min_stars = int(ctx.config.get("min_stars", 10))
        per_page = min(100, int(ctx.config.get("per_page", 30)))
        emitted = 0
        seen: set[str] = set()
        for i in range(min(per_run, len(queries))):
            q = queries[(cursor + i) % len(queries)]
            res = ctx.http.get(
                f"{API}/search/repositories",
                params={"q": f"{q} created:>{since} stars:>={min_stars}", "sort": "stars", "order": "desc",
                        "per_page": per_page},
                headers=HEADERS,
            )
            data = res.json()
            if data.get("incomplete_results"):
                ctx.warnings.append(f"GitHub reported incomplete results for query {q!r}")
            for repo in data.get("items", []):
                if repo.get("fork") or repo.get("private"):
                    continue
                full = repo["full_name"]
                if full.lower() in seen:  # same repo matched by several queries in this run
                    continue
                seen.add(full.lower())
                yield RawRecord(external_id=f"repo:{full.lower()}", url=repo.get("html_url", ""), payload=repo)
                emitted += 1
                if emitted >= ctx.max_items:
                    break
        ctx.set_checkpoint("query_cursor", (cursor + per_run) % len(queries))

    # ------------------------------------------------------------------ releases
    def _releases(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        repos: list[str] = ctx.config.get("repos", [])
        per_page = int(ctx.config.get("per_page", 5))
        etags: dict[str, str] = dict(ctx.get_checkpoint("etags", {}) or {})
        failures: list[str] = []
        ok = 0
        for repo in repos:
            try:
                res = ctx.http.get(
                    f"{API}/repos/{repo}/releases", params={"per_page": per_page}, headers=HEADERS,
                    etag=etags.get(repo),
                )
            except RateLimited:
                raise
            except HttpError as e:
                failures.append(f"{repo}: {e}")
                continue
            ok += 1
            if res.not_modified:
                continue
            if res.etag:
                etags[repo] = res.etag
            for rel in res.json():
                if rel.get("draft"):
                    continue
                rel["_repo"] = repo
                yield RawRecord(external_id=f"release:{repo.lower()}:{rel['id']}", url=rel.get("html_url", ""),
                                payload=rel)
        ctx.set_checkpoint("etags", etags)
        if failures:
            ctx.warnings.extend(failures)
            if ok == 0:
                raise HttpError("all watched repositories failed: " + "; ".join(failures[:3]))

    # ------------------------------------------------------------------ normalize
    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:
        p = raw.payload
        if raw.external_id.startswith("release:"):
            return self._norm_release(raw, p)
        return self._norm_repo(raw, p)

    def _norm_repo(self, raw: RawRecord, p: dict[str, Any]) -> Normalized:
        owner = p.get("owner") or {}
        lic = (p.get("license") or {}).get("spdx_id") or ""
        if lic in ("NOASSERTION", "OTHER"):
            lic = ""
        links = {"repository": p.get("html_url", "")}
        if (home := safe_link(p.get("homepage"))) is not None:
            links["homepage"] = home
        created = parse_iso(p.get("created_at"))
        return Normalized(
            external_id=raw.external_id,
            kind="repository",
            title=p.get("full_name", ""),
            url=p.get("html_url", ""),
            summary=(p.get("description") or "").strip()[:600],
            author=owner.get("login", ""),
            organization=owner.get("login", "") if owner.get("type") == "Organization" else "",
            published_at=created,
            created_at=created,
            source_updated_at=parse_iso(p.get("pushed_at")),
            tags=list(p.get("topics") or [])[:20],
            metrics={"stars": float(p.get("stargazers_count") or 0), "forks": float(p.get("forks_count") or 0),
                     "open_issues": float(p.get("open_issues_count") or 0)},
            links=links,
            license=lic,
            language=p.get("language") or "",
            extra={"archived": bool(p.get("archived")), "default_branch": p.get("default_branch", ""),
                   "owner_type": owner.get("type", "")},
            identity=[f"github:{p.get('full_name', '').lower()}"],
        )

    def _norm_release(self, raw: RawRecord, p: dict[str, Any]) -> Normalized:
        repo = p.get("_repo", "")
        tag = p.get("tag_name", "")
        name = p.get("name") or tag
        title = f"{repo} {name}" if name and repo.split('/')[-1].lower() not in name.lower() else (name or repo)
        prerelease = bool(p.get("prerelease"))
        lower = f"{name} {tag}".lower()
        event_type = "beta" if "beta" in lower else ("preview" if prerelease else "version")
        published = parse_iso(p.get("published_at")) or parse_iso(p.get("created_at"))
        return Normalized(
            external_id=raw.external_id,
            kind="release",
            title=title,
            url=p.get("html_url", ""),
            summary=markdown_to_text(p.get("body"), 600),
            author=(p.get("author") or {}).get("login", ""),
            organization=repo.split("/")[0] if repo else "",
            published_at=published,
            tags=["release", "pre-release"] if prerelease else ["release"],
            links={"repository": f"https://github.com/{repo}", "release": p.get("html_url", "")},
            extra={"repo": repo, "tag": tag, "prerelease": prerelease},
            release_event={"event_type": event_type, "version": tag, "event_date": published,
                           "entity_repo": repo.lower()},
        )
