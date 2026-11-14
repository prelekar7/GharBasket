"""The caller's address (M0 §6.3): our proxy's header behind its secret, CLIENT_IP_SOURCE, values
a caller can forge, IPv6 buckets, startup validation, and the per-address limits keyed by it."""

import re
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx2
import pytest
import structlog
from pydantic import ValidationError
from redis.asyncio import Redis

from gharbasket.main import create_app
from gharbasket.modules.identity import router as identity_router
from gharbasket.platform import client_ip, rate_limit
from gharbasket.platform.client_ip import bucket, from_scope
from gharbasket.platform.db import get_session

from .commit_guard import CommitGuard
from .conftest import API_DIR, DEPLOYED_SECRETS, make_settings
from .identity.conftest import valkey  # noqa: F401

PEER = "10.20.0.5"  # a proxy, as the socket sees it
SECRET = "proxy-secret-4b1e-0123456789abcdef0123456789"
VERCEL = "76.76.21.9"  # the proxy's own egress, as Cloudflare would see it


def scope(*headers: tuple[str, str], peer: str | None = PEER) -> dict[str, Any]:
    return {
        "type": "http",
        "headers": [(name.lower().encode(), value.encode("latin-1")) for name, value in headers],
        "client": (peer, 41234) if peer is not None else None,
    }


def address(request: dict[str, Any], source: str = "peer", **settings: Any) -> str:
    return from_scope(request, make_settings(client_ip_source=source, **settings))


@pytest.fixture(autouse=True)
def _fresh_fallback_log() -> Iterator[None]:
    client_ip._logged_at.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    client_ip._logged_at.clear()  # pyright: ignore[reportPrivateUsage]


# --- CLIENT_IP_SOURCE ----------------------------------------------------------------------------


def test_peer_is_the_socket_and_ignores_every_header() -> None:
    forged = scope(("X-Forwarded-For", "1.1.1.1"), ("True-Client-IP", "2.2.2.2"))
    assert address(forged) == PEER
    assert address(scope(peer=None)) == "unknown"
    assert address(scope(peer="testclient")) == "testclient"


def test_header_reads_that_one_header_only() -> None:
    request = scope(("X-Forwarded-For", "1.1.1.1"), ("True-Client-IP", " 203.0.113.7 "))
    assert address(request, "header:true-client-ip") == "203.0.113.7"
    assert address(request, "header:cf-connecting-ip") == PEER  # absent: the peer


def test_xff_counts_from_the_right() -> None:
    request = scope(("X-Forwarded-For", "198.51.100.1, 203.0.113.7, 10.1.1.1"))
    assert address(request, "xff:1") == "10.1.1.1"
    assert address(request, "xff:2") == "203.0.113.7"
    assert address(request, "xff:3") == "198.51.100.1"
    assert address(request, "xff:4") == PEER  # fewer entries than n


def test_xff_ignores_what_the_caller_wrote_left_of_the_proxies() -> None:
    # One proxy that appends (n = 1 here; the right n is measured for each host): the caller
    # sent "X-Forwarded-For: 6.6.6.6", the proxy appended the address it saw.
    spoofed = scope(("X-Forwarded-For", "6.6.6.6, 203.0.113.7"))
    plain = scope(("X-Forwarded-For", "203.0.113.7"))
    assert address(spoofed, "xff:1") == address(plain, "xff:1") == "203.0.113.7"


def test_repeated_xff_headers_are_joined_in_order() -> None:
    request = scope(("X-Forwarded-For", "6.6.6.6"), ("X-Forwarded-For", "203.0.113.7"))
    assert address(request, "xff:1") == "203.0.113.7"
    assert address(request, "xff:2") == "6.6.6.6"


MALFORMED = [
    "",
    " ",
    "unknown",
    "203.0.113.7:443",
    "[2001:db8::1]",
    "[2001:db8::1]:443",
    "203.0.113.7, 198.51.100.1",
    "203.0.113.256",
    "01.2.3.4",
    "example.com",
    "203.0.113.7\x00",
    "9" * 5000,
]


@pytest.mark.parametrize("value", MALFORMED)
def test_a_malformed_value_gives_the_peer(value: str) -> None:
    assert address(scope(("True-Client-IP", value)), "header:true-client-ip") == PEER
    if "," not in value:  # a list's last entry would be read
        assert address(scope(("X-Forwarded-For", f"1.1.1.1, {value}")), "xff:1") == PEER


