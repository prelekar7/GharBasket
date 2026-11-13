"""The caller's address (M0 §6.3, handbook 13 §13.3): per-address rate limits use only this.

uvicorn runs with `--no-proxy-headers`, so `scope["client"]` is the socket peer (on Render, its
proxy), never a value from a header. The address comes from one of two paths:

- **Our own proxy** (the PWA's Vercel `/v1/*` route): it sets `x-origin-secret` to PROXY_SECRET,
  replacing anything the visitor sent. With that exact secret, the address is the one header
  PROXY_CLIENT_IP_HEADER (`x-real-ip`), which that proxy sets. A request without the secret, or
  with a wrong one, never reads that header.
- **Everything else**, by CLIENT_IP_SOURCE:
  - `peer` (the default; local runs and tests): the socket peer.
  - `header:<name>`: that one header, from a proxy that replaces whatever the caller sent
    (`true-client-ip`, which Cloudflare in front of Render sets).
  - `xff:<n>`: the n-th X-Forwarded-For entry from the right; 1 is the entry the nearest proxy
    appended. Every entry left of the proxies' own is the caller's to write, so n is the number of
    proxies that append, which has to be measured for each host; it is not assumed for Render.

A header that is missing, or is not one IPv4 or IPv6 address, gives the next source (the proxy
path falls back to CLIENT_IP_SOURCE, which falls back to the socket peer). Behind a proxy all such
requests share the proxy's counter, which limits more, never less, and `client_ip_fallback` is
logged at most once a minute per source and reason. No header value or secret is ever logged.
"""

import hmac
import ipaddress
import time

import structlog
from starlette.requests import HTTPConnection
from starlette.types import Scope

from gharbasket.platform.settings import Settings, get_settings

log = structlog.get_logger("gharbasket.client_ip")

PROXY_SECRET_HEADER = b"x-origin-secret"
FALLBACK_LOG_EVERY_S = 60.0
_logged_at: dict[tuple[str, str], float] = {}


def _header(scope: Scope, name: bytes) -> bytes | None:
    """Every value of header `name`, joined with ", " in order as uvicorn does (ASGI servers give
    the names in lower case)."""
    values = [value for key, value in scope.get("headers") or () if key == name]
    return b", ".join(values) if values else None


def _ip(value: bytes | str | None) -> str | None:
    """`value` as one normalised address: IPv4-mapped IPv6 as IPv4, IPv6 compressed and without a
    zone; None for anything else (ports, brackets, lists, names)."""
    if value is None:
        return None
    try:
        text = value.decode("latin-1") if isinstance(value, bytes) else value
        ip = ipaddress.ip_address(text.strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        return str(ip.ipv4_mapped or ipaddress.IPv6Address(int(ip)))
    return str(ip)


def _fallback(source: str, raw: bytes | None) -> None:
    """Log that `source` gave no address, at most once a minute per source and reason."""
    reason = "missing" if raw is None else "malformed"
    now = time.monotonic()
    last = _logged_at.get((source, reason))
    if last is None or now - last >= FALLBACK_LOG_EVERY_S:
        _logged_at[(source, reason)] = now
        log.warning("client_ip_fallback", source=source, reason=reason)


def _from_header(scope: Scope, source: str, name: bytes) -> str | None:
    raw = _header(scope, name)
    found = _ip(raw)
    if found is None:
        _fallback(source, raw)
    return found


def _from_source(scope: Scope, source: str) -> str | None:
    if source.startswith("header:"):
        return _from_header(scope, source, source.removeprefix("header:").encode())
    if source.startswith("xff:"):
        raw, hop = _header(scope, b"x-forwarded-for"), int(source.removeprefix("xff:"))
        entries = raw.split(b",") if raw is not None else []
        found = _ip(entries[-hop]) if len(entries) >= hop else None
        if found is None:
            _fallback(source, raw if len(entries) >= hop else None)
        return found
    return None  # peer


def _from_our_proxy(scope: Scope, settings: Settings) -> bool:
    """The request carries PROXY_SECRET in `x-origin-secret` (one value, compared in constant
    time)."""
    if settings.proxy_secret is None:
        return False
    values = [value for key, value in scope.get("headers") or () if key == PROXY_SECRET_HEADER]
    expected = settings.proxy_secret.get_secret_value().encode()
    return len(values) == 1 and hmac.compare_digest(values[0], expected)


def from_scope(scope: Scope, settings: Settings) -> str:
    """The caller's address: from our proxy's header, else by CLIENT_IP_SOURCE, else the peer."""
    found = None
    if _from_our_proxy(scope, settings):
        header = settings.proxy_client_ip_header
        found = _from_header(scope, f"proxy:{header}", header.encode())
    if found is None:
        found = _from_source(scope, settings.client_ip_source)
    if found is not None:
        return found
    peer = scope.get("client")
    host = str(peer[0]) if peer else ""
    return _ip(host) or host or "unknown"


def client_address(conn: HTTPConnection) -> str:
    return from_scope(conn.scope, get_settings())


def bucket(address: str) -> str:
    """An address as a rate-limit key part.

    An IPv6 address counts as its /64. Networks hand one subscriber a whole /64 (a home line, and
    each phone on a mobile network), and its devices pick their own addresses in it and change them
    often (privacy extensions), so per address one caller would get a fresh counter at will. A /64
    is the closest IPv6 match to one IPv4 address. Only the rate-limit key is bucketed:
    `client_address` keeps the full address."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return address


def limit_key(conn: HTTPConnection) -> str:
    """The caller's rate-limit key: the address, IPv6 by /64."""
    return bucket(client_address(conn))
