"""Two MS-B journeys through the real HTTP API, by the real roles, on the database the simulated
day left behind (it runs first, see `conftest.py`):

1. A neighbour becomes a home-kitchen partner (M8 §3.1, §3.2): she applies, uploads her KYC
   documents (the photos and the FSSAI card as real files in SeaweedFS), passes the training, and
   the hub manager reviews and approves her.
2. That kitchen cooks a tiffin day (M8 §3.4, §3.5, M4 §3.5, M5 §3.5): her menu is approved and she
   sets her capacity, two households book it, the 21:30 round assigns them (the greedy fallback,
   no ML), she accepts, cooks and readies, M7's 09:35 solve routes her lunch (M7 §3.7), the rider
   collects by her pickup QR, the captain takes the tiffins by count and delivers them door by
   door, the customers rate her, and `weekly_payouts` pays her, because she is KYC'd.

It lives on its own dates (two weeks after the day) and its own phones, so it asserts only on its
own people and orders. One thing is not reachable over HTTP on main, and is done as the stand-in
the spec names: a kitchen's own menu write (M2 §3.4: "nobody but the seed") is a row in
`catalog.tiffin_menus` that the hub manager then approves over HTTP. The database stamps an order's
timeline with its own clock, which no test can move, so the dispatch and delivery rows get the
step's time (the rating window and the earning's date read them).
"""

import json
import os
from asyncio import Runner
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx2
import invariants
import pytest
from harness import REPO, App, Config, ist, key
from sqlalchemy import text
from test_simulated_day import VIKRAM, database, scalar, step

from gharbasket.platform import outbox
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

# Open product bugs this journey found, by the invariant that shows them (as in the day).
BUGS: dict[str, str] = {}
KITCHEN = "+919800004001"  # the neighbour who applies
ARJUN = "+919800001006"  # the seed hub's rider
MEENA = "+919800001004"  # Sunrise Towers' captain
CUSTOMERS = {"+919800004002": 2, "+919800004003": 1}  # phone -> portions booked
HUB = f"/v1/hubs/{SEED_HUB_ID}"
PRICE_PAISE = 12_000  # a portion on the menu
TRAININGS = json.loads((REPO / "data/seeds/trainings/modules.json").read_text())["modules"]
BYTES = {  # what the applicant uploads, so the read-back can be compared
    "fssai": [b"synthetic e2e FSSAI card"],
    "kitchen_photos": [b"synthetic e2e kitchen photo 1", b"synthetic e2e kitchen photo 2"],
}
DOCUMENTS: list[dict[str, Any]] = [  # synthetic numbers only: the repo is public, and no Aadhaar
    {"doc_type": "id_proof", "id_kind": "voter_id", "number": "QZX3948201"},
    {"doc_type": "pan", "number": "QZXJA3948K"},
    {
        "doc_type": "bank",
        "number": "50100399482",
        "ifsc": "SBIN0003948",
        "account_holder": "Asha Patil",
    },
    {"doc_type": "fssai", "number": "11599939482001", "valid_until": "2031-03-31"},
    {"doc_type": "kitchen_photos"},
]


@dataclass
class Journey:
    """What the steps learn, for the steps after them."""

    t: date  # the tiffin day, a Tuesday
    application_id: str = ""
    partner_id: str = ""
    menu_id: str = ""
    assignment_id: str = ""
    pickup_qr: str = ""
    route_id: str = ""  # the lunch tiffin route (M7 §3.7)
    stops: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    documents: dict[str, str] = field(default_factory=dict[str, str])  # doc_type -> id
    households: dict[str, str] = field(default_factory=dict[str, str])  # phone -> household id
    orders: dict[str, str] = field(default_factory=dict[str, str])  # phone -> order id


@pytest.fixture(scope="module")
def trip(config: Config) -> Journey:
    return Journey(config.delivery_day + timedelta(days=14))


def expect(answer: httpx2.Response, status: int = 200) -> Any:
    assert answer.status_code == status, (
        f"{answer.request.method} {answer.request.url.path}: {answer.status_code} {answer.text}"
    )
    return answer.json()


def live(loop: Runner, coro: Any) -> Any:
    """Run on the real clock. Presigned links are signed with the clock they are made at and
    object storage checks them against its own, so the steps that touch real files cannot run in
    a frozen past or future."""
    return loop.run(coro)


