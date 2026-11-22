from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hub.api.app import create_app
from hub.db import session_scope
from hub.security.auth import create_user
from hub.security.sanitize import render_markdown

PW = "correct-horse-battery-staple"
H = {"X-Hub-Request": "1"}


@pytest.fixture()
def clients(db):
    with session_scope() as s:
        create_user(s, "owner@example.org", PW, role="owner", handle="owner")
        create_user(s, "member@example.org", PW, role="member", handle="member")
    out = {}
    for who in ("owner", "member"):
        c = TestClient(create_app())
        assert c.post("/api/auth/login", json={"email": f"{who}@example.org", "password": PW}, headers=H).status_code == 200
        out[who] = c
    return out


@pytest.mark.parametrize("payload", [
    "<script>alert(1)</script>",
    "[x](javascript:alert(1))",
    "![x](javascript:alert(1))",
    '<img src=x onerror="alert(1)">',
    "<iframe src='https://evil.example.com'></iframe>",
])
def test_markdown_is_sanitized(payload: str) -> None:
    html = render_markdown(f"# Title\n\n{payload}\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```python\nprint('ok')\n```")
    import re

    low = html.lower()
    assert "<script" not in low and "<iframe" not in low, "no live script/iframe tags"
    assert not re.search(r"<[^>]*\son\w+\s*=", low), "no event-handler attributes inside tags"
    assert not re.search(r"(href|src)\s*=\s*[\"']?\s*javascript:", low), "no javascript: URLs in attributes"
    assert "<table>" in html and "<pre><code" in html


def test_article_lifecycle_ownership_and_moderation(clients) -> None:
    owner, member = clients["owner"], clients["member"]
    art = member.post("/api/articles", headers=H, json={
        "title": "Running a local MCP server", "body_md": "Steps **here**.\n\n[link](https://example.org)",
        "tags": ["MCP", "local llm"], "status": "draft"}).json()
    assert art["status"] == "draft" and art["visibility"] == "private"
    assert 'rel="noopener noreferrer nofollow"' in art["body_html"]
    assert art["tags"] == ["mcp", "localllm"]
    # owner (not a moderator by ownership, but has elevated rank) can read drafts; another member could not
    assert owner.get(f"/api/articles/{art['slug']}").status_code == 200
    with session_scope() as s:
        create_user(s, "other@example.org", PW, role="member", handle="other")
    other = TestClient(create_app())
    other.post("/api/auth/login", json={"email": "other@example.org", "password": PW}, headers=H)
    assert other.get(f"/api/articles/{art['slug']}").status_code == 404, "drafts are private to the author"
    assert other.put(f"/api/articles/{art['id']}", headers=H, json={"title": "hijack", "status": "draft"}).status_code == 404
    pub = member.put(f"/api/articles/{art['id']}", headers=H, json={
        "title": "Running a local MCP server", "body_md": "Updated", "status": "published"}).json()
    assert pub["status"] == "published" and pub["published_at"]
    assert other.get(f"/api/articles/{art['slug']}").status_code == 200
    # comments + likes
    c = other.post("/api/comments", headers=H, json={"article_id": art["id"], "body_md": "Nice <script>x</script>"}).json()
    assert "<script" not in c["body_html"]
    assert other.post(f"/api/reactions/article/{art['id']}", headers=H).json()["liked"] is True
    assert other.get(f"/api/articles/{art['slug']}").json()["likes"] == 1
    # moderation: report -> owner (moderation rights) hides -> author cannot un-hide by republishing
    rep = other.post("/api/moderation/reports", headers=H, json={"target_type": "article", "target_id": art["id"], "reason": "spam test"}).json()
    assert member.get("/api/moderation/reports").status_code == 403
    res = owner.post(f"/api/moderation/reports/{rep['id']}", headers=H, json={"action": "hide"}).json()
    assert res["target_status"] == "hidden"
    member.put(f"/api/articles/{art['id']}", headers=H, json={"title": "Running a local MCP server", "status": "published"})
    assert other.get(f"/api/articles/{art['slug']}").status_code == 404, "hidden content stays hidden"
    page = member.get("/api/authors/member").json()
    assert page["handle"] == "member" and len(page["articles"]) == 1


def test_cover_image_must_be_http(clients) -> None:
    r = clients["member"].post("/api/articles", headers=H, json={"title": "Bad cover", "cover_image_url": "javascript:alert(1)"})
    assert r.status_code == 400
