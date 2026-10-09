"""Outbound fetch guard for user-supplied URLs (SSRF protection, V2).

Rules for every request *and every redirect hop*:

- scheme http/https only, default ports (80/443) unless ``DAEDELUS_FETCH_ALLOW_PORTS`` adds;
- no credentials in the URL;
- the host must resolve only to public addresses: loopback, private (RFC 1918 / ULA),
  link-local (incl. cloud metadata 169.254.169.254), CGNAT, multicast, reserved and
  unspecified ranges are refused, including IPv4-mapped IPv6 forms;
- optional allowlist ``DAEDELUS_FETCH_ALLOWLIST`` (comma-separated domains; subdomains match);
- at most 3 redirects, response body capped (default 10 MB).

V2.1 closes the DNS time-of-check/time-of-use gap for direct connections: ``pinned_client``
resolves the host *inside the connection step*, validates every returned address and connects
to a validated address (TLS still verifies the original hostname via SNI). A rebinding DNS
server can no longer answer "public" to the check and "private" to the connect, because there
is only one resolution. Numeric host spellings that resolvers accept (``2130706433``,
``0x7f.1``, ``0177.0.0.1``, ``127.1``) are normalised and refused; IPv6 forms embedding IPv4
(IPv4-mapped, NAT64 ``64:ff9b::/96``, 6to4 ``2002::/16``) are checked against the embedded
address.

Residual risk: when outbound traffic must go through an HTTP(S) proxy, the proxy resolves the
name and the address cannot be pinned here. Outside the hosted profile a configured proxy is
used (the proxy must enforce egress policy); the hosted profile uses a proxy only with
``DAEDELUS_FETCH_VIA_PROXY=1``.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urljoin, urlparse

MAX_BYTES = int(os.environ.get("DAEDELUS_FETCH_MAX_MB", "10")) * 1024 * 1024
CGNAT = ipaddress.ip_network("100.64.0.0/10")
NAT64 = ipaddress.ip_network("64:ff9b::/96")
NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
SIXTO4 = ipaddress.ip_network("2002::/16")
EXTRA_BAD = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "240.0.0.0/4", "255.255.255.255/32", "fc00::/7", "fe80::/10",
    "100::/64", "2001:db8::/32")]


class FetchRefused(Exception):
    pass


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip in NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.sixtofour:
        return ip.sixtofour
    if ip.teredo:
        return ip.teredo[1]
    return None


def _bad_ip(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip in NAT64_LOCAL:
            return True
        v4 = _embedded_v4(ip)
        if v4 is not None:
            return _bad_ip(v4)
    if any(ip.version == n.version and ip in n for n in EXTRA_BAD):
        return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or
            ip.is_reserved or ip.is_unspecified or not ip.is_global or
            (isinstance(ip, ipaddress.IPv4Address) and ip in CGNAT))


def _numeric_host(host: str) -> ipaddress._BaseAddress | None:
    """Interpret every numeric spelling a resolver would accept (inet_aton rules)."""
    h = host.strip("[]")
    try:
        return ipaddress.ip_address(h.split("%")[0])
    except ValueError:
        pass
    parts = h.split(".")
    if not parts or len(parts) > 4:
        return None
    nums = []
    for part in parts:
        try:
            if part.lower().startswith("0x"):
                nums.append(int(part, 16))
            elif len(part) > 1 and part.startswith("0"):
                nums.append(int(part, 8))
            else:
                nums.append(int(part, 10))
        except ValueError:
            return None
    limits = {1: [2**32], 2: [256, 2**24], 3: [256, 256, 2**16], 4: [256] * 4}[len(nums)]
    if any(n < 0 or n >= lim for n, lim in zip(nums, limits)):
        return None
    val = 0
    for n in nums[:-1]:
        val = val * 256 + n
    val = (val << (8 * (4 - len(nums) + 1))) + nums[-1] if len(nums) > 1 else nums[0]
    return ipaddress.IPv4Address(val)


def resolve_checked(host: str, port: int, resolve=socket.getaddrinfo) -> list[str]:
    """Resolve once and return only if every address is public (else FetchRefused)."""
    literal = _numeric_host(host)
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
    return [str(a) for a in addrs]


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
    resolve_checked(host, port, resolve)
    return url


def proxy_configured() -> bool:
    return any(os.environ.get(v) for v in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                                           "http_proxy", "ALL_PROXY", "all_proxy"))


def use_proxy() -> bool:
    from . import profile
    if not proxy_configured():
        return False
    return not profile.hosted() or profile.flag("DAEDELUS_FETCH_VIA_PROXY")


def pinned_backend(resolve=socket.getaddrinfo):
    """httpcore network backend that resolves, validates and connects in one step."""
    import httpcore

    class PinnedBackend(httpcore.SyncBackend):
        def connect_tcp(self, host, port, timeout=None, local_address=None,
                        socket_options=None):
            last: Exception | None = None
            for ip in resolve_checked(host, port, resolve):
                try:
                    return super().connect_tcp(ip, port, timeout, local_address, socket_options)
                except Exception as exc:  # try the next validated address
                    last = exc
            raise last or FetchRefused(f"cannot connect to {host}")
    return PinnedBackend()


def pinned_client(*, timeout: float = 15.0, verify=True, headers=None, resolve=None):
    """httpx client for user-supplied URLs: no redirects followed, addresses pinned, or the
    configured egress proxy when one must be used (see module docstring)."""
    import httpcore
    import httpx

    if use_proxy():
        return httpx.Client(timeout=timeout, follow_redirects=False, verify=verify,
                            headers=headers)
    t = httpx.HTTPTransport(verify=verify, trust_env=False, retries=0)
    ssl_ctx = t._pool._ssl_context  # the context httpx built from ``verify``
    t._pool = httpcore.ConnectionPool(ssl_context=ssl_ctx, max_connections=10,
                                      network_backend=pinned_backend(resolve or
                                                                     socket.getaddrinfo))
    return httpx.Client(transport=t, timeout=timeout, follow_redirects=False, headers=headers,
                        trust_env=False)


def safe_get(client, url: str, *, max_redirects: int = 3, max_bytes: int = MAX_BYTES,
             resolve=socket.getaddrinfo):
    """GET with per-hop validation; ``client`` must have follow_redirects disabled (use
    ``pinned_client``: the connection itself is then pinned to a validated address)."""
    for _ in range(max_redirects + 1):
        check_url(url, resolve=resolve)
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
