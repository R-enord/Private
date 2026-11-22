"""Common connector contract.

A connector:
* declares its type/version and fetches using ONLY ``ctx.http`` (the SafeHttpClient, already restricted to
  the source's declared hosts),
* yields ``RawRecord`` objects (verbatim payloads — persisted before normalization),
* turns a raw record into a ``Normalized`` item (or ``None`` to filter it out),
* stages incremental checkpoints via ``ctx.set_checkpoint`` — they are committed only after a successful run.

Connectors never execute or install anything they fetch. Fetched text is untrusted data.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar

import nh3

from hub.net.http import SafeHttpClient


@dataclass
class RawRecord:
    external_id: str
    url: str
    payload: dict[str, Any]


@dataclass
class Normalized:
    external_id: str
    kind: str
    title: str
    url: str
    summary: str = ""
    author: str = ""
    organization: str = ""
    published_at: dt.datetime | None = None
    source_updated_at: dt.datetime | None = None
    created_at: dt.datetime | None = None  # when the thing itself was created (repo/model creation)
    tags: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    license: str = ""
    language: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    identity: list[str] = field(default_factory=list)
    discussion_url: str = ""
    require_relevance: float | None = None  # filter unless AI relevance >= this
    release_event: dict[str, Any] | None = None


@dataclass
class ConnectorContext:
    source_id: int
    source_key: str
    config: dict[str, Any]
    http: SafeHttpClient
    checkpoints: dict[str, Any]
    now: dt.datetime
    max_items: int = 200
    lookup_recent_external_ids: Callable[[int], list[str]] = lambda hours: []
    staged: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    not_modified: bool = False

    def get_checkpoint(self, key: str, default: Any = None) -> Any:
        if key in self.staged:
            return self.staged[key]
        return self.checkpoints.get(key, default)

    def set_checkpoint(self, key: str, value: Any) -> None:
        self.staged[key] = value


class Connector:
    type: ClassVar[str] = "base"
    version: ClassVar[str] = "1"

    def fetch(self, ctx: ConnectorContext) -> Iterator[RawRecord]:  # pragma: no cover - interface
        raise NotImplementedError

    def normalize(self, raw: RawRecord, ctx: ConnectorContext) -> Normalized | None:  # pragma: no cover
        raise NotImplementedError


# ----------------------------------------------------------------------------- helpers

_WS = re.compile(r"\s+")


def html_to_text(value: str | None, max_len: int = 600) -> str:
    """Strip ALL markup (script/style contents removed), unescape entities, collapse whitespace, truncate."""
    if not value:
        return ""
    cleaned = nh3.clean(value, tags=set(), attributes={}, strip_comments=True)
    text = _WS.sub(" ", html.unescape(cleaned)).strip()
    if len(text) > max_len:
        cut = text[:max_len].rsplit(" ", 1)[0]
        text = cut + "…"
    return text


def markdown_to_text(value: str | None, max_len: int = 600) -> str:
    if not value:
        return ""
    t = re.sub(r"```.*?```", " ", value, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[#>*_`]+", " ", t)
    return html_to_text(t, max_len)


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.UTC)


def from_epoch(value: int | float | None) -> dt.datetime | None:
    if value is None:
        return None
    try:
        return dt.datetime.fromtimestamp(float(value), dt.UTC)
    except (OverflowError, ValueError, OSError):
        return None
