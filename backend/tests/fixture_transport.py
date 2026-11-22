"""Routes requests to recorded fixtures (offline). Unknown URLs return 404 so tests notice gaps."""

from __future__ import annotations

import json

import httpx

from tests.conftest import load_fixture


def fixture_handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "api.github.com":
        if path == "/search/repositories":
            return httpx.Response(200, content=load_fixture("github_search.json"),
                                  headers={"x-ratelimit-remaining": "9", "x-ratelimit-reset": "1795350000"})
        if path.endswith("/releases"):
            return httpx.Response(200, content=load_fixture("github_releases.json"), headers={"etag": '"rel-etag"'})
    if host == "huggingface.co":
        if path == "/api/models":
            name = "hf_org_models.json" if "author" in request.url.params else "hf_models_trending.json"
            return httpx.Response(200, content=load_fixture(name), headers={"ratelimit": '"api";r=480;t=200'})
        if path == "/api/spaces":
            return httpx.Response(200, content=load_fixture("hf_spaces.json"))
        if path == "/api/daily_papers":
            return httpx.Response(200, content=load_fixture("hf_daily_papers.json"))
        if path == "/blog/feed.xml":
            return httpx.Response(200, content=load_fixture("rss_hf_blog.xml"), headers={"etag": '"feed1"'})
    if host == "hacker-news.firebaseio.com":
        if path.endswith("stories.json"):
            return httpx.Response(200, content=load_fixture("hn_topstories.json"))
        if path.startswith("/v0/item/"):
            items = json.loads(load_fixture("hn_items.json"))
            iid = path.rsplit("/", 1)[1].removesuffix(".json")
            return httpx.Response(200, json=items.get(iid))
    if host == "export.arxiv.org":
        if request.url.params.get("start", "0") == "0":
            return httpx.Response(200, content=load_fixture("arxiv_query.xml"))
        return httpx.Response(200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
    if host == "feeds.example.org":
        return httpx.Response(200, content=load_fixture("malicious_feed.xml"))
    if host == "simonwillison.net":
        return httpx.Response(200, content=load_fixture("atom_simonwillison.xml"))
    return httpx.Response(404, text="no fixture")


TRANSPORT = httpx.MockTransport(fixture_handler)
