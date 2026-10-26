"""The tiffin menu review (M2 §3.4): the hub's list of every status, approve, send back and
reopen; who may do what; the cut-off; the audit entry and the event; and that only approved menus
reach customers."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import tiffin_review
from gharbasket.modules.catalog.models import Product, Recipe, TiffinMenu
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID, seed_partners

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..orders.test_orders import PRIYA, VIKRAM, as_user
from ..orders.tiffin_menus import menu

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

NOW = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)  # Sat 15:30 IST; tomorrow's cut-off is 21:00 IST
TOMORROW = date(2026, 10, 11)
NEHA = "+919800001011"  # admin
REKHA = "+919800001013"  # catalog editor
MEENA = "+919800001004"  # a seeded partner, standing in for a kitchen
HUB_MENUS = f"/v1/hubs/{SEED_HUB_ID}/tiffin-menus"


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json().get("code", "")


async def act(
    api: httpx2.AsyncClient, auth: dict, menu_id: uuid.UUID, action: str, **body: Any
) -> httpx2.Response:
    return await api.post(f"/v1/tiffin-menus/{menu_id}/{action}", json=body, headers=auth)


@time_machine.travel(NOW, tick=False)
async def test_the_list_shows_every_status_waiting_menus_first_with_rules_and_ingredients(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_partners(db_session)
    meena, _ = await find_or_create_user(db_session, MEENA)
    cook = await partners_api.partner_for_user(db_session, meena.id, "captain")
    assert cook is not None
    approved = (await menu(db_session, TOMORROW, status="approved")).id
    newer = (
        await menu(db_session, TOMORROW, meal="dinner", status="submitted", partner_id=cook.id)
    ).id
    older = (await menu(db_session, date(2026, 10, 13), status="submitted")).id
    sent = (await menu(db_session, TOMORROW, status="sent_back", slug="onion")).id
    await menu(db_session, TOMORROW, hub_id=uuid.uuid4())  # another hub
    await menu(db_session, date(2026, 10, 30))  # beyond the default week
    await db_session.execute(
        update(TiffinMenu).where(TiffinMenu.id == newer).values(submitted_at=NOW)
    )
    await db_session.execute(
        update(TiffinMenu)
        .where(TiffinMenu.id == older)
        .values(submitted_at=NOW - timedelta(days=1))
    )
    await db_session.commit()
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)

    listed = await api.get(HUB_MENUS, headers={**vikram, "Accept-Language": "hi"})
    only = await api.get(f"{HUB_MENUS}?status=sent_back", headers=vikram)
    narrow = await api.get(
        f"{HUB_MENUS}?from=2026-10-11&to=2026-10-11&status=submitted", headers=vikram
    )
    bad = await api.get(f"{HUB_MENUS}?from=2026-10-12&to=2026-10-11", headers=vikram)
    bad_status = await api.get(f"{HUB_MENUS}?status=draft", headers=vikram)

    assert listed.status_code == 200, listed.text
    assert listed.headers["cache-control"] == "no-store"
    body = listed.json()
    assert body["next_cursor"] is None
    assert [m["id"] for m in body["items"]] == [
        str(older),  # waiting, submitted a day ago
        str(newer),  # waiting, submitted now
        str(approved),  # then by date and meal
        str(sent),
    ]
    assert [m["status"] for m in body["items"]] == [
        "submitted",
        "submitted",
        "approved",
        "sent_back",
    ]
    first = body["items"][1]
    assert first["meal"] == "dinner" and first["date"] == "2026-10-11"
    card = await partners_api.profile_card(db_session, cook.id)
    assert card is not None
    assert first["kitchen"] == {"partner_id": str(cook.id), "display_name": card.display_name}
    assert first["price_paise"] == 11_000
    assert len(first["dishes"]) == 2 and all(d["name"] for d in first["dishes"])
    assert set(first["rules"]) == {"jain_ok", "no_onion_garlic", "fasting_ok", "diabetic_ok"}
    assert first["contains"] and len(set(first["contains"])) == len(first["contains"])
    assert first["product"]["slug"] == "tomato" and isinstance(first["product"]["tags"], list)
    assert first["order_by"] == "2026-10-10T15:30:00Z"
    assert first["decided_at"] is None and first["decided_by"] is None and first["note"] is None
    assert first["version"] == 1

    assert [m["id"] for m in only.json()["items"]] == [str(sent)]
    assert [m["id"] for m in narrow.json()["items"]] == [str(newer)]
    assert code(bad) == (422, "request.invalid")
    assert bad_status.status_code == 422


@time_machine.travel(NOW, tick=False)
async def test_the_list_pages_in_the_same_order(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    made: list[uuid.UUID] = []
    for n in range(5):
        one = await menu(
            db_session, TOMORROW + timedelta(days=n % 3), meal="dinner" if n % 2 else "lunch"
        )
        waiting = n < 3  # three waiting, two approved
        one.status = "submitted" if waiting else "approved"
        one.submitted_at = NOW - timedelta(hours=n)
        made.append(one.id)
    await db_session.commit()
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)

    whole = (await api.get(HUB_MENUS, headers=vikram)).json()["items"]
    seen: list[str] = []
    cursor = ""
    while True:
        page = (await api.get(f"{HUB_MENUS}?limit=2{cursor}", headers=vikram)).json()
        seen += [m["id"] for m in page["items"]]
        if page["next_cursor"] is None:
            break
        cursor = f"&cursor={page['next_cursor']}"
    bad = await api.get(f"{HUB_MENUS}?cursor=nonsense", headers=vikram)

    assert seen == [m["id"] for m in whole] and len(seen) == 5
    assert [m["id"] for m in whole[:3]] == [str(made[2]), str(made[1]), str(made[0])]
    assert bad.status_code == 422


@time_machine.travel(NOW, tick=False)
async def test_a_menu_is_approved_then_booked_and_reopened_by_the_hub_manager(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    made = await menu(db_session, TOMORROW, status="submitted")
    waiting, partner = made.id, made.partner_id
    await db_session.commit()
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    vikram, vikram_id = await as_user(api, google, VIKRAM)

    before = await api.get("/v1/tiffin-menus", headers=priya)
    approved = await act(api, vikram, waiting, "approve")
    again = await act(api, vikram, waiting, "approve")
    after = await api.get("/v1/tiffin-menus", headers=priya)
    reopened = await act(api, vikram, waiting, "reopen")
    gone = await api.get("/v1/tiffin-menus", headers=priya)
    reopened_twice = await act(api, vikram, waiting, "reopen")

    assert [m["id"] for m in before.json()["items"]] == []
    assert approved.status_code == 200, approved.text
    one = approved.json()
    assert one["status"] == "approved" and one["version"] == 2 and one["note"] is None
    assert one["decided_by"] == {"user_id": str(vikram_id), "name": "Vikram Desai"}
    assert one["decided_at"] == "2026-10-10T10:00:00Z"
    assert again.status_code == 200 and again.json() == one  # unchanged, version too
    assert [m["id"] for m in after.json()["items"]] == [str(waiting)]
    back = reopened.json()
    assert reopened.status_code == 200, reopened.text
    assert back["status"] == "submitted" and back["version"] == 3
    assert back["decided_at"] is None and back["decided_by"] is None and back["note"] is None
    assert gone.json()["items"] == []
    assert code(reopened_twice) == (409, "catalog.menu_not_decided")

    audit = (
        (
            await db_session.execute(
                select(AuditEntry)
                .where(AuditEntry.action == "catalog.tiffin_menu_decided")
                .order_by(AuditEntry.id)
            )
        )
        .scalars()
        .all()
    )
    assert [(a.before["status"], a.after["status"]) for a in audit if a.before and a.after] == [
        ("submitted", "approved"),
        ("approved", "submitted"),
    ]
    assert all(a.entity_id == str(waiting) and a.actor_user_id == vikram_id for a in audit)
    published = (
        (
            await db_session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.event_type == "catalog.tiffin_menu_decided")
                .order_by(OutboxEvent.id)
            )
        )
        .scalars()
        .all()
    )
    assert [e.payload["status"] for e in published] == ["approved", "submitted"]
    assert published[0].payload == {
        "menu_id": str(waiting),
        "partner_id": str(partner),
        "hub_id": str(SEED_HUB_ID),
        "date": "2026-10-11",
        "meal": "lunch",
        "status": "approved",
    }


@time_machine.travel(NOW, tick=False)
async def test_a_menu_is_sent_back_with_a_reason_and_undone(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    waiting = (await menu(db_session, TOMORROW, status="submitted")).id
    approved = (await menu(db_session, TOMORROW, meal="dinner", status="approved")).id
    await db_session.commit()
    api = clients()
    rekha, rekha_id = await as_user(api, google, REKHA)

    no_note = await act(api, rekha, waiting, "send-back")
    blank = await act(api, rekha, waiting, "send-back", note="   ")
    long = await act(api, rekha, waiting, "send-back", note="x" * 501)
    sent = await act(api, rekha, waiting, "send-back", note="  Add the dal  ")
    twice = await act(api, rekha, waiting, "send-back", note="Again")
    approve_sent = await act(api, rekha, waiting, "approve")
    on_approved = await act(api, rekha, approved, "send-back", note="Too late")
    undone = await act(api, rekha, waiting, "reopen")

    assert code(no_note) == (422, "request.invalid")
    assert code(blank) == (422, "request.invalid")
    assert code(long) == (422, "request.invalid")
    one = sent.json()
    assert sent.status_code == 200, sent.text
    assert one["status"] == "sent_back" and one["note"] == "Add the dal" and one["version"] == 2
    assert one["decided_by"]["user_id"] == str(rekha_id)
    assert code(twice) == (409, "catalog.menu_not_submitted")
    assert code(approve_sent) == (409, "catalog.menu_not_submitted")
    assert code(on_approved) == (409, "catalog.menu_not_submitted")
    assert undone.json()["status"] == "submitted" and undone.json()["note"] is None
    assert undone.json()["decided_by"] is None and undone.json()["version"] == 3


@time_machine.travel(NOW, tick=False)
async def test_approval_needs_an_active_product_and_dishes_and_a_menu_before_its_cut_off(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = await menu(db_session, TOMORROW, status="submitted")
    broken, product, recipe = made.id, made.product_id, made.recipe_ids[0]
    late = (await menu(db_session, TOMORROW, meal="dinner", status="submitted")).id
    decided = (await menu(db_session, TOMORROW, meal="dinner", status="approved", slug="onion")).id
    await db_session.execute(update(Product).where(Product.id == product).values(active=False))
    await db_session.execute(update(Recipe).where(Recipe.id == recipe).values(active=False))
    await db_session.commit()
    api = clients()
    neha, _ = await as_user(api, google, NEHA)

    invalid = await act(api, neha, broken, "approve")
    still = await db_session.get(TiffinMenu, broken, populate_existing=True)
    assert still is not None and still.status == "submitted" and still.version == 1
    # 21:00 IST on the 10th: tomorrow's menus are past their cut-off (the token stays valid).
    monkeypatch.setattr(tiffin_review, "now", lambda: datetime(2026, 10, 10, 15, 30, tzinfo=UTC))
    closed = await act(api, neha, late, "approve")
    closed_reopen = await act(api, neha, decided, "reopen")
    closed_send_back = await act(api, neha, late, "send-back", note="Sorry")
    same = await act(api, neha, decided, "approve")

    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["code"] == "catalog.menu_invalid"
    assert invalid.json()["reasons"] == ["product_inactive", "recipe_inactive"]
    assert code(closed) == (409, "catalog.menu_closed")
    assert code(closed_reopen) == (409, "catalog.menu_closed")
    assert closed_send_back.status_code == 200  # nobody booked a menu that was never offered
    assert same.status_code == 200  # an approved menu is still a 200 after the cut-off


@time_machine.travel(NOW, tick=False)
async def test_who_may_review_which_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    here = (await menu(db_session, TOMORROW, status="submitted")).id
    other = await menu(db_session, TOMORROW, status="submitted", hub_id=uuid.uuid4())
    elsewhere, elsewhere_hub = other.id, other.hub_id
    await db_session.commit()
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)
    rekha, _ = await as_user(api, google, REKHA)
    neha, _ = await as_user(api, google, NEHA)
    other_hub = f"/v1/hubs/{elsewhere_hub}/tiffin-menus"
    unknown = uuid.uuid4()

    customer_list = await api.get(HUB_MENUS, headers=priya)
    customer_act = await act(api, priya, here, "approve")
    manager_other_list = await api.get(other_hub, headers=vikram)
    manager_other_act = await act(api, vikram, elsewhere, "approve")
    manager_unknown = await act(api, vikram, unknown, "reopen")
    editor_list = await api.get(other_hub, headers=rekha)
    editor_act = await act(api, rekha, elsewhere, "approve")
    admin_list = await api.get(HUB_MENUS, headers=neha)
    admin_act = await act(api, neha, here, "approve")
    anonymous = await api.get(HUB_MENUS)

    assert code(customer_list) == (403, "auth.forbidden")
    assert code(customer_act) == (403, "auth.forbidden")
    assert code(manager_other_list) == (403, "auth.forbidden")
    assert code(manager_other_act) == (403, "auth.forbidden")
    assert code(manager_unknown) == (404, "catalog.menu_not_found")
    assert editor_list.status_code == 200 and len(editor_list.json()["items"]) == 1
    assert editor_act.status_code == 200 and editor_act.json()["status"] == "approved"
    assert admin_list.status_code == 200 and admin_act.status_code == 200
    assert anonymous.status_code == 401
    other_menu = await db_session.get(TiffinMenu, elsewhere)
    assert other_menu is not None and other_menu.status == "approved"  # by the editor, not Vikram


@time_machine.travel(NOW, tick=False)
async def test_a_decision_reads_the_locked_row_not_the_scope_checks_copy(
    db_session: AsyncSession,
) -> None:
    """Approve and send-back at once: the router's scope check loads the menu unlocked, so the
    locked read must reload it, or the second request would overwrite the first's decision."""
    made = await menu(db_session, TOMORROW, status="submitted")
    menu_id = made.id
    await db_session.commit()
    await tiffin_review.get(db_session, menu_id)  # the scope check's unlocked copy
    # Another request approves it meanwhile (plain SQL: this session's copy is not told).
    await db_session.execute(
        text("UPDATE catalog.tiffin_menus SET status = 'approved', version = 2 WHERE id = :id"),
        {"id": menu_id},
    )

    with pytest.raises(ProblemError) as refused:
        await tiffin_review.send_back(db_session, menu_id, uuid.uuid4(), "Too late")

    assert refused.value.code == "catalog.menu_not_submitted"
