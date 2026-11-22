"""Watchlists -> in-app alerts (deterministic). One alert per (user, item) — never duplicated or spammy."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, text

from hub.db import rowcount, session_scope
from hub.models import Category, Item, ItemCategory, ItemKey, ItemSource, Source, Watchlist

RULE_TYPES = ("keyword", "category", "source", "organization", "author", "label", "kind", "identity")


def _item_facts(session: Any, item: Item) -> dict[str, set[str]]:
    cats = {c for (c,) in session.execute(
        select(Category.slug).join(ItemCategory, ItemCategory.category_id == Category.id)
        .where(ItemCategory.item_id == item.id))}
    srcs = {k for (k,) in session.execute(
        select(Source.key).join(ItemSource, ItemSource.source_id == Source.id).where(ItemSource.item_id == item.id))}
    keys = {k for (k,) in session.execute(select(ItemKey.key).where(ItemKey.item_id == item.id))}
    return {"category": cats, "source": srcs, "identity": keys}


def rule_matches(rule_type: str, value: str, item: Item, facts: dict[str, set[str]]) -> bool:
    v = value.strip().lower()
    if not v:
        return False
    if rule_type == "keyword":
        blob = f"{item.title}\n{item.summary}\n{' '.join(item.tags or [])}".lower()
        return re.search(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", blob) is not None
    if rule_type == "category":
        return v in facts["category"]
    if rule_type == "source":
        return v in facts["source"]
    if rule_type == "organization":
        return v in (item.organization.lower(), item.author.lower())
    if rule_type == "author":
        return v == item.author.lower()
    if rule_type == "label":
        return v == item.label
    if rule_type == "kind":
        return v == item.kind
    if rule_type == "identity":
        return v in facts["identity"]
    return False


def evaluate_items(item_ids: list[int]) -> dict[str, Any]:
    created = 0
    with session_scope() as s:
        watchlists = s.scalars(select(Watchlist).where(Watchlist.alerts_enabled.is_(True))).all()
        if not watchlists or not item_ids:
            return {"alerts": 0}
        for iid in item_ids:
            item = s.get(Item, iid)
            if item is None or item.merged_into_id:
                continue
            facts = _item_facts(s, item)
            for wl in watchlists:
                if item.score < wl.min_score:
                    continue
                hit = next((r for r in wl.rules if rule_matches(r.rule_type, r.value, item, facts)), None)
                if hit is None:
                    continue
                res = s.execute(text(
                    "INSERT INTO alerts (user_id, watchlist_id, item_id, reason, created_at) "
                    "VALUES (:u, :w, :i, :r, now()) ON CONFLICT ON CONSTRAINT uq_alert_user_item DO NOTHING"),
                    {"u": wl.owner_id, "w": wl.id, "i": item.id,
                     "r": f"watchlist '{wl.name}': {hit.rule_type} = {hit.value}"[:500]})
                created += rowcount(res)
    return {"alerts": created}