def test_a_header_sent_twice_gives_the_peer() -> None:
    twice = scope(("True-Client-IP", "203.0.113.7"), ("True-Client-IP", "6.6.6.6"))
    assert address(twice, "header:true-client-ip") == PEER


@pytest.mark.parametrize(
    ("raw", "normalised"),
    [
        ("2001:DB8:0:0::0001", "2001:db8::1"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("fe80::1%eth0", "fe80::1"),
    ],
)
def test_addresses_are_normalised(raw: str, normalised: str) -> None:
    assert address(scope(("True-Client-IP", raw)), "header:true-client-ip") == normalised
    assert address(scope(peer=raw)) == normalised


def test_ipv6_is_bucketed_by_64_for_rate_limits_only() -> None:
    one, same_64, other_64 = "2001:db8:1:2::1", "2001:db8:1:2:ffff::9", "2001:db8:1:3::1"
    assert bucket(one) == bucket(same_64) == "2001:db8:1:2::/64"
    assert bucket(other_64) != bucket(one)
    assert bucket("203.0.113.7") == "203.0.113.7"
    assert bucket("testclient") == "testclient"
    # The address itself stays whole.
    assert address(scope(("True-Client-IP", one)), "header:true-client-ip") == one


# --- our proxy (ORIGIN_SECRET) --------------------------------------------------------------------


def via_proxy(secret: str | None, real_ip: str = "203.0.113.7") -> dict[str, Any]:
    """A request through the PWA's proxy: Cloudflare saw the proxy's egress, the proxy says who
    the visitor was, and the visitor's own X-Forwarded-For passed through."""
    headers = [("True-Client-IP", VERCEL), ("X-Real-IP", real_ip), ("X-Forwarded-For", "6.6.6.6")]
    return scope(*headers, *([("x-origin-secret", secret)] if secret is not None else []))


def test_with_the_origin_secret_the_address_is_the_proxys_header() -> None:
    proxied = address(via_proxy(SECRET), "header:true-client-ip", origin_secret=SECRET)
    assert proxied == "203.0.113.7"
    vercel = scope(("X-Vercel-Forwarded-For", "203.0.113.9"), ("x-origin-secret", SECRET))
    assert address(
        vercel, origin_secret=SECRET, proxy_client_ip_header="x-vercel-forwarded-for"
    ) == ("203.0.113.9")


@pytest.mark.parametrize(
    "secret",
    [None, "wrong", SECRET[:-1], SECRET.upper(), f"{SECRET} ", "", f"{SECRET}, {SECRET}"],
)
def test_without_the_exact_secret_the_proxys_header_is_never_read(secret: str | None) -> None:
    # True-Client-IP (VERCEL here) is CLIENT_IP_SOURCE's answer; X-Real-IP is never read.
    assert address(via_proxy(secret), "header:true-client-ip", origin_secret=SECRET) == VERCEL
    twice = scope(
        ("X-Real-IP", "203.0.113.7"), ("x-origin-secret", SECRET), ("x-origin-secret", SECRET)
    )
    assert address(twice, "header:true-client-ip", origin_secret=SECRET) == PEER


def test_without_a_configured_secret_no_request_is_our_proxy() -> None:
    assert address(via_proxy(""), "header:true-client-ip") == VERCEL
    assert address(via_proxy(SECRET), "header:true-client-ip") == VERCEL


@pytest.mark.parametrize("value", [None, *MALFORMED])
def test_a_proxy_header_that_is_missing_or_malformed_falls_back_to_client_ip_source(
    value: str | None,
) -> None:
    headers = [("True-Client-IP", VERCEL), ("x-origin-secret", SECRET)]
    request = scope(*headers, *([("X-Real-IP", value)] if value is not None else []))
    assert address(request, "header:true-client-ip", origin_secret=SECRET) == VERCEL


def test_fallbacks_are_logged_once_a_minute_by_source_and_reason_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr(client_ip.time, "monotonic", lambda: now[0])
    settings = {"origin_secret": SECRET}
    with structlog.testing.capture_logs() as logs:
        for _ in range(3):
            address(scope(("x-origin-secret", SECRET), ("X-Real-IP", "6.6.6.6:1")), **settings)
        address(scope(("x-origin-secret", SECRET)), **settings)  # missing: another reason
        address(scope(("True-Client-IP", "nonsense-7c2d")), "header:true-client-ip")
        now[0] += 61
        address(scope(("x-origin-secret", SECRET), ("X-Real-IP", "6.6.6.6:1")), **settings)

    lines = [(line["source"], line["reason"]) for line in logs]
    # Three malformed proxy headers in a minute log once; `peer`, the next source, never logs.
    assert lines == [
        ("proxy:x-real-ip", "malformed"),
        ("proxy:x-real-ip", "missing"),
        ("header:true-client-ip", "malformed"),
        ("proxy:x-real-ip", "malformed"),
    ]
    assert all(line["event"] == "client_ip_fallback" for line in logs)
    assert all(line["log_level"] == "warning" for line in logs)
    for value in (SECRET, "6.6.6.6", "nonsense-7c2d"):
        assert value not in str(logs)


def test_a_wrong_or_repeated_secret_is_logged_once_a_minute_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr(client_ip.time, "monotonic", lambda: now[0])
    settings = {"origin_secret": SECRET}
    with structlog.testing.capture_logs() as logs:
        address(via_proxy(SECRET), "header:true-client-ip", **settings)  # right: nothing logged
        address(via_proxy(None), "header:true-client-ip", **settings)  # absent: a direct caller
        for wrong in ("wrong-5e6f", f"{SECRET} ", SECRET.upper()):
            assert address(via_proxy(wrong), "header:true-client-ip", **settings) == VERCEL
        twice = scope(("x-origin-secret", SECRET), ("x-origin-secret", "wrong-5e6f"))
        address(twice, **settings)
        now[0] += 61
        address(via_proxy("wrong-5e6f"), "header:true-client-ip", **settings)

    lines = [(line["event"], line["source"], line["reason"]) for line in logs]
    assert lines == [
        ("client_ip_proxy_secret_mismatch", "x-origin-secret", "mismatch"),
        ("client_ip_proxy_secret_mismatch", "x-origin-secret", "repeated"),
        ("client_ip_proxy_secret_mismatch", "x-origin-secret", "mismatch"),
    ]
    assert all(line["log_level"] == "warning" for line in logs)
    for value in (SECRET, SECRET.upper(), "wrong-5e6f", "203.0.113.7", VERCEL):
        assert value not in str(logs)


def test_during_a_rotation_both_secrets_are_accepted() -> None:
    old, new = SECRET, "new-proxy-secret-7a2b-0123456789abcdef0123"
    rotating = {"origin_secret": new, "origin_secret_previous": old}
    for secret in (old, new):
        assert address(via_proxy(secret), "header:true-client-ip", **rotating) == "203.0.113.7"
    assert address(via_proxy("neither"), "header:true-client-ip", **rotating) == VERCEL
    # Once ORIGIN_SECRET_PREVIOUS is removed, the old secret is only a mismatch.
    assert address(via_proxy(old), "header:true-client-ip", origin_secret=new) == VERCEL


def test_client_ip_debug_logs_the_header_shape_once_a_minute_without_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1000.0]
    monkeypatch.setattr(client_ip.time, "monotonic", lambda: now[0])
    client_ip._debug_logged_at.clear()  # pyright: ignore[reportPrivateUsage]
    debug = {"origin_secret": SECRET, "client_ip_debug": True}
    with structlog.testing.capture_logs() as logs:
        address(via_proxy(SECRET), "header:true-client-ip", origin_secret=SECRET)  # off: silent
        for _ in range(3):  # one shape, one line
            address(via_proxy(SECRET), "header:true-client-ip", **debug)
        # The proxy header overwritten with the proxy's own egress: the shape shows it.
        overwritten = scope(
            ("True-Client-IP", VERCEL),
            ("X-Real-IP", VERCEL),
            ("X-Forwarded-For", f"203.0.113.7, {VERCEL}"),
            ("x-origin-secret", SECRET),
        )
        address(overwritten, "header:true-client-ip", **debug)
        address(via_proxy(None), "header:true-client-ip", **debug)
    client_ip._debug_logged_at.clear()  # pyright: ignore[reportPrivateUsage]

    lines = [line for line in logs if line["event"] == "client_ip_debug"]
    assert [
        (
            line["path"],
            line["secret"],
            line["proxy_header"],
            line["proxy_header_is_true_client_ip"],
            line["proxy_header_xff_from_right"],
            line["xff_entries"],
        )
        for line in lines
    ] == [
        ("proxy", "match", "ip", False, None, 1),
        ("proxy", "match", "ip", True, 1, 2),
        ("source", "absent", "ip", False, None, 1),
    ]
    assert all(line["log_level"] == "info" for line in lines)
    for value in (SECRET, "203.0.113.7", VERCEL, "6.6.6.6"):
        assert value not in str(logs)


