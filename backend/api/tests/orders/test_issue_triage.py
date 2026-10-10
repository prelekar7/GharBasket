"""The console's issue triage (M4 §3.6): the queue with its filters, paging and hub scope, the
detail with 5-minute photo links and the customer's contact (both audit-logged), and resolving
by credit or rejection through issues.resolve."""

import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.wallet import CAP_PAISE
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import UserRole
from gharbasket.modules.orders.models import Order, OrderIssue
from gharbasket.platform.models import AuditEntry
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..test_files import FakeS3
from .test_issues import (
    BOX,
    HUB,
    NOW,
    assert_ledger_balances,
    events,
    items,
    photo,
    placed_order,
    report,
    with_store,
)
from .test_orders import DELIVERY, PRIYA, SUNITA, VIKRAM, as_user

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

NEHA = "+919800001011"  # admin
OTHER_HUB = uuid.UUID("0199a000-0000-7000-8000-00000000a0ff")
TOTAL = sum(q * p for q, p in BOX.values())  # 13,000
QUEUE = "/v1/admin/order-issues"


async def staff(api: httpx2.AsyncClient, google: FakeGoogle, db: AsyncSession) -> dict[str, Any]:
    """Vikram (the seed hub), Sunita as hub manager of another hub, Neha (admin), Priya."""
    _, sunita_id = await as_user(api, google, SUNITA)
    db.add(UserRole(user_id=sunita_id, role="hub_manager", scope_type="hub", scope_id=OTHER_HUB))
    await db.commit()
    vikram, vikram_id = await as_user(api, google, VIKRAM)
    sunita, _ = await as_user(api, google, SUNITA)
    neha, _ = await as_user(api, google, NEHA)
    priya, priya_id = await as_user(api, google, PRIYA)
    return {
        "hub": {**vikram, **HUB},
        "hub_id": vikram_id,
        "other": {**sunita, "X-Active-Role": f"hub_manager:{OTHER_HUB}"},
        "admin": neha,
        "priya": priya,
        "priya_id": priya_id,
    }


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json()["code"]


async def resolve(
    api: httpx2.AsyncClient, auth: dict, issue_id: str, body: dict[str, Any], key: str = ""
) -> httpx2.Response:
    return await api.post(
        f"{QUEUE}/{issue_id}/resolve",
        json=body,
        headers={**auth, "Idempotency-Key": key or uuid.uuid4().hex},
    )


async def audited(db: AsyncSession, action: str) -> list[AuditEntry]:
    rows = await db.execute(
        select(AuditEntry).where(AuditEntry.action == action).order_by(AuditEntry.id)
    )
    return list(rows.scalars())


