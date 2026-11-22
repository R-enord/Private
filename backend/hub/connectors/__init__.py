"""Connector registry: connector_type -> implementation."""

from __future__ import annotations

from hub.connectors.arxiv import ArxivConnector
from hub.connectors.base import Connector
from hub.connectors.github import GitHubConnector
from hub.connectors.hackernews import HackerNewsConnector, HNAlgoliaConnector
from hub.connectors.huggingface import HuggingFaceConnector
from hub.connectors.rss import RSSConnector
from hub.connectors.social import (
    BlueskyConnector,
    DevToConnector,
    ManualConnector,
    MastodonConnector,
    YouTubeConnector,
)

CONNECTORS: dict[str, type[Connector]] = {
    c.type: c
    for c in (GitHubConnector, HuggingFaceConnector, HackerNewsConnector, HNAlgoliaConnector, ArxivConnector,
              RSSConnector, YouTubeConnector, DevToConnector, MastodonConnector, BlueskyConnector, ManualConnector)
}


def get_connector(connector_type: str) -> Connector:
    try:
        return CONNECTORS[connector_type]()
    except KeyError as e:
        raise ValueError(f"no connector for type {connector_type!r}") from e