# --- startup validation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "source"),
    [("peer", "peer"), ("Header:True-Client-IP", "header:true-client-ip"), ("xff:2", "xff:2")],
)
def test_valid_sources(raw: str, source: str) -> None:
    assert make_settings(client_ip_source=raw).client_ip_source == source
    assert make_settings().client_ip_source == "peer"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "peer ",
        "socket",
        "header:",
        "header:true client ip",
        "header:x-forwarded-for",
        "header:x-origin-secret",
        "xff:0",
        "xff:10",
        "xff:-1",
        "xff:one",
        "header:secret-7f3a!",
    ],
)
def test_a_malformed_source_refuses_to_start_and_names_only_the_variable(
    raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLIENT_IP_SOURCE", raw)
    with pytest.raises(ValidationError) as refused:
        make_settings()
    message = str(refused.value)
    assert "CLIENT_IP_SOURCE must be" in message
    assert "secret-7f3a" not in message


@pytest.mark.parametrize("name", ["origin_secret", "origin_secret_previous"])
@pytest.mark.parametrize(
    "bad",
    [
        f" {SECRET}",
        f"{SECRET} ",
        f"{SECRET}\n",
        f"\t{SECRET}",
        f"{SECRET[:20]} {SECRET[20:]}",
        f"{SECRET}é",
        f"{SECRET}\x7f",
    ],
)
def test_a_secret_that_could_never_match_a_header_refuses_to_start(name: str, bad: str) -> None:
    with pytest.raises(ValidationError) as refused:
        make_settings(**{name: bad})
    message = str(refused.value)
    assert f"{name.upper()} must be printable ASCII without spaces" in message
    assert SECRET[:20] not in message and SECRET[20:] not in message


def test_the_proxy_header_setting() -> None:
    assert make_settings().proxy_client_ip_header == "x-real-ip"
    assert make_settings(
        proxy_client_ip_header="X-Vercel-Forwarded-For"
    ).proxy_client_ip_header == ("x-vercel-forwarded-for")
    for raw in ("", "x real ip", "x-forwarded-for", "x-origin-secret", "secret-7f3a!"):
        with pytest.raises(ValidationError) as refused:
            make_settings(proxy_client_ip_header=raw)
        assert "PROXY_CLIENT_IP_HEADER must be" in str(refused.value)
        assert "secret-7f3a" not in str(refused.value)


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_deployed_environments_need_the_origin_secret_and_an_explicit_source(
    env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLIENT_IP_SOURCE", raising=False)
    without_source = {k: v for k, v in DEPLOYED_SECRETS.items() if k != "client_ip_source"}
    with pytest.raises(ValidationError, match="CLIENT_IP_SOURCE required"):
        make_settings(**without_source, app_env=env)
    with pytest.raises(ValidationError, match="ORIGIN_SECRET required"):
        make_settings(**{**DEPLOYED_SECRETS, "origin_secret": None}, app_env=env)
    for name in ("origin_secret", "origin_secret_previous"):
        with pytest.raises(ValidationError, match=f"{name.upper()} must be at least 32 bytes") as e:
            make_settings(**{**DEPLOYED_SECRETS, name: "short-secret-9d4e"}, app_env=env)
        assert "short-secret-9d4e" not in str(e.value)
    # An explicit `peer` is a choice, so it is accepted; so is the source from the environment.
    assert make_settings(**DEPLOYED_SECRETS, app_env=env).client_ip_source == "peer"
    monkeypatch.setenv("CLIENT_IP_SOURCE", "header:true-client-ip")
    assert make_settings(**without_source, app_env=env).client_ip_source == "header:true-client-ip"
    make_settings()  # local runs need neither


def test_uvicorn_never_takes_the_address_from_forwarded_headers() -> None:
    dockerfile = (API_DIR / "Dockerfile").read_text()
    (cmd,) = re.findall(r"^CMD .*$", dockerfile, flags=re.MULTILINE)
    assert "--no-proxy-headers" in cmd
    assert "--proxy-headers" not in cmd and "forwarded-allow-ips" not in cmd.lower()


# --- the limits, end to end ----------------------------------------------------------------------

Client = Callable[..., httpx2.AsyncClient]
ANDROID = {"platform": "android", "push_token": None}
# One per-address limit of each kind: the shared public counter, sign-in and refresh.
ENDPOINTS = {
    "public": ("GET", "/v1/public/cooks/nosuchcook2", None),
    "exchange": ("POST", "/v1/auth/exchange", {"firebase_id_token": "x", "device": ANDROID}),
    "refresh": ("POST", "/v1/auth/refresh", {"refresh_token": "not-a-token"}),
}


@pytest.fixture
async def proxied(commit_guard: CommitGuard, valkey: Redis) -> AsyncIterator[Client]:  # noqa: F811
    opened: list[httpx2.AsyncClient] = []

    def make(**settings: Any) -> httpx2.AsyncClient:
        app = create_app(make_settings(**settings))
        app.dependency_overrides[get_session] = commit_guard.override()
        app.state.valkey = valkey
        transport = httpx2.ASGITransport(app=app, client=(PEER, 41234))
        opened.append(httpx2.AsyncClient(transport=transport, base_url="https://api"))
        return opened[-1]

    yield make
    for each in opened:
        await each.aclose()


DIRECT = {"client_ip_source": "header:true-client-ip", "origin_secret": SECRET}


@pytest.mark.anyio
@pytest.mark.services
@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize(
    ("settings", "real", "other"),
    [
        # Direct: Cloudflare's True-Client-IP; the caller's X-Forwarded-For and X-Real-IP differ
        # on every request.
        pytest.param(
            DIRECT,
            lambda spoof: {
                "True-Client-IP": "203.0.113.7",
                "X-Forwarded-For": spoof,
                "X-Real-IP": spoof,
            },
            {"True-Client-IP": "203.0.113.8", "X-Forwarded-For": "6.6.6.1"},
            id="direct",
        ),
        # Through our proxy: True-Client-IP is the proxy's egress for every visitor, X-Real-IP is
        # the visitor; the visitor's own X-Forwarded-For differs on every request.
        pytest.param(
            DIRECT,
            lambda spoof: {
                "x-origin-secret": SECRET,
                "True-Client-IP": VERCEL,
                "X-Real-IP": "2001:db8:7:7::1",
                "X-Forwarded-For": spoof,
            },
            {"x-origin-secret": SECRET, "True-Client-IP": VERCEL, "X-Real-IP": "2001:db8:8:8::1"},
            id="our-proxy",
        ),
        # A forged secret: X-Real-IP is never read, True-Client-IP counts.
        pytest.param(
            DIRECT,
            lambda spoof: {
                "x-origin-secret": "forged",
                "True-Client-IP": "203.0.113.7",
                "X-Real-IP": spoof,
            },
            {"x-origin-secret": "forged", "True-Client-IP": "203.0.113.8", "X-Real-IP": "6.6.6.1"},
            id="forged-secret",
        ),
        # One proxy that appends to X-Forwarded-For (n = 1 here; measured for each host).
        pytest.param(
            {"client_ip_source": "xff:1"},
            lambda spoof: {"X-Forwarded-For": f"{spoof}, 2001:db8:7:7::1"},
            {"X-Forwarded-For": "6.6.6.1, 2001:db8:8:8::1"},
            id="xff",
        ),
    ],
)
async def test_spoofed_headers_share_the_real_callers_counter(
    proxied: Client,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    settings: dict[str, Any],
    real: Callable[[str], dict[str, str]],
    other: dict[str, str],
) -> None:
    monkeypatch.setattr(rate_limit, "PUBLIC_PER_MINUTE", 1)
    monkeypatch.setattr(identity_router, "EXCHANGE_PER_IP", (1, 600))
    monkeypatch.setattr(identity_router, "REFRESH_PER_IP", (1, 60))
    method, path, body = ENDPOINTS[endpoint]
    api = proxied(**settings)

    async def call(headers: dict[str, str]) -> int:
        return (await api.request(method, path, json=body, headers=headers)).status_code

    first = await call(real("6.6.6.1"))
    second = await call(real("6.6.6.2"))
    elsewhere = await call(other)  # another caller behind the same proxy has its own counter

    assert first != 429 and second == 429 and elsewhere != 429
