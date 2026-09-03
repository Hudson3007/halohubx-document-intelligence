"""Webhook URL validation (Server-Side Request Forgery guard).

Any URL the API is told to POST to (upload form webhook_url, a partner's
default_webhook_url) is a potential SSRF vector: a partner or a compromised
customer could point it at internal services (localhost, the Postgres port,
the metadata service at 169.254.169.254, or other RFC1918 hosts) and use the
API as a proxy.

This guard is enforced at the single point of delivery (app/webhooks.py) so it
cannot be bypassed by however the URL got set. It:

  * requires https (http allowed only for loopback, which is how local dev
    runs the demo ERP), controlled by ALLOW_HTTP_LOOPBACK_WEBHOOKS
  * rejects loopback / link-local / private / reserved ranges, after DNS
    resolution, covering both A and IPv6 _ranges (::1, fe80:: etc.) and the
    standard cloud metadata endpoints.

It raises ValueError(xxx) so callers can map it to a 400 "unsafe webhook URL".
"""

import ipaddress
import socket
from urllib.parse import urlparse

from app.config import ALLOW_HTTP_LOOPBACK_WEBHOOKS

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}

# RFC1918 + CGNAT + link-local + loopback + documentation/reserved nets.
_PRIVATE_NETS = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",   # link-local (cloud metadata often lives here)
    "127.0.0.0/8",      # loopback
    "0.0.0.0/8",        # "this network"
    "100.64.0.0/10",    # CGNAT
    "192.0.0.0/24",     # IETF protocol assignments
    "198.18.0.0/15",    # benchmarking
    "224.0.0.0/4",      # multicast
    "240.0.0.0/4",      # reserved
    "::1/128",          # IPv6 loopback
    "fc00::/7",         # IPv6 unique local
    "fe80::/10",        # IPv6 link-local
]

_PRIVATE_RANGES = tuple(ipaddress.ip_network(n) for n in _PRIVATE_NETS)


def _is_dangerous(ip: ipaddress._BaseAddress) -> bool:
    """True if the address is loopback, link-local, private, or non-global."""
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
        return True
    if ip.is_global is False:
        return True
    for net in _PRIVATE_RANGES:
        if ip in net:
            return True
    return False


def _host_ipv4_and_v6(hostname: str) -> list[ipaddress._BaseAddress]:
    """Resolve a hostname to all IPs (v4 + v6). Raises ValueError on DNS fail."""
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as e:
        raise ValueError(f"Could not resolve webhook host: {hostname} ({e})") from e
    addrs: list[ipaddress._BaseAddress] = []
    for info in infos:
        ip = info[4][0]
        addrs.append(ipaddress.ip_address(ip))
    return addrs


def validate_webhook_url(url: str) -> str:
    """Return the url if it is a safe webhook target, else raise ValueError.

    Loopback is rejected by default (defence in depth). For local dev an
    operator can set ALLOW_HTTP_LOOPBACK_WEBHOOKS=true to allow http://127.0.0.1
    / http://localhost only — still never private ranges/metadata.
    """
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        raise ValueError("Webhook URL must use http:// or https://")

    parts = urlparse(url)
    host = parts.hostname or ""
    host = host.rstrip(".").lower()

    if not host:
        raise ValueError("Webhook URL has no host.")

    is_loopback_host = host in _LOOPBACK_NAMES
    scheme_ok = parts.scheme == "https" or (
        parts.scheme == "http" and ALLOW_HTTP_LOOPBACK_WEBHOOKS and is_loopback_host
    )
    if not scheme_ok:
        raise ValueError("Webhook URL must use https://")

    if is_loopback_host and not ALLOW_HTTP_LOOPBACK_WEBHOOKS:
        raise ValueError("Webhook URL resolves to loopback, which is not allowed.")

    # Validate the explicit IP (literal) or the resolved host.
    raw_ip: ipaddress._BaseAddress | None = None
    try:
        raw_ip = ipaddress.ip_address(host)
    except ValueError:
        raw_ip = None

    candidate_ips: list[ipaddress._BaseAddress] = []
    if raw_ip is not None:
        candidate_ips = [raw_ip]
    else:
        candidate_ips = _host_ipv4_and_v6(host)

    for ip in candidate_ips:
        if _is_dangerous(ip):
            raise ValueError(f"Webhook URL resolves to a non-public address: {ip}")

    return url