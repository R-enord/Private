"""Hard zero-cost policy, enforced in code before any network I/O.

Rules (see API_INTEGRATIONS.md "Zero-cost enforcement"):
1. Requests to known paid / credit-metered inference endpoints are always refused.
2. Cloud AI providers are refused unless listed in ``VERIFIED_FREE_TIER_POLICIES`` (an in-code registry that
   is only updated after a dated review of official terms) AND explicitly configured AND opted in.
   As of 2026-11-22 the registry is EMPTY: no cloud provider has been verified, so none can be used.
3. Ollama "cloud" models (pay-per-token on ollama.com, verified 2026-11-22) are refused; only models that run
   on the local machine are allowed.

There is intentionally no code path that turns this off.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class EgressDenied(Exception):
    """Outbound request refused by egress policy (host not allowed, private address, bad scheme)."""


class CostPolicyViolation(EgressDenied):
    """Outbound request refused because it could incur cost (zero-cost mode)."""


# Hosts serving paid, credit-metered or not-yet-verified inference/data APIs. Never contacted.
PAID_OR_UNVERIFIED_HOSTS: frozenset[str] = frozenset(
    {
        "api.openai.com",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
        "aiplatform.googleapis.com",
        "api.cohere.com",
        "api.cohere.ai",
        "api.mistral.ai",
        "api.together.xyz",
        "api.together.ai",
        "api.replicate.com",
        "api.perplexity.ai",
        "api.groq.com",
        "openrouter.ai",
        "api.fireworks.ai",
        "api.deepseek.com",
        "api.x.ai",
        "api.deepinfra.com",
        "api.cerebras.ai",
        "api.sambanova.ai",
        "models.github.ai",
        "models.inference.ai.azure.com",
        "api-inference.huggingface.co",
        "router.huggingface.co",
        "api.endpoints.huggingface.cloud",
        "ollama.com",  # cloud models + web search API are pay-per-token (ollama.com/pricing, 2026-11-22)
        "www.ollama.com",
        "api.ollama.com",
        "api.producthunt.com",  # not audited yet (Phase 3)
        "oauth.reddit.com",  # not audited yet (Phase 3)
        "api.twitter.com",  # X API is paid
        "api.x.com",
        "api.cloudflare.com",  # Workers AI free allocation not verified
    }
)

# Host suffixes / patterns that are always metered inference services.
PAID_HOST_SUFFIXES: tuple[str, ...] = (
    ".openai.azure.com",
    ".cognitiveservices.azure.com",
    ".services.ai.azure.com",
    ".endpoints.huggingface.cloud",
)
PAID_HOST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^bedrock(-runtime|-agent-runtime)?\.[a-z0-9-]+\.amazonaws\.com$"),
    re.compile(r"^runtime\.sagemaker\.[a-z0-9-]+\.amazonaws\.com$"),
    re.compile(r"^[a-z0-9-]+-aiplatform\.googleapis\.com$"),
)

# Path prefixes on otherwise-allowed hosts that reach metered services.
PAID_PATH_RULES: tuple[tuple[str, str], ...] = (
    ("huggingface.co", "/api/inference-proxy"),
    ("huggingface.co", "/api/inference-endpoints"),
    ("huggingface.co", "/api/spaces/hardware"),
)


@dataclass(frozen=True)
class FreeTierPolicy:
    """A cloud provider whose free tier has been verified from official terms on a given date."""

    key: str
    hosts: frozenset[str]
    verified_on: str
    terms_url: str
    daily_request_cap: int
    model_pattern: str  # regex the model id must match to stay on the free tier (e.g. r".*:free$")
    notes: str


# EMPTY on purpose (2026-11-22): official docs for OpenRouter, Gemini, Groq, GitHub Models, Mistral and
# Cloudflare Workers AI could not be reviewed from the build environment, so none is verified.
VERIFIED_FREE_TIER_POLICIES: dict[str, FreeTierPolicy] = {}


def _norm_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def is_paid_host(host: str) -> bool:
    h = _norm_host(host)
    if h in PAID_OR_UNVERIFIED_HOSTS:
        return True
    if any(h.endswith(sfx) for sfx in PAID_HOST_SUFFIXES):
        return True
    return any(p.match(h) for p in PAID_HOST_PATTERNS)


def check_request(host: str, path: str, *, verified_policy_key: str | None = None) -> None:
    """Raise CostPolicyViolation if a request to host/path could incur cost."""
    h = _norm_host(host)
    if verified_policy_key:
        policy = VERIFIED_FREE_TIER_POLICIES.get(verified_policy_key)
        if policy is None or h not in policy.hosts:
            raise CostPolicyViolation(
                f"zero-cost mode: provider '{verified_policy_key}' has no verified free-tier policy for host {h}"
            )
        return
    if is_paid_host(h):
        raise CostPolicyViolation(f"zero-cost mode: host {h} is a paid or unverified metered service")
    for rule_host, prefix in PAID_PATH_RULES:
        if h == rule_host and path.startswith(prefix):
            raise CostPolicyViolation(f"zero-cost mode: {h}{prefix} is a metered endpoint")


_CLOUD_MODEL_RE = re.compile(r"(^|[-:])cloud($|[-:])", re.IGNORECASE)


def check_local_model_name(model: str) -> None:
    """Ollama cloud models carry a 'cloud' tag (e.g. 'gpt-oss:120b-cloud'); they run remotely and are billed."""
    if _CLOUD_MODEL_RE.search(model or ""):
        raise CostPolicyViolation(f"zero-cost mode: '{model}' is an Ollama cloud model (remote, metered)")


def check_free_tier_model(policy_key: str, model: str) -> None:
    policy = VERIFIED_FREE_TIER_POLICIES.get(policy_key)
    if policy is None:
        raise CostPolicyViolation(f"zero-cost mode: provider '{policy_key}' is not verified free")
    if not re.fullmatch(policy.model_pattern, model):
        raise CostPolicyViolation(f"zero-cost mode: model '{model}' is not on {policy_key}'s verified free tier")
