"""Fixes to the tiffin assignment (M8 §3.4) from the review of the first chain: offers made after
the last 15-minute run still expire, a new weekly default leaves closed dates alone, a malformed
ML plan never blocks the evening, one hub's failure keeps the others, manual assignment takes the
hub's lock, and the run records what actually answered."""

import types
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.intel_gateway import service as intel_service
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import assignments, cooking, kitchens
from gharbasket.modules.partners.models import Assignment, AssignmentOrder, AssignmentRun
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import lock_key
from gharbasket.tools.seed import (
    SEED_HUB_ID,
)

from .. import seed_cache as cached
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import LATA, MEENA, PRIYA, SUNITA, VIKRAM, as_user
from .test_cooking import CHECKLIST, ON_THE_DAY
from .tiffins import EVENING, T, kitchen, tag, tiffin_order

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("tiffin_world")]

RUN = f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/run"
MANUAL = f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/manual"
AFTER_LAST_JOB = datetime(2027, 1, 14, 18, 40, tzinfo=UTC)  # T 00:10 IST, after T-1 21:45-00:30?
THURSDAY = datetime(2027, 1, 14, 4, 30, tzinfo=UTC)  # 10:00 IST: today closed, tomorrow open


@pytest.fixture
async def tiffin_world(db_session: AsyncSession) -> None:
    await cached.seed(db_session)
    await cached.seed_societies(db_session)
    await cached.seed_households(db_session)
    await cached.seed_catalog(db_session)
    await cached.seed_hubs(db_session)
    await cached.seed_partners(db_session)
    await tag(db_session, "tomato", "veg")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    at = [EVENING]
    monkeypatch.setattr(kitchens, "now", lambda: at[0])
    return at


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json().get("code", "")


def manual_body(partner: uuid.UUID, *orders: uuid.UUID) -> dict[str, Any]:
    return {
        "date": T.isoformat(),
        "order_ids": [str(o) for o in orders],
        "partner_id": str(partner),
        "note": "by phone",
    }


# --- 1. an offer made after T 00:30 expires when the next thing needs its orders ---------------


async def test_an_offer_made_after_the_last_job_expires_and_does_not_hold_its_orders(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=3)
    sunita = await kitchen(db_session, SUNITA, "Sunita tai", lunch=3)
    order = await tiffin_order(db_session, PRIYA, 2)
    clock[0] = AFTER_LAST_JOB
    ran = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    assert ran.json()["offers"] == 1, ran.text
    (offer,) = (await db_session.execute(select(Assignment))).scalars().all()
    assert offer.status == "offered" and offer.respond_by > clock[0]

    clock[0] = datetime(2027, 1, 14, 19, 30, tzinfo=UTC)  # T 01:00 IST: the offer is overdue
    made = await api.post(MANUAL, json=manual_body(sunita, order), headers={**vikram, **key()})

    assert made.status_code == 200, made.text
    await db_session.refresh(offer)
    assert offer.status == "expired"
    held = (
        await db_session.execute(select(AssignmentOrder.status, AssignmentOrder.order_id))
    ).all()
    assert sorted(held) == sorted([("released", order), ("assigned", order)])
    assert lata != sunita  # her offer is gone: its capacity is free again
    capacity = await kitchens.assigned_portions(db_session, lata, T, T)
    assert capacity == {}


