from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from hub.api.app import create_app
from hub.config import Settings, reset_settings_cache
from hub.db import session_scope
from hub.net.lanaddr import client_allowed
from hub.security.auth import create_user

PW = "correct-horse-battery-staple"
H = {"X-Hub-Request": "1"}


@pytest.fixture()
def owner(db):
    with session_scope() as s:
        create_user(s, "owner@example.org", PW, role="owner", handle="owner")
    c = TestClient(create_app())
    assert c.post("/api/auth/login", json={"email": "owner@example.org", "password": PW}, headers=H).status_code == 200
    return c


def test_pairing_flow_single_use_and_device_management(owner) -> None:
    code = owner.post("/api/devices/pairing-codes", headers=H).json()["code"]
    assert len(code) == 11 and code[5] == "-"
    phone = TestClient(create_app())
    assert phone.get("/api/auth/me").status_code == 401
    r = phone.post("/api/auth/pair", headers=H, json={"code": code.lower(), "device_name": "Pixel 9"})
    assert r.status_code == 200, r.text
    assert "httponly" in r.headers["set-cookie"].lower() and "max-age=2592000" in r.headers["set-cookie"].lower()
    assert phone.get("/api/auth/me").json()["user"]["role"] == "owner"
    assert TestClient(create_app()).post("/api/auth/pair", headers=H, json={"code": code}).status_code == 401, "single use"
    devices = owner.get("/api/devices").json()
    pixel = next(d for d in devices if d["device_name"] == "Pixel 9")
    assert pixel["created_via"] == "pairing" and not pixel["current"]
    assert any(d["current"] for d in devices)
    assert owner.post(f"/api/devices/{pixel['id']}/revoke", headers=H).json()["revoked"] is True
    assert phone.get("/api/auth/me").status_code == 401, "revoked phone is logged out"


def test_pairing_code_expiry_and_replacement(owner) -> None:
    first = owner.post("/api/devices/pairing-codes", headers=H).json()["code"]
    second = owner.post("/api/devices/pairing-codes", headers=H).json()["code"]
    assert TestClient(create_app()).post("/api/auth/pair", headers=H, json={"code": first}).status_code == 401, \
        "issuing a new code invalidates the old one"
    with session_scope() as s:
        s.execute(text("UPDATE pairing_codes SET expires_at = now() - interval '1 minute'"))
    assert TestClient(create_app()).post("/api/auth/pair", headers=H, json={"code": second}).status_code == 401
    assert TestClient(create_app()).post("/api/auth/pair", headers=H, json={"code": "AAAAA-AAAAA"}).status_code == 401


def test_pairing_requires_login_and_csrf_header(owner) -> None:
    assert TestClient(create_app()).post("/api/devices/pairing-codes", headers=H).status_code == 401
    assert owner.post("/api/devices/pairing-codes").status_code == 403


def test_revoke_others_keeps_current(owner) -> None:
    code = owner.post("/api/devices/pairing-codes", headers=H).json()["code"]
    phone = TestClient(create_app())
    phone.post("/api/auth/pair", headers=H, json={"code": code, "device_name": "iPhone"})
    owner.post("/api/devices/revoke-others", headers=H)
    assert owner.get("/api/auth/me").status_code == 200
    assert phone.get("/api/auth/me").status_code == 401


def test_cannot_revoke_other_users_sessions(owner) -> None:
    with session_scope() as s:
        create_user(s, "member@example.org", PW, role="member", handle="member")
    m = TestClient(create_app())
    m.post("/api/auth/login", json={"email": "member@example.org", "password": PW}, headers=H)
    owner_session = next(d for d in owner.get("/api/devices").json() if d["current"])["id"]
    assert m.post(f"/api/devices/{owner_session}/revoke", headers=H).status_code == 404


def test_phone_access_off_by_default(owner) -> None:
    info = owner.get("/api/devices/phone-access").json()
    assert info["network_mode"] == "local" and info["enabled"] is False and "HUB_NETWORK_MODE" in info["instructions"]


