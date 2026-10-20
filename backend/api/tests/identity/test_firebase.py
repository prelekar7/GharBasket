"""Firebase ID-token checks, per Firebase's rules for third-party JWT libraries."""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization

from gharbasket.modules.identity.firebase import FirebaseIdentity
from gharbasket.platform.errors import ProblemError

from .fake_firebase import FakeGoogle

pytestmark = pytest.mark.anyio


async def _refused(google: FakeGoogle, token: str, code: str = "auth.invalid_token") -> None:
    with pytest.raises(ProblemError) as caught:
        await google.verifier().verify(token)
    assert caught.value.code == code


async def test_a_valid_phone_sign_in_token_is_accepted() -> None:
    google = FakeGoogle()

    identity = await google.verifier().verify(google.id_token(auth_time=1_791_000_000))

    assert identity == FirebaseIdentity(
        uid="firebase-uid-1",
        phone_e164="+919800000001",
        auth_time=datetime.fromtimestamp(1_791_000_000, UTC),  # when the OTP was entered
    )


@pytest.mark.parametrize(
    "claims",
    # Functions of the time the test runs, not of when pytest collected it: on a slow machine
    # "ten minutes from now" taken at collection can be in the past when the test runs.
    [
        lambda now: {"aud": "someone-elses-project"},
        lambda now: {"iss": "https://securetoken.google.com/someone-elses-project"},
        lambda now: {"exp": now - 120},
        lambda now: {"iat": now + 600},
        lambda now: {"auth_time": now + 600},
        lambda now: {"auth_time": None},
        lambda now: {"sub": ""},
        lambda now: {"phone_number": None},
    ],
    ids=[
        "audience",
        "issuer",
        "expired",
        "issued-in-future",
        "auth-in-future",
        "no-auth-time",
        "empty-uid",
        "not-phone-sign-in",
    ],
)
async def test_tokens_breaking_firebase_rules_are_refused(
    claims: Callable[[int], dict[str, Any]],
) -> None:
    google = FakeGoogle()

    await _refused(google, google.id_token(**claims(int(time.time()))))


async def test_only_rs256_signed_by_a_google_key_is_accepted() -> None:
    google = FakeGoogle()
    public_pem = (
        google.keys["kid-1"]
        .public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )

    # Classic attack: sign with HS256 using the *public* key as the shared secret. PyJWT refuses
    # to create such a token, so it is assembled by hand.
    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode().rstrip("=")

    signing_input = (
        f"{b64(json.dumps({'alg': 'HS256', 'kid': 'kid-1'}).encode())}."
        f"{b64(json.dumps({'sub': 'x', 'phone_number': '+919800000001'}).encode())}"
    )
    signature = hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    confused = f"{signing_input}.{b64(signature)}"
    google.add_key("unpublished")
    google.published.discard("unpublished")

    await _refused(google, confused)
    await _refused(google, google.id_token(kid="unpublished"))
    await _refused(google, "not-a-jwt")


async def test_keys_are_cached_for_googles_max_age() -> None:
    google = FakeGoogle()
    verifier = google.verifier()

    await verifier.verify(google.id_token())
    await verifier.verify(google.id_token())
    assert google.fetches == 1

    google.max_age = 0
    expired_cache = google.verifier()
    await expired_cache.verify(google.id_token())
    await expired_cache.verify(google.id_token())
    assert google.fetches == 3


async def test_a_new_google_key_is_picked_up_after_rotation() -> None:
    google = FakeGoogle()
    verifier = google.verifier()
    await verifier.verify(google.id_token())

    google.add_key("kid-2")
    verifier._fetched_at -= 120  # pyright: ignore[reportPrivateUsage]  # the cache is 2 min old

    assert (await verifier.verify(google.id_token(kid="kid-2"))).uid == "firebase-uid-1"
    assert google.fetches == 2


async def test_unknown_keys_do_not_trigger_a_fetch_storm() -> None:
    google = FakeGoogle()
    verifier = google.verifier()
    await verifier.verify(google.id_token())
    google.add_key("kid-x")
    google.published.discard("kid-x")

    for _ in range(5):
        with pytest.raises(ProblemError):
            await verifier.verify(google.id_token(kid="kid-x"))

    assert google.fetches == 1


async def test_google_being_down_is_a_503_not_a_401() -> None:
    google = FakeGoogle()
    google.down = True

    await _refused(google, google.id_token(), code="auth.provider_unavailable")
