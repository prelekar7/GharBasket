"""The order refunds list (M4 §3, M5 §3.8): the money that went back for a hub's orders, joined
from M4's orders to M5's `refund` entries; read only, per hub, newest first."""

import uuid
from datetime import date, timedelta
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.models import JournalEntry, Refund
from gharbasket.modules.billing.wallet import CAP_PAISE
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import refund_list
from gharbasket.modules.orders.models import Order, OrderIssue
from gharbasket.tools.seed import SEED_HUB_ID

from ..billing.paid import paid_topup
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_issue_triage import OTHER_HUB, code, staff
from .test_issues import NOW, items, placed_order, report
from .test_orders import DELIVERY, SUNITA, as_user

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

URL = "/v1/admin/refunds"
SANJAY = "+919800001009"  # franchise owner of the seed hub
PICKING = ("locked", "picking")


async def entry_of(db: AsyncSession, order_id: uuid.UUID, reason: str) -> uuid.UUID:
    """The id of the order's refund entry for `reason`. Entries are append-only, so 'newest first'
    is their creation order here: uuidv7 ids grow within one transaction, where created_at ties."""
    key = f"refund:{order_id}:{reason}"
    entry_id = await db.scalar(select(JournalEntry.id).where(JournalEntry.idempotency_key == key))
    assert entry_id is not None, key
    return entry_id


async def refunds_world(
    api: httpx2.AsyncClient, db: AsyncSession, who: dict[str, Any], sunita_id: uuid.UUID
) -> dict[str, Any]:
    """Refunds of every kind at the seed hub, oldest first: a shortage, an issue credit, an
    undelivered box, a tiffin no kitchen took, a carried item, an odd code, and Sunita's shortage
    sent to the payment source because her wallet was full."""
    owner = who["priya_id"]
    short, ids = await placed_order(db, owner, reach=PICKING, short={"onion": 1})
    delivered, _ = await placed_order(db, owner)
    reported = await report(
        api, who["priya"], delivered, {"kind": "missing", "items": items(ids, tomato=1)}
    )
    assert reported.status_code == 201 and reported.json()["status"] == "credited", reported.text
    issue_id = uuid.UUID(reported.json()["id"])
    manual, _ = await placed_order(db, owner, reach=PICKING)
    for amount, reason in (
        (700, "undelivered"),
        (600, "no_kitchen"),
        (500, f"carry_{ids['bhindi'].hex}"),
        (400, "returned_to_hub"),
    ):
        await billing_api.refund_to_wallet(db, owner, manual, amount, reason=reason)
    full, sids = await placed_order(db, sunita_id, reach=PICKING)
    await paid_topup(db, sunita_id, CAP_PAISE)
    await orders_api.mark_shortage(db, full, sids["onion"], 1)
    await db.commit()

    entries = {
        "short": await entry_of(db, short, f"short_{ids['onion'].hex}"),
        "issue": await entry_of(db, delivered, f"issue_{issue_id.hex}"),
        "undelivered": await entry_of(db, manual, "undelivered"),
        "no_kitchen": await entry_of(db, manual, "no_kitchen"),
        "carry": await entry_of(db, manual, f"carry_{ids['bhindi'].hex}"),
        "other": await entry_of(db, manual, "returned_to_hub"),
        "source": await entry_of(db, full, f"short_{sids['onion'].hex}"),
    }
    credit = await db.scalar(select(OrderIssue.credit_paise).where(OrderIssue.id == issue_id))
    return {
        "entries": entries,
        "orders": {"short": short, "issue": delivered, "manual": manual, "source": full},
        "ids": ids,
        "sids": sids,
        "issue_id": issue_id,
        "credit": credit,
    }


async def fetch(api: httpx2.AsyncClient, auth: dict, query: str = "") -> httpx2.Response:
    return await api.get(f"{URL}?{query}", headers=auth)


