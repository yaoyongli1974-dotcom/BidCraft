"""Network-target validation for server-side downloads.

Administrator-configured endpoints (model providers, knowledge bases and
plugins) may legitimately live on ``localhost`` or a private network.  URL
targets that arrive *from* those services, however, are not trusted: a
compromised provider could otherwise make the server fetch internal metadata
or loopback services (SSRF).  :func:`is_private_url` performs that second-hop
check.  All errors carry user-facing Chinese text.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})


class NetworkError(ValueError):
    """Raised when a URL points at a target that must not be fetched."""


def _is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _parse_ip(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def resolve_host_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve ``host`` to every address it maps to; raise on failure."""

    address = _parse_ip(host)
    if address is not None:
        return [address]
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise NetworkError(f"无法解析跳转地址的域名：{host}") from exc
    resolved: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        raw = info[4][0]
        address = _parse_ip(raw)
        if address is not None and address not in resolved:
            resolved.append(address)
    if not resolved:
        raise NetworkError(f"无法解析跳转地址的域名：{host}")
    return resolved


def is_private_url(url: str) -> bool:
    """Return True when ``url`` is malformed or resolves to an internal host.

    Scheme and hostname are validated first, so a ``True`` result always means
    the target must be refused.  A hostname that cannot be resolved is also
    refused because its reachability cannot be audited.
    """

    parsed = urlparse(url or "")
    if parsed.scheme.lower() not in ALLOWED_SCHEMES or not parsed.hostname:
        raise NetworkError("跳转地址不合法，仅支持 http/https")
    host = parsed.hostname
    if _parse_ip(host) is None and host.lower() == "localhost":
        return True
    return any(_is_blocked(address) for address in resolve_host_ips(host))


def check_redirect_target(url: str) -> None:
    """Reject a redirect target that points to a private/internal address."""

    if is_private_url(url):
        raise NetworkError("跳转地址指向内网或本机，已拒绝下载")