@pytest.mark.parametrize("mode,ip,ok", [
    ("local", "127.0.0.1", True), ("local", "192.168.1.20", False), ("local", "203.0.113.9", False),
    ("lan", "192.168.1.20", True), ("lan", "10.0.0.7", True), ("lan", "fe80::1", True), ("lan", "8.8.8.8", False),
    ("lan", "100.101.1.2", False), ("tailnet", "100.101.1.2", True), ("tailnet", "fd7a:115c:a1e0::5", True),
    ("tailnet", "192.168.1.20", False), ("tailnet", "1.1.1.1", False),
])
def test_client_network_rules(mode: str, ip: str, ok: bool) -> None:
    assert client_allowed(ip, mode) is ok


def test_lan_mode_middleware_rejects_public_clients(db, monkeypatch) -> None:
    monkeypatch.setenv("HUB_NETWORK_MODE", "lan")
    reset_settings_cache()
    try:
        app = create_app()
        assert TestClient(app, client=("192.168.1.50", 50000)).get("/api/health").status_code == 200
        r = TestClient(app, client=("203.0.113.9", 50000)).get("/api/health")
        assert r.status_code == 403 and "not allowed" in r.json()["detail"]
    finally:
        monkeypatch.delenv("HUB_NETWORK_MODE")
        reset_settings_cache()


def test_local_mode_rejects_lan_clients_even_if_reachable(db) -> None:
    assert TestClient(create_app(), client=("192.168.1.50", 50000)).get("/api/health").status_code == 403


def test_exposure_rules() -> None:
    with pytest.raises(RuntimeError):
        Settings(bind_host="0.0.0.0").check_exposure()  # noqa: S104
    Settings(bind_host="0.0.0.0", network_mode="lan").check_exposure()  # noqa: S104
    with pytest.raises(RuntimeError):
        Settings(bind_host="0.0.0.0", network_mode="lan", registration_enabled=True).check_exposure()  # noqa: S104
    with pytest.raises(RuntimeError):
        Settings(tls_cert_file="cert.pem").check_exposure()
    with pytest.raises(ValueError):
        Settings(network_mode="internet")
    assert Settings(tls_cert_file="c.pem", tls_key_file="k.pem").scheme == "https"


def test_auth_rate_limit_per_client(db) -> None:
    c = TestClient(create_app())
    codes = [c.post("/api/auth/pair", headers=H, json={"code": f"BAD{i:02d}-XXXXX"}).status_code for i in range(12)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    r = c.post("/api/auth/login", headers=H, json={"email": "x@example.org", "password": "whatever-long-pw"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0


def test_pwa_files_headers(db, tmp_path, monkeypatch) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html></html>")
    (tmp_path / "sw.js").write_text("self.addEventListener('fetch', () => {});")
    (tmp_path / "manifest.webmanifest").write_text("{}")
    monkeypatch.setenv("HUB_FRONTEND_DIST", str(tmp_path))
    reset_settings_cache()
    try:
        c = TestClient(create_app())
        sw = c.get("/sw.js")
        assert sw.headers["service-worker-allowed"] == "/" and sw.headers["cache-control"] == "no-cache"
        assert sw.headers["content-type"].startswith("text/javascript")
        assert c.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
        assert c.get("/radar").headers["cache-control"] == "no-cache"
    finally:
        monkeypatch.delenv("HUB_FRONTEND_DIST")
        reset_settings_cache()


_ = dt


def test_documentation_ranges_not_treated_as_lan_unless_configured() -> None:
    assert client_allowed("203.0.113.9", "lan") is False
    assert client_allowed("192.0.2.2", "lan") is False
    assert client_allowed("192.0.2.2", "lan", extra_networks=["192.0.2.0/24"]) is True
    assert client_allowed("192.0.2.2", "local", extra_networks=["192.0.2.0/24"]) is False, "local mode ignores extras"
