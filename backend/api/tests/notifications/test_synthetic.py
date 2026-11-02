"""Synthetic accounts (ADR-0008) get no push or WhatsApp outside dev and test (M11 §3.1): the
simulated weeks loaded into staging (`scripts/staging-demo.sh`) must never reach a phone."""

import json
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User
from gharbasket.modules.notifications.jobs import SYNTHETIC
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import JobRun

from ..conftest import DEPLOYED_SECRETS, make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import PRIYA, FakeFcm, Home
from .test_messages import NOON, deliver, messages_of
from .test_send import locked, push_of, sign_in_devices
from .test_whatsapp import WHATSAPP, wants_whatsapp, whatsapp_of

pytestmark = [pytest.mark.anyio, pytest.mark.services]


def nothing_goes_out(request: httpx2.Request) -> httpx2.Response:
    raise AssertionError(f"a synthetic account's message went out: {request.url}")


async def send_on_staging(
    db: AsyncSession, http: httpx2.AsyncClient, account: dict[str, Any]
) -> dict[str, Any]:
    settings = make_settings(
        app_env="staging",
        **DEPLOYED_SECRETS,
        **WHATSAPP,
        fcm_service_account=json.dumps(account),
    )
    run: JobRun = await run_job(db, "send_deliveries", settings=settings, http=http)
    assert run.status == "ok", run.error
    assert run.summary is not None
    return run.summary


@time_machine.travel(NOON, tick=False)
async def test_on_staging_a_synthetic_account_keeps_its_inbox_but_nothing_reaches_a_phone(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    homes: dict[str, Home],
    fcm: FakeFcm,
    service_account: dict[str, Any],
) -> None:
    priya = homes[PRIYA]  # a seed person, so synthetic
    assert await identity_api.is_synthetic_user(db_session, priya.user_id)
    await sign_in_devices(clients, google, PRIYA, "phone-a")
    await wants_whatsapp(db_session, priya.user_id)  # money on WhatsApp, with consent
    on_whatsapp = await deliver(db_session, "orders.locked", locked(priya))
    pushed = await deliver(db_session, "orders.delivered", locked(priya))

    blocked = httpx2.AsyncClient(transport=httpx2.MockTransport(nothing_goes_out))
    summary = await send_on_staging(db_session, blocked, service_account)

    assert summary == {"sent": 0, "retrying": 0, "failed": 0, "skipped": 2}
    for delivery in (
        await whatsapp_of(db_session, on_whatsapp),
        await push_of(db_session, pushed),
    ):
        assert (delivery.status, delivery.attempts, delivery.last_error) == (
            "skipped",
            0,
            SYNTHETIC,
        )
    assert len(await messages_of(db_session, on_whatsapp)) == 1  # the inbox still has them
    assert len(await messages_of(db_session, pushed)) == 1

    # A real account on staging is pushed as before.
    await db_session.execute(
        update(User).where(User.id == priya.user_id).values(data_source="real")
    )
    real = await deliver(db_session, "orders.delivered", locked(priya))
    assert (await send_on_staging(db_session, fcm.http, service_account))["sent"] == 1
    assert (await push_of(db_session, real)).status == "sent"
    assert [s["token"] for s in fcm.sends] == ["phone-a"]
