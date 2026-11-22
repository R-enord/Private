"""AI provider adapters behind one interface.

* ``OllamaProvider`` — primary. Only loopback (or, if explicitly allowed, private-LAN) hosts; cloud models refused.
* ``OpenAICompatibleProvider`` — for future *verified* free tiers only. Construction fails unless the provider
  has an entry in ``cost_guard.VERIFIED_FREE_TIER_POLICIES`` (empty as of 2026-11-22), so it cannot be used.
"""

from __future__ import annotations

import ipaddress
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from hub.net import cost_guard
from hub.net.cost_guard import CostPolicyViolation, EgressDenied
from hub.net.http import SafeHttpClient

EMBED_HINTS = ("embed", "minilm", "bge", "e5-", "gte", "nomic", "mxbai", "arctic-embed", "granite-embedding")
CHAT_PREFERENCE = ("qwen3", "qwen2.5", "llama3.2", "gemma3", "phi4-mini", "llama3.1", "mistral", "granite3")
EMBED_PREFERENCE = ("all-minilm", "nomic-embed-text", "embeddinggemma", "mxbai-embed-large", "bge-m3")


@dataclass
class ProviderHealth:
    ok: bool
    detail: str = ""
    models: list[str] = field(default_factory=list)
    blocked_models: list[str] = field(default_factory=list)


@dataclass
class GenerateResult:
    data: dict[str, Any]
    raw_text: str
    provider: str
    model: str
    latency_ms: int
    tokens_in: int | None = None
    tokens_out: int | None = None


class AIProvider(ABC):
    key: str
    kind: str
    is_local: bool
    cost_class: str  # local | free_tier_verified

    @abstractmethod
    def health(self) -> ProviderHealth: ...

    @abstractmethod
    def chat_model(self) -> str: ...

    @abstractmethod
    def embed_model(self) -> str: ...

    @abstractmethod
    def generate_json(self, system: str, prompt: str, schema: dict[str, Any], *, max_tokens: int = 600,
                      temperature: float = 0.1) -> GenerateResult: ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _is_local_host(host: str, allow_lan: bool, trusted: tuple[str, ...] = ()) -> bool:
    if host in ("localhost", "127.0.0.1", "::1") or host in trusted:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or (allow_lan and ip.is_private)


def _is_cloud_entry(m: dict[str, Any]) -> bool:
    name = m.get("name") or m.get("model") or ""
    if m.get("remote_host") or m.get("remote_model"):
        return True
    try:
        cost_guard.check_local_model_name(name)
    except CostPolicyViolation:
        return True
    return False


