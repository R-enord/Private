"""Daily / weekly intelligence reports — generated locally and deterministically from stored items.

Every entry links to the original source, shows publication + discovery dates, the label and the verification
level. No claims are added beyond what the stored, source-provided data says. AI summaries (if any) are quoted
and explicitly labeled as AI-generated.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import select, text

from hub.db import session_scope
from hub.models import Item, Opportunity, Source

SECTIONS = [
    ("Top discoveries", lambda q: q, 10),
    ("Model releases", lambda q: q.where(Item.kind.in_(("model", "dataset"))), 8),
    ("Emerging repositories", lambda q: q.where(Item.kind == "repository"), 8),
    ("Research", lambda q: q.where(Item.kind == "paper"), 8),
    ("Releases, updates & deprecations", lambda q: q.where(
        (Item.kind == "release") | Item.label.in_(("update", "deprecation", "preview", "beta"))), 8),
    ("Announcements", lambda q: q.where(Item.label == "announcement"), 8),
    ("Rumors & unverified reports", lambda q: q.where(Item.label == "rumor"), 5),
]


def _md_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("*", "\\*").replace("_", "\\_") \
        .replace("<", "&lt;").replace(">", "&gt;").replace("\n", " ")


def _fmt_date(d: dt.datetime | None) -> str:
    return d.strftime("%Y-%m-%d %H:%M UTC") if d else "unknown"


def _entry(item: Item, publishers: str) -> str:
    line = (f"- [{_md_escape(item.title[:160])}]({item.url}) — *{item.label}*, verification: **{item.verification}**, "
            f"score {item.score:.0f}  \n  Source: {_md_escape(publishers)} · published {_fmt_date(item.published_at)} · "
            f"discovered {_fmt_date(item.discovered_at)}")
    if item.summary:
        line += f"  \n  Source excerpt: “{_md_escape(item.summary[:220])}{'…' if len(item.summary) > 220 else ''}”"
    ai = item.ai_summary or {}
    if ai.get("status") == "ok" and ai.get("summary"):
        line += f"  \n  AI summary ({_md_escape(ai.get('model', 'local model'))}, unverified): {_md_escape(ai['summary'][:260])}"
    return line


def generate_report(period: str, period_start: dt.datetime, owner_id: int | None = None) -> dict[str, Any]:
    if period_start.tzinfo is None:
        period_start = period_start.replace(tzinfo=dt.UTC)
    period_end = period_start + (dt.timedelta(days=7) if period == "weekly" else dt.timedelta(days=1))
    used: set[int] = set()
    parts = [f"# {'Weekly' if period == 'weekly' else 'Daily'} AI intelligence report",
             f"Period: {_fmt_date(period_start)} → {_fmt_date(period_end)} (by discovery time). "
             "Generated locally and deterministically from collected source data. Labels and verification levels are "
             "rule-based; 'rumor' and 'community' items are not confirmed."]
    with session_scope() as s:
        base = select(Item).where(Item.merged_into_id.is_(None), Item.discovered_at >= period_start,
                                  Item.discovered_at < period_end, Item.is_fixture.is_(False))
        total = s.scalar(text("SELECT count(*) FROM items WHERE merged_into_id IS NULL AND discovered_at >= :a "
                              "AND discovered_at < :b AND NOT is_fixture"), {"a": period_start, "b": period_end})
        parts.append(f"Items discovered in period: **{total}**.")
        for title, filt, n in SECTIONS:
            rows = s.scalars(filt(base).where(Item.id.notin_(used) if used else True)
                             .order_by(Item.score.desc()).limit(n)).all()
            if not rows:
                continue
            parts.append(f"\n## {title}")
            for it in rows:
                used.add(it.id)
                src_ids = [x.source_id for x in it.sources]
                pubs = ", ".join(sorted({src.publisher or src.name for src in
                                         s.scalars(select(Source).where(Source.id.in_(src_ids)))}))
                parts.append(_entry(it, pubs or "unknown"))
        opps = s.execute(select(Opportunity, Item).join(Item, Item.id == Opportunity.item_id)
                         .where(Item.discovered_at >= period_start, Item.discovered_at < period_end)
                         .order_by(Opportunity.deadline.asc().nulls_last()).limit(10)).all()
        if opps:
            parts.append("\n## Opportunities")
            for opp, it in opps:
                used.add(it.id)
                parts.append(
                    f"- [{_md_escape(it.title[:160])}]({it.url}) — type: {opp.opp_type}; organization: "
                    f"{_md_escape(opp.organization) or 'not stated'}; remote: {opp.remote}; compensation: "
                    f"{_md_escape(opp.compensation) or 'not stated'}; deadline: "
                    f"{opp.deadline.date().isoformat() if opp.deadline else 'not stated'}; status: {opp.status}")
        failing = s.scalars(select(Source).where(Source.enabled.is_(True),
                                                 Source.health.in_(("failing", "blocked", "rate_limited")))).all()
        if failing:
            parts.append("\n## Source health")
            for src in failing:
                parts.append(f"- {_md_escape(src.name)}: **{src.health}** — {_md_escape(src.last_error[:160])}")
        content = "\n".join(parts) + "\n"
        s.execute(text(
            "INSERT INTO reports (owner_id, period, period_start, period_end, generator, content_md, item_ids, created_at) "
            "VALUES (:o, :p, :a, :b, 'deterministic-v1', :c, :ids, now()) "
            "ON CONFLICT ON CONSTRAINT uq_report_period DO UPDATE SET content_md = EXCLUDED.content_md, "
            "item_ids = EXCLUDED.item_ids, period_end = EXCLUDED.period_end, created_at = now()"),
            {"o": owner_id, "p": period, "a": period_start, "b": period_end, "c": content, "ids": sorted(used)})
    return {"period": period, "period_start": period_start.isoformat(), "items": len(used)}
