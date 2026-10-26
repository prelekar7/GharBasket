"""M7 in the data export and the account purge (M1 §3.3, §3.5; M7 Privacy): a household's
deliveries without photos or codes, a rider's routes and pings; the purge deletes the pings and the
door photos and nulls the user ids on what they did."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity import jobs as identity_jobs
from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.logistics import tracking
from gharbasket.modules.logistics.models import (
    Delivery,
    FieldEvent,
    GpsPing,
    Handover,
    Route,
    Vehicle,
)
from gharbasket.modules.logistics.schemas import PingIn
from gharbasket.platform.files import ObjectStore, create_store
from gharbasket.platform.models import Upload
from gharbasket.tools.seed import SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..test_files import SETTINGS as STORAGE_SETTINGS
from ..test_files import FakeS3
from .conftest import ARJUN, MEENA, MORNING, SUNITA, as_user, key, pack, published_day, user_id
from .test_review_fixes import GREEN_VALLEY, bag, stops_of, upload

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]


async def test_the_logistics_section_and_eraser_are_registered_where_m1_lists_modules() -> None:
    modules = [name for name, _ in identity_jobs.ERASERS]
    assert modules.index("logistics") == modules.index("hub_ops") + 1  # M1 §3.5 matrix order
    assert identity_jobs.SECTIONS["logistics"] is logistics_api.export_for_user


@time_machine.travel(MORNING, tick=False)
async def test_export_and_erasure_of_a_rider_and_a_household(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    s3 = FakeS3()
    store: ObjectStore | None = create_store(STORAGE_SETTINGS)
    assert store is not None
    store.client = s3
    api = clients(storage=store)
    day = await published_day(db_session)
    await pack(db_session, *day.orders.values())
    rider, captain = await as_user(api, google, ARJUN), await as_user(api, google, MEENA)
    started = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    assert started.status_code == 200, started.text
    arjun = await user_id(db_session, ARJUN)
    meena = await user_id(db_session, MEENA)
    sunita_user = await user_id(db_session, SUNITA)
    for n in range(3):
        ping = PingIn(
            at=MORNING - timedelta(minutes=n), lat=19.03 + n / 1000, lng=73.03, accuracy_m=8
        )
        await tracking.insert_pings(db_session, [tracking._row(arjun, day.route_id, ping)])  # pyright: ignore[reportPrivateUsage]
    stops = await stops_of(db_session, day.route_id)
    sunrise = stops[SEED_SOCIETY_ID]
    assert (
        await api.post(
            f"/v1/stops/{sunrise.id}/handover",
            json={"crates": 1, "bags": 2, "handover_code": sunrise.handover_code},
            headers={**captain, **key()},
        )
    ).status_code == 200
    assert (
        await api.post(
            f"/v1/stops/{stops[GREEN_VALLEY].id}/handover",
            json={"crates": 1, "bags": 1},
            headers={**rider, **key()},
        )
    ).status_code == 200
    door, sunita = await upload(db_session, s3, arjun), day.orders[SUNITA]
    delivered = await api.post(
        f"/v1/deliveries/{sunita}/proof",
        json={"bag_qr": bag(sunita), "proof": "photo", "photo_key": door},
        headers={**rider, **key()},
    )
    assert delivered.status_code == 200, delivered.text

    # The household sees its delivery with the proof kind and no photo, code or note.
    household = await identity_api.household_for_user(db_session, sunita_user)
    assert household is not None
    mine = await logistics_api.export_for_user(db_session, sunita_user)
    [row] = mine["deliveries"]
    assert (row["order_id"], row["status"], row["proof"], row["attempt"]) == (
        sunita,
        "delivered",
        "photo",
        1,
    )
    assert not {"photo_key", "delivery_code", "note", "user_id"} & set(row)
    assert mine["routes"] == [] and mine["gps_pings"] == []

    # The rider's export: the route, three pings (oldest first), the handover they made.
    theirs = await logistics_api.export_for_user(db_session, arjun)
    assert [r["id"] for r in theirs["routes"]] == [day.route_id]
    assert [p["accuracy_m"] for p in theirs["gps_pings"]] == [8, 8, 8]
    assert theirs["gps_pings"][0]["at"] < theirs["gps_pings"][-1]["at"]
    assert round(theirs["gps_pings"][0]["lat"], 3) == 19.032 and not theirs["gps_pings_truncated"]
    assert [h["by_actor"] for h in theirs["handovers"]] == ["rider"]
    assert theirs["deliveries"] == []

    # The household's purge: the door photo is due at once and the OTP record cleared.
    counts = await logistics_api.anonymise_for_user(db_session, sunita_user, household.id)
    assert counts["delivery_proofs"] == 1 and counts["gps_pings"] == 0
    attempt = await db_session.scalar(
        select(Delivery)
        .where(Delivery.order_id == sunita)
        .execution_options(populate_existing=True)
    )
    assert attempt is not None
    assert (attempt.photo_key, attempt.delivery_code, attempt.note) == (None, "", None)
    photo = await db_session.get(Upload, door, populate_existing=True)
    assert photo is not None and photo.delete_after is not None
    assert photo.delete_after <= datetime.now(UTC)

    # The rider's purge: pings gone, their user id off everything they touched.
    erased = await logistics_api.anonymise_for_user(db_session, arjun, None)
    assert erased["gps_pings"] == 3 and erased["handovers"] == 1 and erased["routes"] == 1
    assert erased["field_events"] >= 3 and erased["deliveries"] >= 1
    assert await db_session.scalar(select(func.count()).select_from(GpsPing)) == 0
    for model, column in (
        (Handover, Handover.user_id),
        (Delivery, Delivery.user_id),
        (FieldEvent, FieldEvent.user_id),
        (Route, Route.rider_user_id),
        (Vehicle, Vehicle.default_rider_user_id),
    ):
        left = await db_session.scalar(
            select(func.count()).select_from(model).where(column == arjun)
        )
        assert left == 0, model.__name__
    # The captain's handover keeps its time and counts, not the person.
    captain_row = await db_session.scalar(
        select(Handover)
        .where(Handover.stop_id == sunrise.id)
        .execution_options(populate_existing=True)
    )
    assert captain_row is not None and captain_row.user_id == meena
    assert (await logistics_api.anonymise_for_user(db_session, uuid.uuid4(), None)) == {
        "delivery_proofs": 0,
        "gps_pings": 0,
        "handovers": 0,
        "deliveries": 0,
        "field_events": 0,
        "routes": 0,
        "vehicles": 0,
        "pickups": 0,
    }
