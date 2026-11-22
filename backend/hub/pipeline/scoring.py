"""Transparent discovery ranking.

score = 100 * sum(w_i * v_i) / sum(w_i), each component v_i in [0, 1]. Every component carries:
  kind: "measured" (numbers/timestamps reported by sources or our own snapshots) or "inferred" (heuristics),
  evidence: human-readable reason. Popularity alone never dominates: adoption is based on *growth*
  (velocity), and recency/novelty/corroboration/credibility all contribute.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any

DEFAULT_WEIGHTS: dict[str, float] = {
    "recency": 0.22,
    "credibility": 0.15,
    "relevance": 0.15,
    "novelty": 0.12,
    "adoption": 0.18,
    "utility": 0.08,
    "corroboration": 0.10,
}

HALF_LIFE_H = {"paper": 72, "repository": 72, "model": 72, "dataset": 96, "space": 72, "article": 48,
               "discussion": 24, "release": 96, "opportunity": 168, "other": 48}
CREDIBILITY = {"official": 0.9, "corroborated": 0.8, "primary": 0.7, "community": 0.5, "unverified": 0.3}


@dataclass
class Growth:
    """Velocity per day for a metric, from snapshots (or since-creation estimate)."""

    metric: str
    per_day: float
    method: str  # "snapshots" | "since_creation" | "since_post"
    window_h: float


@dataclass
class ScoreInput:
    kind: str
    published_at: dt.datetime | None
    discovered_at: dt.datetime
    created_at: dt.datetime | None
    verification: str
    relevance: float
    relevance_terms: list[str]
    metrics: dict[str, float]
    growth: list[Growth]
    distinct_publishers: int
    publishers: list[str]
    has_license: bool
    summary_len: int
    links: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)


def _log_norm(v: float, ref: float) -> float:
    if v <= 0:
        return 0.0
    return min(1.0, math.log10(1 + v) / math.log10(1 + ref))


def _age_h(t: dt.datetime | None, now: dt.datetime) -> float | None:
    if t is None:
        return None
    return max(0.0, (now - t).total_seconds() / 3600)


def compute_score(si: ScoreInput, now: dt.datetime, weights: dict[str, float] | None = None
                  ) -> tuple[float, dict[str, Any]]:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    comps: dict[str, dict[str, Any]] = {}

    # recency (measured: source timestamp, else our discovery time)
    ref = si.published_at or si.discovered_at
    age = _age_h(ref, now) or 0.0
    hl = HALF_LIFE_H.get(si.kind, 48)
    comps["recency"] = {
        "value": round(0.5 ** (age / hl), 4), "kind": "measured",
        "evidence": f"{'published' if si.published_at else 'discovered'} {age:.1f} h ago (half-life {hl} h)",
    }

    # credibility (inferred from source class / corroboration)
    comps["credibility"] = {
        "value": CREDIBILITY.get(si.verification, 0.3), "kind": "inferred",
        "evidence": f"verification = {si.verification}",
    }

    # relevance (inferred from keyword evidence)
    terms = [t for t in si.relevance_terms if not t.startswith("~")][:6]
    comps["relevance"] = {
        "value": round(si.relevance, 4), "kind": "inferred",
        "evidence": ("AI terms: " + ", ".join(terms)) if terms else "few AI-specific terms",
    }

    # novelty
    created_age = _age_h(si.created_at, now)
    if created_age is not None:
        nov = max(0.0, 1.0 - created_age / (14 * 24))
        comps["novelty"] = {"value": round(nov, 4), "kind": "measured",
                            "evidence": f"created {created_age / 24:.1f} days ago (source timestamp)"}
    else:
        disc_age = _age_h(si.discovered_at, now) or 0
        pub_age = _age_h(si.published_at, now)
        nov = 0.6 if (pub_age is not None and pub_age < 7 * 24 and disc_age < 48) else 0.25
        comps["novelty"] = {"value": nov, "kind": "inferred",
                            "evidence": "recently published and newly discovered" if nov > 0.5 else "not new"}

    # adoption / growth (measured; velocity, not totals)
    best = (0.0, "no adoption signal reported by sources")
    for g in si.growth:
        ref_v = {"stars": 150.0, "hf_likes": 80.0, "hn_points": 400.0, "hf_upvotes": 60.0,
                 "hf_downloads": 50000.0}.get(g.metric, 100.0)
        v = _log_norm(g.per_day, ref_v)
        label = {"snapshots": f"measured over {g.window_h:.0f} h", "since_creation": "average since creation",
                 "since_post": "since posted"}[g.method]
        if v > best[0]:
            best = (v, f"+{g.per_day:.1f} {g.metric.replace('hf_', 'HF ').replace('hn_', 'HN ')}/day ({label})")
    if "hf_trending" in si.metrics:
        v = _log_norm(si.metrics["hf_trending"], 800.0)
        if v > best[0]:
            best = (v, f"Hugging Face trendingScore {si.metrics['hf_trending']:.0f} (reported by HF)")
    comps["adoption"] = {"value": round(best[0], 4), "kind": "measured" if best[0] > 0 else "none",
                         "evidence": best[1]}

    # utility (inferred heuristics)
    u = 0.0
    why = []
    if si.has_license:
        u += 0.25
        why.append("license stated")
    if si.summary_len >= 40:
        u += 0.25
        why.append("has description")
    if any(k in si.links for k in ("homepage", "code", "docs", "repository", "huggingface")):
        u += 0.25
        why.append("code/docs link")
    if si.extra.get("library") or si.extra.get("tag") or "code" in si.links:
        u += 0.25
        why.append("usable artifact (library/version/code)")
    comps["utility"] = {"value": round(u, 4), "kind": "inferred", "evidence": ", ".join(why) or "little practical info"}

    # corroboration (measured: independent publishers)
    n = si.distinct_publishers
    comps["corroboration"] = {
        "value": round(min(1.0, max(0, n - 1) / 2), 4), "kind": "measured",
        "evidence": f"reported by {n} independent source(s): {', '.join(si.publishers[:4])}",
    }

    total_w = sum(w.values())
    score = 100 * sum(w[k] * comps[k]["value"] for k in comps) / total_w
    for k in comps:
        comps[k]["weight"] = w[k]
        comps[k]["contribution"] = round(100 * w[k] * comps[k]["value"] / total_w, 2)
    return round(score, 2), comps


def growth_from_snapshots(metric: str, points: list[tuple[dt.datetime, float]], created_at: dt.datetime | None,
                          now: dt.datetime, current: float | None) -> Growth | None:
    """Velocity from our own snapshots (>= 1 h apart); otherwise an estimate since creation/post."""
    pts = sorted(points)
    if len(pts) >= 2:
        (t0, v0), (t1, v1) = pts[0], pts[-1]
        hours = (t1 - t0).total_seconds() / 3600
        if hours >= 1:
            return Growth(metric, max(0.0, (v1 - v0) / (hours / 24)), "snapshots", hours)
    if current is not None and created_at is not None:
        days = max((now - created_at).total_seconds() / 86400, 1 / 24)
        method = "since_post" if metric.startswith("hn_") else "since_creation"
        if days <= 60:  # older averages say little about current growth
            return Growth(metric, current / days, method, days * 24)
    return None
