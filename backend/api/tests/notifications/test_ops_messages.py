"""The staff messages of the ops alerts (ADR-0033, M11 §3.1 event map): a reconciliation run that
opened items tells every admin (`billing.reconciliation_mismatch`, M5 §3.4), and a route plan left
for review tells the hub's managers and every admin (`logistics.plan_needs_review`, M7 §3.2). Both
are account messages, always pushed, opening the console (absolute when CONSOLE_URL is set)."""

import uuid
from typing import Any

import pytest
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.hub_ops.api import HubRef
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.modules.notifications.jobs import on_event
from gharbasket.modules.notifications.kinds import ACCOUNT, KINDS
from gharbasket.modules.notifications.models import Delivery, Message
from gharbasket.modules.notifications.render import format_value
from gharbasket.modules.notifications.service import inbox
from gharbasket.platform import settings as settings_module
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from .. import seed_cache as cached
from ..conftest import make_settings
from .conftest import MEENA, PRIYA, Home

pytestmark = [pytest.mark.anyio, pytest.mark.services]

NEHA = "+919800001011"  # the seed's admin; en-IN
VIKRAM = "+919800001008"  # the seed hub's manager
CONSOLE = "https://console.example.app"
MISMATCH, REVIEW = "billing.reconciliation_mismatch", "logistics.plan_needs_review"
WEDNESDAY = "2026-11-11"


@pytest.fixture(autouse=True)
def no_console_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """No CONSOLE_URL unless a test asks for one."""
    monkeypatch.setattr(settings_module, "_in_use", make_settings())


@pytest.fixture
def console_url(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings_module, "_in_use", make_settings(console_url=CONSOLE + "/"))
    return CONSOLE


@pytest.fixture
async def hub(db_session: AsyncSession) -> HubRef:
    await cached.seed_hubs(db_session)
    found = await hub_ops_api.get_hub(db_session, SEED_HUB_ID)
    assert found is not None
    return found


async def user(db: AsyncSession, phone: str) -> uuid.UUID:
    found = await db.scalar(select(User.id).where(User.phone_e164 == phone))
    assert found is not None
    return found


async def grant(
    db: AsyncSession, user_id: uuid.UUID, role: str, scope_id: uuid.UUID | None = None
) -> None:
    db.add(UserRole(user_id=user_id, role=role, scope_id=scope_id, status="verified"))
    await db.flush()


def mismatch(**more: Any) -> dict[str, Any]:
    return {"run_id": str(uuid.uuid4()), "opened": 3, "kinds": ["amount_mismatch"]} | more


def review(**more: Any) -> dict[str, Any]:
    return {
        "plan_id": str(uuid.uuid4()),
        "hub_id": str(SEED_HUB_ID),
        "delivery_date": WEDNESDAY,
        "kind": "box",
        "problems": ["2 stops late"],
    } | more


async def deliver(db: AsyncSession, event_type: str, payload: dict[str, Any]) -> list[Message]:
    event = OutboxEvent(id=uuid.uuid4(), event_type=event_type, payload=payload)
    await on_event(db, event)
    rows = await db.execute(select(Message).where(Message.event_id == event.id))
    return list(rows.scalars())


def test_both_kinds_are_account_messages_that_open_the_console() -> None:
    assert (KINDS[MISMATCH].category, KINDS[MISMATCH].link) == (ACCOUNT, "/reconciliation")
    assert (KINDS[REVIEW].category, KINDS[REVIEW].link) == (ACCOUNT, "/routes")


def test_reconciliation_kinds_read_in_each_language_and_a_new_one_as_its_code() -> None:
    kinds = ["amount_mismatch", "refund_stuck"]
    assert format_value("recon_kinds", kinds, "en") == "amounts differ, refund stuck"
    assert format_value("recon_kinds", kinds, "hi") == "रकम अलग, रिफ़ंड अटका"
    assert format_value("recon_kinds", kinds, "mr") == "रक्कम वेगळी, रिफंड अडकला"
    assert format_value("recon_kinds", ["fee_mismatch"], "hi") == "fee_mismatch"
    with pytest.raises(TypeError):
        format_value("recon_kinds", "amount_mismatch", "en")


