"""Fixtures for notifications tests: seeded households to notify, and a fake FCM."""

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx2
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User
from gharbasket.modules.notifications.fcm import FcmClient
from gharbasket.tools.seed import seed, seed_households, seed_societies

# Shared with the identity tests (pytest finds fixtures imported into a conftest).
from ..identity.conftest import clients, google  # noqa: F401

PRIYA = "+919800001001"  # owns a household in Sunrise Towers; en-IN
MEENA = "+919800001004"  # owns another one; hi-IN
TOKEN_URI = "https://oauth2.test/token"


@dataclass(frozen=True)
class Home:
    user_id: uuid.UUID
    household_id: uuid.UUID


@pytest.fixture
async def homes(db_session: AsyncSession) -> dict[str, Home]:
    """The seed people and households, by phone (Priya and Meena)."""
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)
    found: dict[str, Home] = {}
    for phone in (PRIYA, MEENA):
        user_id = await db_session.scalar(select(User.id).where(User.phone_e164 == phone))
        assert user_id is not None
        household = await identity_api.household_for_user(db_session, user_id)
        assert household is not None
        found[phone] = Home(user_id, household.id)
    return found


@pytest.fixture(scope="session")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def service_account(rsa_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {
        "type": "service_account",
        "project_id": "gharbasket-test",
        "private_key": pem,
        "client_email": "push@gharbasket-test.iam.gserviceaccount.com",
        "token_uri": TOKEN_URI,
    }


@pytest.fixture(autouse=True)
def fresh_fcm_tokens() -> Iterator[None]:
    """Access tokens are cached per service account across clients: none leak between tests."""
    FcmClient._tokens.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    FcmClient._tokens.clear()  # pyright: ignore[reportPrivateUsage]


class FakeFcm:
    """Google's token endpoint and FCM's send endpoint on a mock transport.

    What a send answers depends on the device token: `gone…` is unregistered (404), `bad…` is not
    a valid token (400), `flaky…` gets 503, anything else is sent.
    """

    def __init__(self) -> None:
        self.token_requests: list[httpx2.Request] = []
        self.sends: list[dict[str, Any]] = []
        self.http = httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle))

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == TOKEN_URI:
            self.token_requests.append(request)
            return httpx2.Response(200, json={"access_token": "ya29.fake", "expires_in": 3599})
        assert request.headers["authorization"] == "Bearer ya29.fake"
        message = json.loads(request.content)["message"]
        self.sends.append(message)
        token = message["token"]
        if token.startswith("gone"):
            return fcm_error(404, "NOT_FOUND", "UNREGISTERED", "Requested entity was not found.")
        if token.startswith("bad"):
            text = "The registration token is not a valid FCM registration token"
            return fcm_error(400, "INVALID_ARGUMENT", "INVALID_ARGUMENT", text)
        if token.startswith("flaky"):
            return fcm_error(503, "UNAVAILABLE", "UNAVAILABLE", "The service is unavailable.")
        return httpx2.Response(200, json={"name": f"projects/gharbasket-test/messages/{token}"})


def fcm_error(status: int, name: str, code: str, message: str) -> httpx2.Response:
    detail = {"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": code}
    error = {"code": status, "message": message, "status": name, "details": [detail]}
    return httpx2.Response(status, json={"error": error})


@pytest.fixture
def fcm() -> FakeFcm:
    return FakeFcm()
