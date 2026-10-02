"""Access tokens (platform/tokens.py) and the key settings behind them."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
import time_machine
from pydantic import ValidationError

from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings
from gharbasket.platform.tokens import ACCESS_TOKEN_TTL, issue_access_token, verify_access_token

from .conftest import make_settings

# Fake keys for tests only (never used anywhere else).
KEY_1 = "k1-signing-key-for-tests-0123456789abcdef"  # gitleaks:allow
KEY_2 = "k2-signing-key-for-tests-0123456789abcdef"  # gitleaks:allow
ISSUED = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)
USER = uuid.UUID("0199a1b2-0000-7000-8000-000000000001")
SESSION = uuid.UUID("0199a1b2-0000-7000-8000-000000000002")


def _settings(**overrides: object) -> Settings:
    return make_settings(jwt_signing_key=KEY_1, **overrides)


def _issue(settings: Settings, roles: tuple[str, ...] = ("customer",)) -> str:
    token, _ = issue_access_token(
        settings, user_id=USER, session_id=SESSION, roles=roles, now=ISSUED
    )
    return token


def _rejected(settings: Settings, token: str) -> ProblemError:
    with pytest.raises(ProblemError) as caught:
        verify_access_token(settings, token)
    assert caught.value.status == 401
    assert caught.value.code == "auth.invalid_token"
    return caught.value


def test_a_token_round_trips_with_its_claims() -> None:
    settings = _settings()
    token, expires_in = issue_access_token(
        settings, user_id=USER, session_id=SESSION, roles=("customer", "captain:s-1"), now=ISSUED
    )

    with time_machine.travel(ISSUED + timedelta(minutes=5), tick=False):
        claims = verify_access_token(settings, token)

    assert expires_in == 900
    assert jwt.get_unverified_header(token) == {"alg": "HS256", "kid": "k1", "typ": "JWT"}
    assert claims.user_id == USER
    assert claims.session_id == SESSION
    assert claims.roles == ("customer", "captain:s-1")
    assert claims.expires_at == ISSUED + ACCESS_TOKEN_TTL


def test_expiry_allows_30_seconds_of_clock_difference() -> None:
    settings = _settings()
    token = _issue(settings)

    with time_machine.travel(ISSUED + ACCESS_TOKEN_TTL + timedelta(seconds=20), tick=False):
        verify_access_token(settings, token)
    with time_machine.travel(ISSUED + ACCESS_TOKEN_TTL + timedelta(seconds=31), tick=False):
        assert "expired" in (_rejected(settings, token).detail or "")


@pytest.mark.parametrize("other", [{"jwt_audience": "someone-else"}, {"jwt_issuer": "another-api"}])
def test_tokens_for_another_audience_or_issuer_are_refused(other: dict[str, str]) -> None:
    token = _issue(_settings(**other))

    with time_machine.travel(ISSUED, tick=False):
        _rejected(_settings(), token)


def test_a_changed_payload_breaks_the_signature() -> None:
    header, payload, signature = _issue(_settings()).split(".")
    forged = jwt.encode({"sub": str(uuid.uuid4())}, "x" * 32, algorithm="HS256").split(".")[1]

    with time_machine.travel(ISSUED, tick=False):
        _rejected(_settings(), f"{header}.{forged}.{signature}")
        _rejected(_settings(), f"{header}.{payload}.{signature[:-2]}AA")


def test_unsigned_and_unknown_key_tokens_are_refused() -> None:
    claims = {
        "sub": str(USER),
        "sid": str(SESSION),
        "iat": ISSUED,
        "exp": ISSUED + ACCESS_TOKEN_TTL,
    }
    unsigned = jwt.encode(claims, None, algorithm="none", headers={"kid": "k1"})  # pyright: ignore[reportArgumentType]
    unknown_kid = jwt.encode(claims, KEY_1, algorithm="HS256", headers={"kid": "k9"})
    no_kid = jwt.encode(claims, KEY_1, algorithm="HS256")

    with time_machine.travel(ISSUED, tick=False):
        for token in (unsigned, unknown_kid, no_kid, "not-a-jwt"):
            _rejected(_settings(), token)


def test_a_token_missing_a_required_claim_is_refused() -> None:
    settings = _settings()
    claims = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(USER),
        "iat": ISSUED,
        "exp": ISSUED + ACCESS_TOKEN_TTL,
        "jti": "j",
    }  # no "sid"
    token = jwt.encode(claims, KEY_1, algorithm="HS256", headers={"kid": "k1"})

    with time_machine.travel(ISSUED, tick=False):
        _rejected(settings, token)


def test_rotating_the_signing_key_keeps_recent_tokens_valid() -> None:
    old_token = _issue(_settings())
    rotated = make_settings(jwt_signing_key=KEY_2, jwt_key_id="k2", jwt_retired_keys=f"k1:{KEY_1}")
    fully_rotated = make_settings(jwt_signing_key=KEY_2, jwt_key_id="k2")

    with time_machine.travel(ISSUED, tick=False):
        assert verify_access_token(rotated, old_token).user_id == USER
        assert jwt.get_unverified_header(_issue(rotated))["kid"] == "k2"
        _rejected(fully_rotated, old_token)


def test_deployed_environments_need_signing_key_and_pepper() -> None:
    with pytest.raises(
        ValidationError, match="JWT_SIGNING_KEY, REFRESH_TOKEN_PEPPER, FIREBASE_PROJECT_ID required"
    ):
        make_settings(app_env="staging", jobs_token="t")


@pytest.mark.parametrize(
    "bad",
    [
        {"jwt_signing_key": "too-short"},
        {"refresh_token_pepper": "too-short"},
        {"jwt_retired_keys": "k0:too-short"},
        {"jwt_retired_keys": "no-separator"},
    ],
)
def test_weak_or_malformed_keys_are_refused(bad: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**bad)


def test_local_runs_work_without_any_keys() -> None:
    settings = make_settings()

    with time_machine.travel(ISSUED, tick=False):
        assert verify_access_token(settings, _issue(settings)).user_id == USER
