"""Problems reported on an order (M4 §3.2): the window, the kinds, photos, the ₹50 auto-credit,
the wallet cap, triage through `resolve_issue`, the events and the ledger."""

import uuid
from datetime import UTC, datetime, time, timedelta
from typing import Any, cast

import httpx2
import pytest
import time_machine
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.models import Posting
from gharbasket.modules.billing.wallet import CAP_PAISE, credit_topup
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service
from gharbasket.modules.orders.models import OrderEvent
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.files import ObjectStore, create_store
from gharbasket.platform.models import OutboxEvent, Upload
from gharbasket.tools.seed import SEED_HUB_ID

from ..billing.paid import order_refund_to_source, paid_topup
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..test_files import SETTINGS as STORAGE_SETTINGS
from ..test_files import FakeS3
from .test_orders import DELIVERY, PRIYA, SUNITA, VIKRAM, as_user

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

NOW = datetime(2026, 10, 13, 6, 0, tzinfo=UTC)  # Tue 11:30 IST, the delivery day
DELIVERED_AT = datetime(2026, 10, 13, 3, 0, tzinfo=UTC)
# (steps, paid price per step) per product
BOX = {"tomato": (2, 1_500), "bhindi": (2, 1_000), "onion": (2, 4_000)}
HUB = {"X-Active-Role": f"hub_manager:{SEED_HUB_ID}"}
Reach = tuple[str, ...]
DELIVERED: Reach = ("locked", "picking", "packed", "dispatched", "at_society", "delivered")
DISPATCHED: Reach = DELIVERED[:4]


async def placed_order(
    db: AsyncSession,
    owner: uuid.UUID,
    reach: Reach = DELIVERED,
    box: dict[str, tuple[int, int]] = BOX,
    extra_topup: int = 0,
    short: dict[str, int] | None = None,
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """A box paid from the owner's wallet and moved along to `reach[-1]`, delivered at
    DELIVERED_AT, with the `short` steps marked (and refunded) while picking; committed, so
    failing requests later do not undo it."""
    household = await identity_api.household_for_user(db, owner)
    assert household is not None
    slot = await service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label=f"Issues {uuid.uuid4().hex[:8]}",
        kind="box",
        start=time(7),
        end=time(9),
        capacity=10,
    )
    ids = await catalog_api.product_ids_by_slug(db, list(box))
    order = await orders_api.create_from_cycle(
        db,
        orders_api.ApprovedCycle(
            cycle_id=uuid.uuid4(),
            household_id=household.id,
            hub_id=SEED_HUB_ID,
            society_id=household.society_id,
            delivery_date=DELIVERY,
            slot_id=slot.id,
            lines=tuple(orders_api.CycleLine(ids[s], q, p) for s, (q, p) in box.items()),
        ),
    )
    order_id, total = order.id, order.total_paise
    await credit_topup(db, owner, total, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4())
    await billing_api.debit_for_order(db, owner, order_id, total, DELIVERY)
    if extra_topup:
        await credit_topup(
            db, owner, extra_topup, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
        )
    for to in reach:
        await orders_api.transition(db, order_id, to, actor="system" if to == "locked" else "hub")
        if to == "picking":
            for slug, qty in (short or {}).items():
                await orders_api.mark_shortage(db, order_id, ids[slug], qty)
    await db.execute(
        update(OrderEvent)
        .where(OrderEvent.order_id == order_id, OrderEvent.to_status == "delivered")
        .values(at=DELIVERED_AT)
    )
    await db.commit()
    return order_id, ids


def with_store(api: httpx2.AsyncClient, s3: FakeS3) -> httpx2.AsyncClient:
    store: ObjectStore | None = create_store(STORAGE_SETTINGS)
    assert store is not None
    store.client = s3
    transport = api._transport
    assert isinstance(transport, httpx2.ASGITransport)
    cast(FastAPI, transport.app).state.storage = store
    return api


async def photo(
    db: AsyncSession, s3: FakeS3, user_id: uuid.UUID, *, purpose: str = "order_issue_photo"
) -> str:
    """What POST /v1/files/presign and the app's PUT leave behind."""
    key = f"{purpose}/{user_id}/{uuid.uuid4()}.jpg"
    db.add(
        Upload(key=key, user_id=user_id, purpose=purpose, content_type="image/jpeg", size_bytes=9)
    )
    s3.objects[key] = ("image/jpeg", 9)
    await db.commit()
    return key


