"""Our own thin async Razorpay client on httpx2 (handbook 10: not the official SDK).

Only what M5 needs: create an order for a top-up, refund a payment, and check a webhook's
signature. Card and UPI details never reach us; Razorpay Checkout runs in the app.
"""

import hashlib
import hmac
from typing import Any

import httpx2
import structlog

from gharbasket.platform.errors import ProblemError

API = "https://api.razorpay.com/v1"
log = structlog.get_logger("gharbasket.billing.razorpay")


def payments_unavailable() -> ProblemError:
    return ProblemError(
        503,
        "billing.payments_unavailable",
        "Payments are not available right now. Try again in a few minutes.",
    )


def valid_signature(body: bytes, signature: str, secret: str) -> bool:
    """A webhook is genuine if X-Razorpay-Signature is HMAC-SHA256(secret, raw body), in hex."""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class RazorpayClient:
    def __init__(self, http: httpx2.AsyncClient, key_id: str, key_secret: str) -> None:
        self.http = http
        self.key_id = key_id
        self._auth = (key_id, key_secret)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.http.post(f"{API}{path}", json=body, auth=self._auth)
            response.raise_for_status()
            return response.json()
        except (httpx2.HTTPError, ValueError) as exc:
            # The status only: Razorpay's error body can echo request data.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            log.warning("razorpay_failed", path=path, reason=type(exc).__name__, status=status)
            raise payments_unavailable() from exc

    async def create_order(self, amount_paise: int, receipt: str, notes: dict[str, str]) -> str:
        """A Razorpay order for exactly this amount in INR; returns its id ("order_…")."""
        order = await self._post(
            "/orders",
            {"amount": amount_paise, "currency": "INR", "receipt": receipt, "notes": notes},
        )
        return str(order["id"])

    async def refund(self, payment_id: str, amount_paise: int) -> str:
        """Money back to where it came from; returns the refund id ("rfnd_…")."""
        refund = await self._post(f"/payments/{payment_id}/refund", {"amount": amount_paise})
        return str(refund["id"])
