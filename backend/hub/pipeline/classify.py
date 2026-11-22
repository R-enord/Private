"""Deterministic classification: AI relevance, categories, labels, verification, opportunity fields.

Everything here is rule-based and explainable: each decision records the matched evidence. AI-based
classification (when a local model is available) can only *add* suggestions from the same closed vocabularies.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field


def _rx(*terms: str) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(terms) + r")(?![A-Za-z0-9])", re.IGNORECASE)


# --------------------------------------------------------------------------- relevance

STRONG_AI = _rx(
    r"llms?", r"large language models?", r"language models?", r"foundation models?", r"reasoning models?",
    r"gpt-?\d[\w.-]*", r"chatgpt", r"claude", r"gemini", r"llama[\w.-]*", r"mistral", r"mixtral", r"qwen[\w.-]*",
    r"deepseek[\w.-]*", r"gemma[\w.-]*", r"phi-\d[\w.-]*", r"kimi", r"glm-?\d[\w.-]*",
    r"machine learning", r"deep learning", r"neural networks?", r"artificial intelligence", r"generative ai",
    r"gen ?ai", r"mcp", r"model context protocol", r"ai agents?", r"agentic", r"multi-agent", r"rag",
    r"retrieval[- ]augmented", r"embeddings?", r"fine-?tun(?:e|es|ed|ing)", r"q?lora", r"quantiz\w*", r"gguf",
    r"ollama", r"vllm", r"llama\.cpp", r"hugging ?face", r"openai", r"anthropic", r"deepmind", r"inference",
    r"text-to-(?:image|video|speech|audio|3d)", r"image generation", r"video generation", r"tts", r"asr",
    r"whisper", r"speech recognition", r"computer vision", r"multimodal", r"vlms?", r"vision[- ]language",
    r"copilot", r"chatbots?", r"prompt (?:engineering|injection)", r"pytorch", r"tensorflow", r"jax",
    r"reinforcement learning", r"rlhf", r"transformers?", r"diffusion models?", r"stable diffusion", r"comfyui",
    r"ai", r"a\.i\.", r"ml", r"nlp", r"genai", r"agi", r"moe", r"mixture[- ]of[- ]experts", r"tokeniz\w+",
)
WEAK_AI = _rx(r"models?", r"agents?", r"neural", r"gpus?", r"datasets?", r"benchmarks?", r"evals?", r"robotics",
              r"autonomous", r"cuda", r"weights", r"training", r"semantic search", r"vector (?:db|database)s?")


_URLISH = re.compile(r"https?://\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:ai|ml|io|com|org|net|dev|app)\b", re.IGNORECASE)


def ai_relevance(title: str, text: str = "", tags: list[str] | None = None) -> tuple[float, list[str]]:
    """Return (relevance 0..1, matched terms). Title matches count double. URLs/domains never count."""
    title = _URLISH.sub(" ", title or "")
    text = _URLISH.sub(" ", text or "")
    t_strong = {m.group(0).lower() for m in STRONG_AI.finditer(title or "")}
    b_strong = {m.group(0).lower() for m in STRONG_AI.finditer(text or "")} - t_strong
    tag_text = " ".join(tags or []).replace("-", " ")
    g_strong = {m.group(0).lower() for m in STRONG_AI.finditer(tag_text)} - t_strong - b_strong
    weak = {m.group(0).lower() for m in WEAK_AI.finditer(f"{title} {text}")}
    score = 1.0 * len(t_strong) + 0.5 * len(b_strong) + 0.4 * len(g_strong) + 0.12 * len(weak)
    matched = sorted(t_strong | b_strong | g_strong) + [f"~{w}" for w in sorted(weak)]
    return min(1.0, score), matched


# --------------------------------------------------------------------------- categories

CATEGORY_RULES: dict[str, re.Pattern[str]] = {
    "llm": _rx(r"llms?", r"large language models?", r"language models?", r"foundation models?", r"gpt-?\d[\w.-]*",
               r"chatgpt", r"claude", r"gemini", r"llama[\w.-]*", r"mistral", r"qwen[\w.-]*", r"deepseek[\w.-]*",
               r"gemma[\w.-]*", r"phi-\d[\w.-]*", r"chat models?", r"instruct", r"text-generation", r"reasoning models?"),
    "model-releases": _rx(r"fine-?tun(?:e|es|ed|ing)", r"finetune[sd]?", r"quantiz\w*", r"gguf", r"checkpoints?",
                          r"benchmarks?", r"leaderboards?", r"open weights", r"weights", r"q?lora", r"safetensors",
                          r"model (?:release|card)", r"distill\w*"),
    "mcp": _rx(r"mcp", r"mcp[- ]servers?", r"model context protocol", r"mcp-server"),
    "agents": _rx(r"ai agents?", r"agents?", r"agentic", r"multi-agent", r"orchestrat\w+", r"autogen", r"crewai",
                  r"langgraph", r"tool[- ]use", r"function[- ]calling", r"computer[- ]use", r"browser[- ]use"),
    "open-source": _rx(r"open[- ]source", r"open weights", r"apache-2\.0", r"mit license", r"github"),
    "devtools-infra": _rx(r"inference", r"serving", r"sdks?", r"apis?", r"cli", r"vllm", r"llama\.cpp", r"ollama",
                          r"kubernetes", r"gpus?", r"cuda", r"deploy\w*", r"vector (?:db|database)s?", r"embeddings?",
                          r"observability", r"devtools?", r"ide", r"copilot", r"runtime", r"compiler", r"kernels?",
                          r"sentence-similarity", r"feature-extraction", r"rag"),
    "creative-ai": _rx(r"text-to-(?:image|video|speech|audio|3d)", r"image[- ]generation", r"diffusion", r"video",
                       r"tts", r"voice", r"music", r"audio", r"3d", r"animation", r"comfyui", r"stable diffusion",
                       r"flux", r"image-to-(?:image|video)", r"text-to-image", r"speech"),
    "research": _rx(r"arxiv", r"papers?", r"preprints?", r"we propose", r"we present", r"state[- ]of[- ]the[- ]art",
                    r"ablation"),
    "industry": _rx(r"announc\w+", r"introducing", r"launch\w*", r"partnership", r"acquir\w+", r"funding",
                    r"raises \$[\d.]+", r"general availability", r"now available"),
    "workflows": _rx(r"tutorials?", r"how to", r"how-to", r"guides?", r"walkthrough", r"use cases?", r"workflows?",
                     r"cookbook", r"step[- ]by[- ]step", r"recipes?", r"til"),
    "opportunities": _rx(r"hiring", r"jobs?", r"internships?", r"interns?", r"fellowships?", r"grants?",
                         r"hackathons?", r"bount(?:y|ies)", r"beta testers?", r"beta program", r"red[- ]team\w*",
                         r"annotat\w+", r"data label\w*", r"contractors?", r"ai trainers?", r"evaluators?",
                         r"paid testing", r"call for (?:papers|participants|proposals)", r"apply now"),
    "emerging": _rx(r"show hn", r"early[- ]stage", r"side project", r"indie", r"weekend project", r"prototype"),
}

KIND_CATEGORY = {
    "paper": "research", "model": "model-releases", "dataset": "model-releases", "repository": "open-source",
    "space": "emerging", "opportunity": "opportunities",
}


@dataclass
class CategoryHit:
    slug: str
    method: str
    confidence: float
    evidence: str


def categorize(
    title: str, text: str, kind: str, tags: list[str], source_hints: list[str], source_class: str,
    created_recently: bool = False,
) -> list[CategoryHit]:
    hits: dict[str, CategoryHit] = {}
    blob_title = title or ""
    blob = f"{text or ''} {' '.join(t.replace('-', ' ') for t in tags)} {' '.join(tags)}"
    for slug, rx in CATEGORY_RULES.items():
        t_matches = sorted({m.group(0).lower() for m in rx.finditer(blob_title)})
        b_matches = sorted({m.group(0).lower() for m in rx.finditer(blob)} - set(t_matches))
        if not t_matches and not b_matches:
            continue
        conf = min(0.95, 0.55 * bool(t_matches) + 0.15 * len(t_matches) + 0.1 * len(b_matches) + 0.2)
        if slug == "agents" and t_matches == ["agent"] and not b_matches:
            conf = 0.45
        hits[slug] = CategoryHit(slug, "rule", round(conf, 2), "matched: " + ", ".join((t_matches + b_matches)[:6]))
    if kind in KIND_CATEGORY:
        slug = KIND_CATEGORY[kind]
        hits[slug] = CategoryHit(slug, "source", 0.9, f"item kind: {kind}")
    for slug in source_hints:
        hits.setdefault(slug, CategoryHit(slug, "source", 0.6, "source category"))
    if source_class == "official" and "industry" not in hits and kind in ("article", "release"):
        hits["industry"] = CategoryHit("industry", "source", 0.6, "official publisher")
    if created_recently and kind in ("repository", "space"):
        hits.setdefault("emerging", CategoryHit("emerging", "rule", 0.5, "created within the last 14 days"))
    # Weak single-word 'agent' alone is too noisy for repos/models without other AI signal
    return [h for h in hits.values() if h.confidence >= 0.4]


# --------------------------------------------------------------------------- labels

LABEL_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("deprecation", _rx(r"deprecat\w*", r"sunset\w*", r"end[- ]of[- ]life", r"shutting down", r"shut down",
                        r"discontinu\w+", r"will be removed", r"retir(?:e|es|ed|ing)", r"no longer (?:supported|maintained)")),
    ("opportunity", _rx(r"(?:is|are|we're|we are) hiring", r"hiring", r"internships?", r"fellowships?", r"grants?",
                        r"hackathons?", r"bount(?:y|ies)", r"beta testers?", r"call for (?:papers|participants|proposals)",
                        r"job opening", r"apply (?:now|by)", r"red[- ]teaming program")),
    ("rumor", _rx(r"rumou?rs?", r"reportedly", r"leak(?:ed|s)?", r"allegedly", r"unconfirmed", r"sources say",
                  r"according to (?:people|sources)")),
    ("beta", _rx(r"beta")),
    ("preview", _rx(r"preview", r"waitlist", r"early access", r"alpha", r"release candidate", r"rc\d*",
                    r"experimental", r"research preview")),
]
ANNOUNCE_RX = _rx(r"announc\w+", r"introducing", r"launch\w*", r"today we", r"now available", r"generally available",
                  r"general availability", r"unveil\w*")
UPDATE_RX = _rx(r"update[sd]?", r"improv\w+", r"now supports?", r"adds? support", r"changelog", r"what's new",
                r"what’s new", r"release notes")
RELEASE_RX = _rx(r"releas(?:e|es|ed|ing)", r"v\d+\.\d+(?:\.\d+)?", r"show hn", r"open[- ]sourc(?:e|ed|ing)")


@dataclass
class LabelResult:
    label: str
    evidence: str
    method: str = "rule"


def assign_label(title: str, text: str, kind: str, source_class: str, extra: dict | None = None) -> LabelResult:
    extra = extra or {}
    t = title or ""
    for label, rx in LABEL_RULES:
        m = rx.search(t)
        if m:
            return LabelResult(label, f"title contains '{m.group(0)}'")
    if kind == "release" and extra.get("prerelease"):
        return LabelResult("preview", "GitHub marks this release as a pre-release")
    for label, rx in LABEL_RULES[:1]:  # deprecations mentioned in body still matter
        m = rx.search(text or "")
        if m:
            return LabelResult(label, f"text mentions '{m.group(0)}'")
    if kind == "paper":
        return LabelResult("research", "item is a research paper/preprint")
    if kind == "opportunity":
        return LabelResult("opportunity", "source lists it as a job/opportunity")
    if kind in ("model", "dataset") or kind == "release":
        return LabelResult("release", f"new {kind} published by the source")
    if source_class == "official" and (m := ANNOUNCE_RX.search(f"{t} {text[:400] if text else ''}")):
        return LabelResult("announcement", f"official source; '{m.group(0)}'")
    if (m := UPDATE_RX.search(t)):
        return LabelResult("update", f"title contains '{m.group(0)}'")
    if (m := RELEASE_RX.search(t)) and kind in ("repository", "discussion", "article", "space"):
        return LabelResult("release", f"title contains '{m.group(0)}'")
    if kind == "repository" or kind == "space":
        return LabelResult("release", f"newly discovered public {kind}")
    if kind in ("discussion", "post"):
        return LabelResult("discussion", "community post or discussion")
    if source_class == "official":
        return LabelResult("announcement", "published by an official source")
    return LabelResult("unlabeled", "no rule matched")


# --------------------------------------------------------------------------- verification

VERIFICATION_ORDER = ["official", "corroborated", "primary", "reported", "community", "unverified"]


def verification_level(source_classes: list[str], distinct_publishers: int) -> str:
    classes = set(source_classes)
    if "official" in classes:
        return "official"
    if distinct_publishers >= 2:
        return "corroborated"
    if "primary" in classes:
        return "primary"
    if "press" in classes:
        return "reported"  # independent news reporting: not an official statement
    if classes & {"community", "independent"}:
        return "community"
    return "unverified"


# --------------------------------------------------------------------------- opportunity extraction

_MONEY_RX = re.compile(
    r"(?:(?:US)?\$|€|£)\s?\d[\d,.]*(?:\s?[kK])?(?:\s?(?:-|–|to)\s?(?:(?:US)?\$|€|£)?\s?\d[\d,.]*(?:\s?[kK])?)?"
    r"(?:\s?(?:/|per)\s?(?:hour|hr|h|year|yr|month|mo))?"
)
_DEADLINE_RX = re.compile(
    r"(?:deadline|apply by|applications? (?:close|due)|due by|closes on)[:\s]+"
    r"([A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}|\d{4}-\d{2}-\d{2}|\d{1,2} [A-Z][a-z]{2,8}\.? \d{4})",
    re.IGNORECASE,
)
_HN_HIRING_RX = re.compile(r"^(?P<org>.+?)\s*(?:\((?:YC [A-Z]\d{2}|YC)\))?\s+(?:is|are)\s+hiring", re.IGNORECASE)
_OPP_TYPES = [
    ("internship", _rx(r"internships?", r"interns?")),
    ("grant", _rx(r"grants?", r"fellowships?", r"funding program")),
    ("hackathon", _rx(r"hackathons?", r"competitions?", r"challenge")),
    ("beta", _rx(r"beta testers?", r"beta program", r"early access", r"waitlist")),
    ("red-teaming", _rx(r"red[- ]team\w*", r"bug bount(?:y|ies)", r"bount(?:y|ies)")),
    ("annotation-evaluation", _rx(r"annotat\w+", r"data label\w*", r"evaluators?", r"ai trainers?", r"rater")),
    ("role", _rx(r"hiring", r"jobs?", r"roles?", r"engineer", r"researcher", r"contractors?")),
]
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _parse_human_date(s: str) -> dt.datetime | None:
    s = s.strip().replace(",", "").replace(".", "")
    try:
        return dt.datetime.fromisoformat(s).replace(tzinfo=dt.UTC)
    except ValueError:
        pass
    parts = s.split()
    try:
        if len(parts) == 3 and parts[0][:3].lower() in _MONTHS:
            return dt.datetime(int(parts[2]), _MONTHS[parts[0][:3].lower()], int(parts[1]), tzinfo=dt.UTC)
        if len(parts) == 3 and parts[1][:3].lower() in _MONTHS:
            return dt.datetime(int(parts[2]), _MONTHS[parts[1][:3].lower()], int(parts[0]), tzinfo=dt.UTC)
    except ValueError:
        return None
    return None


@dataclass
class OpportunityFields:
    opp_type: str = "unknown"
    organization: str = ""
    remote: str = "unknown"
    compensation: str = ""
    deadline: dt.datetime | None = None
    evidence: dict[str, str] = field(default_factory=dict)


def extract_opportunity(title: str, text: str, author: str = "") -> OpportunityFields:
    """Only facts stated verbatim in the source are captured; everything else stays 'unknown'/empty."""
    out = OpportunityFields()
    blob = f"{title}\n{text}"
    for name, rx in _OPP_TYPES:
        if m := rx.search(blob):
            out.opp_type = name
            out.evidence["type"] = m.group(0)
            break
    if m := _HN_HIRING_RX.match(title or ""):
        out.organization = m.group("org").strip()
        out.evidence["organization"] = m.group(0)
    if re.search(r"\b(?:fully )?remote\b", blob, re.IGNORECASE):
        out.remote = "yes" if not re.search(r"\bno remote\b|\bnot remote\b", blob, re.IGNORECASE) else "no"
        out.evidence["remote"] = "text mentions remote"
    elif re.search(r"\bon[- ]?site\b|\bin[- ]office\b", blob, re.IGNORECASE):
        out.remote = "no"
        out.evidence["remote"] = "text mentions onsite"
    if m := _MONEY_RX.search(blob):
        out.compensation = m.group(0).strip()
        out.evidence["compensation"] = m.group(0)
    if m := _DEADLINE_RX.search(blob):
        d = _parse_human_date(m.group(1))
        if d:
            out.deadline = d
            out.evidence["deadline"] = m.group(0)
    return out