def need_storage(config: Config) -> None:
    if config.s3_url is None:
        if os.environ.get("GB_REQUIRE_SERVICES") == "1":
            pytest.fail("E2E_S3_URL is not set (CI starts SeaweedFS)")
        pytest.skip(
            "KYC files need object storage: `just up-storage`, E2E_S3_URL=http://localhost:8333"
        )


def need_partner(trip: Journey) -> None:
    if not trip.partner_id:
        pytest.skip("no kitchen partner: the KYC upload step was skipped")


def household(flat: str) -> dict[str, Any]:
    adult = {"age_band": "18_59", "sex": "f", "fasting_rules": {}}
    return {
        "society_id": str(SEED_SOCIETY_ID),
        "address": {"tower_id": "c", "flat": flat, "location": {"lat": 19.0396, "lng": 73.0676}},
        "diet": "veg",
        "cuisine_tags": ["maharashtrian"],
        "weekday_cook_minutes": 30,
        "weekend_cook_minutes": 60,
        "no_cook_days": [],
        "budget_paise": None,
        "members": [adult, {**adult, "sex": "m"}],
        "consents": [
            {"purpose": purpose, "version": "v1", "granted": True}
            for purpose in ("service_delivery", "personalisation")
        ],
    }


# --- 1. the partner applies, with her KYC -----------------------------------------------------


def test_the_neighbours_sign_up_with_a_household_each(
    app: App, trip: Journey, loop: Runner
) -> None:
    """`POST /v1/households` for the future kitchen and the two customers (new phones)."""

    async def go() -> None:
        for number, phone in enumerate((KITCHEN, *CUSTOMERS), start=1):
            auth = await app.sign_in(phone)
            made = expect(
                await app.http.post(
                    "/v1/households", json=household(f"C-{900 + number}"), headers={**auth, **key()}
                ),
                201,
            )
            assert made["hub_id"] == str(SEED_HUB_ID), made
            trip.households[phone] = made["id"]

    live(loop, go())


async def _put_files(app: App, auth: dict[str, str], bodies: list[bytes]) -> list[str]:
    """Presign (`POST /v1/files/presign`, purpose kyc_document) and PUT each file straight to
    object storage, as the app does; the keys are what the document carries."""
    keys: list[str] = []
    async with httpx2.AsyncClient() as storage:
        for body in bodies:
            ask = {"purpose": "kyc_document", "content_type": "image/jpeg", "size_bytes": len(body)}
            link = expect(await app.http.post("/v1/files/presign", json=ask, headers=auth))
            sent = await storage.put(link["url"], content=body, headers=link["headers"])
            assert sent.status_code == 200, f"PUT to storage: {sent.status_code} {sent.text}"
            keys.append(link["key"])
    return keys


def test_she_applies_and_uploads_every_document_the_kitchen_needs(
    config: Config, app: App, trip: Journey, loop: Runner
) -> None:
    """`POST /v1/partner-applications`, then `POST /v1/kyc-documents` per document; the last one
    sends the application to the review queue (`kyc_pending`). The numbers come back masked."""
    need_storage(config)

    async def go() -> dict[str, Any]:
        auth = await app.sign_in(KITCHEN)
        applied = expect(
            await app.http.post(
                "/v1/partner-applications",
                json={"kind": "kitchen", "display_name": "Asha tai"},
                headers={**auth, **key()},
            ),
            201,
        )
        assert (applied["status"], applied["next_step"]) == ("applied", "documents"), applied
        trip.application_id = applied["id"]
        for document in DOCUMENTS:
            keys = await _put_files(app, auth, BYTES.get(document["doc_type"], []))
            body = {**document, "application_id": trip.application_id, "file_keys": keys}
            made = expect(
                await app.http.post("/v1/kyc-documents", json=body, headers={**auth, **key()}),
                201,
            )
            assert made["check"]["status"] == "passed", made
            assert made["file_count"] == len(keys), made
            if document["doc_type"] == "fssai":  # public by law: printed on the card
                assert made["fssai_reg_no"] == document["number"], made
            elif "number" in document:  # only the last characters, never the number itself
                assert document["number"] not in json.dumps(made), made
                assert (
                    made["number_masked"]
                    == "X" * (len(document["number"]) - 4) + (document["number"][-4:])
                ), made
            trip.documents[document["doc_type"]] = made["id"]
        return expect(await app.http.get("/v1/partner-applications/me", headers=auth))

    mine = live(loop, go())
    assert (mine["status"], mine["next_step"]) == ("kyc_pending", "review"), mine


