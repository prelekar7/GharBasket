"""The income certificate (M8 §3.7): the months, the figures from M5's ledger, the PDF's text, what
never appears on it, the stored object and its link, the audit row, the limits and the roles."""

import hashlib
import io
import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
from pypdf import PdfReader
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.models import Payout
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity.models import User
from gharbasket.modules.partners import certificate
from gharbasket.modules.partners import service as partners_service
from gharbasket.modules.partners.models import KycDocument, Partner
from gharbasket.platform import rate_limit
from gharbasket.platform.models import AuditEntry, Upload
from gharbasket.tools.seed import SEED_HUB_ID, seed_hubs

from ..billing.test_partner_months import earn, settle
from ..identity.conftest import ClientFactory, valkey  # noqa: F401
from ..identity.fake_firebase import FakeGoogle
from ..test_files import FakeS3
from .conftest import LATA, MEENA, NEHA, PRIYA, SANJAY, VIKRAM, as_user, other_hub_manager
from .payees import make_partner
from .test_kyc import store_for

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("partners_world")]

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime.now(UTC)
HERE = NOW.astimezone(IST)
URL = "/v1/earnings/certificate"
REFERENCE = re.compile(r"^GBIC(-[0-9A-HJKMNP-TV-Z]{4}){5}$")


def month(offset: int) -> str:
    """The IST month `offset` months from this one, as YYYY-MM."""
    index = HERE.year * 12 + HERE.month - 1 + offset
    return f"{index // 12}-{index % 12 + 1:02d}"


def worked(offset: int, day: int = 10) -> datetime:
    """Noon IST on the `day`th of the month `offset` months from this one."""
    year, number = (int(part) for part in month(offset).split("-"))
    return datetime(year, number, day, 12, tzinfo=IST).astimezone(UTC)


@pytest.fixture(autouse=True)
async def hubs(db_session: AsyncSession) -> None:
    await seed_hubs(db_session)


@pytest.fixture
def s3() -> FakeS3:
    return FakeS3()


async def user_of(db: AsyncSession, phone: str) -> uuid.UUID:
    return (await db.execute(select(User.id).where(User.phone_e164 == phone))).scalar_one()


async def record(db: AsyncSession, phone: str, kind: str, **fields: object) -> Partner:
    """A partner record of `kind` for the person with this phone, at the seed hub."""
    partner = await partners_service.verify(
        db,
        user_id=await user_of(db, phone),
        kind=kind,
        hub_id=SEED_HUB_ID,
        display_name=f"{kind} test",
    )
    for name, value in fields.items():
        setattr(partner, name, value)
    if partner.status == "suspended":
        partner.suspended_at, partner.suspend_reason_code = NOW, "conduct"
    if partner.status == "left":
        partner.suspend_reason_code = "left"
    await db.flush()
    return partner


async def captain_of(db: AsyncSession) -> Partner:
    """Meena's seeded captain record: she has a household and a phone, but no role until M9's
    appointment is relayed, so only hub staff ask for hers."""
    (found,) = [
        p
        for p in await partners_service.for_user(db, await user_of(db, MEENA))
        if p.kind == "captain"
    ]
    return found


def last_pdf(s3: FakeS3) -> bytes:
    return list(s3.bodies.values())[-1]


