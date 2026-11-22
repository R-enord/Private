"""Hugging Face Hub connector (public REST API, anonymous by default).

Modes: ``list`` (e.g. trending models/spaces), ``orgs`` (newest models per org), ``daily_papers``.
Only source-stated metadata is stored (license comes from the repo's own ``license:`` tag).
HF's own AI-generated paper summaries (``ai_summary``) are deliberately ignored — not source facts.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from hub.connectors.base import Connector, ConnectorContext, Normalized, RawRecord, html_to_text, parse_iso
from hub.net.http import HttpError, RateLimited

BASE = "https://huggingface.co"
_NOISE_TAG_PREFIXES = ("region:", "endpoints_compatible", "autotrain_compatible", "deploy:", "text-generation-inference")


class HuggingFaceConnector(Connector):
    type = "huggingface"
    version = "1"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:
        mode = ctx.config.get("mode", "list")
        repo_type = ctx.config.get("repo_type", "models")
        params = dict(ctx.config.get("params", {}))
        if mode == "list":
            yield from self._list(ctx, repo_type, params, ckpt="etag")
        elif mode == "orgs":
            failures: list[str] = []
            for org in ctx.config.get("orgs", []):
                try:
                    yield from self._list(ctx, repo_type, {**params, "author": org}, ckpt=f"etag:{org}")
                except RateLimited:
                    raise
                except HttpError as e:
                    failures.append(f"{org}: {e}")
            if failures:
                ctx.warnings.extend(failures)
        elif mode == "daily_papers":
            res = ctx.http.get(f"{BASE}/api/daily_papers", params=params, etag=ctx.get_checkpoint("etag"))
            if res.not_modified:
                ctx.not_modified = True
                return
            if res.etag:
                ctx.set_checkpoint("etag", res.etag)
            for entry in res.json()[: ctx.max_items]:
                paper = entry.get("paper") or {}
                pid = paper.get("id")
                if not pid:
                    continue
                entry.pop("thumbnail", None)
                yield RawRecord(external_id=f"hfpaper:{pid}", url=f"{BASE}/papers/{pid}", payload=entry)
        else:
            raise ValueError(f"unknown huggingface mode {mode!r}")

    def _list(self, ctx: ConnectorContext, repo_type: str, params: dict[str, Any], ckpt: str) -> Iterator[RawRecord]:
        res = ctx.http.get(f"{BASE}/api/{repo_type}", params=params, etag=ctx.get_checkpoint(ckpt))
        if res.not_modified:
            ctx.not_modified = True
            return
        if res.etag:
            ctx.set_checkpoint(ckpt, res.etag)
        prefix = "space" if repo_type == "spaces" else ("dataset" if repo_type == "datasets" else "model")
        for repo in res.json()[: ctx.max_items]:
            rid = repo.get("id") or repo.get("modelId")
            if not rid or repo.get("private") or repo.get("disabled"):
                continue
            # drop bulky fields we never use
            for k in ("siblings", "widgetData", "config", "transformersInfo", "spaces", "safetensors", "gguf"):
                repo.pop(k, None)
            url = f"{BASE}/{rid}" if prefix == "model" else f"{BASE}/{prefix}s/{rid}"
            yield RawRecord(external_id=f"{prefix}:{rid}", url=url, payload=repo)

    # ------------------------------------------------------------------ normalize
    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:
        if raw.external_id.startswith("hfpaper:"):
            return self._norm_paper(raw)
        p = raw.payload
        rid = p.get("id") or p.get("modelId")
        kind = raw.external_id.split(":", 1)[0]
        tags_all = [t for t in (p.get("tags") or []) if isinstance(t, str)]
        lic = next((t.split(":", 1)[1] for t in tags_all if t.startswith("license:")), "")
        arxiv_refs = sorted({t.split(":", 1)[1] for t in tags_all if t.startswith("arxiv:")})
        tags = [t for t in tags_all if not t.startswith(_NOISE_TAG_PREFIXES) and not t.startswith(("license:", "arxiv:", "base_model:", "dataset:"))]
        base_models = [t.split(":", 2)[-1] for t in tags_all if t.startswith("base_model:")]
        card = p.get("cardData") or {}
        author = rid.split("/")[0] if "/" in rid else (p.get("author") or "")
        created = parse_iso(p.get("createdAt"))
        summary = ""
        title = rid
        extra: dict[str, Any] = {"pipeline_tag": p.get("pipeline_tag") or "", "library": p.get("library_name") or "",
                                 "gated": p.get("gated") or False, "arxiv_refs": arxiv_refs,
                                 "base_models": base_models[:5]}
        if kind == "space":
            summary = html_to_text(card.get("short_description") or "", 300)
            if card.get("title"):
                title = f"{card['title']} ({rid})"
            extra["sdk"] = p.get("sdk") or card.get("sdk") or ""
        metrics = {"hf_likes": float(p.get("likes") or 0)}
        if p.get("downloads") is not None:
            metrics["hf_downloads"] = float(p.get("downloads") or 0)
        if p.get("trendingScore") is not None:
            metrics["hf_trending"] = float(p.get("trendingScore") or 0)
        url = raw.url
        org_like = author
        return Normalized(
            external_id=raw.external_id,
            kind="space" if kind == "space" else ("dataset" if kind == "dataset" else "model"),
            title=title,
            url=url,
            summary=summary,
            author=author,
            organization=org_like,
            published_at=created,
            created_at=created,
            source_updated_at=parse_iso(p.get("lastModified")),
            tags=tags[:25],
            metrics=metrics,
            links={"huggingface": url},
            license=lic,
            extra=extra,
            identity=[f"hf:{kind}:{rid.lower()}"],
            release_event=(
                {"event_type": "ga" if not p.get("gated") else "preview", "version": "", "event_date": created,
                 "title": f"{rid} published on Hugging Face"}
                if kind == "model" and ctx.config.get("mode") == "orgs" else None
            ),
        )

    def _norm_paper(self, raw: RawRecord) -> Normalized:
        e = raw.payload
        paper = e.get("paper") or {}
        pid = paper["id"]
        authors = [a.get("name", "") for a in paper.get("authors", []) if a.get("name")]
        author = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        abstract = html_to_text(paper.get("summary") or e.get("summary") or "", 1200)
        upvotes = paper.get("upvotes", e.get("upvotes"))
        metrics: dict[str, float] = {}
        if upvotes is not None:
            metrics["hf_upvotes"] = float(upvotes)
        if e.get("numComments") is not None:
            metrics["hf_comments"] = float(e["numComments"])
        links = {"arxiv": f"https://arxiv.org/abs/{pid}", "hf_paper": raw.url}
        if gh := paper.get("githubRepo"):
            links["code"] = gh
        return Normalized(
            external_id=raw.external_id,
            kind="paper",
            title=html_to_text(paper.get("title") or e.get("title") or "", 400),
            url=f"https://arxiv.org/abs/{pid}",
            summary=abstract,
            author=author,
            published_at=parse_iso(paper.get("publishedAt") or e.get("publishedAt")),
            tags=["hf-daily-papers"],
            metrics=metrics,
            links=links,
            extra={"submitted_on_daily_at": paper.get("submittedOnDailyAt") or e.get("submittedOnDailyAt") or "",
                   "authors": authors[:50]},
            identity=[f"arxiv:{pid}"],
            discussion_url=raw.url,
        )
