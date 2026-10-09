"""Our own thin async client for Firebase Cloud Messaging's HTTP v1 API on httpx2 (handbook 10:
no SDK).

FCM wants an OAuth access token: we sign a JWT with the service account's key (RS256), trade it at
Google's token endpoint and keep the access token until a minute before it expires, shared by
every client with the same account (a job run builds a new client each minute). `send` never
raises: it says what happened, and the job decides what to do about it.
"""

import time
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx2
import jwt
import structlog

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
TOKEN_LIFETIME = 3600
EARLY = 60  # renew this many seconds before the access token expires
log = structlog.get_logger("gharbasket.notifications.fcm")


@dataclass(frozen=True)
class Sent:
    name: str  # "projects/<id>/messages/<id>"


@dataclass(frozen=True)
class Unregistered:
    """The token is gone (the app was uninstalled or the token rotated): stop using it."""


@dataclass(frozen=True)
class Retry:
    reason: str


@dataclass(frozen=True)
class Failed:
    reason: str


Outcome = Sent | Unregistered | Retry | Failed


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


def _error(response: httpx2.Response) -> tuple[str, str]:
    """FCM's error code (the FcmError detail, else the status) and message, from its body."""
    try:
        body = response.json()
    except ValueError:
        return "", ""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return "", ""
    details = [d for d in error.get("details") or [] if isinstance(d, dict)]
    codes = [d["errorCode"] for d in details if "errorCode" in d]
    return str(codes[0] if codes else error.get("status", "")), str(error.get("message", ""))


class FcmClient:
    # client_email → (access token, expires at as a Unix time)
    _tokens: ClassVar[dict[str, tuple[str, float]]] = {}

    def __init__(self, http: httpx2.AsyncClient, service_account: dict[str, Any]) -> None:
        self.http = http
        self.email = str(service_account["client_email"])
        self._key = str(service_account["private_key"])
        self.token_uri = str(service_account["token_uri"])
        self.send_url = SEND_URL.format(project=service_account["project_id"])

    async def _access_token(self) -> str:
        cached = self._tokens.get(self.email)
        now = time.time()
        if cached is not None and now < cached[1] - EARLY:
            return cached[0]
        issued = int(now)
        claims = {
            "iss": self.email,
            "sub": self.email,
            "scope": SCOPE,
            "aud": self.token_uri,
            "iat": issued,
            "exp": issued + TOKEN_LIFETIME,
        }
        assertion = jwt.encode(claims, self._key, algorithm="RS256")
        response = await self.http.post(
            self.token_uri, data={"grant_type": GRANT_TYPE, "assertion": assertion}
        )
        response.raise_for_status()
        body = response.json()
        token = str(body["access_token"])
        self._tokens[self.email] = (token, now + float(body.get("expires_in", TOKEN_LIFETIME)))
        return token

    async def send(self, token: str, title: str, body: str, data: dict[str, str]) -> Outcome:
        """One push to one device."""
        try:
            access = await self._access_token()
        except httpx2.HTTPStatusError as exc:
            status = exc.response.status_code
            log.warning("fcm_token_refused", status=status)
            reason = f"token endpoint: HTTP {status}"
            return Retry(reason) if _retryable(status) else Failed(reason)
        except httpx2.HTTPError as exc:
            log.warning("fcm_token_unreachable", reason=type(exc).__name__)
            return Retry(f"token endpoint: {type(exc).__name__}")
        except (ValueError, KeyError, jwt.PyJWTError) as exc:  # a bad key, or a strange answer
            log.warning("fcm_token_failed", reason=type(exc).__name__)
            return Failed(f"token: {type(exc).__name__}")

        message = {"token": token, "notification": {"title": title, "body": body}, "data": data}
        try:
            response = await self.http.post(
                self.send_url,
                json={"message": message},
                headers={"Authorization": f"Bearer {access}"},
            )
        except httpx2.HTTPError as exc:
            log.warning("fcm_unreachable", reason=type(exc).__name__)
            return Retry(type(exc).__name__)
        status = response.status_code
        if status == 200:
            try:
                return Sent(str(response.json().get("name", "")))
            except ValueError:
                return Sent("")
        code, text = _error(response)
        # Only the status and FCM's code are logged: the message can echo the token.
        log.info("fcm_refused", status=status, code=code)
        if (
            status == 404
            or code == "UNREGISTERED"
            or (status == 400 and code == "INVALID_ARGUMENT" and "token" in text.lower())
        ):
            return Unregistered()
        if status == 401:  # our access token was revoked early: get a new one next time
            self._tokens.pop(self.email, None)
            return Retry("HTTP 401")
        if _retryable(status):
            return Retry(f"HTTP {status} {code}".strip())
        return Failed(f"HTTP {status} {code}".strip())