def text_of(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def squash(text: str) -> str:
    """Without whitespace: a heading that wraps in its cell extracts as 'Earned: Homekitchen'."""
    return re.sub(r"\s+", "", text)


async def audit_rows(db: AsyncSession) -> list[AuditEntry]:
    found = await db.execute(
        select(AuditEntry)
        .where(AuditEntry.action == "partners.income_certificate_issued")
        .order_by(AuditEntry.id)
    )
    return list(found.scalars())


def after(row: AuditEntry) -> dict[str, Any]:
    assert row.after is not None
    return row.after


async def earn_some(db: AsyncSession, partner: Partner) -> None:
    await earn(db, partner.id, "tiffin_portion", 30, 18_000, worked(0, 3), NOW)
    await earn(db, partner.id, "tiffin_portion", 20, 12_000, worked(-2), NOW)


# --- the months ---------------------------------------------------------------------------------


async def test_the_months_default_to_the_six_ending_now_and_are_validated(
    clients: ClientFactory, google: FakeGoogle, s3: FakeS3, db_session: AsyncSession
) -> None:
    await record(db_session, LATA, "kitchen")
    api = clients(storage=store_for(s3))
    auth, _ = await as_user(api, google, LATA)

    async def ask(query: str) -> httpx2.Response:
        return await api.get(f"{URL}{query}", headers=auth)

    both = (await ask("")).json()
    only_to = (await ask(f"?to={month(-2)}")).json()
    only_from = (await ask(f"?from={month(-3)}")).json()
    twelve = await ask(f"?from={month(-11)}&to={month(0)}")
    assert (both["from"], both["to"]) == (month(-5), month(0))
    assert (only_to["from"], only_to["to"]) == (month(-7), month(-2))
    assert (only_from["from"], only_from["to"]) == (month(-3), month(0))
    assert twelve.status_code == 200

    for query in (
        f"?from={month(-12)}&to={month(0)}",  # 13 months
        f"?to={month(1)}",  # after the current month
        f"?from={month(-1)}&to={month(-3)}",  # from after to
        "?from=2026-13",
        "?from=26-05",
        "?to=may",
        "?from=2026-5",
        "?from=",
    ):
        bad = await ask(query)
        assert bad.status_code == 422, query
        assert bad.json()["code"] == "partners.certificate_period_invalid"
        assert bad.json()["max_months"] == 12


# --- the figures and the PDF's text --------------------------------------------------------------


async def test_the_pdf_has_the_name_kinds_hubs_figures_and_the_reference(
    clients: ClientFactory,
    google: FakeGoogle,
    s3: FakeS3,
    db_session: AsyncSession,
) -> None:
    captain = await record(db_session, LATA, "captain")
    kitchen = await record(db_session, LATA, "kitchen")
    await earn(db_session, kitchen.id, "tiffin_portion", 1_150, 69_000_00, worked(-1), NOW)
    await earn(db_session, kitchen.id, "tiffin_portion", 30, 1_800_00, worked(0, 2), NOW)
    await earn(db_session, captain.id, "bag_delivered", 30, 180_00, worked(0, 3), NOW)
    await settle(db_session, kitchen.id, 60_000_00, NOW - timedelta(minutes=1))
    hub = await hub_ops_api.get_hub(db_session, SEED_HUB_ID)
    assert hub is not None
    api = clients(storage=store_for(s3))
    auth, _ = await as_user(api, google, LATA)

    answer = await api.get(f"{URL}?from={month(-1)}&to={month(0)}", headers=auth)

    assert answer.status_code == 200 and answer.headers["cache-control"] == "no-store"
    text = flat(text_of(last_pdf(s3)))
    assert "Lata Shinde" in text and answer.json()["reference"] in text
    assert f"Home kitchen partner, {hub.name} hub, since" in text
    assert f"Society Captain, {hub.name} hub, since" in text
    assert "Earned:Homekitchenpartner" in squash(text) and "Earned:SocietyCaptain" in squash(text)
    # Last month: ₹69,000.00 earned; this month ₹1,800 + ₹180 earned and ₹60,000 paid out.
    assert f"{certificate.month_name(month(-1))} ₹69,000.00 ₹0.00 ₹69,000.00 ₹0.00" in text
    assert f"{certificate.month_name(month(0))} ₹1,800.00 ₹180.00 ₹1,980.00 ₹60,000.00" in text
    assert "Total ₹70,800.00 ₹180.00 ₹70,980.00 ₹60,000.00" in text
    assert "1,180 tiffin portions cooked" in text and "30 bags delivered" in text
    assert "Earned but not yet paid out on" in text and "₹10,980.00" in text  # owed
    assert "PROTOTYPE: payouts are simulated and no money was paid." in text
    assert certificate.FOOTER in text and "Page 1 of 1" in text
    assert certificate.parse_period(None, None, NOW)[1] == month(0)


async def test_a_kind_with_no_earnings_has_a_column_of_zeros_and_an_empty_range_is_200(
    clients: ClientFactory, google: FakeGoogle, s3: FakeS3, db_session: AsyncSession
) -> None:
    await record(db_session, LATA, "kitchen")
    await record(db_session, LATA, "rider")
    api = clients(storage=store_for(s3))
    auth, _ = await as_user(api, google, LATA)

    answer = await api.get(URL, headers=auth)

    assert answer.status_code == 200
    text = flat(text_of(last_pdf(s3)))
    assert "Earned:Homekitchenpartner" in squash(text) and "Earned:Deliveryrider" in squash(text)
    assert f"{certificate.month_name(month(0))} ₹0.00 ₹0.00 ₹0.00 ₹0.00" in text
    assert "No earnings were recorded in this period." in text
    assert "0 tiffin portions cooked" in text and "0 orders picked up for delivery" in text


async def test_a_devanagari_name_renders_and_the_banner_goes_with_simulated_payouts(
    clients: ClientFactory, google: FakeGoogle, s3: FakeS3, db_session: AsyncSession
) -> None:
    kitchen = await record(db_session, LATA, "kitchen")
    await db_session.execute(update(User).where(User.phone_e164 == LATA).values(name="आशा पाटील"))
    api = clients(storage=store_for(s3))
    auth, _ = await as_user(api, google, LATA)

    await api.get(URL, headers=auth)

    pdf = last_pdf(s3)
    assert "आशा पाटील" in text_of(pdf)
    facts = await certificate.collect(db_session, [kitchen], month(0), month(0), NOW)
    args = {"reference": "GBIC-AAAA", "issued_at": NOW, "issuer": "Issuer"}
    assert "PROTOTYPE" in text_of(certificate.render(facts, simulated=True, **args))
    assert "PROTOTYPE" not in text_of(certificate.render(facts, simulated=False, **args))


async def test_the_issuer_is_the_setting_and_the_total_matches_the_ledger_statement(
    clients: ClientFactory, google: FakeGoogle, s3: FakeS3, db_session: AsyncSession
) -> None:
    kitchen = await record(db_session, LATA, "kitchen")
    await earn_some(db_session, kitchen)
    await settle(db_session, kitchen.id, 10_000, NOW - timedelta(minutes=1))
    api = clients(storage=store_for(s3), partners_certificate_issuer="Test Issuer Pvt")
    auth, _ = await as_user(api, google, LATA)

    await api.get(f"{URL}?to={month(0)}", headers=auth)
    statement = await api.get(f"/v1/statements?period={month(0)}&account=earnings", headers=auth)

    assert "Test Issuer Pvt" in text_of(last_pdf(s3))
    (row,) = await audit_rows(db_session)
    assert after(row)["owed_paise"] == statement.json()["closing_paise"] == 20_000
    assert (after(row)["earned_paise"], after(row)["paid_out_paise"]) == (30_000, 10_000)


# --- what is never on it -------------------------------------------------------------------------


async def test_no_phone_address_kyc_bank_fssai_hold_or_id_is_in_any_answer(
    clients: ClientFactory,
    google: FakeGoogle,
    s3: FakeS3,
    db_session: AsyncSession,
    capsys: pytest.CaptureFixture[str],
) -> None:
    owner = await user_of(db_session, MEENA)
    kitchen = await make_partner(
        db_session,
        kind="kitchen",
        status="suspended",
        hub_id=SEED_HUB_ID,
        user_id=owner,
        name="Meena kitchen",
        bank_number="918273645501",
    )
    for document in await _documents(db_session, kitchen):
        document.number_masked = document.number_masked or "XXXXXX7788"
        document.fssai_reg_no = "11223344556677" if document.doc_type == "fssai" else None
    await earn(db_session, kitchen.id, "tiffin_portion", 5, 300_00, worked(0, 2), NOW)
    await db_session.flush()
    db_session.add(
        Payout(
            partner_id=kitchen.id,
            hub_id=SEED_HUB_ID,
            week="2026-W40",
            period_from=date(2026, 9, 28),
            period_to=date(2026, 10, 4),
            amount_paise=30_000,
            status="held",
            hold_reason="suspended",
            provider="simulated",
        )
    )
    captain = await captain_of(db_session)
    api = clients(storage=store_for(s3))
    auth, _ = await as_user(api, google, VIKRAM)

    answer = await api.get(f"/v1/partners/{captain.id}/earnings/certificate", headers=auth)

    assert answer.status_code == 200
    secrets = ["9800001004", "B-402", "Sunrise", "918273645501", "XXXXXXXX5501", "XXXXXX7788"]
    secrets += ["11223344556677", "suspended", "kyc_incomplete", "no_payout_account", "SBIN0001234"]
    ids = [str(owner), str(kitchen.id), str(captain.id)]
    # The log is JSON lines on stdout (created with the app, so capsys sees it, not caplog).
    lines = [json.loads(n) for n in capsys.readouterr().out.splitlines() if n.startswith("{")]
    own_logs = [n for n in lines if n.get("logger") == "gharbasket.partners.certificate"]
    assert [n["event"] for n in own_logs] == ["income_certificate_issued"]
    shown = {
        "pdf": flat(text_of(last_pdf(s3))),
        "json": {k: v for k, v in answer.json().items() if k != "url"},
        "audit": [(r.before, r.entity_id) for r in await audit_rows(db_session)],
        "logs": lines,
    }
    for place, content in shown.items():
        for secret in secrets:
            assert secret not in str(content), (secret, place)
    # The request line of the staff path names the partner in its URL; our own log never does.
    shown["logs"] = own_logs
    for place in ("pdf", "json", "logs"):
        for found_id in ids:
            assert found_id not in str(shown[place]), (found_id, place)


async def _documents(db: AsyncSession, partner: Partner) -> list:
    found = await db.execute(
        select(KycDocument).where(KycDocument.application_id == partner.application_id)
    )
    return list(found.scalars())


# --- the object, the link and the audit row -----------------------------------------------------


async def test_the_pdf_is_a_one_day_object_under_the_persons_id_with_a_5_minute_link(
    clients: ClientFactory, google: FakeGoogle, s3: FakeS3, db_session: AsyncSession
) -> None:
    await earn_some(db_session, await record(db_session, LATA, "kitchen"))
    api = clients(storage=store_for(s3))
    auth, me = await as_user(api, google, LATA)
    before = datetime.now(UTC)

    answer = await api.get(f"{URL}?from={month(-3)}&to={month(0)}", headers=auth)

    body = answer.json()
    (upload,) = (await db_session.execute(select(Upload))).scalars().all()
    assert upload.purpose == "income_certificate" and upload.claimed_by == "partners"
    assert upload.key.startswith(f"income_certificate/{me}/") and upload.key.endswith(".pdf")
    assert upload.content_type == "application/pdf"
    assert upload.delete_after is not None
    assert timedelta(hours=23) < upload.delete_after - before < timedelta(hours=25)
    (put,) = s3.puts
    assert put["Key"] == upload.key and put["ContentType"] == "application/pdf"
    filename = f"gharbasket-income-certificate-{month(-3)}-to-{month(0)}.pdf"
    assert body["filename"] == filename
    assert put["ContentDisposition"] == f'attachment; filename="{filename}"'
    assert upload.key in body["url"].replace("%2F", "/") and "X-Amz-Expires=300" in body["url"]
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(minutes=4, seconds=50) < expires - before < timedelta(minutes=5, seconds=10)
    stored = s3.bodies[upload.key]
    assert stored.startswith(b"%PDF") and upload.size_bytes == len(stored)


async def test_without_storage_it_is_503_and_nothing_is_recorded(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients(storage=None)
    auth, _ = await as_user(api, google, LATA)

    answer = await api.get(URL, headers=auth)

    assert answer.status_code == 503 and answer.json()["code"] == "platform.storage_unavailable"
    assert not (await db_session.execute(select(Upload))).scalars().all()
    assert not await audit_rows(db_session)


async def test_every_issue_writes_one_audit_row_with_the_totals_and_the_pdfs_hash(
    clients: ClientFactory, google: FakeGoogle, s3: FakeS3, db_session: AsyncSession
) -> None:
    kitchen = await record(db_session, LATA, "kitchen")
    await earn_some(db_session, kitchen)
    api = clients(storage=store_for(s3))
    auth, me = await as_user(api, google, LATA)

    first = (await api.get(f"{URL}?from={month(-2)}&to={month(0)}", headers=auth)).json()
    second = (await api.get(f"{URL}?from={month(-2)}&to={month(0)}", headers=auth)).json()

    assert first["reference"] != second["reference"]
    assert REFERENCE.match(first["reference"])
    one, two = await audit_rows(db_session)
    assert (one.entity_id, two.entity_id) == (first["reference"], second["reference"])
    assert one.actor_user_id == me and one.entity_type == "income_certificate"
    assert after(one)["user_id"] == str(me) and after(one)["partner_ids"] == [str(kitchen.id)]
    assert (after(one)["from"], after(one)["to"]) == (month(-2), month(0))
    assert (after(one)["earned_paise"], after(one)["paid_out_paise"], after(one)["owed_paise"]) == (
        30_000,
        0,
        30_000,
    )
    stored = s3.bodies[after(one)["key"]]
    assert after(one)["sha256"] == hashlib.sha256(stored).hexdigest()
    assert "Lata" not in str(one.after)


# --- limits and roles ----------------------------------------------------------------------------


async def test_the_11th_partner_call_and_the_61st_staff_call_in_an_hour_are_429(
    clients: ClientFactory,
    google: FakeGoogle,
    s3: FakeS3,
    db_session: AsyncSession,
    valkey: Redis,  # noqa: F811
) -> None:
    await record(db_session, LATA, "kitchen")
    api = clients(storage=store_for(s3), valkey=valkey)
    meena, _ = await as_user(api, google, LATA)
    for _ in range(10):
        assert (await api.get(URL, headers=meena)).status_code == 200
    limited = await api.get(URL, headers=meena)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert limited.json()["code"] == "partners.certificate_rate_limited"

    vikram, staff = await as_user(api, google, VIKRAM)
    for _ in range(60):  # the limiter's own count, so the test does not render 60 PDFs
        await rate_limit.enforce(
            valkey, f"partners.certificate:staff:{staff}", limit=60, window_s=3600
        )
    captain = await captain_of(db_session)
    over = await api.get(f"/v1/partners/{captain.id}/earnings/certificate", headers=vikram)
    assert over.status_code == 429
    assert over.json()["code"] == "partners.certificate_rate_limited"
    assert "retry-after" in over.headers


async def test_who_can_get_a_certificate(
    clients: ClientFactory, google: FakeGoogle, s3: FakeS3, db_session: AsyncSession
) -> None:
    api = clients(storage=store_for(s3))
    captain = await captain_of(db_session)
    path = f"/v1/partners/{captain.id}/earnings/certificate"
    priya, _ = await as_user(api, google, PRIYA)
    sanjay, _ = await as_user(api, google, SANJAY)
    elsewhere = await other_hub_manager(api, google, db_session)

    assert (await api.get(URL)).status_code == 401
    assert (await api.get(URL, headers=priya)).status_code == 403  # a customer, no partner role
    assert (await api.get(path, headers=priya)).status_code == 403
    assert (await api.get(path, headers=sanjay)).status_code == 403  # a franchise owner
    assert (await api.get(path, headers=elsewhere)).status_code == 403  # another hub
    assert not await audit_rows(db_session)


async def test_hub_staff_issue_for_her_even_when_suspended_and_an_admin_sees_every_record(
    clients: ClientFactory, google: FakeGoogle, s3: FakeS3, db_session: AsyncSession
) -> None:
    api = clients(storage=store_for(s3))
    captain = await captain_of(db_session)
    rider = await record(db_session, MEENA, "rider", status="suspended")
    other_hub = uuid.uuid4()
    packer = await record(db_session, MEENA, "packer", hub_id=other_hub)
    left = await record(db_session, MEENA, "kitchen", status="left")
    vikram, staff = await as_user(api, google, VIKRAM)
    neha, _ = await as_user(api, google, NEHA)

    mine = await api.get(f"/v1/partners/{rider.id}/earnings/certificate", headers=vikram)
    everything = await api.get(f"/v1/partners/{captain.id}/earnings/certificate", headers=neha)

    assert mine.status_code == 200 and everything.status_code == 200
    first, second = await audit_rows(db_session)
    # Her hub's manager: the captain and the suspended rider, not the packer of another hub, and
    # never the `left` kitchen. An admin: both hubs' records.
    assert first.actor_user_id == staff
    assert sorted(after(first)["partner_ids"]) == sorted([str(captain.id), str(rider.id)])
    assert sorted(after(second)["partner_ids"]) == sorted(
        [str(captain.id), str(rider.id), str(packer.id)]
    )
    assert str(left.id) not in after(second)["partner_ids"]
    text = flat(text_of(s3.bodies[after(first)["key"]]))
    assert "Delivery rider" in text and "Packer" not in text and "Home kitchen" not in text
    assert "Suspended" not in text and "suspended" not in text


async def test_a_left_or_unknown_record_is_404(
    clients: ClientFactory, google: FakeGoogle, s3: FakeS3, db_session: AsyncSession
) -> None:
    api = clients(storage=store_for(s3))
    gone = await record(db_session, LATA, "kitchen", status="left")
    vikram, _ = await as_user(api, google, VIKRAM)

    for target in (gone.id, uuid.uuid4()):
        answer = await api.get(f"/v1/partners/{target}/earnings/certificate", headers=vikram)
        assert answer.status_code == 404
        assert answer.json()["code"] == "partners.partner_not_found"
    assert not await audit_rows(db_session)
