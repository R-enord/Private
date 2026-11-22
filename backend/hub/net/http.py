"""SafeHttpClient: the only way the application talks to the network.

Every request is checked against:
* the zero-cost policy (``cost_guard``) — before any I/O,
* an explicit host allowlist declared by the enabled source/provider,
* scheme rules (HTTPS only, except explicitly allowed local hosts such as Ollama on loopback),
* SSRF rules (no private/loopback/link-local IP literals for connector traffic).

Redirects are followed manually so every hop is re-checked. Retries use exponential backoff with full jitter
and honour ``Retry-After``/rate-limit reset headers. Response bodies are size-capped. All attempts are recorded
(as dicts) for the operations dashboard; credentials are never recorded.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import ipaddress
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from hub.net.cost_guard import CostPolicyViolation, EgressDenied, check_request


class HttpError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RateLimited(HttpError):
    def __init__(self, message: str, reset_at: dt.datetime | None, status: int | None = None):
        super().__init__(message, status)
        self.reset_at = reset_at


@dataclass
class HttpResult:
    status: int
    url: str
    headers: httpx.Headers
    content: bytes = b""
    not_modified: bool = False

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("last-modified")

    def json(self) -> Any:
        import json

        return json.loads(self.content)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


@dataclass
class RateInfo:
    remaining: int | None = None
    reset_at: dt.datetime | None = None
    retry_after_s: float | None = None


_HF_RATELIMIT_RE = re.compile(r'"?(?P<name>[a-z|]+)"?\s*;\s*r=(?P<r>\d+)\s*;\s*t=(?P<t>\d+)')
_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "metadata.google.internal", "metadata"}


def parse_rate_headers(headers: httpx.Headers, now: dt.datetime | None = None) -> RateInfo:
    now = now or dt.datetime.now(dt.UTC)
    info = RateInfo()
    # GitHub style
    if (rem := headers.get("x-ratelimit-remaining")) is not None:
        try:
            info.remaining = int(rem)
        except ValueError:
            pass
    if (reset := headers.get("x-ratelimit-reset")) is not None:
        try:
            info.reset_at = dt.datetime.fromtimestamp(int(reset), dt.UTC)
        except (ValueError, OverflowError):
            pass
    # IETF draft (Hugging Face): RateLimit: "api";r=485;t=257
    if (rl := headers.get("ratelimit")) is not None and (m := _HF_RATELIMIT_RE.search(rl)):
        info.remaining = int(m.group("r"))
        info.reset_at = now + dt.timedelta(seconds=int(m.group("t")))
    if (ra := headers.get("retry-after")) is not None:
        ra = ra.strip()
        if ra.isdigit():
            info.retry_after_s = float(ra)
        else:
            try:
                parsed = email.utils.parsedate_to_datetime(ra)
                info.retry_after_s = max(0.0, (parsed - now).total_seconds())
            except (TypeError, ValueError):
                pass
    return info


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 30.0) -> float:
    """Exponential backoff with full jitter (AWS architecture blog style)."""
    return random.uniform(0, min(cap, base * (2 ** max(0, attempt - 1))))


def sanitize_path(url: str) -> str:
    """Path + query without credentials; tokens never travel in query strings here, but be defensive."""
    parts = urlsplit(url)
    q = re.sub(r"(?i)(token|key|secret|password|auth)=[^&]*", r"\1=REDACTED", parts.query)
    return parts.path + (f"?{q}" if q else "")


@dataclass
class SafeHttpClient:
    allowed_hosts: set[str]
    user_agent: str
    purpose: str = "connector"
    source_id: int | None = None
    allow_http_hosts: set[str] = field(default_factory=set)
    allow_private_hosts: set[str] = field(default_factory=set)
    auth_headers: dict[str, dict[str, str]] = field(default_factory=dict)  # host -> headers (never logged)
    host_spacing: dict[str, float] = field(default_factory=dict)  # host -> min seconds between requests
    throttle: Callable[[str, float], None] | None = None
    transport: httpx.BaseTransport | None = None
    timeout_s: float = 30.0
    max_retries: int = 3
    max_inline_wait_s: float = 20.0
    max_redirects: int = 5
    max_bytes: int = 15 * 1024 * 1024
    sleep: Callable[[float], None] = time.sleep
    log: list[dict[str, Any]] = field(default_factory=list)
    last_rate: RateInfo = field(default_factory=RateInfo)
    _client: httpx.Client | None = None

    # ------------------------------------------------------------------ policy
    def check_url(self, url: str) -> tuple[str, str]:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        if not host:
            raise EgressDenied(f"egress: URL has no host: {url!r}")
        if parts.username or parts.password:
            raise EgressDenied("egress: credentials in URL are not allowed")
        # 1) zero-cost: before anything else, and regardless of allowlists
        check_request(host, parts.path or "/")
        # 2) scheme
        if parts.scheme == "http":
            if host not in self.allow_http_hosts:
                raise EgressDenied(f"egress: plain HTTP not allowed for {host}")
        elif parts.scheme != "https":
            raise EgressDenied(f"egress: scheme {parts.scheme!r} not allowed")
        # 3) explicit allowlist
        if host not in self.allowed_hosts:
            raise EgressDenied(f"egress: host {host} is not declared by this source/provider")
        # 4) SSRF: private/loopback targets only when explicitly allowed (e.g. local Ollama)
        if host not in self.allow_private_hosts:
            if host in _BLOCKED_HOSTNAMES or host.endswith((".local", ".internal", ".localhost", ".lan")):
                raise EgressDenied(f"egress: internal hostname {host} not allowed")
            try:
                ip = ipaddress.ip_address(host.strip("[]"))
            except ValueError:
                ip = None
            if ip is not None and (
                ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified
            ):
                raise EgressDenied(f"egress: private address {host} not allowed")
        return host, parts.path or "/"

    # ------------------------------------------------------------------ plumbing
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                transport=self.transport,
                timeout=httpx.Timeout(self.timeout_s, connect=min(15.0, self.timeout_s)),
                follow_redirects=False,
                trust_env=self.transport is None,  # honour HTTPS_PROXY / CA bundle in real runs
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> SafeHttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _record(self, **kw: Any) -> None:
        kw.setdefault("source_id", self.source_id)
        kw.setdefault("purpose", self.purpose)
        kw.setdefault("created_at", dt.datetime.now(dt.UTC))
        self.log.append(kw)

    @property
    def request_count(self) -> int:
        return sum(1 for e in self.log if e["outcome"] != "blocked")

    @property
    def retry_count(self) -> int:
        return sum(1 for e in self.log if e["outcome"] == "retry")

    # ------------------------------------------------------------------ request
    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        etag: str | None = None,
        last_modified: str | None = None,
        raise_for_status: bool = True,
        timeout_s: float | None = None,
    ) -> HttpResult:
        current_url = str(httpx.URL(url, params=params)) if params else url
        redirects = 0
        attempt = 0
        while True:
            try:
                host, _path = self.check_url(current_url)
            except EgressDenied as e:
                self._record(
                    method=method, host=urlsplit(current_url).hostname or "?", path=sanitize_path(current_url),
                    status=None, outcome="blocked", attempt=attempt + 1, duration_ms=0, bytes=0,
                    rate_limit_remaining=None,
                    error=("cost_policy: " if isinstance(e, CostPolicyViolation) else "egress: ") + str(e),
                )
                raise
            attempt += 1
            spacing = self.host_spacing.get(host, 0.0)
            if spacing > 0 and self.throttle is not None:
                self.throttle(host, spacing)
            req_headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
            req_headers.update(headers or {})
            if etag:
                req_headers["If-None-Match"] = etag
            if last_modified:
                req_headers["If-Modified-Since"] = last_modified
            req_headers.update(self.auth_headers.get(host, {}))
            started = time.monotonic()
            try:
                req = self._http().build_request(
                    method, current_url, headers=req_headers, json=json_body,
                    timeout=httpx.Timeout(timeout_s) if timeout_s else None,
                )
                resp = self._http().send(req, stream=True)
                try:
                    body = self._read_capped(resp)
                finally:
                    resp.close()
            except (httpx.TransportError, HttpError) as e:
                duration = int((time.monotonic() - started) * 1000)
                will_retry = attempt <= self.max_retries and not (isinstance(e, HttpError) and e.status == -1)
                self._record(
                    method=method, host=host, path=sanitize_path(current_url), status=None,
                    outcome="retry" if will_retry else "error", attempt=attempt, duration_ms=duration, bytes=0,
                    rate_limit_remaining=None, error=f"{type(e).__name__}: {e}"[:500],
                )
                if will_retry:
                    self.sleep(backoff_delay(attempt))
                    continue
                raise HttpError(f"network error contacting {host}: {type(e).__name__}: {e}") from e

            duration = int((time.monotonic() - started) * 1000)
            rate = parse_rate_headers(resp.headers)
            self.last_rate = rate
            status = resp.status_code

            # Redirects: re-check every hop against policy; do not count as retries.
            if status in (301, 302, 303, 307, 308) and "location" in resp.headers:
                self._record(
                    method=method, host=host, path=sanitize_path(current_url), status=status, outcome="redirect",
                    attempt=attempt, duration_ms=duration, bytes=len(body), rate_limit_remaining=rate.remaining,
                    error="",
                )
                redirects += 1
                if redirects > self.max_redirects:
                    raise HttpError(f"too many redirects from {url}", status)
                current_url = str(httpx.URL(current_url).join(resp.headers["location"]))
                attempt -= 1
                if status == 303:
                    method, json_body = "GET", None
                continue

            if status == 304:
                self._record(
                    method=method, host=host, path=sanitize_path(current_url), status=304,
                    outcome="not_modified", attempt=attempt, duration_ms=duration, bytes=0,
                    rate_limit_remaining=rate.remaining, error="",
                )
                return HttpResult(status=304, url=current_url, headers=resp.headers, not_modified=True)

            rate_limited = status == 429 or (
                status == 403
                and (rate.remaining == 0 or b"rate limit" in body[:2000].lower() or rate.retry_after_s is not None)
            )
            if rate_limited:
                now = dt.datetime.now(dt.UTC)
                wait = rate.retry_after_s
                if wait is None and rate.reset_at is not None:
                    wait = max(0.0, (rate.reset_at - now).total_seconds())
                if wait is None:
                    wait = 60.0  # GitHub guidance for secondary limits without headers: wait >= 1 minute
                reset_at = now + dt.timedelta(seconds=wait)
                can_wait = attempt <= self.max_retries and wait <= self.max_inline_wait_s
                self._record(
                    method=method, host=host, path=sanitize_path(current_url), status=status,
                    outcome="retry" if can_wait else "rate_limited", attempt=attempt, duration_ms=duration,
                    bytes=len(body), rate_limit_remaining=rate.remaining, error=f"rate limited; wait {wait:.0f}s",
                )
                if can_wait:
                    self.sleep(wait + random.uniform(0, 1.0))
                    continue
                raise RateLimited(f"rate limited by {host} until {reset_at.isoformat()}", reset_at, status)

            if 500 <= status < 600:
                will_retry = attempt <= self.max_retries
                self._record(
                    method=method, host=host, path=sanitize_path(current_url), status=status,
                    outcome="retry" if will_retry else "error", attempt=attempt, duration_ms=duration,
                    bytes=len(body), rate_limit_remaining=rate.remaining, error=f"server error {status}",
                )
                if will_retry:
                    self.sleep(rate.retry_after_s if rate.retry_after_s is not None else backoff_delay(attempt))
                    continue
                raise HttpError(f"{host} returned {status} after {attempt} attempts", status)

            ok = 200 <= status < 300
            self._record(
                method=method, host=host, path=sanitize_path(current_url), status=status,
                outcome="ok" if ok else "error", attempt=attempt, duration_ms=duration, bytes=len(body),
                rate_limit_remaining=rate.remaining, error="" if ok else f"HTTP {status}",
            )
            result = HttpResult(status=status, url=current_url, headers=resp.headers, content=body)
            if not ok and raise_for_status:
                raise HttpError(f"{host} returned HTTP {status}", status)
            return result

    def get(self, url: str, **kw: Any) -> HttpResult:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> HttpResult:
        return self.request("POST", url, **kw)

    def _read_capped(self, resp: httpx.Response) -> bytes:
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            raise HttpError(f"response too large ({declared} bytes > {self.max_bytes})", -1)
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > self.max_bytes:
                raise HttpError(f"response exceeded {self.max_bytes} bytes", -1)
            chunks.append(chunk)
        return b"".join(chunks)
