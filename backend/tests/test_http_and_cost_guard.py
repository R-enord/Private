from __future__ import annotations

import datetime as dt

import httpx
import pytest
from pydantic import ValidationError

from hub.config import Settings
from hub.net import cost_guard
from hub.net.cost_guard import CostPolicyViolation, EgressDenied
from hub.net.http import HttpError, RateLimited, SafeHttpClient, parse_rate_headers
from hub.net.throttle import reserve_slot
from tests.conftest import Recorder, no_sleep


def make_client(rec: Recorder, hosts: set[str], **kw) -> SafeHttpClient:
    return SafeHttpClient(allowed_hosts=hosts, user_agent="test-agent", transport=rec.transport, sleep=no_sleep, **kw)


# ----------------------------------------------------------------- zero-cost mode


def test_zero_cost_mode_cannot_be_disabled() -> None:
    with pytest.raises(ValidationError):
        Settings(zero_cost_mode=False)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1/chat/completions",
        "https://api.anthropic.com/v1/messages",
        "https://generativelanguage.googleapis.com/v1beta/models",
        "https://openrouter.ai/api/v1/chat/completions",
        "https://api.groq.com/openai/v1/chat/completions",
        "https://router.huggingface.co/v1/chat/completions",
        "https://ollama.com/api/chat",
        "https://myres.openai.azure.com/openai/deployments/x",
        "https://bedrock-runtime.us-east-1.amazonaws.com/model/x/invoke",
        "https://huggingface.co/api/inference-proxy/together/v1/chat",
    ],
)
def test_paid_endpoints_blocked_before_network(url: str) -> None:
    rec = Recorder(lambda r: httpx.Response(200, json={"ok": True}))
    host = httpx.URL(url).host
    # Even if a misconfiguration put the paid host on the allowlist, the cost guard wins.
    client = make_client(rec, {host})
    with pytest.raises(CostPolicyViolation):
        client.get(url)
    assert rec.requests == [], "no bytes may reach the network for a paid endpoint"
    assert client.log[-1]["outcome"] == "blocked"
    assert client.log[-1]["error"].startswith("cost_policy")


def test_free_hosts_not_flagged_as_paid() -> None:
    for h in ("api.github.com", "huggingface.co", "export.arxiv.org", "hacker-news.firebaseio.com",
              "blog.cloudflare.com", "aws.amazon.com", "s3.amazonaws.com"):
        assert not cost_guard.is_paid_host(h), h


def test_ollama_cloud_models_refused() -> None:
    for m in ("gpt-oss:120b-cloud", "qwen3-coder:480b-cloud", "deepseek-v4:cloud", "cloud"):
        with pytest.raises(CostPolicyViolation):
            cost_guard.check_local_model_name(m)
    for m in ("qwen2.5:1.5b", "llama3.2:3b", "all-minilm:latest", "cloudy-model-test:7b"):
        cost_guard.check_local_model_name(m)


def test_no_cloud_provider_is_verified_free() -> None:
    assert cost_guard.VERIFIED_FREE_TIER_POLICIES == {}
    with pytest.raises(CostPolicyViolation):
        cost_guard.check_request("api.example-free-llm.com", "/v1", verified_policy_key="openrouter")


# ----------------------------------------------------------------- egress policy


def test_undeclared_host_blocked() -> None:
    rec = Recorder(lambda r: httpx.Response(200))
    client = make_client(rec, {"api.github.com"})
    with pytest.raises(EgressDenied):
        client.get("https://evil.example.com/x")
    assert rec.requests == []


@pytest.mark.parametrize(
    "url",
    ["http://api.github.com/x", "ftp://api.github.com/x", "https://user:pw@api.github.com/x"],
)
def test_scheme_and_credentials_rules(url: str) -> None:
    rec = Recorder(lambda r: httpx.Response(200))
    with pytest.raises(EgressDenied):
        make_client(rec, {"api.github.com"}).get(url)
    assert rec.requests == []


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "localhost", "db.internal"])
def test_ssrf_private_targets_blocked(host: str) -> None:
    rec = Recorder(lambda r: httpx.Response(200))
    with pytest.raises(EgressDenied):
        make_client(rec, {host}).get(f"https://{host}/latest/meta-data")
    assert rec.requests == []


def test_redirect_to_disallowed_host_blocked() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://169.254.169.254/latest/meta-data"})

    rec = Recorder(handler)
    client = make_client(rec, {"feeds.example.org", "169.254.169.254"})
    with pytest.raises(EgressDenied):
        client.get("https://feeds.example.org/rss")
    assert len(rec.requests) == 1  # the redirect target was never contacted


