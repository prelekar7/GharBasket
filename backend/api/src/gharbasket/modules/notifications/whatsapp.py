"""Our own thin async client for the WhatsApp Cloud API on httpx2 (handbook 10: no SDK), and the
webhook's signature check (spec §3.2).

Only approved template messages are sent: the template's name, its language and the body's
parameters in order. `send` never raises; like the FCM client it says what happened (`Sent` with
Meta's message id, `Retry` or `Failed`) and the job decides what to do about it.
"""

import hashlib
import hmac

import httpx2
import structlog

from gharbasket.modules.notifications.fcm import Failed, Retry, Sent

GRAPH = "https://graph.facebook.com/v23.0"
# Meta's codes for "try again later": temporary errors and the throughput and spam rate limits.
RETRYABLE_CODES = frozenset({1, 2, 4, 80007, 130429, 131000, 131016, 131048, 131056})
LANGUAGE = {"en-IN": "en", "hi-IN": "hi", "mr-IN": "mr"}  # a template row's locale → Meta's code
log = structlog.get_logger("gharbasket.notifications.whatsapp")

Outcome = Sent | Retry | Failed


def valid_signature(body: bytes, header: str, app_secret: str) -> bool:
    """Genuine if X-Hub-Signature-256 is "sha256=" + HMAC-SHA256(app secret, raw body), in hex."""
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header.encode())


def _code(response: httpx2.Response) -> int | None:
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, int) else None


class WhatsAppClient:
    def __init__(self, http: httpx2.AsyncClient, token: str, phone_number_id: str) -> None:
        self.http = http
        self._token = token
        self.url = f"{GRAPH}/{phone_number_id}/messages"

    async def send(self, phone: str, template: str, locale: str, params: list[str]) -> Outcome:
        """One template message to one phone (E.164)."""
        message = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone.removeprefix("+"),
            "type": "template",
            "template": {
                "name": template,
                "language": {"code": LANGUAGE.get(locale, "en")},
                "components": [
                    {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
                ]
                if params
                else [],
            },
        }
        try:
            response = await self.http.post(
                self.url, json=message, headers={"Authorization": f"Bearer {self._token}"}
            )
        except httpx2.HTTPError as exc:
            log.warning("whatsapp_unreachable", reason=type(exc).__name__)
            return Retry(type(exc).__name__)
        status = response.status_code
        if status == 200:
            try:
                return Sent(str(response.json()["messages"][0]["id"]))
            except (ValueError, KeyError, IndexError, TypeError):
                return Sent("")
        code = _code(response)
        # Only the status and Meta's code are logged: the message can echo the phone number.
        log.info("whatsapp_refused", status=status, code=code)
        reason = f"HTTP {status}" + (f" code {code}" if code is not None else "")
        if status == 429 or status >= 500 or code in RETRYABLE_CODES:
            return Retry(reason)
        return Failed(reason)