class OllamaProvider(AIProvider):
    kind = "ollama"
    is_local = True
    cost_class = "local"

    def __init__(self, base_url: str, model: str = "", embed_model: str = "", *, num_ctx: int = 4096,
                 timeout_s: float = 180.0, allow_lan: bool = False, transport: httpx.BaseTransport | None = None,
                 key: str = "ollama-local", trusted_hosts: tuple[str, ...] = ()):
        parts = urlsplit(base_url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in ("http", "https") or not host:
            raise EgressDenied(f"invalid Ollama URL {base_url!r}")
        if host in cost_guard.PAID_OR_UNVERIFIED_HOSTS or not _is_local_host(host, allow_lan, trusted_hosts):
            raise CostPolicyViolation(
                f"Ollama host {host} is not local; remote inference endpoints are not allowed (zero-cost/privacy)")
        self.key = key
        self.base = f"{parts.scheme}://{parts.netloc}"
        self._model = model
        self._embed_model = embed_model
        self.num_ctx = num_ctx
        self._installed: list[str] | None = None
        self.http = SafeHttpClient(
            allowed_hosts={host}, allow_http_hosts={host}, allow_private_hosts={host}, purpose="ai",
            user_agent="ai-intelligence-hub", transport=transport, timeout_s=timeout_s, max_retries=1,
            use_env_proxy=False,
        )

    # ------------------------------------------------------------------ models
    def _list_models(self) -> tuple[list[str], list[str]]:
        data = self.http.get(f"{self.base}/api/tags", timeout_s=10).json()
        local: list[str] = []
        blocked: list[str] = []
        for m in data.get("models", []):
            name = m.get("name") or m.get("model") or ""
            (blocked if _is_cloud_entry(m) else local).append(name)
        return local, blocked

    def health(self) -> ProviderHealth:
        try:
            version = self.http.get(f"{self.base}/api/version", timeout_s=5).json().get("version", "?")
            local, blocked = self._list_models()
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(False, f"Ollama not reachable at {self.base}: {type(e).__name__}")
        self._installed = local
        if not local:
            return ProviderHealth(False, f"Ollama {version} running but no local models installed", [], blocked)
        return ProviderHealth(True, f"Ollama {version}", local, blocked)

    def _installed_models(self) -> list[str]:
        if self._installed is None:
            self._installed, _ = self._list_models()
        return self._installed

    def chat_model(self) -> str:
        if self._model:
            cost_guard.check_local_model_name(self._model)
            return self._model
        models = [m for m in self._installed_models() if not any(h in m.lower() for h in EMBED_HINTS)]
        for pref in CHAT_PREFERENCE:
            for m in models:
                if m.lower().startswith(pref):
                    return m
        if models:
            return models[0]
        raise RuntimeError("no local chat model installed (e.g. `ollama pull qwen2.5:1.5b`)")

    def embed_model(self) -> str:
        if self._embed_model:
            cost_guard.check_local_model_name(self._embed_model)
            return self._embed_model
        models = [m for m in self._installed_models() if any(h in m.lower() for h in EMBED_HINTS)]
        for pref in EMBED_PREFERENCE:
            for m in models:
                if m.lower().startswith(pref):
                    return m
        if models:
            return models[0]
        raise RuntimeError("no local embedding model installed (e.g. `ollama pull all-minilm`)")

    # ------------------------------------------------------------------ calls
    def generate_json(self, system: str, prompt: str, schema: dict[str, Any], *, max_tokens: int = 600,
                      temperature: float = 0.1) -> GenerateResult:
        model = self.chat_model()
        cost_guard.check_local_model_name(model)
        started = time.monotonic()
        res = self.http.post(f"{self.base}/api/chat", json_body={
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": False,
            "format": schema,
            "options": {"temperature": temperature, "num_ctx": self.num_ctx, "num_predict": max_tokens},
            "keep_alive": "5m",
        })
        body = res.json()
        text = (body.get("message") or {}).get("content", "")
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("model did not return a JSON object")
        return GenerateResult(data=data, raw_text=text, provider=self.key, model=model,
                              latency_ms=int((time.monotonic() - started) * 1000),
                              tokens_in=body.get("prompt_eval_count"), tokens_out=body.get("eval_count"))

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self.embed_model()
        cost_guard.check_local_model_name(model)
        res = self.http.post(f"{self.base}/api/embed", json_body={"model": model, "input": texts, "keep_alive": "5m"})
        vecs = res.json().get("embeddings") or []
        if len(vecs) != len(texts):
            raise ValueError("embedding count mismatch")
        return vecs


class OpenAICompatibleProvider(AIProvider):
    """Generic adapter for a *verified* free tier. Refuses to exist without a verified policy."""

    kind = "openai_compatible"
    is_local = False
    cost_class = "free_tier_verified"

    def __init__(self, key: str, base_url: str, model: str, policy_key: str):
        host = (urlsplit(base_url).hostname or "").lower()
        cost_guard.check_request(host, urlsplit(base_url).path or "/", verified_policy_key=policy_key)
        cost_guard.check_free_tier_model(policy_key, model)
        # Unreachable while VERIFIED_FREE_TIER_POLICIES is empty; kept to document the contract.
        raise CostPolicyViolation("cloud adapters are not enabled in this release")  # pragma: no cover

    def health(self) -> ProviderHealth:  # pragma: no cover
        return ProviderHealth(False, "disabled")

    def chat_model(self) -> str:  # pragma: no cover
        return ""

    def embed_model(self) -> str:  # pragma: no cover
        return ""

    def generate_json(self, *a: Any, **k: Any) -> GenerateResult:  # pragma: no cover
        raise CostPolicyViolation("disabled")

    def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        raise CostPolicyViolation("disabled")
