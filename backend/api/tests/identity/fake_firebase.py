"""A stand-in for Google's Firebase signing keys: a real RSA key and certificate, served through
httpx2's MockTransport, so the verifier is tested exactly as it runs against Google."""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from gharbasket.modules.identity.firebase import CERTS_URL, FirebaseTokenVerifier

PROJECT_ID = "gharbasket-test"


def _certificate(key: rsa.RSAPrivateKey) -> str:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "securetoken.test")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


class FakeGoogle:
    def __init__(self) -> None:
        self.keys = {"kid-1": rsa.generate_private_key(public_exponent=65537, key_size=2048)}
        self.published = {"kid-1"}
        self.max_age = 3600
        self.fetches = 0
        self.down = False

    def add_key(self, kid: str) -> None:
        self.keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.published.add(kid)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == CERTS_URL
        self.fetches += 1
        if self.down:
            return httpx2.Response(500)
        body = {kid: _certificate(self.keys[kid]) for kid in self.published}
        return httpx2.Response(
            200, json=body, headers={"Cache-Control": f"public, max-age={self.max_age}"}
        )

    def verifier(self) -> FirebaseTokenVerifier:
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(self._handle))
        return FirebaseTokenVerifier(PROJECT_ID, client)

    def id_token(self, kid: str = "kid-1", **overrides: Any) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": f"https://securetoken.google.com/{PROJECT_ID}",
            "aud": PROJECT_ID,
            "sub": "firebase-uid-1",
            "iat": now - 5,
            "exp": now + 3600,
            "auth_time": now - 5,
            "phone_number": "+919800000001",
            "firebase": {"sign_in_provider": "phone"},
        }
        claims.update(overrides)
        claims = {key: value for key, value in claims.items() if value is not None}
        return jwt.encode(claims, self.keys[kid], algorithm="RS256", headers={"kid": kid})