def test_she_passes_the_three_training_modules(app: App, trip: Journey, loop: Runner) -> None:
    """`GET /v1/trainings`, then `POST /v1/trainings/{code}/complete` with the seed's answer key
    (the placeholder modules' answers are in the repo)."""
    need_partner_or_application(trip)

    async def go() -> dict[str, Any]:
        auth = await app.sign_in(KITCHEN)
        todo = expect(await app.http.get("/v1/trainings", headers=auth))["items"]
        assert [m["code"] for m in todo] == ["app_basics", "hygiene", "kitchen_ops"], todo
        assert {m["status"] for m in todo} == {"todo"}
        for module in todo:
            answers = next(m for m in TRAININGS if m["code"] == module["code"])["answers"]
            sent = {
                "version": module["version"],
                "answers": [{"question_id": q, "option": o} for q, o in answers.items()],
            }
            done = expect(
                await app.http.post(
                    f"/v1/trainings/{module['code']}/complete",
                    json=sent,
                    headers={**auth, **key()},
                )
            )
            assert done["passed"] is True and done["application"]["status"] != "verified", done
        return expect(await app.http.get("/v1/partner-applications/me", headers=auth))

    mine = live(loop, go())
    assert mine["status"] == "kyc_pending", mine  # approval is the hub manager's, not the quiz's
    assert {t["status"] for t in mine["requirements"]["trainings"]} == {"passed"}, mine


def need_partner_or_application(trip: Journey) -> None:
    if not trip.application_id:
        pytest.skip("no application: the KYC upload step was skipped")


def test_the_hub_manager_reads_the_files_and_approves_her(
    app: App, trip: Journey, loop: Runner
) -> None:
    """The review queue, the 5-minute file links (read back from storage), and
    `POST /v1/partner-applications/{id}/decision` approve with the kitchen's hygiene grade: she is
    verified in the same breath (training is done), M1 grants the role, and she is `active`."""
    need_partner_or_application(trip)

    async def go() -> dict[str, Any]:
        manager = await app.sign_in(VIKRAM)
        queue = expect(
            await app.http.get(
                f"{HUB}/partner-applications?kind=kitchen&limit=100", headers=manager
            )
        )["items"]
        assert trip.application_id in [a["id"] for a in queue], "not in the review queue"
        async with httpx2.AsyncClient() as storage:
            for doc_type, uploaded in BYTES.items():
                links = expect(
                    await app.http.post(
                        f"/v1/kyc-documents/{trip.documents[doc_type]}/file-links", headers=manager
                    )
                )["files"]
                assert [(await storage.get(f["url"])).content for f in links] == uploaded
        refused = await app.http.post(
            f"/v1/partner-applications/{trip.application_id}/decision",
            json={"outcome": "approve"},
            headers={**manager, **key()},
        )
        assert refused.status_code == 422, refused.text  # a kitchen needs its hygiene grade
        assert refused.json()["code"] == "partners.reason_required", refused.text
        approved = expect(
            await app.http.post(
                f"/v1/partner-applications/{trip.application_id}/decision",
                json={"outcome": "approve", "hygiene_grade": "A"},
                headers={**manager, **key()},
            )
        )
        await app.sweep(ist(trip.t - timedelta(days=7), 12, 0))
        return approved

    approved = live(loop, go())
    assert approved["status"] == "verified" and approved["partner_id"], approved
    assert {d["review"]["status"] for d in approved["documents"]} == {"accepted"}, approved
    trip.partner_id = approved["partner_id"]

    async def roles() -> tuple[Any, Any]:
        auth = await app.sign_in(KITCHEN)
        return (
            expect(await app.http.get("/v1/me/roles", headers=auth)),
            expect(await app.http.get("/v1/partners/me", headers=auth))["items"],
        )

    granted, partners = live(loop, roles())
    assert {"role": "kitchen_partner", "scope_type": "hub", "scope_id": str(SEED_HUB_ID)} | {
        "status": "verified"
    } in granted, granted
    assert [(p["id"], p["kind"], p["status"]) for p in partners] == [
        (trip.partner_id, "kitchen", "active")
    ], partners