@time_machine.travel(NOW, tick=False)
async def test_the_queue_filters_pages_and_keeps_to_the_callers_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    first, ids = await placed_order(db_session, who["priya_id"])
    second, _ = await placed_order(db_session, who["priya_id"])
    priya = who["priya"]
    quality = await report(api, priya, first, {"kind": "quality", "items": items(ids, onion=1)})
    late = await report(api, priya, first, {"kind": "late"})
    missing = await report(api, priya, first, {"kind": "missing", "items": items(ids, tomato=1)})
    damaged = await report(api, priya, second, {"kind": "damaged", "items": items(ids, bhindi=1)})
    quality, late, missing, damaged = (a.json() for a in (quality, late, missing, damaged))
    assert missing["status"] == "credited" and damaged["open_reason"] == "photo_needed"
    # The second order belongs to another hub.
    await db_session.execute(update(Order).where(Order.id == second).values(hub_id=OTHER_HUB))
    await db_session.execute(
        update(OrderIssue).where(OrderIssue.order_id == second).values(hub_id=OTHER_HUB)
    )
    await db_session.commit()

    async def queue(auth: dict, query: str = "") -> httpx2.Response:
        return await api.get(f"{QUEUE}?{query}", headers=auth)

    hub = await queue(who["hub"], f"hub_id={SEED_HUB_ID}")
    assert hub.status_code == 200, hub.text
    assert hub.headers["cache-control"] == "no-store"
    page = hub.json()
    assert [i["id"] for i in page["items"]] == [quality["id"], late["id"]]  # oldest open first
    assert page["next_cursor"] is None
    q, lt = page["items"]
    assert {k: q[k] for k in ("kind", "status", "amount_paise", "photo_count", "open_reason")} == {
        "kind": "quality",
        "status": "open",
        "amount_paise": 4000,
        "photo_count": 0,
        "open_reason": "triage",
    }
    assert q["items"][0]["slug"] == "onion" and q["hub_id"] == str(SEED_HUB_ID)
    household = await identity_api.household_for_user(db_session, who["priya_id"])
    assert household is not None
    assert (q["household_id"], q["society_id"], q["delivery_date"]) == (
        str(household.id),
        str(household.society_id),
        DELIVERY.isoformat(),
    )
    assert (q["order_total_paise"], q["creditable_paise"], q["max_credit_paise"]) == (
        TOTAL,
        TOTAL - 1500,
        4000,
    )
    assert q["auto_credited_7d_paise"] == 1500
    created = datetime.fromisoformat(q["created_at"])
    assert datetime.fromisoformat(q["callback_due_at"]) == created + timedelta(minutes=120)
    assert lt["max_credit_paise"] == TOTAL - 1500  # late: up to what is left of the order

    late_only = (await queue(who["hub"], f"hub_id={SEED_HUB_ID}&kind=late")).json()["items"]
    assert [i["id"] for i in late_only] == [late["id"]]
    credited = (await queue(who["hub"], f"hub_id={SEED_HUB_ID}&status=credited")).json()
    assert [(i["id"], i["max_credit_paise"]) for i in credited["items"]] == [(missing["id"], 0)]
    assert credited["items"][0]["callback_due_at"] is None
    one = (await queue(who["hub"], f"hub_id={SEED_HUB_ID}&limit=1")).json()
    assert [i["id"] for i in one["items"]] == [quality["id"]] and one["next_cursor"]
    rest = (
        await queue(who["hub"], f"hub_id={SEED_HUB_ID}&limit=1&cursor={one['next_cursor']}")
    ).json()
    assert [i["id"] for i in rest["items"]] == [late["id"]] and rest["next_cursor"] is None

    theirs = (await queue(who["other"], f"hub_id={OTHER_HUB}")).json()["items"]
    assert [i["id"] for i in theirs] == [damaged["id"]]
    everything = (await queue(who["admin"])).json()["items"]
    assert [i["id"] for i in everything] == [quality["id"], late["id"], damaged["id"]]
    assert len((await queue(who["admin"], f"hub_id={OTHER_HUB}")).json()["items"]) == 1

    assert code(await queue(who["hub"])) == (422, "orders.hub_required")
    assert code(await queue(who["hub"], f"hub_id={OTHER_HUB}")) == (403, "auth.forbidden")
    assert code(await queue(who["other"], f"hub_id={SEED_HUB_ID}")) == (403, "auth.forbidden")
    assert code(await queue(priya, f"hub_id={SEED_HUB_ID}")) == (403, "auth.forbidden")
    bad = await queue(who["hub"], f"hub_id={SEED_HUB_ID}&cursor=bm90LWEtY3Vyc29y")
    assert code(bad) == (422, "request.invalid_cursor")
    assert code(await queue(who["admin"], "status=lost")) == (422, "request.invalid")