async def test_a_dead_offer_does_not_keep_a_paid_lunch_from_its_refund(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await kitchen(db_session, LATA, "Lata tai", lunch=3)
    order = await tiffin_order(db_session, PRIYA, 2)
    clock[0] = AFTER_LAST_JOB
    await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    refunded = await api.post(
        "/v1/internal/jobs/refund_unassigned_tiffins",
        json={"scheduled_for": "2027-01-15T04:00:00Z"},  # T 09:30 IST
    )

    assert refunded.json()["summary"]["refunded"] == 1
    assert (await orders_api.get(db_session, order)).status == "refunded"


async def test_a_round_run_late_takes_back_orders_of_an_overdue_offer(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await kitchen(db_session, LATA, "Lata tai", lunch=3)
    await tiffin_order(db_session, PRIYA, 2)
    clock[0] = AFTER_LAST_JOB
    await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    clock[0] = datetime(2027, 1, 14, 19, 30, tzinfo=UTC)
    await kitchen(db_session, SUNITA, "Sunita tai", lunch=3)

    again = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    assert (again.json()["offers"], again.json()["unassigned"]) == (1, 0)  # to Sunita, not Lata


# --- 2. a new weekly default leaves closed dates alone -----------------------------------------


async def test_a_new_weekly_default_does_not_change_the_capacity_of_a_closed_date(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    await kitchen(db_session, LATA, "Lata tai", lunch=10)
    lata, _ = await as_user(api, google, LATA)

    async def lunch_on_each_day() -> dict[str, tuple[int, str]]:
        read = await api.get("/v1/kitchen-capacity?from=2027-01-14&to=2027-01-16", headers=lata)
        return {
            i["date"]: (i["max_portions"], i["source"])
            for i in read.json()["items"]
            if i["meal"] == "lunch"
        }

    def week(lunch: int) -> dict[str, list[int]]:
        return {"lunch": [lunch] * 7, "dinner": [0] * 7}

    clock[0] = THURSDAY  # today (Thu) is closed since Wednesday 21:00; Friday is open
    first = await api.patch(
        "/v1/kitchens/me", json={"default_portions": week(3)}, headers={**lata, "If-Match": '"1"'}
    )
    assert first.status_code == 200, first.text
    assert await lunch_on_each_day() == {
        "2027-01-14": (10, "set"),  # kept what she had when it closed
        "2027-01-15": (3, "default"),  # still open: follows the new default
        "2027-01-16": (3, "default"),
    }

    clock[0] = EVENING  # 21:30: Friday closed too
    second = await api.patch(
        "/v1/kitchens/me", json={"default_portions": week(7)}, headers={**lata, "If-Match": '"2"'}
    )
    assert second.status_code == 200, second.text
    assert await lunch_on_each_day() == {
        "2027-01-14": (10, "set"),
        "2027-01-15": (3, "set"),  # the default she had at 21:00
        "2027-01-16": (7, "default"),
    }
    # An explicit override is never touched, and a patch without portions pins nothing.
    clock[0] = THURSDAY
    set_ = await api.put(
        "/v1/kitchen-capacity",
        json={"date": "2027-01-16", "meal": "lunch", "max_portions": 1},
        headers=lata,
    )
    assert set_.status_code == 200
    third = await api.patch(
        "/v1/kitchens/me", json={"default_portions": week(9)}, headers={**lata, "If-Match": '"3"'}
    )
    assert third.status_code == 200
    assert (await lunch_on_each_day())["2027-01-16"] == (1, "set")


# --- 3. a malformed ML plan falls back to the greedy -------------------------------------------


def plan(assignments_: list[dict[str, Any]], unassigned: list[dict[str, str]]) -> Any:
    return intel_api.KitchenAssignment.model_validate(
        {
            "status": "solved",
            "assignments": assignments_,
            "unassigned": unassigned,
            "summary": None,
            "seconds": 0.1,
            "model": "cp-sat-v1",
        }
    )


@pytest.mark.parametrize("mistake", ["unknown_order", "duplicate", "over_capacity", "bad_kitchen"])
async def test_a_malformed_plan_is_logged_and_the_greedy_answers_instead(
    mistake: str,
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=3)
    big = await tiffin_order(db_session, PRIYA, 2)
    small = await tiffin_order(db_session, MEENA, 2)

    def row(
        order: uuid.UUID | str, kitchen_id: uuid.UUID | str, portions: int = 2
    ) -> dict[str, Any]:
        return {
            "order_id": str(order),
            "kitchen_id": str(kitchen_id),
            "portions": portions,
            "km": 0.4,
        }

    bad = {
        "unknown_order": [row(uuid.uuid4(), lata)],
        "duplicate": [row(big, lata), row(big, lata)],
        "over_capacity": [row(big, lata), row(small, lata)],  # 4 portions in a room of 3
        "bad_kitchen": [row(big, uuid.uuid4())],
    }[mistake]

    async def solve(*args: Any, **kw: Any) -> Any:
        return plan(bad, [])

    monkeypatch.setattr(intel_api, "assign_kitchens", solve)
    warned: list[str] = []
    monkeypatch.setattr(
        assignments, "log", types.SimpleNamespace(warning=lambda event, **kw: warned.append(event))
    )  # structlog caches a logger once used, which capture_logs then misses
    ran = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    assert ran.status_code == 200, ran.text
    run = (await db_session.execute(select(AssignmentRun))).scalar_one()
    assert (run.source, run.model) == ("fallback", "greedy-m8")  # M8's own answer
    # The greedy fits one of the two orders in Lata's 3 portions; the other is left out.
    assert (ran.json()["offers"], ran.json()["unassigned"]) == (1, 1)
    assert warned == ["assignment_plan_rejected"]


async def test_an_order_the_plan_does_not_mention_is_not_lost(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=5)
    placed = await tiffin_order(db_session, PRIYA, 2)
    missing = await tiffin_order(db_session, MEENA, 1)

    async def solve(*args: Any, **kw: Any) -> Any:
        return plan(
            [{"order_id": str(placed), "kitchen_id": str(lata), "portions": 2, "km": 0.3}], []
        )

    monkeypatch.setattr(intel_api, "assign_kitchens", solve)
    ran = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    run = (await db_session.execute(select(AssignmentRun))).scalar_one()
    assert (ran.json()["offers"], ran.json()["unassigned"]) == (1, 1)
    assert run.source == "ml" and run.unassigned == [
        {"order_id": str(missing), "reason": "no_room"}
    ]


async def test_one_hubs_failure_keeps_the_other_hubs_offers(
    clients: ClientFactory,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    await kitchen(db_session, LATA, "Lata tai", lunch=3)
    await tiffin_order(db_session, PRIYA, 2)
    broken = uuid.uuid4()
    real_round = assignments.run_round

    async def active(session: AsyncSession) -> list[uuid.UUID]:
        return [broken, SEED_HUB_ID]

    async def run_round(
        session: AsyncSession, http: Any, settings: Any, hub_id: uuid.UUID, *a: Any, **kw: Any
    ) -> Any:
        if hub_id == broken:
            await session.execute(text("SELECT 1/0"))  # a database error poisons a transaction
        return await real_round(session, http, settings, hub_id, *a, **kw)

    monkeypatch.setattr("gharbasket.modules.hub_ops.api.active_hub_ids", active)
    monkeypatch.setattr(assignments, "run_round", run_round)

    ran = await api.post(
        "/v1/internal/jobs/assign_kitchens", json={"scheduled_for": EVENING.isoformat()}
    )

    assert ran.status_code == 200, ran.text
    assert ran.json()["summary"] == {
        "delivery_date": T.isoformat(),
        "runs": 1,
        "offers": 1,
        "unassigned": 0,
        "failed_hubs": [str(broken)],
    }
    assert len((await db_session.execute(select(Assignment))).scalars().all()) == 1


# --- 4. the summary counts as M10's does -------------------------------------------------------


async def test_the_fallbacks_summary_is_M10s_on_the_same_input() -> None:
    from gharbasket_ml import assignment as ml
    from gharbasket_ml.schemas import AssignRequest

    from gharbasket.modules.partners import matching

    a, b, z = (uuid.UUID(int=n) for n in (1, 2, 3))
    here, near, far = (19.0395, 73.0675), (19.0430, 73.0720), (19.0300, 73.0600)
    orders = [(uuid.UUID(int=10), 2), (uuid.UUID(int=11), 1)]
    kitchens_ = [(a, here, 0, 4.5), (b, near, 0, 4.5), (z, far, 12_000, 3.0)]  # z: under the floor
    request = AssignRequest.model_validate(
        {
            "orders": [
                {"id": str(i), "lat": here[0], "lng": here[1], "portions": p, "skills": []}
                for i, p in orders
            ],
            "kitchens": [
                {
                    "id": str(i),
                    "lat": at[0],
                    "lng": at[1],
                    "max_portions": 4,
                    "skills": [],
                    "rating": r,
                    "earnings_paise": e,
                }
                for i, at, e, r in kitchens_
            ],
            "paise_per_portion": 6000,
        }
    )
    theirs = ml.baseline(request).summary
    assert theirs is not None

    cooks = [matching.Cook(i, at[0], at[1], 4, frozenset(), r, e) for i, at, e, r in kitchens_]
    todo = [matching.Order(i, here[0], here[1], p, frozenset()) for i, p in orders]
    eligible = matching.eligible_ids(todo, cooks)
    chosen, _ = matching.greedy(todo, cooks)
    ours = matching.summary(chosen, cooks, eligible)

    assert ours["gini"] != ours["gini_eligible"]  # the under-floor kitchen counts in one only
    assert ours["gini"] == pytest.approx(theirs.gini, abs=0.001)
    assert ours["gini_eligible"] == pytest.approx(theirs.gini_eligible, abs=0.001)
    assert (ours["portions"], ours["kitchens_used"]) == (theirs.portions, theirs.kitchens_used)
    assert ours["km_per_portion"] == pytest.approx(theirs.km_per_portion, abs=0.01)


async def test_plan_problems_are_named() -> None:
    from gharbasket.modules.partners import matching

    a, k = uuid.UUID(int=1), uuid.UUID(int=2)
    orders = [matching.Order(a, 19.0, 73.0, 2, frozenset())]
    cooks = [matching.Cook(k, 19.0, 73.0, 3, frozenset(), 4.5, 0)]
    ok = [(str(a), str(k), 2)]
    assert matching.plan_problem(orders, cooks, ok, []) is None
    assert matching.plan_problem(orders, cooks, [], [str(a)]) is None
    assert matching.plan_problem(orders, cooks, [(str(uuid.uuid4()), str(k), 2)], [])
    assert matching.plan_problem(orders, cooks, [(str(a), str(uuid.uuid4()), 2)], [])
    assert matching.plan_problem(orders, cooks, ok + ok, [])
    assert matching.plan_problem(orders, cooks, [(str(a), str(k), 4)], [])  # not its portions
    assert matching.plan_problem(orders, cooks, ok, [str(a)])  # assigned and left out
    assert matching.plan_problem(orders, cooks, [("x", str(k), 2)], [])
    assert matching.plan_problem(orders, cooks, [], ["x"])
    assert matching.plan_problem(
        orders, [matching.Cook(k, 19.0, 73.0, 1, frozenset(), 4.5, 0)], ok, []
    )
    # The pair must be one the rules allow: skills, the rating floor and reach.
    cannot = "a kitchen that cannot cook the order"
    jain = [matching.Order(a, 19.0, 73.0, 2, frozenset({"jain"}))]
    assert matching.plan_problem(jain, cooks, ok, []) == cannot
    low = [matching.Cook(k, 19.0, 73.0, 3, frozenset(), 3.0, 0)]
    assert matching.plan_problem(orders, low, ok, []) == cannot
    far = [matching.Cook(k, 19.5, 73.5, 3, frozenset(), 4.5, 0)]
    assert matching.plan_problem(orders, far, ok, []) == cannot


# --- 5. manual assignment and the refund take the hub's lock -----------------------------------


async def advisory_lock_held(db: AsyncSession) -> bool:
    key_ = lock_key(f"partners.assign:{SEED_HUB_ID}")
    held = await db.scalar(
        text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()"
            " AND ((classid::bigint << 32) | objid::bigint) = :key"
        ),
        {"key": key_},
    )
    return bool(held)


async def test_manual_assignment_and_the_refund_job_take_the_rounds_lock(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=3)
    order = await tiffin_order(db_session, PRIYA, 2)
    other = await tiffin_order(db_session, MEENA, 1)
    assert not await advisory_lock_held(db_session)

    made = await api.post(MANUAL, json=manual_body(lata, order), headers={**vikram, **key()})
    assert made.status_code == 200, made.text
    assert await advisory_lock_held(db_session)

    # The refund job takes it too: a fresh transaction's lock shows in the same backend.
    await db_session.commit()
    refund = await api.post(
        "/v1/internal/jobs/refund_unassigned_tiffins",
        json={"scheduled_for": "2027-01-15T04:00:00Z"},
    )
    assert refund.json()["summary"]["refunded"] == 1  # `other`, which nobody took
    assert (await orders_api.get(db_session, other)).status == "refunded"


async def test_a_unique_violation_in_manual_assignment_is_a_409_not_a_500(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=3)
    order = await tiffin_order(db_session, PRIYA, 2)

    async def collide(*args: Any, **kw: Any) -> Any:
        raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    monkeypatch.setattr(assignments, "create", collide)
    answer = await api.post(MANUAL, json=manual_body(lata, order), headers={**vikram, **key()})

    assert code(answer) == (409, "partners.order_not_assignable")
    assert answer.json()["order_ids"] == [str(order)]


# --- nits --------------------------------------------------------------------------------------


async def test_a_run_nothing_had_to_solve_is_not_recorded_as_a_fallback_by_the_greedy(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import update

    from gharbasket.modules.orders.models import Order

    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=5)
    order = await tiffin_order(db_session, PRIYA, 2)
    await db_session.execute(
        update(Order).where(Order.id == order).values(preferred_partner_id=lata)
    )
    await db_session.commit()

    async def never(*args: Any, **kw: Any) -> Any:
        raise AssertionError("the solver is not asked when every order has its cook")

    monkeypatch.setattr(intel_api, "assign_kitchens", never)
    ran = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    assert ran.json()["offers"] == 1, ran.text
    run = (await db_session.execute(select(AssignmentRun))).scalar_one()
    assert (run.model, run.status, run.unassigned) == ("preferred-m8", "solved", [])

    # And with no kitchen to ask at all.
    await db_session.execute(text("DELETE FROM partners.assignment_events"))
    await db_session.execute(text("DELETE FROM partners.assignment_orders"))
    await db_session.execute(text("DELETE FROM partners.assignments"))
    await db_session.execute(text("DELETE FROM partners.assignment_runs"))
    await db_session.execute(text("DELETE FROM partners.kitchens"))
    await db_session.commit()
    again = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    assert (again.json()["offers"], again.json()["unassigned"]) == (0, 1)
    (second,) = (await db_session.execute(select(AssignmentRun))).scalars().all()
    assert (second.model, second.status) == ("no-kitchens-m8", "infeasible")


async def test_a_ready_request_with_a_repeated_order_id_works_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await kitchen(db_session, LATA, "Lata tai", lunch=3)
    order = await tiffin_order(db_session, PRIYA, 2)
    await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    lata, _ = await as_user(api, google, LATA)
    (offer,) = (await api.get(f"/v1/assignments?date={T}", headers=lata)).json()["items"]
    await api.post(f"/v1/assignments/{offer['id']}/accept", headers={**lata, **key()})
    clock[0] = ON_THE_DAY

    ready = await api.post(
        f"/v1/assignments/{offer['id']}/ready",
        json={"order_ids": [str(order), str(order)], "checklist": CHECKLIST},
        headers={**lata, **key()},
    )

    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "ready"


async def test_one_hubs_failure_keeps_the_other_hubs_refunds(
    clients: ClientFactory,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    orphan = await tiffin_order(db_session, PRIYA, 2)  # nobody cooks it
    broken = uuid.uuid4()
    real_refund = cooking.refund_unassigned

    async def active(session: AsyncSession) -> list[uuid.UUID]:
        return [broken, SEED_HUB_ID]

    async def refund(
        session: AsyncSession, hub_id: uuid.UUID, *args: Any, **kw: Any
    ) -> tuple[int, int]:
        if hub_id == broken:
            await session.execute(text("SELECT 1/0"))  # poisons the transaction
        return await real_refund(session, hub_id, *args, **kw)

    monkeypatch.setattr("gharbasket.modules.hub_ops.api.active_hub_ids", active)
    monkeypatch.setattr(cooking, "refund_unassigned", refund)

    ran = await api.post(
        "/v1/internal/jobs/refund_unassigned_tiffins",
        json={"scheduled_for": "2027-01-15T04:00:00Z"},  # T 09:30 IST
    )

    assert ran.status_code == 200, ran.text
    assert ran.json()["summary"] == {
        "delivery_date": T.isoformat(),
        "refunded": 1,
        "refused": 0,
        "failed_hubs": [str(broken)],
    }
    assert (await orders_api.get(db_session, orphan)).status == "refunded"


# --- lock order, deadlocks and the plan's pairs --------------------------------------------------


async def test_the_15_minute_job_takes_each_hubs_lock_before_that_hubs_offers(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await kitchen(db_session, LATA, "Lata tai", lunch=3)
    await tiffin_order(db_session, PRIYA, 2)
    await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    await db_session.commit()
    events: list[tuple[str, uuid.UUID | None]] = []
    real_expire, real_lock = assignments.expire_overdue, assignments.lock_hub

    async def expire(session: AsyncSession, at: datetime, hub_id: uuid.UUID | None = None) -> int:
        events.append(("expire", hub_id))
        return await real_expire(session, at, hub_id)

    async def lock(session: AsyncSession, hub_id: uuid.UUID) -> None:
        events.append(("lock", hub_id))
        await real_lock(session, hub_id)

    monkeypatch.setattr(assignments, "expire_overdue", expire)
    monkeypatch.setattr(assignments, "lock_hub", lock)

    for when in ("2027-01-14T16:15:00Z", "2027-01-14T19:00:00Z"):  # 21:45 and 00:30 IST
        events.clear()
        done = await api.post(
            "/v1/internal/jobs/assignment_acceptance_timeout", json={"scheduled_for": when}
        )
        assert done.status_code == 200, done.text
        assert ("expire", None) not in events  # never a global sweep over every hub's rows
        for n, (what, hub) in enumerate(events):
            if what == "expire":
                assert ("lock", hub) in events[:n], events  # the hub's lock first, then its rows
        assert ("lock", SEED_HUB_ID) in events


async def test_a_deadlock_or_serialization_failure_is_a_retryable_409(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=3)
    order = await tiffin_order(db_session, PRIYA, 2)

    class Deadlock(Exception):
        sqlstate = "40P01"

    async def deadlocked(*args: Any, **kw: Any) -> Any:
        raise DBAPIError("UPDATE", {}, Deadlock("deadlock detected"))

    monkeypatch.setattr(cooking, "manual", deadlocked)
    monkeypatch.setattr(assignments, "run_round", deadlocked)
    manual = await api.post(MANUAL, json=manual_body(lata, order), headers={**vikram, **key()})
    run = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    assert code(manual) == (409, "partners.assignment_busy")
    assert code(run) == (409, "partners.assignment_busy")

    class Other(Exception):
        sqlstate = "23505"

    with pytest.raises(DBAPIError), assignments.retry_on_conflict():  # others are not hidden
        raise DBAPIError("INSERT", {}, Other("duplicate"))
    with pytest.raises(ProblemError) as busy, assignments.retry_on_conflict():
        raise DBAPIError("SELECT", {}, type("S", (Exception,), {"sqlstate": "40001"})("x"))
    assert busy.value.code == "partners.assignment_busy"


async def test_a_plan_that_sends_a_jain_order_to_a_kitchen_without_the_skill_is_rejected(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await tag(db_session, "potato", "veg", "jain")
    veg_only = await kitchen(db_session, LATA, "Lata tai", lunch=5, skills=("veg",))
    jain = await tiffin_order(db_session, PRIYA, 2, slug="potato")
    warned: list[str] = []
    monkeypatch.setattr(
        assignments, "log", types.SimpleNamespace(warning=lambda event, **kw: warned.append(event))
    )

    async def solve(
        http: Any,
        settings: Any,
        request: Any,
        *,
        session: AsyncSession,
        hub_id: uuid.UUID,
        **kw: Any,
    ) -> Any:
        answer = plan(
            [{"order_id": str(jain), "kitchen_id": str(veg_only), "portions": 2, "km": 0.3}], []
        )
        await intel_service.record_prediction(
            session,
            kind="assignment",
            subject_type="hub",
            subject_id=hub_id,
            for_date=T,
            source="ml",
            payload=answer.model_dump(mode="json"),
            model_name=answer.model,
        )
        return answer

    monkeypatch.setattr(intel_api, "assign_kitchens", solve)
    ran = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    assert ran.status_code == 200, ran.text
    assert warned == ["assignment_plan_rejected"]
    assert (ran.json()["offers"], ran.json()["unassigned"]) == (0, 1)  # no skill: nobody cooks it
    kept = await intel_service.latest_prediction(
        db_session, kind="assignment", subject_id=SEED_HUB_ID, for_date=T
    )
    assert kept is not None and kept.source == "fallback"
    assert kept.payload["rejected"] == "a kitchen that cannot cook the order"
    # A prediction that is not there, or not the ML's, is left alone.
    assert not await intel_api.reject_assignment_plan(db_session, SEED_HUB_ID, T, "again")
    assert not await intel_api.reject_assignment_plan(db_session, uuid.uuid4(), T, "nothing")
