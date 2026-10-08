"""Outbound fetch guard for user-supplied URLs (SSRF protection, V2).

Rules for every request *and every redirect hop*:

- scheme http/https only, default ports (80/443) unless ``DAEDELUS_FETCH_ALLOW_PORTS`` adds;
- no credentials in the URL;
- the host must resolve only to public addresses: loopback, private (RFC 1918 / ULA),
  link-local (incl. cloud metadata 169.254.169.254), CGNAT, multicast, reserved and
  unspecified ranges are refused, including IPv4-mapped IPv6 forms;
- optional allowlist ``DAEDELUS_FETCH_ALLOWLIST`` (comma-separated domains; subdomains match);
- at most 3 redirects, response body capped (default 10 MB).

Residual risk (documented in SECURITY_MODEL.md): the address is checked before the
connection; a DNS answer that changes between check and connect (rebinding) is not pinned
when an HTTP(S) proxy performs the resolution.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

MAX_BYTES = int(os.environ.get("DAEDELUS_FETCH_MAX_MB", "10")) * 1024 * 1024
CGNAT = ipaddress.ip_network("100.64.0.0/10")


class FetchRefused(Exception):
    pass


def _bad_ip(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or
            ip.is_reserved or ip.is_unspecified or (isinstance(ip, ipaddress.IPv4Address)
                                                    and ip in CGNAT))


def check_url(url: str, resolve=socket.getaddrinfo) -> str:
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise FetchRefused(f"scheme '{u.scheme}' not allowed")
    if u.username or u.password:
        raise FetchRefused("credentials in URLs are not allowed")
    host = (u.hostname or "").rstrip(".").lower()
    if not host:
        raise FetchRefused("URL has no host")
    ports = {80, 443} | {int(p) for p in os.environ.get("DAEDELUS_FETCH_ALLOW_PORTS", "")
                         .split(",") if p.strip().isdigit()}
    port = u.port or (443 if u.scheme == "https" else 80)
    if port not in ports:
        raise FetchRefused(f"port {port} not allowed")
    allow = [d.strip().lower() for d in os.environ.get("DAEDELUS_FETCH_ALLOWLIST", "")
             .split(",") if d.strip()]
    if allow and not any(host == d or host.endswith("." + d) for d in allow):
        raise FetchRefused(f"host '{host}' is not on the fetch allowlist")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        addrs = [literal]
    else:
        try:
            infos = resolve(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise FetchRefused(f"cannot resolve '{host}': {exc}") from None
        addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    bad = [str(a) for a in addrs if _bad_ip(a)]
    if bad or not addrs:
        raise FetchRefused(f"'{host}' resolves to a non-public address ({', '.join(bad)})")
    return url


def safe_get(client, url: str, *, max_redirects: int = 3, max_bytes: int = MAX_BYTES):
    """GET with per-hop validation; ``client`` must have follow_redirects disabled."""
    for _ in range(max_redirects + 1):
        check_url(url)
        with client.stream("GET", url, follow_redirects=False) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            body = bytearray()
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise FetchRefused(f"response larger than {max_bytes} bytes")
            r._content = bytes(body)  # make .text/.json usable after streaming
            return r
    raise FetchRefused("too many redirects")