@time_machine.travel(NOW, tick=False)
async def test_the_list_gives_every_refund_with_its_reason_and_where_the_money_went(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    sunita, sunita_id = await as_user(api, google, SUNITA)
    made = await refunds_world(api, db_session, who, sunita_id)
    entries, orders, ids, sids = made["entries"], made["orders"], made["ids"], made["sids"]

    answer = await fetch(api, who["hub"], f"hub_id={SEED_HUB_ID}")

    assert answer.status_code == 200, answer.text
    assert answer.headers["cache-control"] == "no-store"
    page = answer.json()
    assert page["next_cursor"] is None
    rows = page["items"]
    # Newest first.
    assert [r["id"] for r in rows] == [
        str(entries[k])
        for k in ("source", "other", "carry", "no_kitchen", "undelivered", "issue", "short")
    ]
    by_id = {r["id"]: r for r in rows}
    source = by_id[str(entries["source"])]
    assert source == {
        "id": str(entries["source"]),
        "order_id": str(orders["source"]),
        "order_kind": "box",
        "delivery_date": DELIVERY.isoformat(),
        "society": {"id": source["society"]["id"], "name": "Green Valley Heights"},
        "flat_label": source["flat_label"],
        "reason": "shortage",
        "product_id": str(sids["onion"]),
        "issue_id": None,
        "amount_paise": 4_000,
        "to": "source",
        "refund_id": source["refund_id"],
        "status": "pending",
        "created_at": source["created_at"],
    }
    refund = await db_session.get(Refund, uuid.UUID(source["refund_id"]))
    assert refund is not None and refund.order_id == orders["source"]
    assert source["flat_label"] and "B-504" in source["flat_label"]

    short = by_id[str(entries["short"])]
    assert (short["reason"], short["product_id"], short["issue_id"]) == (
        "shortage",
        str(ids["onion"]),
        None,
    )
    assert (short["to"], short["status"], short["refund_id"]) == ("wallet", "credited", None)
    assert (short["amount_paise"], short["society"]["name"]) == (4_000, "Sunrise Towers")
    assert short["flat_label"] == "A wing, A-1203"
    issue = by_id[str(entries["issue"])]
    assert (issue["reason"], issue["issue_id"], issue["product_id"]) == (
        "issue",
        str(made["issue_id"]),
        None,
    )
    assert issue["amount_paise"] == made["credit"] > 0
    carried = by_id[str(entries["carry"])]
    assert (carried["reason"], carried["product_id"], carried["amount_paise"]) == (
        "carry_over",
        str(ids["bhindi"]),
        500,
    )
    for name, reason, amount in (
        ("undelivered", "undelivered", 700),
        ("no_kitchen", "no_kitchen", 600),
        ("other", "other", 400),
    ):
        row = by_id[str(entries[name])]
        assert (row["reason"], row["amount_paise"], row["product_id"], row["issue_id"]) == (
            reason,
            amount,
            None,
            None,
        )
    # Money, ids and labels only: no name, phone or balance of the household.
    assert set(short) == {
        "id", "order_id", "order_kind", "delivery_date", "society", "flat_label", "reason",
        "product_id", "issue_id", "amount_paise", "to", "refund_id", "status", "created_at",
    }  # fmt: skip
    assert "Priya" not in answer.text and "+9198" not in answer.text and sunita


@time_machine.travel(NOW, tick=False)
async def test_a_refund_to_source_shows_the_status_of_its_refund(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    _, sunita_id = await as_user(api, google, SUNITA)
    made = await refunds_world(api, db_session, who, sunita_id)
    entry = str(made["entries"]["source"])
    refund = (
        await db_session.execute(select(Refund).where(Refund.order_id == made["orders"]["source"]))
    ).scalar_one()

    for status in ("processing", "processed", "partly_failed", "failed"):
        refund.status = status
        await db_session.commit()
        rows = (await fetch(api, who["hub"], f"hub_id={SEED_HUB_ID}&destination=source")).json()
        (row,) = rows["items"]
        assert (row["id"], row["to"], row["status"], row["refund_id"]) == (
            entry,
            "source",
            status,
            str(refund.id),
        )


@time_machine.travel(NOW, tick=False)
async def test_filters_by_reason_destination_and_delivery_dates(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    _, sunita_id = await as_user(api, google, SUNITA)
    made = await refunds_world(api, db_session, who, sunita_id)
    entries = made["entries"]
    base = f"hub_id={SEED_HUB_ID}"

    async def ids_of(query: str) -> list[str]:
        answer = await fetch(api, who["hub"], f"{base}&{query}")
        assert answer.status_code == 200, answer.text
        return [r["id"] for r in answer.json()["items"]]

    assert await ids_of("reason=shortage") == [str(entries["source"]), str(entries["short"])]
    assert await ids_of("reason=issue") == [str(entries["issue"])]
    assert await ids_of("reason=carry_over") == [str(entries["carry"])]
    assert await ids_of("reason=other") == [str(entries["other"])]
    assert await ids_of("destination=source") == [str(entries["source"])]
    assert len(await ids_of("destination=wallet")) == 6
    assert await ids_of("destination=wallet&reason=shortage") == [str(entries["short"])]
    assert await ids_of("destination=source&reason=issue") == []
    # Delivery dates: the default is the last 7 days to today (IST).
    assert len(await ids_of(f"from={DELIVERY}&to={DELIVERY}")) == 7
    later = DELIVERY + timedelta(days=1)
    assert await ids_of(f"from={later}&to={later}") == []
    assert await ids_of(f"to={DELIVERY - timedelta(days=1)}") == []
    old = DELIVERY - timedelta(days=8)
    await db_session.execute(
        update(Order).where(Order.id == made["orders"]["short"]).values(delivery_date=old)
    )
    await db_session.commit()
    assert str(entries["short"]) not in await ids_of("")
    assert await ids_of(f"from={old}&to={old}") == [str(entries["short"])]


@time_machine.travel(NOW, tick=False)
async def test_pages_follow_the_cursor_without_gaps_or_repeats(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    _, sunita_id = await as_user(api, google, SUNITA)
    await refunds_world(api, db_session, who, sunita_id)
    query = f"hub_id={SEED_HUB_ID}"
    everything = [r["id"] for r in (await fetch(api, who["hub"], query)).json()["items"]]

    seen: list[str] = []
    cursor, sizes = None, []
    while True:
        extra = f"&cursor={cursor}" if cursor else ""
        page = (await fetch(api, who["hub"], f"{query}&limit=3{extra}")).json()
        sizes.append(len(page["items"]))
        seen += [r["id"] for r in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert sizes == [3, 3, 1] and seen == everything and len(set(seen)) == 7
    bad = await fetch(api, who["hub"], f"{query}&cursor=garbage")
    assert code(bad) == (422, "request.invalid_cursor")
    odd = await fetch(api, who["hub"], f"{query}&cursor={refund_list.encode_cursor({'after': 1})}")
    assert code(odd) == (422, "request.invalid_cursor")
    naive = refund_list.encode_cursor({"after": ["2026-10-12T10:00:00", str(uuid.uuid4())]})
    assert code(await fetch(api, who["hub"], f"{query}&cursor={naive}")) == (
        422,
        "request.invalid_cursor",
    )


@time_machine.travel(NOW, tick=False)
async def test_hub_staff_see_their_hub_and_admins_every_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    _, sunita_id = await as_user(api, google, SUNITA)
    made = await refunds_world(api, db_session, who, sunita_id)
    neha = who["admin"]
    # Sunita's order belongs to another hub.
    await db_session.execute(
        update(Order).where(Order.id == made["orders"]["source"]).values(hub_id=OTHER_HUB)
    )
    await db_session.commit()
    source = str(made["entries"]["source"])

    mine = (await fetch(api, who["hub"], f"hub_id={SEED_HUB_ID}")).json()["items"]
    theirs = (await fetch(api, who["other"], f"hub_id={OTHER_HUB}")).json()["items"]
    admin_all = (await fetch(api, neha)).json()["items"]
    admin_one = (await fetch(api, neha, f"hub_id={OTHER_HUB}")).json()["items"]

    assert source not in [r["id"] for r in mine] and len(mine) == 6
    assert [r["id"] for r in theirs] == [source] == [r["id"] for r in admin_one]
    assert len(admin_all) == 7 and admin_all[0]["id"] == source
    # A hub manager passes their own hub; another hub's is forbidden.
    assert code(await fetch(api, who["hub"])) == (422, "orders.hub_required")
    assert code(await fetch(api, who["hub"], f"hub_id={OTHER_HUB}")) == (403, "auth.forbidden")
    assert code(await fetch(api, who["other"], f"hub_id={SEED_HUB_ID}")) == (
        403,
        "auth.forbidden",
    )


@time_machine.travel(NOW, tick=False)
async def test_only_hub_managers_and_admins_read_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    owner, _ = await as_user(api, google, SANJAY)

    assert (await fetch(api, who["priya"], f"hub_id={SEED_HUB_ID}")).status_code == 403
    assert code(await fetch(api, owner, f"hub_id={SEED_HUB_ID}")) == (403, "auth.forbidden")
    assert (await api.get(f"{URL}?hub_id={SEED_HUB_ID}")).status_code == 401
    assert (await fetch(api, who["hub"], f"hub_id={SEED_HUB_ID}")).json() == {
        "items": [],
        "next_cursor": None,
    }


@time_machine.travel(NOW, tick=False)
async def test_bad_windows_and_values_are_refused(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    base = f"hub_id={SEED_HUB_ID}"
    today = DELIVERY

    async def get(query: str) -> httpx2.Response:
        return await fetch(api, who["hub"], f"{base}&{query}")

    # At most 31 days apart.
    assert (await get(f"from={today - timedelta(days=31)}&to={today}")).status_code == 200
    for query in (
        f"from={today - timedelta(days=32)}&to={today}",
        f"from={today - timedelta(days=60)}",
        f"from={today}&to={today - timedelta(days=1)}",
        "from=nonsense",
        "reason=bogus",
        "destination=bank",
        "limit=0",
        "limit=101",
    ):
        refused = await get(query)
        assert code(refused) == (422, "request.invalid"), query


async def test_the_codes_map_to_reasons_and_ids() -> None:
    product = uuid.uuid4()
    issue = uuid.uuid4()
    assert refund_list.classify(f"short_{product.hex}") == ("shortage", product)
    assert refund_list.classify(f"carry_{product.hex}") == ("carry_over", product)
    assert refund_list.classify(f"issue_{issue.hex}") == ("issue", issue)
    assert refund_list.classify("undelivered") == ("undelivered", None)
    assert refund_list.classify("no_kitchen") == ("no_kitchen", None)
    for odd in ("returned_to_hub", "short_zz", f"short_{product.hex}0", "left_behind", "short"):
        assert refund_list.classify(odd) == ("other", None)
    today = date(2026, 10, 13)
    assert refund_list.window(None, None, today) == (date(2026, 10, 6), today)
    assert refund_list.window(date(2026, 10, 1), None, today) == (date(2026, 10, 1), today)


@time_machine.travel(NOW, tick=False)
async def test_the_billing_facade_lists_the_refund_entries_of_the_orders_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    _, sunita_id = await as_user(api, google, SUNITA)
    made = await refunds_world(api, db_session, who, sunita_id)
    orders = made["orders"]

    found = await billing_api.order_refunds(db_session, [orders["manual"], orders["source"]])

    assert [(r.order_id, r.reason, r.amount_paise) for r in found] == [
        (orders["source"], f"short_{made['sids']['onion'].hex}", 4_000),
        (orders["manual"], "returned_to_hub", 400),
        (orders["manual"], f"carry_{made['ids']['bhindi'].hex}", 500),
        (orders["manual"], "no_kitchen", 600),
        (orders["manual"], "undelivered", 700),
    ]
    assert found[0].refund_id is not None and found[0].refund_status == "pending"
    assert all(r.refund_id is None and r.refund_status is None for r in found[1:])
    assert await billing_api.order_refunds(db_session, []) == []
    assert await billing_api.order_refunds(db_session, [uuid.uuid4()]) == []
