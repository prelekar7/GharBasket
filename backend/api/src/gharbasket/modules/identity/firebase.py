"""Checks Firebase ID tokens from phone sign-in, following Firebase's rules for third-party JWT
libraries (firebase.google.com/docs/auth/admin/verify-id-tokens):

- header: `alg` RS256, `kid` one of Google's current certificates;
- claims: `aud` = our project id, `iss` = https://securetoken.google.com/<project id>,
  `exp` in the future, `iat` and `auth_time` in the past, `sub` (the Firebase uid) not empty;
- certificates are cached for as long as Google's Cache-Control max-age says.

We additionally require `phone_number`: phone OTP is the only way to sign in.
"""

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx2
import jwt
import structlog
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.x509 import load_pem_x509_certificate

from gharbasket.platform.errors import ProblemError
from gharbasket.platform.tokens import invalid_token

CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
)
LEEWAY_S = 30
# An unknown kid usually means Google rotated its keys; refetch, but at most this often.
MIN_REFETCH_S = 60

log = structlog.get_logger("gharbasket.identity.firebase")


@dataclass(frozen=True)
class FirebaseIdentity:
    uid: str
    phone_e164: str
    auth_time: datetime  # when the OTP was entered; DELETE /v1/me needs it recent (spec §3.5)


def _max_age(cache_control: str | None) -> int:
    match = re.search(r"max-age=(\d+)", cache_control or "")
    return int(match.group(1)) if match else 3600


class FirebaseTokenVerifier:
    def __init__(self, project_id: str, client: httpx2.AsyncClient) -> None:
        self.project_id = project_id
        self._client = client
        self._keys: dict[str, RSAPublicKey] = {}
        self._expires_at = 0.0
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def _fetch_keys(self) -> None:
        try:
            response = await self._client.get(CERTS_URL)
            response.raise_for_status()
            certificates: dict[str, str] = response.json()
        except (httpx2.HTTPError, ValueError) as exc:
            log.warning("firebase_keys_unavailable", error=type(exc).__name__)
            raise ProblemError(
                503,
                "auth.provider_unavailable",
                "Phone sign-in is briefly unavailable. Try again in a minute.",
            ) from exc
        keys: dict[str, RSAPublicKey] = {}
        for kid, pem in certificates.items():
            public_key = load_pem_x509_certificate(pem.encode()).public_key()
            if isinstance(public_key, RSAPublicKey):
                keys[kid] = public_key
        now = time.monotonic()
        self._keys = keys
        self._fetched_at = now
        self._expires_at = now + _max_age(response.headers.get("cache-control"))

    async def _key_for(self, kid: str) -> RSAPublicKey | None:
        async with self._lock:
            now = time.monotonic()
            stale = now >= self._expires_at
            unknown = kid not in self._keys and now - self._fetched_at >= MIN_REFETCH_S
            if stale or unknown:
                await self._fetch_keys()
            return self._keys.get(kid)

    async def verify(self, id_token: str) -> FirebaseIdentity:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.InvalidTokenError as exc:
            raise invalid_token("The phone verification is not valid.") from exc
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str):
            raise invalid_token("The phone verification is not valid.")
        key = await self._key_for(kid)
        if key is None:
            raise invalid_token("The phone verification is not valid.")
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=self.project_id,
                issuer=f"https://securetoken.google.com/{self.project_id}",
                leeway=LEEWAY_S,
                options={"require": ["exp", "iat", "aud", "iss", "sub", "auth_time"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise invalid_token("The phone verification has expired. Try again.") from exc
        except jwt.InvalidTokenError as exc:
            raise invalid_token("The phone verification is not valid.") from exc

        uid, phone, auth_time = claims["sub"], claims.get("phone_number"), claims["auth_time"]
        if not isinstance(uid, str) or not uid:
            raise invalid_token("The phone verification is not valid.")
        if not isinstance(auth_time, int) or auth_time > time.time() + LEEWAY_S:
            raise invalid_token("The phone verification is not valid.")
        if not isinstance(phone, str) or not phone:
            raise invalid_token("Sign in with your phone number.")
        return FirebaseIdentity(
            uid=uid, phone_e164=phone, auth_time=datetime.fromtimestamp(auth_time, UTC)
        )