# --- 2. the tiffin day ------------------------------------------------------------------------


def test_the_kitchen_sets_up_her_pin_capacity_and_menu_and_the_hub_approves_it(
    config: Config, app: App, trip: Journey, loop: Runner
) -> None:
    """Six days before: `PATCH /v1/kitchens/me`, `PUT /v1/kitchen-capacity`, the hub's tiffin
    lunch slot (`POST /v1/admin/slots`), the menu (a row: M2 has no menu write yet) and its
    approval (`POST /v1/tiffin-menus/{id}/approve`)."""
    need_partner(trip)
    when = ist(trip.t - timedelta(days=6), 10, 0)

    async def go() -> None:
        auth = await app.sign_in(KITCHEN)
        mine = await app.http.get("/v1/kitchens/me", headers=auth)
        assert expect(mine)["hygiene"]["grade"] == "A", mine.text
        patched = expect(
            await app.http.patch(
                "/v1/kitchens/me",
                json={
                    "area": "Sector 20, Kharghar",
                    "address": "C-901, Sunrise Towers, Sector 20",
                    "location": {"lat": 19.0398, "lng": 73.0678},
                    "skills": ["veg"],
                },
                headers={**auth, "If-Match": f'"{mine.json()["version"]}"'},
            )
        )
        assert patched["skills"] == ["veg"] and patched["location"], patched
        capacity = expect(
            await app.http.put(
                "/v1/kitchen-capacity",
                json={"date": trip.t.isoformat(), "meal": "lunch", "max_portions": 10},
                headers=auth,
            )
        )
        assert (capacity["max_portions"], capacity["source"]) == (10, "set"), capacity

        manager = await app.sign_in(VIKRAM)
        slot = {
            "hub_id": str(SEED_HUB_ID),
            "label": "Tiffin lunch 12:30-13:30",
            "kind": "tiffin",
            "meal": "lunch",
            "start": "12:30",
            "end": "13:30",
            "capacity": 50,
        }
        expect(await app.http.post("/v1/admin/slots", json=slot, headers={**manager, **key()}), 201)
        async with database(config) as session:  # M2 has no endpoint for a kitchen's menu yet
            trip.menu_id = str(
                await session.scalar(
                    text(
                        """
                        INSERT INTO catalog.tiffin_menus
                          (hub_id, partner_id, date, meal, product_id, recipe_ids, price_paise,
                           status)
                        VALUES (:hub, :partner, :day, 'lunch',
                          (SELECT id FROM catalog.products WHERE slug = 'tomato'),
                          ARRAY(SELECT id FROM catalog.recipes ORDER BY slug LIMIT 2),
                          :price, 'submitted')
                        RETURNING id
                        """
                    ),
                    {
                        "hub": SEED_HUB_ID,
                        "partner": trip.partner_id,
                        "day": trip.t,
                        "price": PRICE_PAISE,
                    },
                )
            )
            await session.commit()
        waiting = expect(
            await app.http.get(
                f"{HUB}/tiffin-menus?status=submitted&from={trip.t}&to={trip.t}", headers=manager
            )
        )["items"]
        assert trip.menu_id in [m["id"] for m in waiting], waiting
        approved = expect(
            await app.http.post(
                f"/v1/tiffin-menus/{trip.menu_id}/approve", json={}, headers=manager
            )
        )
        assert approved["status"] == "approved", approved

    step(loop, when, go())