@time_machine.travel(NOW, tick=False)
async def test_the_detail_mints_photo_links_and_reads_the_contact_with_an_audit_entry_each(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    s3 = FakeS3()
    api = with_store(clients(), s3)
    who = await staff(api, google, db_session)
    order_id, ids = await placed_order(db_session, who["priya_id"])
    keys = [await photo(db_session, s3, who["priya_id"]) for _ in range(2)]
    issue = (
        await report(
            api,
            who["priya"],
            order_id,
            {"kind": "quality", "items": items(ids, onion=1), "photo_keys": keys},
        )
    ).json()
    assert issue["status"] == "open" and issue["photo_count"] == 2

    detail = await api.get(f"{QUEUE}/{issue['id']}", headers=who["hub"])

    assert detail.status_code == 200, detail.text
    out = detail.json()
    assert detail.headers["cache-control"] == "no-store"
    assert out["contact"] == {"name": "Priya Sharma", "phone_e164": PRIYA}
    assert (out["max_credit_paise"], out["photo_count"]) == (4000, 2)
    assert [datetime.fromisoformat(p["expires_at"]) for p in out["photos"]] == [
        NOW + timedelta(minutes=5)
    ] * 2
    for key, link in zip(keys, out["photos"], strict=True):
        url = urlparse(link["url"])
        assert url.path.endswith(key) and parse_qs(url.query)["X-Amz-Expires"] == ["300"]
    links = await audited(db_session, "order_issues.photo_link")
    contacts = await audited(db_session, "order_issues.contact_read")
    assert [(e.entity_id, e.actor_user_id, e.after) for e in links] == [
        (issue["id"], who["hub_id"], {"key": key}) for key in keys
    ]
    assert [(e.entity_type, e.entity_id, e.actor_user_id) for e in contacts] == [
        ("order_issue", issue["id"], who["hub_id"])
    ]
    assert "phone" not in str(contacts[0].after)

    # Without storage the links are left out; photo_count still says there are two.
    plain = (await clients().get(f"{QUEUE}/{issue['id']}", headers=who["admin"])).json()
    assert (plain["photos"], plain["photo_count"]) == ([], 2)
    assert len(await audited(db_session, "order_issues.photo_link")) == 2
    assert len(await audited(db_session, "order_issues.contact_read")) == 2

    assert code(await api.get(f"{QUEUE}/{uuid.uuid4()}", headers=who["hub"])) == (
        404,
        "orders.issue_not_found",
    )
    assert code(await api.get(f"{QUEUE}/{issue['id']}", headers=who["other"])) == (
        403,
        "auth.forbidden",
    )
    assert code(await api.get(f"{QUEUE}/{issue['id']}", headers=who["priya"])) == (
        403,
        "auth.forbidden",
    )


@time_machine.travel(NOW, tick=False)
async def test_the_hub_credits_once_per_key_or_rejects_with_a_note(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    priya_id = who["priya_id"]
    order_id, ids = await placed_order(db_session, priya_id)
    quality = await report(
        api, who["priya"], order_id, {"kind": "quality", "items": items(ids, onion=1)}
    )
    late = await report(api, who["priya"], order_id, {"kind": "late"})
    other = await report(api, who["priya"], order_id, {"kind": "other", "note": "Rude"})
    quality, late, other = quality.json(), late.json(), other.json()
    await db_session.commit()
    before = await billing_api.balance(db_session, priya_id)
    credit = {"outcome": "credit", "credit_paise": 3000, "note": "Soft onions"}

    too_much = await resolve(
        api, who["hub"], quality["id"], {"outcome": "credit", "credit_paise": 4001}
    )
    assert code(too_much) == (422, "orders.issue_credit_invalid")
    assert too_much.json()["max_paise"] == 4000
    nothing = await resolve(api, who["hub"], quality["id"], {"outcome": "credit"})
    assert (code(nothing), nothing.json()["max_paise"]) == (
        (422, "orders.issue_credit_invalid"),
        4000,
    )
    no_key = await api.post(f"{QUEUE}/{quality['id']}/resolve", json=credit, headers=who["hub"])
    assert no_key.json()["code"] == "request.idempotency_key_required"
    for refused in (who["priya"], who["other"]):
        assert code(await resolve(api, refused, quality["id"], credit)) == (403, "auth.forbidden")
    assert code(await resolve(api, who["hub"], str(uuid.uuid4()), credit)) == (
        404,
        "orders.issue_not_found",
    )

    first = await resolve(api, who["hub"], quality["id"], credit, key="triage-1")
    replay = await resolve(api, who["hub"], quality["id"], credit, key="triage-1")
    mismatch = await resolve(
        api, who["hub"], quality["id"], {**credit, "credit_paise": 1}, key="triage-1"
    )
    again = await resolve(api, who["hub"], quality["id"], credit)

    assert first.status_code == 200, first.text
    out = first.json()
    assert (out["status"], out["credit_paise"], out["open_reason"]) == ("credited", 3000, None)
    assert out["resolution"]["by"] == "hub" and out["resolution"]["note"] == "Soft onions"
    assert (out["max_credit_paise"], out["creditable_paise"]) == (0, TOTAL - 3000)
    assert out["callback_due_at"] is None and out["callback_within_minutes"] is None
    assert replay.json() == out and replay.headers["idempotent-replayed"] == "true"
    assert mismatch.json()["code"] == "request.idempotency_mismatch"
    assert code(again) == (409, "orders.issue_not_open")
    assert await billing_api.balance(db_session, priya_id) == before + 3000

    over_total = await resolve(
        api, who["admin"], late["id"], {"outcome": "credit", "credit_paise": TOTAL}
    )
    assert over_total.json()["max_paise"] == TOTAL - 3000
    by_admin = await resolve(
        api, who["admin"], late["id"], {"outcome": "credit", "credit_paise": 500}
    )
    assert (by_admin.status_code, by_admin.json()["credit_paise"]) == (200, 500)
    no_note = await resolve(api, who["hub"], other["id"], {"outcome": "reject"})
    assert code(no_note) == (422, "request.invalid")
    rejected = await resolve(
        api, who["hub"], other["id"], {"outcome": "reject", "note": "Not ours"}
    )
    assert rejected.status_code == 200, rejected.text
    assert (rejected.json()["status"], rejected.json()["credit_paise"]) == ("rejected", 0)
    assert rejected.json()["resolution"]["note"] == "Not ours"

    assert await billing_api.balance(db_session, priya_id) == before + 3500
    await assert_ledger_balances(db_session)
    assert [
        (e["issue_id"], e["status"], e["credit_paise"], e["by"])
        for e in await events(db_session, "orders.issue_resolved")
    ] == [
        (quality["id"], "credited", 3000, "hub"),
        (late["id"], "credited", 500, "hub"),
        (other["id"], "rejected", 0, "hub"),
    ]
    resolved = await audited(db_session, "order_issues.resolved")
    assert [(e.entity_id, e.before, e.after) for e in resolved] == [
        (quality["id"], {"status": "open"}, {"status": "credited", "credit_paise": 3000}),
        (late["id"], {"status": "open"}, {"status": "credited", "credit_paise": 500}),
        (other["id"], {"status": "open"}, {"status": "rejected", "credit_paise": 0}),
    ]
    assert resolved[0].actor_user_id == who["hub_id"]


@time_machine.travel(NOW, tick=False)
async def test_a_full_wallet_refuses_the_credit_and_the_issue_stays_open(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    order_id, ids = await placed_order(db_session, who["priya_id"], extra_topup=CAP_PAISE)
    issue = (
        await report(
            api, who["priya"], order_id, {"kind": "missing", "items": items(ids, tomato=1)}
        )
    ).json()
    assert issue["open_reason"] == "wallet_limit"
    await db_session.commit()

    full = await resolve(api, who["hub"], issue["id"], {"outcome": "credit", "credit_paise": 1500})

    assert code(full) == (422, "billing.wallet_limit") and full.json()["room_paise"] == 0
    detail = (await api.get(f"{QUEUE}/{issue['id']}", headers=who["hub"])).json()
    assert (detail["status"], detail["max_credit_paise"]) == ("open", 1500)
    assert await billing_api.balance(db_session, who["priya_id"]) == CAP_PAISE
    assert await events(db_session, "orders.issue_resolved") == []
    assert await audited(db_session, "order_issues.resolved") == []
