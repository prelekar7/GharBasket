"""The fairness read (M8 §3.8): the λ in use and a hub's latest assignment runs."""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.partners import kitchens
from gharbasket.modules.partners.models import AssignmentRun
from gharbasket.platform.settings import get_settings
from gharbasket.tools.seed import (
    SEED_HUB_ID,
    seed,
    seed_households,
    seed_hubs,
    seed_partners,
    seed_societies,
)

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..seed_cache import seed_catalog
from .conftest import LATA, NEHA, PRIYA, SANJAY, VIKRAM, as_user, other_hub_manager
from .tiffins import EVENING, T, kitchen, tag, tiffin_order

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

URL = f"/v1/hubs/{SEED_HUB_ID}/fairness"
RUN = f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/run"


@pytest.fixture
async def world(db_session: AsyncSession) -> None:
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_hubs(db_session)
    await seed_partners(db_session)
    await tag(db_session, "tomato", "veg")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    at = [EVENING]
    monkeypatch.setattr(kitchens, "now", lambda: at[0])
    return at


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def run(
    day: date,
    *,
    meal: str = "lunch",
    round_no: int = 1,
    model: str = "cp-sat-v1",
    at: datetime | None = None,
    summary: dict[str, Any] | None = None,
    unassigned: int = 0,
    hub_id: uuid.UUID = SEED_HUB_ID,
) -> AssignmentRun:
    return AssignmentRun(
        hub_id=hub_id,
        on_date=day,
        meal=meal,
        round=round_no,
        source="fallback" if model.startswith("greedy") else "ml",
        model=model,
        status="fallback" if model.startswith("greedy") else "solved",
        portions=46,
        unassigned=[{"order_id": str(uuid.uuid4()), "reason": "no_room"}] * unassigned,
        summary=summary if summary is not None else {},
        seconds=Decimal("0.10"),
        at=at or datetime(2026, 10, 20, 16, 0).astimezone(),
    )