def test_two_households_book_the_kitchens_menu_and_the_cut_off_locks_them(
    app: App, trip: Journey, loop: Runner
) -> None:
    """Four days before: top up (`POST /v1/wallet/topups`), see the menu (`GET /v1/tiffin-menus`)
    and book (`POST /v1/orders`, kind tiffin). At T-1 21:00 `cutoff_lock` debits and locks."""
    need_partner(trip)
    booking = ist(trip.t - timedelta(days=4), 11, 0)

    async def book() -> None:
        for phone, portions in CUSTOMERS.items():
            auth = await app.sign_in(phone)
            topup = expect(
                await app.http.post(
                    "/v1/wallet/topups", json={"amount_paise": 50_000}, headers={**auth, **key()}
                ),
                201,
            )
            expect(
                await app.http.post(
                    "/v1/dev/razorpay/pay",
                    json={"razorpay_order_id": topup["razorpay_order_id"]},
                    headers=auth,
                )
            )
            menus = expect(
                await app.http.get(f"/v1/tiffin-menus?date={trip.t}&meal=lunch", headers=auth)
            )["items"]
            assert trip.menu_id in [m["id"] for m in menus], menus
            order = expect(
                await app.http.post(
                    "/v1/orders",
                    json={"kind": "tiffin", "menu_id": trip.menu_id, "portions": portions},
                    headers={**auth, **key()},
                ),
                201,
            )
            assert (order["kind"], order["status"]) == ("tiffin", "approved"), order
            assert order["total_paise"] == portions * PRICE_PAISE, order
            trip.orders[phone] = order["id"]
        await app.sweep(booking)

    step(loop, booking, book())

    lock = ist(trip.t - timedelta(days=1), 21, 0)

    async def locked() -> list[str]:
        await app.job("cutoff_lock", lock)
        await app.sweep(lock)
        found: list[str] = []
        for phone, order_id in trip.orders.items():
            auth = await app.sign_in(phone)
            found.append(
                expect(await app.http.get(f"/v1/orders/{order_id}", headers=auth))["status"]
            )
        return found

    assert step(loop, lock + timedelta(minutes=1), locked()) == ["locked"] * len(CUSTOMERS)


def test_the_21_30_round_offers_the_orders_to_the_kitchen_and_she_accepts(
    app: App, trip: Journey, loop: Runner
) -> None:
    """T-1 21:30 `assign_kitchens` (no ML: the greedy fallback) makes her one offer for both
    orders; she sees it (`GET /v1/assignments`) and accepts it (`POST …/accept`) before
    `assignment_acceptance_timeout` runs; the orders then name their cook."""
    need_partner(trip)
    evening = trip.t - timedelta(days=1)

    async def assign() -> None:
        ran = await app.job("assign_kitchens", ist(evening, 21, 30))
        assert ran["offers"] >= 1 and not ran.get("failed_hubs"), ran
        await app.sweep(ist(evening, 21, 30))

    step(loop, ist(evening, 21, 30), assign())

    async def accept() -> dict[str, Any]:
        auth = await app.sign_in(KITCHEN)
        offers = expect(await app.http.get(f"/v1/assignments?date={trip.t}", headers=auth))["items"]
        assert len(offers) == 1, offers
        offer = offers[0]
        assert (offer["status"], offer["source"], offer["meal"]) == (
            "offered",
            "fallback",
            "lunch",
        ), offer
        assert {o["order_id"] for o in offer["orders"]} == set(trip.orders.values()), offer
        assert offer["portions"] == sum(CUSTOMERS.values()), offer
        trip.assignment_id = offer["id"]
        taken = expect(
            await app.http.post(f"/v1/assignments/{offer['id']}/accept", headers={**auth, **key()})
        )
        await app.job("assignment_acceptance_timeout", ist(evening, 22, 0))
        await app.sweep(ist(evening, 22, 0))
        return taken

    taken = step(loop, ist(evening, 21, 45), accept())
    assert taken["status"] == "accepted" and taken["accepted_at"], taken

    async def board() -> tuple[Any, Any]:
        manager = await app.sign_in(VIKRAM)
        auth = await app.sign_in(next(iter(CUSTOMERS)))
        order = expect(
            await app.http.get(f"/v1/orders/{next(iter(trip.orders.values()))}", headers=auth)
        )
        shown = expect(await app.http.get(f"{HUB}/tiffin-board?date={trip.t}", headers=manager))
        return order, shown

    order, shown = step(loop, ist(evening, 22, 5), board())
    assert order["kitchen"] == {"partner_id": trip.partner_id}, order
    mine = next(a for a in shown["assignments"] if a["id"] == trip.assignment_id)
    assert mine["status"] == "accepted", mine