async def test_a_mismatch_tells_every_admin_in_their_language_and_nobody_else(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    neha, meena = await user(db_session, NEHA), homes[MEENA].user_id  # Meena: hi-IN
    await grant(db_session, meena, "admin")

    messages = await deliver(
        db_session, MISMATCH, mismatch(kinds=["amount_mismatch", "refund_stuck"])
    )

    assert {m.user_id for m in messages} == {neha, meena}  # not Priya, not the hub manager
    for message in messages:
        assert (message.kind, message.category, message.link) == (
            MISMATCH,
            ACCOUNT,
            "/reconciliation",
        )
        assert message.params == {"opened": 3, "kinds": ["amount_mismatch", "refund_stuck"]}
        pushes = await db_session.scalars(select(Delivery).where(Delivery.message_id == message.id))
        assert [d.channel for d in pushes] == ["push"]
    english, _, _ = await inbox(db_session, neha, "en", None, 10)
    hindi, _, _ = await inbox(db_session, meena, "hi", None, 10)
    assert (english[0].title, english[0].body) == (
        "Payment check: 3 to look at",
        "New in this run: amounts differ, refund stuck. Open Reconciliation to look.",
    )
    assert hindi[0].title == "भुगतान जाँच: 3 मामले देखने हैं"
    assert "रकम अलग, रिफ़ंड अटका" in hindi[0].body


async def test_a_mismatch_links_to_the_consoles_absolute_url_when_it_is_set(
    db_session: AsyncSession, homes: dict[str, Home], console_url: str
) -> None:
    messages = await deliver(db_session, MISMATCH, mismatch())

    assert messages and {m.link for m in messages} == {f"{console_url}/reconciliation"}


@pytest.mark.parametrize(
    "payload",
    [
        {"run_id": "r", "kinds": ["amount_mismatch"]},  # no count
        mismatch(kinds="amount_mismatch"),  # not a list
        mismatch(opened="three"),
    ],
)
async def test_a_mismatch_without_usable_values_is_dropped(
    db_session: AsyncSession, homes: dict[str, Home], payload: dict[str, Any]
) -> None:
    assert await deliver(db_session, MISMATCH, payload) == []


async def test_a_plan_to_review_tells_the_hubs_managers_and_every_admin(
    db_session: AsyncSession, homes: dict[str, Home], hub: HubRef
) -> None:
    neha, vikram = await user(db_session, NEHA), await user(db_session, VIKRAM)
    priya, meena = homes[PRIYA].user_id, homes[MEENA].user_id
    await grant(db_session, priya, "hub_manager", uuid.uuid4())  # another hub's manager
    await grant(db_session, meena, "hub_manager", SEED_HUB_ID)  # a second manager here …
    await grant(db_session, meena, "admin")  # … who is also an admin: told once
    assert await identity_api.users_with_role(db_session, "hub_manager", SEED_HUB_ID) == [
        vikram,
        meena,
    ]

    messages = await deliver(db_session, REVIEW, review())

    assert sorted(m.user_id for m in messages) == sorted([vikram, meena, neha])
    for message in messages:
        assert (message.kind, message.category, message.link) == (REVIEW, ACCOUNT, "/routes")
        assert message.params == {"hub": hub.name, "day": WEDNESDAY}
    english, _, _ = await inbox(db_session, neha, "en", None, 10)
    hindi, _, _ = await inbox(db_session, meena, "hi", None, 10)
    assert english[0].title == f"Routes for {hub.name} need a look"
    assert english[0].body.startswith("Wednesday's plan has problems")
    assert hindi[0].title == f"{hub.name} के रूट देखने हैं"
    assert hindi[0].body.startswith("बुधवार के प्लान")


async def test_a_plan_to_review_links_to_the_consoles_absolute_url_when_it_is_set(
    db_session: AsyncSession, homes: dict[str, Home], hub: HubRef, console_url: str
) -> None:
    messages = await deliver(db_session, REVIEW, review())

    assert messages and {m.link for m in messages} == {f"{console_url}/routes"}


async def test_a_plan_at_a_hub_that_is_gone_is_logged_and_nobody_is_told(
    db_session: AsyncSession, homes: dict[str, Home], hub: HubRef
) -> None:
    with structlog.testing.capture_logs() as logs:
        assert await deliver(db_session, REVIEW, review(hub_id=str(uuid.uuid4()))) == []

    assert [e["event"] for e in logs] == ["plan_hub_missing"]
