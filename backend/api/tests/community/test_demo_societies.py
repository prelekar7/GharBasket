"""Demo societies once money is real (M9 §3.1, ADR-0039): the synthetic societies of the seed and
`just staging-demo` are offered until the cut-over to Razorpay's live account (the team's demos use
them), and never after it: not in search, not by slug, not on the landing page, and not to join."""

from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.models import Society
from gharbasket.tools.seed import SEED_SOCIETY_ID

from ..billing.test_live_mode import LIVE
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..identity.onboarding import customer, onboard

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("societies")]

SUNRISE = "sunrise-towers-kharghar"  # seed, live
GREEN_VALLEY = "green-valley-heights-kharghar"  # seed, unlocking


async def _slugs(api: Any, headers: dict[str, str], **params: Any) -> list[str]:
    response = await api.get("/v1/societies", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return [item["slug"] for item in response.json()["items"]]


async def test_test_mode_still_offers_the_demo_societies(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)

    assert SUNRISE in await _slugs(api, headers)
    assert await _slugs(api, headers, slug=SUNRISE) == [SUNRISE]
    assert (await api.get(f"/v1/public/societies/{GREEN_VALLEY}")).status_code == 200


async def test_live_money_offers_only_real_societies(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients(**LIVE)
    headers = await customer(api, google)

    assert await _slugs(api, headers) == []  # every seed society is a demo one
    assert await _slugs(api, headers, q="sunrise") == []
    assert await _slugs(api, headers, near="19.0430,73.0720") == []
    assert await _slugs(api, headers, slug=SUNRISE) == []
    assert (await api.get(f"/v1/public/societies/{GREEN_VALLEY}")).status_code == 404

    # A real society (one the hub adds in the console) is offered as before.
    await db_session.execute(
        update(Society).where(Society.id == SEED_SOCIETY_ID).values(data_source="real")
    )
    await db_session.flush()
    assert await _slugs(api, headers) == [SUNRISE]
    assert await _slugs(api, headers, slug=SUNRISE) == [SUNRISE]


async def test_live_money_refuses_to_join_a_demo_society(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients(**LIVE)
    headers = await customer(api, google)

    response = await onboard(api, headers)  # ONBOARDING asks for Sunrise Towers (seed)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "identity.society_unknown"