async def test_the_read_lists_the_runs_newest_first_with_the_lambda_in_use(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    now = datetime(2026, 10, 20, 16, 0).astimezone()
    old = run(date(2026, 10, 19), at=now - timedelta(days=1))
    new = run(
        date(2026, 10, 20),
        meal="dinner",
        round_no=2,
        at=now,
        unassigned=2,
        summary={
            "kitchens_used": 4,
            "gini": 0.14,
            "gini_eligible": 0.11,
            "km_per_portion": 1.8,
            "fairness_lambda": 300.0,
        },
    )
    other = run(date(2026, 10, 20), hub_id=uuid.uuid4(), at=now)
    db_session.add_all([old, new, other])
    await db_session.flush()

    read = await api.get(URL, headers=vikram)

    assert read.status_code == 200, read.text
    assert read.headers["Cache-Control"] == "no-store"
    body = read.json()
    assert body["hub_id"] == str(SEED_HUB_ID)
    # Paise of cost per portion of imbalance: the setting as it is, not scaled (M10 §3.5).
    assert body["lambda_paise"] == round(get_settings().partners_fairness_lambda) == 300
    assert body["fallback_active"] is False
    assert [r["run_id"] for r in body["runs"]] == [str(new.id), str(old.id)]
    first, second = body["runs"]
    assert first == {
        "run_id": str(new.id),
        "date": "2026-10-20",
        "meal": "dinner",
        "round": 2,
        "source": "ml",
        "model": "cp-sat-v1",
        "status": "solved",
        "portions": 46,
        "kitchens_used": 4,
        "unassigned": 2,
        "gini": 0.14,
        "gini_eligible": 0.11,
        "km_per_portion": 1.8,
        "lambda_paise": 300,
        "at": first["at"],
    }
    # A run from before the λ was recorded, with no numbers in its summary.
    assert (second["lambda_paise"], second["gini"], second["gini_eligible"]) == (None, None, None)
    assert (second["km_per_portion"], second["kitchens_used"], second["unassigned"]) == (None, 0, 0)


async def test_fallback_is_active_when_the_newest_run_is_the_greedy_one(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    now = datetime(2026, 10, 20, 16, 0).astimezone()
    db_session.add_all(
        [
            run(date(2026, 10, 19), at=now - timedelta(hours=5)),
            run(date(2026, 10, 20), model="greedy-m8", at=now),
        ]
    )
    await db_session.flush()

    greedy = (await api.get(URL, headers=vikram)).json()
    db_session.add(run(date(2026, 10, 21), at=now + timedelta(hours=1)))
    await db_session.flush()
    recovered = (await api.get(URL, headers=vikram)).json()

    assert greedy["fallback_active"] is True
    assert recovered["fallback_active"] is False


async def test_a_hub_with_no_runs_gets_an_empty_list(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)

    body = (await api.get(URL, headers=vikram)).json()

    assert body["runs"] == [] and body["fallback_active"] is False and body["lambda_paise"] == 300


async def test_runs_bounds_the_list_from_1_to_100(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    now = datetime(2026, 10, 20, 16, 0).astimezone()
    db_session.add_all(
        [
            run(date(2026, 10, 1) + timedelta(days=i), at=now + timedelta(minutes=i))
            for i in range(30)
        ]
    )
    await db_session.flush()

    default = await api.get(URL, headers=vikram)
    three = await api.get(f"{URL}?runs=3", headers=vikram)
    zero = await api.get(f"{URL}?runs=0", headers=vikram)
    many = await api.get(f"{URL}?runs=101", headers=vikram)

    assert len(default.json()["runs"]) == 28
    assert [r["date"] for r in three.json()["runs"]] == ["2026-10-30", "2026-10-29", "2026-10-28"]
    assert zero.status_code == 422 and many.status_code == 422
    assert zero.json()["code"] == "request.invalid"


async def test_hub_staff_and_admin_read_it_and_others_do_not(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    sanjay, _ = await as_user(api, google, SANJAY)
    neha, _ = await as_user(api, google, NEHA)
    priya, _ = await as_user(api, google, PRIYA)
    lata, _ = await as_user(api, google, LATA)
    stranger = await other_hub_manager(api, google, db_session)

    answers = {
        "franchise_owner": await api.get(URL, headers=sanjay),
        "admin": await api.get(URL, headers=neha),
        "customer": await api.get(URL, headers=priya),
        "kitchen_partner": await api.get(URL, headers=lata),
        "other_hub": await api.get(URL, headers=stranger),
        "anonymous": await api.get(URL),
    }

    assert answers["franchise_owner"].status_code == 200
    assert answers["admin"].status_code == 200
    assert answers["customer"].status_code == 403
    assert answers["kitchen_partner"].status_code == 403
    assert answers["other_hub"].status_code == 403
    assert answers["other_hub"].json()["code"] == "auth.forbidden"
    assert answers["anonymous"].status_code == 401


async def test_a_round_records_the_lambda_it_sent_and_the_read_shows_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await kitchen(db_session, LATA, "Lata tai", lunch=4)
    await tiffin_order(db_session, PRIYA, 2)

    ran = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    read = (await api.get(URL, headers=vikram)).json()

    assert ran.status_code == 200, ran.text
    # No ML service in this test: M8's greedy answered, after the request with the λ was built.
    (only,) = read["runs"]
    assert (only["model"], only["source"], only["lambda_paise"]) == ("greedy-m8", "fallback", 300)
    assert only["gini"] is not None and only["kitchens_used"] == 1
    assert read["fallback_active"] is True


async def test_an_ml_run_without_a_summary_still_records_the_lambda(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients(ml_service_url="http://ml.test")
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=5)
    priya = await tiffin_order(db_session, PRIYA, 2)

    async def solve(
        http: httpx2.AsyncClient, settings: Any, request: intel_api.AssignRequest, **kw: Any
    ) -> intel_api.KitchenAssignment:
        return intel_api.KitchenAssignment.model_validate(
            {
                "status": "solved",
                "assignments": [
                    {"order_id": str(priya), "kitchen_id": str(lata), "portions": 2, "km": 0.4}
                ],
                "unassigned": [],
                "summary": None,
                "seconds": 0.2,
                "model": "cp-sat-v1",
            }
        )

    monkeypatch.setattr(intel_api, "assign_kitchens", solve)
    ran = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    read = (await api.get(URL, headers=vikram)).json()
    assert ran.status_code == 200, ran.text
    (only,) = read["runs"]
    assert (only["source"], only["model"], only["lambda_paise"]) == ("ml", "cp-sat-v1", 300)
    assert read["fallback_active"] is False


async def test_a_round_with_nothing_to_solve_records_no_lambda(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await tiffin_order(db_session, PRIYA, 2)  # no kitchen has room

    ran = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    assert ran.status_code == 200, ran.text
    (only,) = (await api.get(URL, headers=vikram)).json()["runs"]
    assert (only["model"], only["lambda_paise"]) == ("no-kitchens-m8", None)
