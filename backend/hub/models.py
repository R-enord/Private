"""Relational data model.

Design notes (see DATABASE.md for the full rationale):
* Provenance first: every fetched payload is kept verbatim in ``raw_records`` (append-only, one row per
  distinct payload version). Normalized ``items`` link back to all their sources via ``item_sources``.
* History is never overwritten: ``release_events`` and ``metric_snapshots`` are append-only.
* Ownership is designed in from day one: user-generated rows carry ``owner_id``/``user_id``.
* Canonical identity: ``item_keys`` maps stable identity keys (``github:owner/repo``, ``arxiv:2401.00001``,
  ``url:<canonical>``) to one item, which is how cross-source deduplication/corroboration works.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now_col() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ---------------------------------------------------------------------------
# Users, sessions, audit
# ---------------------------------------------------------------------------

ROLES = ("owner", "admin", "moderator", "member", "viewer")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    handle: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bio: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = _now_col()
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[dt.datetime] = _now_col()
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user: Mapped[User] = relationship()


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    client: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[dt.datetime] = _now_col()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_label: Mapped[str] = mapped_column(String(100), nullable=False, default="")  # e.g. "system", "cli"
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    target_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = _now_col()


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col()
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


# ---------------------------------------------------------------------------
# Source registry, runs, checkpoints, HTTP telemetry
# ---------------------------------------------------------------------------


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)  # github | huggingface | hn | arxiv | rss
    connector_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1")
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    homepage: Mapped[str] = mapped_column(Text, nullable=False, default="")
    publisher: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    # official = the publisher is the subject (lab blog); primary = self-published (papers, repos);
    # community = aggregators/discussion; independent = independent commentary
    source_class: Mapped[str] = mapped_column(String(20), nullable=False, default="community")
    access_method: Mapped[str] = mapped_column(String(100), nullable=False)  # "REST API", "RSS 2.0", ...
    auth_method: Mapped[str] = mapped_column(String(100), nullable=False, default="none")
    cost_status: Mapped[str] = mapped_column(String(30), nullable=False)  # free_public | free_tier | trial | paid | unknown
    quota_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    terms_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    terms_status: Mapped[str] = mapped_column(String(30), nullable=False, default="unverified")  # verified | unverified
    terms_checked_at: Mapped[dt.date | None] = mapped_column(DateTime(timezone=True))
    attribution: Mapped[str] = mapped_column(Text, nullable=False, default="")
    storage_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    allowed_hosts: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    min_interval_s: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    interval_s: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    host_min_spacing_s: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    categories_hint: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disabled_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    health: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    backoff_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer)
    rate_limit_reset_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now_col()
    updated_at: Mapped[dt.datetime] = _now_col()


class SourceCheckpoint(Base):
    __tablename__ = "source_checkpoints"
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[dt.datetime] = _now_col()


class SourceRun(Base):
    __tablename__ = "source_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[int | None] = mapped_column(BigInteger)
    started_at: Mapped[dt.datetime] = _now_col()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")  # running|ok|error|rate_limited|not_modified
    records_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_merged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_filtered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    http_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (Index("ix_source_runs_source_started", "source_id", "started_at"),)


class HttpRequestLog(Base):
    __tablename__ = "http_request_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(30), nullable=False, default="connector")  # connector | ai
    method: Mapped[str] = mapped_column(String(10), nullable=False, default="GET")
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)  # ok|not_modified|retry|error|rate_limited|blocked
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limit_remaining: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = _now_col()

    __table_args__ = (Index("ix_http_log_created", "created_at"),)


class HostThrottle(Base):
    """Cross-process per-host request spacing (e.g. arXiv: one request every 3 s)."""

    __tablename__ = "host_throttle"
    host: Mapped[str] = mapped_column(String(255), primary_key=True)
    next_allowed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RawRecord(Base):
    """Verbatim payload as received (untrusted data). One row per distinct payload version."""

    __tablename__ = "raw_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("source_runs.id", ondelete="SET NULL"))
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[dt.datetime] = _now_col()
    last_seen_at: Mapped[dt.datetime] = _now_col()
    is_fixture: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", "payload_hash", name="uq_raw_version"),
        Index("ix_raw_source_external", "source_id", "external_id"),
    )


# ---------------------------------------------------------------------------
# Canonical discoveries
# ---------------------------------------------------------------------------

ITEM_KINDS = (
    "repository", "model", "dataset", "space", "paper", "article", "discussion", "release", "opportunity", "other",
)
ITEM_LABELS = (
    "announcement", "research", "preview", "beta", "release", "update", "deprecation", "rumor", "opportunity",
    "discussion", "unlabeled",
)
VERIFICATION = ("official", "primary", "corroborated", "community", "unverified")


class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="other")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)  # original link as given by the source
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")  # source-provided excerpt only
    author: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    organization: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))  # per the source
    discovered_at: Mapped[dt.datetime] = _now_col()  # first seen by this platform
    source_updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[dt.datetime] = _now_col()
    first_source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    label: Mapped[str] = mapped_column(String(20), nullable=False, default="unlabeled")
    label_method: Mapped[str] = mapped_column(String(20), nullable=False, default="rule")
    label_evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    verification: Mapped[str] = mapped_column(String(20), nullable=False, default="unverified")
    license: Mapped[str] = mapped_column(String(100), nullable=False, default="")  # only if source-stated
    language: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    links: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)  # repo/docs/paper/demo
    extra: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    relevance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    score_components: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    scored_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ai_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id", ondelete="SET NULL"), index=True)
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"))
    is_fixture: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False, default="")  # tags/authors/org for FTS
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(summary, '')), 'B') || "
            "setweight(to_tsvector('english', coalesce(search_text, '')), 'C')",
            persisted=True,
        ),
    )

    sources: Mapped[list[ItemSource]] = relationship(back_populates="item", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_items_discovered", "discovered_at"),
        Index("ix_items_published", "published_at"),
        Index("ix_items_score", "score"),
        Index("ix_items_kind", "kind"),
        Index("ix_items_search", "search_vector", postgresql_using="gin"),
        Index("ix_items_title_trgm", "title", postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"}),
        Index("ix_items_canonical", "canonical_url"),
    )


class ItemKey(Base):
    """Stable identity keys used for dedup and cross-source matching."""

    __tablename__ = "item_keys"
    key: Mapped[str] = mapped_column(String(600), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[dt.datetime] = _now_col()


class ItemSource(Base):
    """Provenance: which source reported this item, when, and with which raw record."""

    __tablename__ = "item_sources"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False, default="")  # e.g. HN discussion URL
    raw_record_id: Mapped[int | None] = mapped_column(ForeignKey("raw_records.id", ondelete="SET NULL"))
    relation: Mapped[str] = mapped_column(String(20), nullable=False, default="primary")  # primary | mention
    title_at_source: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    first_seen_at: Mapped[dt.datetime] = _now_col()
    last_seen_at: Mapped[dt.datetime] = _now_col()
    item: Mapped[Item] = relationship(back_populates="sources")

    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_item_source"),)


class DedupeCandidate(Base):
    """Possible duplicates that need human review (never auto-merged)."""

    __tablename__ = "dedupe_candidates"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")  # open|merged|dismissed
    created_at: Mapped[dt.datetime] = _now_col()
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    __table_args__ = (UniqueConstraint("item_id", "candidate_id", name="uq_dedupe_pair"),)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)


class ItemCategory(Base):
    __tablename__ = "item_categories"
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)
    method: Mapped[str] = mapped_column(String(20), nullable=False, default="rule")  # rule | ai | manual | source
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (Index("ix_item_categories_cat", "category_id"),)


class MetricSnapshot(Base):
    """Append-only metric history (stars, likes, downloads, points) — growth, not totals."""

    __tablename__ = "metric_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    captured_at: Mapped[dt.datetime] = _now_col()

    __table_args__ = (Index("ix_metric_item_metric_time", "item_id", "metric", "captured_at"),)


# ---------------------------------------------------------------------------
# Entities, relationships, releases, opportunities (Phase 4/3 — schema ready)
# ---------------------------------------------------------------------------


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    kinds: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)  # project, model, org, ...
    organization: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    links: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # facts: {"license": {"value": "MIT", "status": "source-confirmed", "source": "...", "checked_at": "..."}}
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = _now_col()
    updated_at: Mapped[dt.datetime] = _now_col()


class EntityRelation(Base):
    __tablename__ = "entity_relations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    from_entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    to_entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    relation: Mapped[str] = mapped_column(String(40), nullable=False)  # uses|integrates_with|built_on|maintained_by|alternative_to
    confidence: Mapped[str] = mapped_column(String(30), nullable=False)  # source-confirmed|community-reported|inferred
    evidence_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"))
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = _now_col()

    __table_args__ = (UniqueConstraint("from_entity_id", "to_entity_id", "relation", name="uq_entity_relation"),)


RELEASE_EVENT_TYPES = (
    "announcement", "paper", "preview", "beta", "ga", "version", "api_change", "pricing_change",
    "license_change", "compatibility_change", "deprecation", "shutdown",
)


class ReleaseEvent(Base):
    """Append-only timeline. Never updated in place; corrections are new events."""

    __tablename__ = "release_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id", ondelete="SET NULL"), index=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"), index=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    event_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[str] = mapped_column(String(30), nullable=False, default="source-confirmed")
    verification: Mapped[str] = mapped_column(String(20), nullable=False, default="unverified")
    dedupe_key: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    created_at: Mapped[dt.datetime] = _now_col()


class Opportunity(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), unique=True, nullable=False)
    opp_type: Mapped[str] = mapped_column(String(40), nullable=False, default="unknown")
    organization: Mapped[str] = mapped_column(String(300), nullable=False, default="")  # "" = unknown
    role: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    eligibility: Mapped[str] = mapped_column(Text, nullable=False, default="")
    location: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    remote: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")  # yes|no|hybrid|unknown
    compensation: Mapped[str] = mapped_column(Text, nullable=False, default="")  # only if stated verbatim
    deadline: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    application_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")  # open|expired|unknown
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now_col()


# ---------------------------------------------------------------------------
# AI providers, cache, usage, embeddings
# ---------------------------------------------------------------------------


class AIProviderState(Base):
    __tablename__ = "ai_providers"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # ollama | openai_compatible
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    embed_model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    is_local: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cost_class: Mapped[str] = mapped_column(String(30), nullable=False, default="local")  # local|free_tier_verified|unverified|paid
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")  # ok|unavailable|error|blocked
    status_detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    daily_request_quota: Mapped[int | None] = mapped_column(Integer)
    requests_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_day: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    consecutive_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    circuit_open_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    avg_latency_ms: Mapped[float | None] = mapped_column(Float)
    last_ok_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    models_available: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    updated_at: Mapped[dt.datetime] = _now_col()


class AICache(Base):
    __tablename__ = "ai_cache"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    task: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    output: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = _now_col()


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    task: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # ok|error|blocked|cache_hit
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = _now_col()

    __table_args__ = (Index("ix_ai_usage_created", "created_at"),)


class ItemEmbedding(Base):
    """Embeddings stored as float arrays (pgvector optional; cosine computed in SQL/Python)."""

    __tablename__ = "item_embeddings"
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    dims: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = _now_col()


# ---------------------------------------------------------------------------
# Jobs & workers (PostgreSQL-backed queue)
# ---------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(300))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")  # queued|running|succeeded|failed|dead|cancelled
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    run_after: Mapped[dt.datetime] = _now_col()
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    locked_by: Mapped[str | None] = mapped_column(String(200))
    locked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now_col()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_jobs_claim", "status", "priority", "run_after"),
        Index(
            "uq_jobs_active_dedupe",
            "type",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running') AND dedupe_key IS NOT NULL"),
        ),
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    pid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    roles: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    is_scheduler_leader: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[dt.datetime] = _now_col()
    last_heartbeat_at: Mapped[dt.datetime] = _now_col()
    jobs_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    jobs_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_job_id: Mapped[int | None] = mapped_column(BigInteger)
    stopped_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Personal features: bookmarks, watchlists, alerts, reports
# ---------------------------------------------------------------------------


class Bookmark(Base):
    __tablename__ = "bookmarks"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), primary_key=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = _now_col()


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    min_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[dt.datetime] = _now_col()
    rules: Mapped[list[WatchRule]] = relationship(cascade="all, delete-orphan")


class WatchRule(Base):
    __tablename__ = "watch_rules"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(30), nullable=False)  # keyword|category|source|organization|author|label|kind
    value: Mapped[str] = mapped_column(String(300), nullable=False)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    watchlist_id: Mapped[int | None] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _now_col()

    __table_args__ = (UniqueConstraint("user_id", "item_id", name="uq_alert_user_item"),)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String(10), nullable=False)  # daily | weekly
    period_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generator: Mapped[str] = mapped_column(String(60), nullable=False, default="deterministic")
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    item_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), nullable=False, default=list)
    created_at: Mapped[dt.datetime] = _now_col()

    # NULLS NOT DISTINCT: global reports (owner_id NULL) must also be unique per period (PostgreSQL >= 15)
    __table_args__ = (UniqueConstraint("owner_id", "period", "period_start", name="uq_report_period",
                                       postgresql_nulls_not_distinct=True),)


# ---------------------------------------------------------------------------
# Community publishing (Phase 5 — schema + ownership designed now)
# ---------------------------------------------------------------------------


class Article(Base):
    __tablename__ = "articles"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    article_type: Mapped[str] = mapped_column(String(30), nullable=False, default="tutorial")
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="")  # sanitized server-side
    cover_image_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")  # draft|published|hidden|removed
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, default="private")  # private|public
    related_item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"))
    created_at: Mapped[dt.datetime] = _now_col()
    updated_at: Mapped[dt.datetime] = _now_col()
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    article_id: Mapped[int | None] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"))
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="visible")  # visible|hidden|removed
    created_at: Mapped[dt.datetime] = _now_col()


class Reaction(Base):
    __tablename__ = "reactions"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    target_type: Mapped[str] = mapped_column(String(20), primary_key=True)  # article|comment|item
    target_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reaction: Mapped[str] = mapped_column(String(20), primary_key=True, default="like")
    created_at: Mapped[dt.datetime] = _now_col()


class Follow(Base):
    __tablename__ = "follows"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    target_type: Mapped[str] = mapped_column(String(20), primary_key=True)  # user|entity|category
    target_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    created_at: Mapped[dt.datetime] = _now_col()


class ModerationReport(Base):
    __tablename__ = "moderation_reports"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reporter_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    target_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    resolution: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[dt.datetime] = _now_col()
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
