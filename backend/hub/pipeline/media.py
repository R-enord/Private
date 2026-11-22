"""Media detection from URLs (deterministic, no network).

Turns links that arrive from any permitted source (HN, RSS, Mastodon, saved links, ...) into media items:
YouTube videos/Shorts, TikTok videos, Instagram Reels/posts, X posts, Bluesky posts, Reddit threads, Vimeo
videos and write-ups on blogging platforms. We never fetch the linked page to find this out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

_YT_ID = r"[A-Za-z0-9_-]{11}"
WRITEUP_HOSTS = ("medium.com", "substack.com", "dev.to", "hashnode.dev", "hashnode.com", "mirror.xyz", "ghost.io",
                 "beehiiv.com", "towardsdatascience.com")


@dataclass
class Media:
    platform: str  # youtube | tiktok | instagram | x | bluesky | reddit | vimeo | mastodon | writeup
    format: str  # short | video | post | writeup
    media_id: str | None = None
    canonical_url: str | None = None
    channel: str = ""
    embed: str | None = None  # embeddable provider we support in-app ("youtube"), else link-out
    thumbnail: str | None = None
    duration_s: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return {"short": "short", "video": "video", "post": "post"}.get(self.format, "article")

    @property
    def identity_key(self) -> str | None:
        return f"{self.platform}:{self.media_id}" if self.media_id else None

    def as_dict(self) -> dict[str, Any]:
        return {"platform": self.platform, "format": self.format, "id": self.media_id, "embed": self.embed,
                "thumbnail": self.thumbnail, "duration_s": self.duration_s, "channel": self.channel,
                "vertical": self.format == "short", **self.extra}


def youtube_thumbnail(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def detect_media(url: str | None) -> Media | None:
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    host = host[2:] if host.startswith("m.") else host
    path = parts.path or "/"

    # YouTube
    if host in ("youtube.com", "youtube-nocookie.com", "music.youtube.com") or host == "youtu.be":
        vid = None
        fmt = "video"
        if host == "youtu.be":
            m = re.match(rf"^/({_YT_ID})", path)
            vid = m.group(1) if m else None
        elif (m := re.match(rf"^/shorts/({_YT_ID})", path)):
            vid, fmt = m.group(1), "short"
        elif (m := re.match(rf"^/(?:embed|live|v)/({_YT_ID})", path)):
            vid = m.group(1)
        elif path.startswith("/watch"):
            v = parse_qs(parts.query).get("v", [None])[0]
            vid = v if v and re.fullmatch(_YT_ID, v) else None
        if not vid:
            return None
        canonical = f"https://www.youtube.com/shorts/{vid}" if fmt == "short" else f"https://www.youtube.com/watch?v={vid}"
        return Media("youtube", fmt, vid, canonical, embed="youtube", thumbnail=youtube_thumbnail(vid))

    # TikTok (all TikTok videos are short-form vertical video)
    if host.endswith("tiktok.com"):
        m = re.match(r"^/@([^/]+)/video/(\d{8,25})", path)
        if m:
            return Media("tiktok", "short", m.group(2), f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}",
                         channel="@" + m.group(1))
        if host in ("vm.tiktok.com", "vt.tiktok.com") or path.startswith("/t/"):
            return Media("tiktok", "short", None, None)  # short link: not resolved (we do not fetch pages)
        return None

    # Instagram
    if host.endswith("instagram.com"):
        m = re.match(r"^/(reels?|p|tv)/([A-Za-z0-9_-]{5,40})", path)
        if m:
            fmt = "short" if m.group(1) in ("reel", "reels") else "post"
            kind_path = "reel" if fmt == "short" else "p"
            return Media("instagram", fmt, m.group(2), f"https://www.instagram.com/{kind_path}/{m.group(2)}/")
        return None

    # X / Twitter
    if host in ("x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com"):
        m = re.match(r"^/([A-Za-z0-9_]{1,15})/status/(\d{5,25})", path)
        if m:
            return Media("x", "post", m.group(2), f"https://x.com/{m.group(1)}/status/{m.group(2)}",
                         channel="@" + m.group(1))
        return None

    # Bluesky
    if host == "bsky.app":
        m = re.match(r"^/profile/([^/]+)/post/([A-Za-z0-9]+)", path)
        if m:
            return Media("bluesky", "post", f"{m.group(1)}/{m.group(2)}", f"https://bsky.app/profile/{m.group(1)}/post/{m.group(2)}",
                         channel="@" + m.group(1))
        return None

    # Reddit threads
    if host.endswith("reddit.com"):
        m = re.match(r"^/r/([A-Za-z0-9_]+)/comments/([a-z0-9]+)", path)
        if m:
            return Media("reddit", "post", m.group(2), f"https://www.reddit.com/r/{m.group(1)}/comments/{m.group(2)}/",
                         channel="r/" + m.group(1))
        return None

    # Vimeo
    if host == "vimeo.com":
        m = re.match(r"^/(\d{5,12})", path)
        if m:
            return Media("vimeo", "video", m.group(1), f"https://vimeo.com/{m.group(1)}")
        return None

    # Write-ups on blogging platforms
    full = host + path
    if any(host == h or host.endswith("." + h) or full.startswith(h) for h in WRITEUP_HOSTS):
        if path in ("/", ""):
            return None
        return Media("writeup", "writeup", None, None, channel=host)
    return None


def apply_media(norm: Any) -> None:
    """Upgrade a Normalized item in place if its URL is a media link (keeps source-provided metadata)."""
    m = detect_media(norm.url)
    if m is None or norm.kind in ("repository", "model", "dataset", "space", "paper", "release"):
        return
    existing = dict(norm.extra.get("media") or {})
    if m.format != "writeup":
        norm.kind = m.kind
    media = {**m.as_dict(), **{k: v for k, v in existing.items() if v not in (None, "")}}
    norm.extra = {**norm.extra, "media": media}
    if m.canonical_url and m.format != "writeup":
        norm.url = m.canonical_url
    if m.identity_key and m.identity_key not in norm.identity:
        norm.identity = [*norm.identity, m.identity_key]