def test_she_cooks_and_readies_and_the_tiffins_are_picked_up_and_delivered(
    config: Config, app: App, trip: Journey, loop: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T 06:00 `POST …/start` (Being cooked); `refund_unassigned_tiffins` at 09:30 refunds nothing
    of hers and `solve_tiffin_routes` at 09:35 publishes the lunch route; 10:30 `POST …/ready`
    with the hygiene checklist (Ready, and the pickup QR appears); the rider starts and collects
    by that QR (`POST /v1/stops/{id}/collect`), the captain takes the tiffins by count and proves
    each door with its short code and the household's code (M7 §3.7)."""
    need_partner(trip)

    async def cook(path: str, **body: Any) -> dict[str, Any]:
        auth = await app.sign_in(KITCHEN)
        return expect(
            await app.http.post(
                f"/v1/assignments/{trip.assignment_id}/{path}", json=body, headers={**auth, **key()}
            )
        )

    async def statuses() -> list[str]:
        found: list[str] = []
        for phone, order_id in trip.orders.items():
            auth = await app.sign_in(phone)
            found.append(
                expect(await app.http.get(f"/v1/orders/{order_id}", headers=auth))["status"]
            )
        return found

    started = step(loop, ist(trip.t, 6, 0), cook("start"))
    assert started["status"] == "preparing", started
    assert step(loop, ist(trip.t, 6, 1), statuses()) == ["picking"] * len(CUSTOMERS)

    async def refund_round() -> None:
        ran = await app.job("refund_unassigned_tiffins", ist(trip.t, 9, 30))
        assert ran["refused"] == 0, ran

    step(loop, ist(trip.t, 9, 30), refund_round())

    async def routes() -> dict[str, Any]:
        """The hub's two-wheeler with Arjun as its rider; at 09:35 `solve_tiffin_routes` plans
        and publishes the lunch run (M7 §3.7), which the rider sees on his day."""
        manager, rider = await app.sign_in(VIKRAM), await app.sign_in(ARJUN)
        arjun = await scalar(config, "SELECT id FROM identity.users WHERE phone_e164 = :p", p=ARJUN)
        expect(
            await app.http.post(
                f"{HUB}/vehicles",
                json={
                    "label": "TW-E2E",
                    "kind": "two_wheeler",
                    "default_rider_user_id": str(arjun),
                },
                headers={**manager, **key()},
            ),
            201,
        )
        ran = await app.job("solve_tiffin_routes", ist(trip.t, 9, 35))
        assert ran["published"] >= 1 and ran["failed"] == 0, ran
        await app.sweep(ist(trip.t, 9, 35))
        mine = expect(await app.http.get(f"/v1/riders/me/routes?date={trip.t}", headers=rider))
        [route] = [r for r in mine["items"] if r["kind"] == "tiffin"]
        return expect(await app.http.get(f"/v1/routes/{route['id']}", headers=rider))

    route = step(loop, ist(trip.t, 9, 36), routes())
    trip.route_id, trip.stops = route["id"], route["stops"]
    assert [s["kind"] for s in trip.stops] == ["kitchen", "drop"], trip.stops
    assert trip.stops[0]["kitchen"]["partner_id"] == trip.partner_id, trip.stops
    assert trip.stops[0]["kitchen"]["address"] is None  # before the start: the area only
    checklist = dict.fromkeys(
        ("handwash", "hair_cover", "clean_surface", "sealed", "fresh_today"), True
    )
    ready = step(loop, ist(trip.t, 10, 30), cook("ready", checklist=checklist))
    assert ready["status"] == "ready" and ready["pickup_qr"].startswith("GB1A:"), ready
    trip.pickup_qr = ready["pickup_qr"]
    assert step(loop, ist(trip.t, 10, 31), statuses()) == ["packed"] * len(CUSTOMERS)

    async def stamp(to: str) -> None:
        """The database stamps the timeline with its own clock, which no test can move: the
        rating window and the earning's date read these rows, so they take the step's time."""
        async with database(config) as session:
            for order_id in trip.orders.values():
                await session.execute(
                    text(
                        "UPDATE orders.order_events SET at = :now "
                        "WHERE order_id = :order AND to_status = :to"
                    ),
                    {"now": datetime.now(UTC), "order": UUID(order_id), "to": to},
                )
            await session.commit()

    async def collect() -> dict[str, Any]:
        """11:00 the rider starts his lunch route; 12:15 he scans the kitchen's code (M7 §3.7)."""
        rider = await app.sign_in(ARJUN)
        expect(await app.http.post(f"/v1/routes/{trip.route_id}/start", headers={**rider, **key()}))
        kitchen_stop = next(s for s in trip.stops if s["kind"] == "kitchen")
        taken = expect(
            await app.http.post(
                f"/v1/stops/{kitchen_stop['id']}/collect",
                json={"pickup_qr": trip.pickup_qr},
                headers={**rider, **key()},
            )
        )
        await stamp("dispatched")
        await app.sweep(ist(trip.t, 12, 15))
        return taken

    # The after-commit relay waits for the sweep in these steps, so M8's earnings and the rating
    # window read the timeline rows once `stamp` has given them the step's time.
    monkeypatch.setattr(outbox, "relay_soon", lambda sessionmaker: None)
    taken = step(loop, ist(trip.t, 12, 15), collect())
    assert {c["order_id"] for c in taken["collected"]} == set(trip.orders.values()), taken
    assert taken["stop"]["status"] == "handed_over", taken
    assert step(loop, ist(trip.t, 12, 16), statuses()) == ["dispatched"] * len(CUSTOMERS)

    async def doors() -> list[str]:
        """12:45 the captain takes the tiffins by count and the rider's code; 13:00 she proves each
        door with the label's short code and the household's code; the rider completes."""
        rider, captain = await app.sign_in(ARJUN), await app.sign_in(MEENA)
        route = expect(await app.http.get(f"/v1/routes/{trip.route_id}", headers=rider))
        drop = next(s for s in route["stops"] if s["kind"] == "drop")
        assert not drop["rider_mode"] and drop["handover_code"], drop
        expect(
            await app.http.post(
                f"/v1/stops/{drop['id']}/handover",
                json={
                    "crates": 0,
                    "bags": len(CUSTOMERS),
                    "handover_code": drop["handover_code"],
                },
                headers={**captain, **key()},
            )
        )
        labels = {t["order_id"]: t["short_code"] for t in drop["tiffins"]}
        proofs = []
        for phone, order_id in trip.orders.items():
            card = expect(
                await app.http.get(f"/v1/deliveries/{order_id}", headers=await app.sign_in(phone))
            )
            proved = expect(
                await app.http.post(
                    f"/v1/deliveries/{order_id}/proof",
                    json={
                        "short_code": labels[order_id],
                        "proof": "otp",
                        "delivery_code": card["delivery_code"],
                    },
                    headers={**captain, **key()},
                )
            )
            proofs.append(proved["status"])
        expect(
            await app.http.post(f"/v1/routes/{trip.route_id}/complete", headers={**rider, **key()})
        )
        await stamp("delivered")
        await app.sweep(ist(trip.t, 13, 0))
        return proofs

    assert step(loop, ist(trip.t, 13, 0), doors()) == ["delivered"] * len(CUSTOMERS)
    assert step(loop, ist(trip.t, 13, 1), statuses()) == ["delivered"] * len(CUSTOMERS)

    async def done() -> dict[str, Any]:
        auth = await app.sign_in(KITCHEN)
        items = expect(await app.http.get(f"/v1/assignments?date={trip.t}", headers=auth))["items"]
        return next(a for a in items if a["id"] == trip.assignment_id)

    finished = step(loop, ist(trip.t, 13, 2), done())
    assert finished["status"] == "completed", finished
    assert {o["status"] for o in finished["orders"]} == {"delivered"}, finished


def test_the_customers_are_asked_to_rate_and_each_rates_the_kitchen_once(
    app: App, trip: Journey, loop: Runner
) -> None:
    """T 14:30 `prompt_tiffin_ratings`, then `POST /v1/ratings` (once, a second one is refused),
    `GET /v1/ratings` and the kitchen's public card (`GET /v1/partners/{id}/card`)."""
    need_partner(trip)

    async def prompt() -> None:
        await app.job("prompt_tiffin_ratings", ist(trip.t, 14, 30))
        await app.sweep(ist(trip.t, 14, 30))

    step(loop, ist(trip.t, 14, 30), prompt())

    async def rate() -> dict[str, Any]:
        for score, (phone, order_id) in zip((5, 4), trip.orders.items(), strict=True):
            auth = await app.sign_in(phone)
            given = expect(
                await app.http.post(
                    "/v1/ratings",
                    json={"order_id": order_id, "score": score, "comment": "tasty"},
                    headers={**auth, **key()},
                ),
                201,
            )
            assert (given["partner_id"], given["score"]) == (trip.partner_id, score), given
            again = await app.http.post(
                "/v1/ratings",
                json={"order_id": order_id, "score": 1},
                headers={**auth, **key()},
            )
            assert (again.status_code, again.json()["code"]) == (409, "partners.rating_exists")
            mine = expect(await app.http.get(f"/v1/ratings?order_id={order_id}", headers=auth))
            assert mine["score"] == score, mine
        card = f"/v1/partners/{trip.partner_id}/card"  # "know your cook", as a customer sees it
        return expect(await app.http.get(card, headers=await app.sign_in(next(iter(CUSTOMERS)))))

    card = step(loop, ist(trip.t, 15, 0), rate())
    assert card["portions_served"] == sum(CUSTOMERS.values()), card
    assert card["hygiene"]["grade"] == "A" and card["rating"] is None, card  # under 5 ratings


async def _prompted(config: Config, trip: Journey) -> int:
    found = await scalar(
        config,
        "SELECT count(*) FROM partners.assignment_orders WHERE assignment_id = CAST(:a AS uuid) "
        "AND prompted_at IS NOT NULL",
        a=trip.assignment_id,
    )
    return int(found)


def test_the_rating_prompt_went_to_both_orders(config: Config, trip: Journey, loop: Runner) -> None:
    need_partner(trip)
    assert loop.run(_prompted(config, trip)) == len(CUSTOMERS)


def test_monday_pays_the_kycd_kitchen_for_the_portions_she_delivered(
    config: Config, app: App, trip: Journey, loop: Runner
) -> None:
    """The Monday after: `weekly_payouts` pays her week (the simulated provider), not held, as the
    KYC she uploaded was accepted and her bank's penny drop passed. `GET /v1/payouts` and
    `GET /v1/earnings` agree with the rate card."""
    need_partner(trip)
    monday = trip.t + timedelta(days=6)
    year, week, _ = trip.t.isocalendar()

    async def pay() -> dict[str, Any]:
        ran = await app.job("weekly_payouts", ist(monday, 10, 0))
        await app.sweep(ist(monday, 10, 5))
        return ran

    ran = step(loop, ist(monday, 10, 0), pay())
    assert (ran["failed"], ran["send_failed"], ran["refused"]) == (0, 0, 0), ran

    async def kitchen() -> tuple[Any, Any, int]:
        auth = await app.sign_in(KITCHEN)
        manager = await app.sign_in(VIKRAM)
        card = expect(await app.http.get(f"{HUB}/partner-rates", headers=manager))["rates"]
        rate = max(
            (r for r in card if r["basis"] == "tiffin_portion" and r["valid_from"] <= str(trip.t)),
            key=lambda r: r["valid_from"],
        )["amount_paise"]
        payouts = expect(await app.http.get("/v1/payouts", headers=auth))["items"]
        earned = expect(await app.http.get(f"/v1/earnings?week={year}-W{week:02d}", headers=auth))
        return payouts, earned, rate

    payouts, earned, rate = step(loop, ist(monday, 10, 10), kitchen())
    owed = rate * sum(CUSTOMERS.values())
    assert owed >= 10_000, owed  # under ₹100 would be carried to the next week, not paid
    assert [
        (p["partner_id"], p["status"], p["amount_paise"], p["hold_reason"]) for p in payouts
    ] == [(trip.partner_id, "processed", owed, None)], payouts
    assert earned["total_paise"] == owed, earned
    assert (loop.run(_payout_entries(config, trip))) == 1


async def _payout_entries(config: Config, trip: Journey) -> int:
    found = await scalar(
        config,
        "SELECT count(*) FROM billing.journal_entries WHERE kind = 'payout' "
        "AND idempotency_key LIKE :key",
        key=f"payout:{trip.partner_id}:%",
    )
    return int(found)


# --- 3. invariants ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(name, marks=pytest.mark.xfail(strict=True, reason=BUGS[name]))
        if name in BUGS
        else name
        for name in invariants.ALL
    ],
)
def test_invariant(name: str, config: Config, loop: Runner, app: App) -> None:
    async def check() -> list[str] | None:
        async with database(config) as session:
            return await invariants.ALL[name](session)

    violations = loop.run(check())
    if violations is None:
        pytest.skip(f"{name}: its tables are not built yet")
    assert violations == [], "\n".join(violations[:50])
