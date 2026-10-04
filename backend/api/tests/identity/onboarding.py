"""Shared by the household tests: a valid onboarding request and small helpers."""

import copy
import uuid
from typing import Any

import httpx2
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.tools.seed import SEED_SOCIETY_ID

from .conftest import bearer, sign_in
from .fake_firebase import FakeGoogle

ONBOARDING: dict[str, Any] = {
    "society_id": str(SEED_SOCIETY_ID),
    "address": {"tower_id": "a", "flat": " a-1203 ", "location": {"lat": 19.0396, "lng": 73.0676}},
    "diet": "veg",
    "cuisine_tags": ["maharashtrian"],
    "weekday_cook_minutes": 30,
    "weekend_cook_minutes": 60,
    "no_cook_days": [6],
    "budget_paise": 300_000,
    "members": [
        {
            "age_band": "18_59",
            "sex": "f",
            "fasting_rules": {"jain": False, "fasts": ["ekadashi"]},
            "health_tags": ["diabetes"],
        },
        {"age_band": "60_plus"},
    ],
    "consents": [
        {"purpose": "service_delivery", "version": "v1", "granted": True},
        {"purpose": "personalisation", "version": "v1", "granted": True},
        {"purpose": "health_personalisation", "version": "v1", "granted": True},
        {"purpose": "marketing", "version": "v1", "granted": False},
    ],
}


def onboarding(**changes: Any) -> dict[str, Any]:
    body = copy.deepcopy(ONBOARDING)
    body.update(changes)
    return body


async def customer(api: httpx2.AsyncClient, google: FakeGoogle, n: int = 1) -> dict[str, str]:
    signed_in = await sign_in(api, google, phone_number=f"+91980000{n:04d}", sub=f"uid-{n}")
    return bearer(signed_in["access_token"])


async def onboard(
    api: httpx2.AsyncClient, headers: dict[str, str], body: dict[str, Any] | None = None
) -> httpx2.Response:
    return await api.post(
        "/v1/households",
        json=body or ONBOARDING,
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )


async def count(db: AsyncSession, model: type[Any]) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()
