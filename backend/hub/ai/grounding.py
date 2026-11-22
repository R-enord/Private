"""Deterministic post-validation of model output (anti-hallucination, anti-injection).

Model output is treated as untrusted. We:
* drop sentences that contain numbers, versions, sizes, money, percentages, dates or license names that do not
  appear in the source material (no invented specs/benchmarks/pricing/licenses),
* drop URLs, shell commands and install instructions (links come only from source metadata),
* restrict categories/maturity to closed vocabularies.
"""

from __future__ import annotations

import re
from typing import Any

_FACT_TOKEN = re.compile(
    r"(?:(?:US)?\$|€|£)\s?\d[\d,.]*\s?[kKmMbB]?"          # money
    r"|\b\d+(?:\.\d+)?\s?%"                                 # percent
    r"|\bv?\d+(?:\.\d+){1,3}\b"                             # versions / decimals
    r"|\b\d+(?:\.\d+)?\s?[BbMmKkTt]\b"                      # sizes like 7B, 1.5b
    r"|\b\d{2,}\b"                                          # bare multi-digit numbers (years, counts)
)
_LICENSE = re.compile(
    r"\b(?:MIT|Apache(?:[- ]2(?:\.0)?)?|GPL(?:-?[23](?:\.0)?)?|AGPL|LGPL|BSD(?:-[23]-Clause)?|MPL|CC[- ]BY(?:[- ](?:SA|NC|ND))*|"
    r"Unlicense|Llama \d+ (?:Community )?License|OpenRAIL|proprietary|open[- ]weights?)\b",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_COMMAND = re.compile(
    r"`[^`]+`|\$ \S+|\b(?:curl|wget|pip3? install|npm (?:i|install)|pnpm add|brew install|apt(?:-get)? install|"
    r"docker run|ollama (?:run|pull)|sudo|chmod|bash -c|\| ?sh)\b",
    re.IGNORECASE,
)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _norm(s: str) -> str:
    return re.sub(r"[\s,]", "", s.lower())


def ungrounded(text: str, source: str) -> list[str]:
    src = _norm(source)
    out = []
    for m in _FACT_TOKEN.finditer(text):
        tok = m.group(0).strip()
        if _norm(tok) not in src:
            out.append(tok)
    for m in _LICENSE.finditer(text):
        if _norm(m.group(0)) not in src:
            out.append(m.group(0))
    return out


def clean_text(text: str, source: str, removed: list[dict[str, Any]], field: str, max_len: int = 600) -> str:
    if not isinstance(text, str):
        return ""
    kept = []
    for sent in _SENT_SPLIT.split(text.strip()):
        if not sent:
            continue
        reasons = []
        if _URL.search(sent):
            reasons.append("contains URL")
        if _COMMAND.search(sent):
            reasons.append("contains command/install instruction")
        bad = ungrounded(sent, source)
        if bad:
            reasons.append("not in source: " + ", ".join(dict.fromkeys(bad)))
        if reasons:
            removed.append({"field": field, "sentence": sent[:300], "reasons": reasons})
        else:
            kept.append(sent)
    out = ""
    for sent in kept:  # whole sentences only, never cut mid-sentence
        if len(out) + len(sent) + 1 > max_len:
            break
        out = f"{out} {sent}".strip()
    return out


def validate_summary(data: dict[str, Any], source: str, allowed_categories: list[str], allowed_maturity: list[str]
                     ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    removed: list[dict[str, Any]] = []
    out: dict[str, Any] = {
        "summary": clean_text(data.get("summary", ""), source, removed, "summary"),
        "what_it_is": clean_text(data.get("what_it_is", ""), source, removed, "what_it_is", 300),
        "why_it_matters": clean_text(data.get("why_it_matters", ""), source, removed, "why_it_matters", 400),
    }
    points = []
    for p in (data.get("key_points") or [])[:8]:
        c = clean_text(str(p), source, removed, "key_points", 200)
        if c:
            points.append(c)
    out["key_points"] = points[:5]
    cats = [c for c in (data.get("category_suggestions") or []) if c in allowed_categories]
    dropped = [c for c in (data.get("category_suggestions") or []) if c not in allowed_categories]
    if dropped:
        removed.append({"field": "category_suggestions", "sentence": ", ".join(map(str, dropped))[:200],
                        "reasons": ["outside allowed vocabulary"]})
    out["category_suggestions"] = list(dict.fromkeys(cats))[:4]
    m = data.get("maturity", "unknown")
    out["maturity"] = m if m in allowed_maturity else "unknown"
    return out, removed
