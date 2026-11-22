"""Local network helpers for phone access: which client addresses are allowed and how a phone reaches us."""

from __future__ import annotations

import ipaddress
import socket

TAILNET_NETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
# Explicit home/office network ranges (RFC 1918, link-local, IPv6 ULA + link-local). Deliberately NOT
# ipaddress.is_private, which also includes documentation/benchmark ranges.
LAN_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7", "fe80::/10"))


def client_allowed(host: str | None, mode: str, public_approved: bool = False,
                   extra_networks: list[str] | tuple[str, ...] = ()) -> bool:
    """Loopback always; RFC1918/link-local in 'lan'; Tailscale ranges in 'tailnet'; explicitly configured extra
    networks in lan/tailnet; anything else only with public approval."""
    if public_approved:
        return True
    if not host:
        return False
    if host == "testclient":  # Starlette TestClient
        return True
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    if mode == "local":
        return False
    if any(ip in ipaddress.ip_network(n, strict=False) for n in extra_networks):
        return True
    nets = LAN_NETS if mode == "lan" else TAILNET_NETS
    return any(ip in n for n in nets)


def local_ips() -> list[str]:
    """Best-effort list of this machine's non-loopback IPv4 addresses (no packets are sent)."""
    ips: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(str(info[4][0]))
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # UDP connect only selects a route; nothing is transmitted
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def effective_allowed_hosts(base: list[str], extra: list[str], mode: str) -> list[str]:
    hosts = [h.lower() for h in [*base, *extra]]
    if mode in ("lan", "tailnet"):
        hosts += local_ips()
    return sorted(set(hosts))


def phone_urls(mode: str, extra: list[str], scheme: str, port: int) -> list[str]:
    if mode == "local":
        return [f"https://{h}" for h in extra if h.endswith(".ts.net")]  # e.g. via `tailscale serve`
    hosts = [*extra, *local_ips()]
    out = []
    for h in hosts:
        if h.endswith(".ts.net"):
            out.append(f"https://{h}")
        else:
            out.append(f"{scheme}://{h}:{port}")
    return list(dict.fromkeys(out))