async def report(
    api: httpx2.AsyncClient, auth: dict, order_id: uuid.UUID, body: dict[str, Any], key: str = ""
) -> httpx2.Response:
    return await api.post(
        f"/v1/orders/{order_id}/issues",
        json=body,
        headers={**auth, "Idempotency-Key": key or uuid.uuid4().hex},
    )


def items(ids: dict[str, uuid.UUID], **qty: int) -> list[dict[str, Any]]:
    return [{"product_id": str(ids[slug]), "qty": n} for slug, n in qty.items()]


async def events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent.payload)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.id)
    )
    return list(rows.scalars())


async def assert_ledger_balances(db: AsyncSession) -> None:
    unbalanced = await db.execute(
        select(Posting.entry_id).group_by(Posting.entry_id).having(func.sum(Posting.amount) != 0)
    )
    assert unbalanced.all() == []


@time_machine.travel(NOW, tick=False)
async def test_missing_items_are_credited_at_once_once_per_key_and_only_for_the_owner(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    order_id, ids = await placed_order(db_session, priya_id)
    before = await billing_api.balance(db_session, priya_id)
    body = {"kind": "missing", "items": items(ids, tomato=1, bhindi=1), "note": "Not in the bag"}

    no_key = await api.post(f"/v1/orders/{order_id}/issues", json=body, headers=priya)
    first = await report(api, priya, order_id, body, key="miss-1")
    replay = await report(api, priya, order_id, body, key="miss-1")
    mismatch = await report(api, priya, order_id, {**body, "note": "x"}, key="miss-1")

    assert no_key.json()["code"] == "request.idempotency_key_required"
    assert first.status_code == 201, first.text
    out = first.json()
    assert (out["status"], out["amount_paise"], out["credit_paise"]) == ("credited", 2500, 2500)
    assert out["resolution"]["by"] == "auto" and out["resolution"]["note"] is None
    assert out["open_reason"] is None and out["callback_within_minutes"] is None
    assert out["photo_count"] == 0 and out["note"] == "Not in the bag"
    assert {
        (i["slug"], i["qty"], i["unit_price_paise"], i["amount_paise"]) for i in out["items"]
    } == {
        ("tomato", 1, 1500, 1500),
        ("bhindi", 1, 1000, 1000),
    }
    assert replay.json() == out and replay.headers["idempotent-replayed"] == "true"
    assert mismatch.json()["code"] == "request.idempotency_mismatch"
    assert await billing_api.balance(db_session, priya_id) == before + 2500
    await assert_ledger_balances(db_session)

    reported, resolved = (
        await events(db_session, "orders.issue_reported"),
        await events(db_session, "orders.issue_resolved"),
    )
    assert reported == [
        {
            "issue_id": out["id"],
            "order_id": str(order_id),
            "household_id": reported[0]["household_id"],
            "hub_id": str(SEED_HUB_ID),
            "kind": "missing",
            "amount_paise": 2500,
            "status": "credited",
        }
    ]
    assert resolved == [
        {
            "issue_id": out["id"],
            "order_id": str(order_id),
            "household_id": reported[0]["household_id"],
            "status": "credited",
            "credit_paise": 2500,
            "by": "auto",
        }
    ]

    listed = await api.get(f"/v1/orders/{order_id}/issues", headers=priya)
    assert listed.json() == {"items": [out]} and listed.headers["cache-control"] == "no-store"
    sunita, _ = await as_user(api, google, SUNITA)
    assert (await api.get(f"/v1/orders/{order_id}/issues", headers=sunita)).status_code == 404
    theirs = await report(api, sunita, order_id, body)
    assert (theirs.status_code, theirs.json()["code"]) == (404, "orders.not_found")
    vikram, _ = await as_user(api, google, VIKRAM)
    staff = await api.get(f"/v1/orders/{order_id}/issues", headers={**vikram, **HUB})
    assert staff.status_code == 200 and staff.json()["items"][0]["id"] == out["id"]


@time_machine.travel(NOW, tick=False)
async def test_each_kind_and_the_checks_on_items_and_the_count(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    order_id, ids = await placed_order(db_session, priya_id, short={"onion": 1})

    def code(answer: httpx2.Response) -> tuple[int, str]:
        return answer.status_code, answer.json()["code"]

    assert code(await report(api, priya, order_id, {"kind": "stolen"})) == (
        422,
        "orders.issue_kind_invalid",
    )
    for kind in ("missing", "damaged", "quality", "wrong_item"):
        assert code(await report(api, priya, order_id, {"kind": kind})) == (
            422,
            "orders.issue_items_required",
        )
    stranger = [{"product_id": str(uuid.uuid4()), "qty": 1}]
    unknown = await report(api, priya, order_id, {"kind": "quality", "items": stranger})
    assert code(unknown) == (422, "orders.issue_item_unknown")
    assert unknown.json()["product_ids"] == [stranger[0]["product_id"]]
    # Two onions ordered, one short at packing: only one was delivered.
    too_many = await report(api, priya, order_id, {"kind": "quality", "items": items(ids, onion=2)})
    assert code(too_many) == (422, "orders.issue_qty_invalid") and too_many.json()["max_qty"] == 1
    four = await report(
        api, priya, order_id, {"kind": "missing", "photo_keys": ["a", "b", "c", "d"]}
    )
    assert code(four) == (422, "request.invalid")

    quality = await report(api, priya, order_id, {"kind": "quality", "items": items(ids, onion=1)})
    damaged = await report(api, priya, order_id, {"kind": "damaged", "items": items(ids, tomato=2)})
    wrong = await report(
        api, priya, order_id, {"kind": "wrong_item", "items": items(ids, bhindi=1)}
    )
    fourth = await report(api, priya, order_id, {"kind": "other", "note": "Rude rider"})

    assert [
        (a.status_code, a.json()["status"], a.json()["open_reason"])
        for a in (quality, damaged, wrong)
    ] == [(201, "open", "triage"), (201, "open", "photo_needed"), (201, "open", "triage")]
    assert quality.json()["callback_within_minutes"] == 120 and quality.json()["credit_paise"] == 0
    assert damaged.json()["amount_paise"] == 3000 and damaged.json()["resolution"] is None
    assert code(fourth) == (409, "orders.too_many_issues")
    listed = (await api.get(f"/v1/orders/{order_id}/issues", headers=priya)).json()["items"]
    assert [i["kind"] for i in listed] == ["wrong_item", "damaged", "quality"]  # newest first
    # A second order: late and other need no items.
    second, _ = await placed_order(db_session, priya_id)
    assert (await report(api, priya, second, {"kind": "late"})).json()["open_reason"] == "triage"
    assert (await report(api, priya, second, {"kind": "other"})).json()["status"] == "open"
    assert await events(db_session, "orders.issue_resolved") == []


async def test_the_window_opens_at_dispatch_or_delivery_and_closes_48_hours_after_delivery(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(NOW, tick=False):
        priya, priya_id = await as_user(api, google, PRIYA)
        locked, ids = await placed_order(db_session, priya_id, reach=("locked",))
        on_the_way, _ = await placed_order(db_session, priya_id, reach=DISPATCHED)
        delivered, _ = await placed_order(db_session, priya_id)
        missing = {"kind": "missing", "items": items(ids, tomato=1)}

        assert (await report(api, priya, locked, {"kind": "late"})).json()["code"] == (
            "orders.issue_window_closed"
        )
        early = await report(api, priya, on_the_way, missing)
        late = await report(api, priya, on_the_way, {"kind": "late"})
        other = await report(api, priya, on_the_way, {"kind": "other"})
    assert (early.status_code, early.json()["code"]) == (409, "orders.issue_window_closed")
    assert (late.status_code, other.status_code) == (201, 201)

    with time_machine.travel(DELIVERED_AT + timedelta(hours=47, minutes=59), tick=False):
        priya, _ = await as_user(api, google, PRIYA)  # the earlier token has expired
        inside = await report(api, priya, delivered, missing)
        inside_late = await report(api, priya, delivered, {"kind": "late"})
    with time_machine.travel(DELIVERED_AT + timedelta(hours=48, minutes=1), tick=False):
        priya, _ = await as_user(api, google, PRIYA)
        outside = await report(api, priya, delivered, {"kind": "other"})
    assert inside.json()["status"] == "credited" and inside_late.status_code == 201
    assert (outside.status_code, outside.json()["code"]) == (409, "orders.issue_window_closed")


@time_machine.travel(NOW, tick=False)
async def test_damaged_with_a_photo_is_credited_and_the_photo_kept_for_90_days(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    s3 = FakeS3()
    api = with_store(clients(), s3)
    priya, priya_id = await as_user(api, google, PRIYA)
    _, sunita_id = await as_user(api, google, SUNITA)
    order_id, ids = await placed_order(db_session, priya_id)
    damaged = {"kind": "damaged", "items": items(ids, tomato=1)}
    mine = await photo(db_session, s3, priya_id)
    theirs = await photo(db_session, s3, sunita_id)
    kyc = await photo(db_session, s3, priya_id, purpose="kyc_document")
    not_uploaded = await photo(db_session, s3, priya_id)
    del s3.objects[not_uploaded]
    resized = await photo(db_session, s3, priya_id)
    s3.objects[resized] = ("image/jpeg", 10)

    for bad in (theirs, kyc, not_uploaded, resized, "order_issue_photo/nothing.jpg"):
        answer = await report(api, priya, order_id, {**damaged, "photo_keys": [bad]})
        assert (answer.status_code, answer.json()["code"]) == (422, "orders.photo_key_invalid")
    no_storage = await report(clients(), priya, order_id, {**damaged, "photo_keys": [mine]})
    assert no_storage.json()["code"] == "platform.storage_unavailable"

    credited = await report(api, priya, order_id, {**damaged, "photo_keys": [mine, mine]})
    assert credited.status_code == 201, credited.text
    assert (credited.json()["status"], credited.json()["photo_count"]) == ("credited", 1)
    upload = await db_session.get(Upload, mine)
    assert upload is not None and upload.claimed_by == "orders"
    assert upload.delete_after == NOW + timedelta(days=90)
    reused = await report(api, priya, order_id, {**damaged, "photo_keys": [mine]})
    assert reused.json()["code"] == "orders.photo_key_invalid"


@time_machine.travel(NOW, tick=False)
async def test_auto_credit_stops_at_50_rupees_per_household_in_7_days(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    first, ids = await placed_order(db_session, priya_id)
    second, _ = await placed_order(db_session, priya_id)

    onion = await report(api, priya, first, {"kind": "missing", "items": items(ids, onion=1)})
    tomato = await report(api, priya, second, {"kind": "missing", "items": items(ids, tomato=1)})
    bhindi = await report(api, priya, second, {"kind": "missing", "items": items(ids, bhindi=1)})

    assert onion.json()["status"] == "credited"  # ₹40
    assert (tomato.json()["status"], tomato.json()["open_reason"]) == (
        "open",
        "over_auto_credit_limit",
    )  # ₹40 + ₹15 > ₹50: none of it
    assert bhindi.json()["status"] == "credited"  # ₹40 + ₹10 = ₹50 exactly
    with time_machine.travel(NOW + timedelta(days=7, minutes=1), tick=False):
        priya, _ = await as_user(api, google, PRIYA)
        third, _ = await placed_order(db_session, priya_id)
        await db_session.execute(
            update(OrderEvent)
            .where(OrderEvent.order_id == third, OrderEvent.to_status == "delivered")
            .values(at=NOW + timedelta(days=7))
        )
        await db_session.commit()
        later = await report(api, priya, third, {"kind": "missing", "items": items(ids, onion=1)})
    assert later.json()["status"] == "credited"  # the earlier credits are over 7 days old
    await assert_ledger_balances(db_session)


@time_machine.travel(NOW, tick=False)
async def test_a_full_wallet_leaves_the_issue_open_and_moves_no_money(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    order_id, ids = await placed_order(db_session, priya_id)
    await paid_topup(db_session, priya_id, CAP_PAISE, age_days=181)  # too old to refund to
    await db_session.commit()
    assert await billing_api.balance(db_session, priya_id) == CAP_PAISE

    answer = await report(api, priya, order_id, {"kind": "missing", "items": items(ids, tomato=1)})

    assert answer.status_code == 201, answer.text
    assert (answer.json()["status"], answer.json()["open_reason"]) == ("open", "wallet_limit")
    assert await billing_api.balance(db_session, priya_id) == CAP_PAISE
    assert [e["status"] for e in await events(db_session, "orders.issue_reported")] == ["open"]


@time_machine.travel(NOW, tick=False)
async def test_a_full_wallet_credits_an_issue_to_the_payment_source(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    order_id, ids = await placed_order(db_session, priya_id)
    await paid_topup(db_session, priya_id, CAP_PAISE)
    await db_session.commit()

    answer = await report(api, priya, order_id, {"kind": "missing", "items": items(ids, tomato=1)})

    assert answer.status_code == 201, answer.text
    assert (answer.json()["status"], answer.json()["credit_paise"]) == ("credited", 1500)
    assert await billing_api.balance(db_session, priya_id) == CAP_PAISE
    await order_refund_to_source(db_session, order_id, 1500)
    await assert_ledger_balances(db_session)


@time_machine.travel(NOW, tick=False)
async def test_a_full_wallet_refunds_an_undelivered_order_to_the_payment_source(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    _, priya_id = await as_user(api, google, PRIYA)
    reach = (*DISPATCHED, "at_society", "undelivered")
    order_id, _ = await placed_order(db_session, priya_id, reach=reach)
    await paid_topup(db_session, priya_id, CAP_PAISE)
    total = (await orders_api.get(db_session, order_id)).total_paise

    moved = await orders_api.transition(db_session, order_id, "refunded", actor="system")

    assert moved.status == "refunded"
    assert await billing_api.balance(db_session, priya_id) == CAP_PAISE
    refund, _ = await order_refund_to_source(db_session, order_id, total)
    assert refund.idempotency_key == f"order:{order_id}:undelivered"
    await assert_ledger_balances(db_session)


@time_machine.travel(NOW, tick=False)
async def test_the_hub_credits_or_rejects_open_issues_through_the_facade(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    _, vikram_id = await as_user(api, google, VIKRAM)
    order_id, ids = await placed_order(db_session, priya_id)
    quality = (
        await report(api, priya, order_id, {"kind": "quality", "items": items(ids, onion=1)})
    ).json()
    late = (await report(api, priya, order_id, {"kind": "late"})).json()
    other = (await report(api, priya, order_id, {"kind": "other"})).json()
    before = await billing_api.balance(db_session, priya_id)
    total = sum(q * p for q, p in BOX.values())

    with pytest.raises(ProblemError) as too_much:
        await orders_api.resolve_issue(
            db_session, uuid.UUID(quality["id"]), "credit", 4001, vikram_id
        )
    assert too_much.value.code == "orders.issue_credit_invalid"
    credited = await orders_api.resolve_issue(
        db_session, uuid.UUID(quality["id"]), "credit", 3000, vikram_id, "Soft onions"
    )
    with pytest.raises(ProblemError) as over_total:  # 3,000 of the order is already refunded
        await orders_api.resolve_issue(
            db_session, uuid.UUID(late["id"]), "credit", total, vikram_id
        )
    assert over_total.value.code == "orders.issue_credit_invalid"
    assert over_total.value.extra == {"max_paise": total - 3000}
    late_credit = await orders_api.resolve_issue(
        db_session, uuid.UUID(late["id"]), "credit", 9000, vikram_id
    )
    rejected = await orders_api.resolve_issue(
        db_session, uuid.UUID(other["id"]), "reject", 0, vikram_id, "Not our rider"
    )
    with pytest.raises(ProblemError) as again:
        await orders_api.resolve_issue(db_session, uuid.UUID(other["id"]), "credit", 100, vikram_id)
    with pytest.raises(ProblemError) as unknown:
        await orders_api.resolve_issue(db_session, uuid.uuid4(), "reject", 0, vikram_id)
    await db_session.commit()

    assert (credited.status, credited.credit_paise, credited.resolved_by) == (
        "credited",
        3000,
        "hub",
    )
    assert credited.resolver_user_id == vikram_id and credited.refund_entry_id is not None
    assert (late_credit.status, late_credit.credit_paise) == ("credited", 9000)
    assert (rejected.status, rejected.credit_paise, rejected.open_reason) == ("rejected", 0, None)
    assert again.value.code == "orders.issue_not_open"
    assert unknown.value.code == "orders.issue_not_found"
    assert await billing_api.balance(db_session, priya_id) == before + 3000 + 9000
    await assert_ledger_balances(db_session)
    listed = (await api.get(f"/v1/orders/{order_id}/issues", headers=priya)).json()["items"]
    assert [(i["status"], i["resolution"] and i["resolution"]["by"]) for i in listed] == [
        ("rejected", "hub"),
        ("credited", "hub"),
        ("credited", "hub"),
    ]
    assert listed[0]["resolution"]["note"] == "Not our rider"
    assert [(e["status"], e["by"]) for e in await events(db_session, "orders.issue_resolved")] == [
        ("credited", "hub"),
        ("credited", "hub"),
        ("rejected", "hub"),
    ]


SMALL_BOX = {"tomato": (2, 1_500)}  # paid 3,000


async def credit_invalid(db: AsyncSession, issue: dict, paise: int, actor: uuid.UUID) -> object:
    with pytest.raises(ProblemError) as refused:
        await orders_api.resolve_issue(db, uuid.UUID(issue["id"]), "credit", paise, actor)
    assert refused.value.code == "orders.issue_credit_invalid"
    return (refused.value.extra or {}).get("max_paise")


@time_machine.travel(NOW, tick=False)
async def test_late_and_other_credits_together_never_exceed_what_was_paid(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    _, vikram_id = await as_user(api, google, VIKRAM)
    order_id, ids = await placed_order(db_session, priya_id, box=SMALL_BOX)
    late = (await report(api, priya, order_id, {"kind": "late"})).json()
    other = (await report(api, priya, order_id, {"kind": "other"})).json()
    before = await billing_api.balance(db_session, priya_id)

    await orders_api.resolve_issue(db_session, uuid.UUID(late["id"]), "credit", 2000, vikram_id)
    assert await credit_invalid(db_session, other, 1500, vikram_id) == 1000
    await orders_api.resolve_issue(db_session, uuid.UUID(other["id"]), "credit", 1000, vikram_id)
    await db_session.commit()
    # Everything paid is back: even a small missing item is left for the hub.
    missing = await report(api, priya, order_id, {"kind": "missing", "items": items(ids, tomato=1)})

    assert (missing.json()["status"], missing.json()["open_reason"]) == ("open", "over_order_total")
    assert await credit_invalid(db_session, missing.json(), 1, vikram_id) == 0
    assert await billing_api.balance(db_session, priya_id) == before + 3000
    await assert_ledger_balances(db_session)


@time_machine.travel(NOW, tick=False)
async def test_no_auto_credit_beyond_what_a_manual_credit_left_of_the_order(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    _, vikram_id = await as_user(api, google, VIKRAM)
    order_id, ids = await placed_order(db_session, priya_id, box=SMALL_BOX)
    late = (await report(api, priya, order_id, {"kind": "late"})).json()
    await orders_api.resolve_issue(db_session, uuid.UUID(late["id"]), "credit", 2000, vikram_id)
    await db_session.commit()
    before = await billing_api.balance(db_session, priya_id)

    missing = await report(api, priya, order_id, {"kind": "missing", "items": items(ids, tomato=1)})

    # 1,500 is within the ₹50 allowance but only 1,000 of the order is left: none of it, at once.
    assert (missing.json()["status"], missing.json()["open_reason"]) == ("open", "over_order_total")
    assert missing.json()["credit_paise"] == 0
    assert await billing_api.balance(db_session, priya_id) == before
    assert await credit_invalid(db_session, missing.json(), 1500, vikram_id) == 1000
    hub = await orders_api.resolve_issue(
        db_session, uuid.UUID(missing.json()["id"]), "credit", 1000, vikram_id
    )
    assert (hub.status, hub.credit_paise) == ("credited", 1000)
    assert await billing_api.balance(db_session, priya_id) == before + 1000
    await assert_ledger_balances(db_session)
