"""Markdown -> safe HTML (server-side). Used for reports and community content.

markdown-it-py renders (raw HTML disabled), then nh3 (Rust ammonia) whitelists tags/attributes, forces
rel="noopener noreferrer nofollow" on links and drops non-http(s) URLs. Embeds are limited to an allowlist of
video hosts rendered as plain links (no iframes) until a vetted embed component exists.
"""

from __future__ import annotations

import nh3
from markdown_it import MarkdownIt

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table").enable(
    "strikethrough")

ALLOWED_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "del", "code", "pre", "blockquote",
    "ul", "ol", "li", "a", "img", "table", "thead", "tbody", "tr", "th", "td",
}
ALLOWED_ATTRS = {"a": {"href", "title"}, "img": {"src", "alt", "title"}, "th": {"align"}, "td": {"align"},
                 "code": {"class"}}


def render_markdown(md: str) -> str:
    html = _md.render(md or "")
    return nh3.clean(
        html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow", strip_comments=True,
    )