def test_redirect_within_allowed_hosts_followed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return httpx.Response(200, text="ok")

    rec = Recorder(handler)
    res = make_client(rec, {"feeds.example.org"}).get("https://feeds.example.org/old")
    assert res.status == 200 and res.text == "ok" and len(rec.requests) == 2


def test_auth_header_only_sent_to_its_host_and_never_logged() -> None:
    seen: dict[str, str | None] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen[req.url.host] = req.headers.get("authorization")
        return httpx.Response(200, json={})

    rec = Recorder(handler)
    client = make_client(rec, {"api.github.com", "huggingface.co"},
                         auth_headers={"api.github.com": {"Authorization": "Bearer SECRET123"}})
    client.get("https://api.github.com/rate_limit")
    client.get("https://huggingface.co/api/models")
    assert seen["api.github.com"] == "Bearer SECRET123"
    assert seen["huggingface.co"] is None
    assert "SECRET123" not in repr(client.log)


# ----------------------------------------------------------------- retries / rate limits


def test_retries_server_errors_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, json={"ok": 1})

    rec = Recorder(handler)
    sleeps: list[float] = []
    client = SafeHttpClient(allowed_hosts={"x.example.org"}, user_agent="t", transport=rec.transport,
                            sleep=sleeps.append, max_retries=3)
    assert client.get("https://x.example.org/a").json() == {"ok": 1}
    assert calls["n"] == 3 and len(sleeps) == 2 and client.retry_count == 2


def test_gives_up_after_max_retries() -> None:
    rec = Recorder(lambda r: httpx.Response(500))
    client = make_client(rec, {"x.example.org"}, max_retries=2)
    with pytest.raises(HttpError):
        client.get("https://x.example.org/a")
    assert len(rec.requests) == 3


def test_network_errors_retried() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=req)
        return httpx.Response(200, text="fine")

    assert make_client(Recorder(handler), {"x.example.org"}).get("https://x.example.org/").text == "fine"


def test_429_with_short_retry_after_waits_and_retries() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "2"}) if calls["n"] == 1 else httpx.Response(200)

    sleeps: list[float] = []
    rec = Recorder(handler)
    client = SafeHttpClient(allowed_hosts={"x.example.org"}, user_agent="t", transport=rec.transport, sleep=sleeps.append)
    assert client.get("https://x.example.org/").status == 200
    assert sleeps and 2 <= sleeps[0] <= 3


def test_github_primary_rate_limit_raises_with_reset() -> None:
    reset = int((dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)).timestamp())

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
                              json={"message": "API rate limit exceeded"})

    rec = Recorder(handler)
    with pytest.raises(RateLimited) as ei:
        make_client(rec, {"api.github.com"}).get("https://api.github.com/search/repositories")
    assert ei.value.reset_at is not None
    assert abs(ei.value.reset_at.timestamp() - reset) < 5
    assert len(rec.requests) == 1  # long waits are deferred to the scheduler, not slept inline


def test_parse_hf_ratelimit_header() -> None:
    now = dt.datetime(2026, 11, 22, tzinfo=dt.UTC)
    info = parse_rate_headers(httpx.Headers({"ratelimit": '"api";r=485;t=257'}), now)
    assert info.remaining == 485
    assert info.reset_at == now + dt.timedelta(seconds=257)


def test_conditional_request_304() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["if-none-match"] == '"abc"'
        assert req.headers["if-modified-since"] == "Sat, 21 Nov 2026 10:00:00 GMT"
        return httpx.Response(304)

    res = make_client(Recorder(handler), {"x.example.org"}).get(
        "https://x.example.org/feed", etag='"abc"', last_modified="Sat, 21 Nov 2026 10:00:00 GMT"
    )
    assert res.not_modified and res.status == 304


def test_response_size_cap() -> None:
    rec = Recorder(lambda r: httpx.Response(200, content=b"x" * 5000))
    with pytest.raises(HttpError):
        make_client(rec, {"x.example.org"}, max_bytes=1000).get("https://x.example.org/big")
    assert len(rec.requests) == 1  # oversize responses are not retried


def test_user_agent_is_generic() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["ua"] = req.headers["user-agent"]
        return httpx.Response(200)

    s = Settings()
    SafeHttpClient(allowed_hosts={"x.example.org"}, user_agent=s.user_agent, transport=Recorder(handler).transport).get(
        "https://x.example.org/"
    )
    assert "@" not in seen["ua"]


def test_host_throttle_spaces_requests(db) -> None:
    w1 = reserve_slot(db, "export.arxiv.org", 3.0)
    w2 = reserve_slot(db, "export.arxiv.org", 3.0)
    w3 = reserve_slot(db, "export.arxiv.org", 3.0)
    assert w1 == pytest.approx(0, abs=0.5)
    assert w2 == pytest.approx(3.0, abs=0.5)
    assert w3 == pytest.approx(6.0, abs=0.5)
