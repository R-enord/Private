"""URL canonicalization and identity keys (deterministic; no network)."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url", "source",
    "s", "si", "igshid", "spm", "_hsenc", "_hsmi", "mkt_tok", "cmpid", "feature",
}
_ARXIV_ID_RE = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(v\d+)?(?![\d])")
_ARXIV_OLD_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([a-z\-]+(?:\.[A-Z]{2})?/\d{7})", re.I)
_GH_REPO_RE = re.compile(r"^/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]{1,100})(?:/|$)")
_GH_RESERVED = {
    "about", "features", "topics", "trending", "orgs", "settings", "marketplace", "sponsors", "collections",
    "explore", "login", "join", "pricing", "search", "site", "apps", "enterprise", "security", "customer-stories",
    "readme", "events", "github", "issues", "pulls", "notifications", "codespaces",
}
_HF_RESERVED = {"blog", "docs", "papers", "spaces", "datasets", "models", "learn", "posts", "settings", "join",
                "login", "pricing", "enterprise", "api", "collections", "organizations", "chat", "tasks"}


def safe_link(url: str | None) -> str | None:
    """Only http(s) absolute URLs are ever rendered as links (blocks javascript:, data:, file: ...)."""
    if not url:
        return None
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    return url


def canonicalize_url(url: str) -> str:
    """Normalize a URL for identity: https, lowercase host, no www, no tracking params/fragments."""
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme.lower() in ("http", "https", "") else parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if host in ("m.youtube.com", "youtu.be"):
        host = "youtube.com"
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    # Site-specific canonical forms
    arxiv_id = extract_arxiv_id(url) if "arxiv.org" in host else None
    if arxiv_id:
        return f"https://arxiv.org/abs/{arxiv_id}"
    if host == "github.com":
        m = _GH_REPO_RE.match(path)
        if m and m.group(1).lower() not in _GH_RESERVED:
            owner, repo = m.group(1), m.group(2).removesuffix(".git")
            rest = path[m.end():].rstrip("/")
            # Repo root (and tree/blob of default views) collapse to the repo; releases keep their path.
            if not rest or rest.startswith(("tree/", "blob/")) or rest in ("tree", "blob"):
                return f"https://github.com/{owner.lower()}/{repo.lower()}"
            return f"https://github.com/{owner.lower()}/{repo.lower()}/{rest}"
    if host == "news.ycombinator.com":
        ids = [v for k, v in query_pairs if k == "id"]
        return f"https://news.ycombinator.com/item?id={ids[0]}" if ids else "https://news.ycombinator.com/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = urlencode(sorted(query_pairs))
    return urlunsplit((scheme, netloc, path, query, ""))


def extract_arxiv_id(text: str | None) -> str | None:
    if not text:
        return None
    if "arxiv" in text.lower():
        m = _ARXIV_OLD_RE.search(text)
        if m:
            return m.group(1)
        m = re.search(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?", text, re.I)
        if m:
            return m.group(1)
        m = re.search(r"arxiv:\s*(\d{4}\.\d{4,5})", text, re.I)
        if m:
            return m.group(1)
    return None


def bare_arxiv_id(value: str) -> str | None:
    m = _ARXIV_ID_RE.fullmatch(value.strip())
    return m.group(1) if m else None


def github_repo_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    if (parts.hostname or "").lower().removeprefix("www.") != "github.com":
        return None
    m = _GH_REPO_RE.match(parts.path or "")
    if not m or m.group(1).lower() in _GH_RESERVED:
        return None
    return f"{m.group(1)}/{m.group(2).removesuffix('.git')}".lower()


def hf_repo_from_url(url: str | None) -> tuple[str, str] | None:
    """('model'|'dataset'|'space', 'owner/name') for huggingface.co repo URLs."""
    if not url:
        return None
    parts = urlsplit(url)
    if (parts.hostname or "").lower().removeprefix("www.") != "huggingface.co":
        return None
    segs = [s for s in (parts.path or "").split("/") if s]
    if len(segs) >= 3 and segs[0] in ("datasets", "spaces"):
        return ("dataset" if segs[0] == "datasets" else "space", f"{segs[1]}/{segs[2]}".lower())
    if len(segs) >= 2 and segs[0] not in _HF_RESERVED:
        return ("model", f"{segs[0]}/{segs[1]}".lower())
    return None


def identity_keys(url: str | None, extra: list[str] | None = None) -> list[str]:
    """All identity keys for an item; any shared key means 'same thing'."""
    keys: list[str] = list(extra or [])
    if url and safe_link(url):
        keys.append("url:" + canonicalize_url(url))
        repo = github_repo_from_url(url)
        # Only the repository root identifies the repo; release/issue URLs are their own items.
        if repo and canonicalize_url(url) == f"https://github.com/{repo}":
            keys.append(f"github:{repo}")
        if (aid := extract_arxiv_id(url)) is not None:
            keys.append(f"arxiv:{aid}")
        if hf := hf_repo_from_url(url):
            keys.append(f"hf:{hf[0]}:{hf[1]}")
    # de-duplicate, keep order
    seen: set[str] = set()
    out = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out
