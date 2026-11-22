"""Hybrid AI provider manager.

Order of preference: local providers first. A provider is used only if
* it passes the zero-cost policy (cloud requires a verified free-tier policy; none exist today),
* the content's privacy class allows it (private content never leaves the machine unless explicitly opted in),
* its circuit breaker is closed and its daily quota (if any) is not exhausted.
Results are cached by (task, prompt version, provider, model, input hash) — no repeated inference.
If nothing is available, ``AIUnavailable`` is raised and the caller defers/queues the task; collection and
deterministic processing are never blocked by AI.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hub.ai.providers import AIProvider, OllamaProvider, ProviderHealth
from hub.config import get_settings
from hub.models import AICache, AIProviderState, AIUsage
from hub.net.cost_guard import CostPolicyViolation, EgressDenied

CIRCUIT_ERRORS = 3
CIRCUIT_OPEN_S = 600


class AIUnavailable(Exception):  # noqa: N818
    def __init__(self, message: str, retry_after_s: float = 900):
        super().__init__(message)
        self.retry_after_s = retry_after_s


@dataclass
class TaskMeta:
    provider: str
    model: str
    cache_hit: bool
    latency_ms: int


def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


class ProviderManager:
    def __init__(self, providers: list[AIProvider] | None = None, construction_errors: dict[str, str] | None = None):
        self.construction_errors = construction_errors or {}
        if providers is None:
            providers, errs = self._build_default()
            self.construction_errors.update(errs)
        self.providers = sorted(providers, key=lambda p: (not p.is_local, p.key))

    @staticmethod
    def _build_default() -> tuple[list[AIProvider], dict[str, str]]:
        s = get_settings()
        out: list[AIProvider] = []
        errs: dict[str, str] = {}
        try:
            out.append(OllamaProvider(s.ollama_url, s.ollama_model, s.ollama_embed_model, num_ctx=s.ai_num_ctx,
                                      timeout_s=s.ai_timeout_s, allow_lan=s.ollama_allow_lan,
                                      trusted_hosts=tuple(h.lower() for h in s.ollama_trusted_hosts)))
        except (CostPolicyViolation, EgressDenied) as e:
            errs["ollama-local"] = f"blocked: {e}"
        # Cloud adapters: none verified (see cost_guard.VERIFIED_FREE_TIER_POLICIES). Even with
        # HUB_CLOUD_AI_ENABLED=true nothing is constructed; the state is shown in the UI.
        if s.cloud_ai_enabled:
            errs["cloud"] = "blocked: no cloud provider has a verified free-tier policy (zero-cost mode)"
        return out, errs

    # ------------------------------------------------------------------ state
    def _state(self, session: Session, p: AIProvider) -> AIProviderState:
        st = session.get(AIProviderState, p.key)
        if st is None:
            st = AIProviderState(key=p.key, kind=p.kind, base_url=getattr(p, "base", ""), is_local=p.is_local,
                                 cost_class=p.cost_class, enabled=True, status="unknown")
            session.add(st)
            session.flush()
        today = dt.date.today().isoformat()
        if st.quota_day != today:
            st.quota_day, st.requests_today = today, 0
        return st

    def refresh_status(self, session: Session) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for p in self.providers:
            st = self._state(session, p)
            h: ProviderHealth = p.health()
            st.status = "ok" if h.ok else "unavailable"
            st.status_detail = h.detail + (f"; cloud models ignored: {', '.join(h.blocked_models)}" if h.blocked_models else "")
            st.models_available = h.models
            if h.ok:
                try:
                    st.model = p.chat_model()
                except Exception as e:  # noqa: BLE001
                    st.model, st.status, st.status_detail = "", "unavailable", str(e)
                try:
                    st.embed_model = p.embed_model()
                except Exception:  # noqa: BLE001
                    st.embed_model = ""
            st.updated_at = dt.datetime.now(dt.UTC)
            out[p.key] = {"status": st.status, "detail": st.status_detail, "model": st.model}
        for key, err in self.construction_errors.items():
            row = session.get(AIProviderState, key)
            if row is None:
                row = AIProviderState(key=key, kind="ollama" if "ollama" in key else "openai_compatible", base_url="",
                                     is_local=key.startswith("ollama"), cost_class="blocked", enabled=False)
                session.add(row)
            row.status, row.status_detail = "blocked", err
            out[key] = {"status": "blocked", "detail": err}
        return out

    def _usable(self, session: Session, p: AIProvider, privacy: str, now: dt.datetime) -> str | None:
        settings = get_settings()
        st = self._state(session, p)
        if not st.enabled:
            return "disabled"
        if not p.is_local and privacy == "private" and not settings.allow_private_content_to_cloud:
            return "private content stays local"
        if not p.is_local and not settings.cloud_ai_enabled:
            return "cloud AI disabled"
        if st.circuit_open_until and st.circuit_open_until > now:
            return f"circuit open until {st.circuit_open_until.isoformat()}"
        if st.daily_request_quota is not None and st.requests_today >= st.daily_request_quota:
            return "daily quota exhausted"
        return None

    def _record(self, session: Session, p: AIProvider, model: str, task: str, status: str, latency: int = 0,
                tin: int | None = None, tout: int | None = None, error: str = "") -> None:
        session.add(AIUsage(provider=p.key, model=model, task=task, status=status, latency_ms=latency,
                            tokens_in=tin, tokens_out=tout, error=error[:1000]))

    # ------------------------------------------------------------------ tasks
    def run_json_task(self, session: Session, *, task: str, prompt_version: str, system: str, prompt: str,
                      schema: dict[str, Any], privacy: str = "public", max_tokens: int = 600) -> tuple[dict[str, Any], TaskMeta]:
        now = dt.datetime.now(dt.UTC)
        reasons: list[str] = []
        input_hash = _hash(system, prompt, json.dumps(schema, sort_keys=True))
        for p in self.providers:
            why = self._usable(session, p, privacy, now)
            if why:
                reasons.append(f"{p.key}: {why}")
                continue
            st = self._state(session, p)
            try:
                model = p.chat_model()
            except Exception as e:  # noqa: BLE001
                reasons.append(f"{p.key}: {type(e).__name__}: {e}")
                st.status, st.status_detail = "unavailable", f"{type(e).__name__}: {e}"[:500]
                st.last_error, st.last_error_at = st.status_detail, now
                continue
            key = _hash(task, prompt_version, p.key, model, input_hash)
            cached = session.scalar(select(AICache).where(AICache.cache_key == key))
            if cached is not None:
                self._record(session, p, model, task, "cache_hit")
                return cached.output, TaskMeta(p.key, model, True, 0)
            try:
                res = p.generate_json(system, prompt, schema, max_tokens=max_tokens)
            except CostPolicyViolation as e:
                st.status, st.status_detail = "blocked", str(e)[:500]
                self._record(session, p, model, task, "blocked", error=str(e))
                reasons.append(f"{p.key}: blocked by zero-cost policy")
                continue
            except Exception as e:  # noqa: BLE001
                st.consecutive_errors += 1
                st.last_error, st.last_error_at = f"{type(e).__name__}: {e}"[:1000], now
                if st.consecutive_errors >= CIRCUIT_ERRORS:
                    st.circuit_open_until = now + dt.timedelta(seconds=CIRCUIT_OPEN_S)
                    st.status = "error"
                self._record(session, p, model, task, "error", error=f"{type(e).__name__}: {e}")
                reasons.append(f"{p.key}: {type(e).__name__}")
                continue
            st.consecutive_errors = 0
            st.circuit_open_until = None
            st.status, st.last_ok_at, st.model = "ok", now, model
            st.requests_today += 1
            st.avg_latency_ms = res.latency_ms if st.avg_latency_ms is None else 0.8 * st.avg_latency_ms + 0.2 * res.latency_ms
            self._record(session, p, model, task, "ok", res.latency_ms, res.tokens_in, res.tokens_out)
            session.add(AICache(cache_key=key, task=task, prompt_version=prompt_version, provider=p.key, model=model,
                                output=res.data))
            session.flush()
            return res.data, TaskMeta(p.key, model, False, res.latency_ms)
        detail = "; ".join(reasons + [f"{k}: {v}" for k, v in self.construction_errors.items()]) or "no providers"
        raise AIUnavailable(f"no AI provider available ({detail})")

    def embed(self, session: Session, texts: list[str], privacy: str = "public") -> tuple[list[list[float]], str, str]:
        now = dt.datetime.now(dt.UTC)
        reasons = []
        for p in self.providers:
            why = self._usable(session, p, privacy, now)
            if why:
                reasons.append(f"{p.key}: {why}")
                continue
            try:
                model = p.embed_model()
                started = dt.datetime.now(dt.UTC)
                vecs = p.embed(texts)
            except Exception as e:  # noqa: BLE001
                reasons.append(f"{p.key}: {type(e).__name__}: {e}")
                continue
            latency = int((dt.datetime.now(dt.UTC) - started).total_seconds() * 1000)
            self._record(session, p, model, "embed", "ok", latency)
            return vecs, p.key, model
        raise AIUnavailable("no embedding provider available (" + "; ".join(reasons or ["no providers"]) + ")")


_manager: ProviderManager | None = None


def get_manager() -> ProviderManager:
    global _manager
    if _manager is None:
        _manager = ProviderManager()
    return _manager


def set_manager(m: ProviderManager | None) -> None:
    global _manager
    _manager = m
