"""The MS-A demo's ledger report (tools/demo_ledger.py): a customer's entries with every leg."""

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import wallet
from gharbasket.modules.identity.models import User
from gharbasket.tools import demo_ledger

from .paid import paid_topup

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def test_the_report_shows_both_legs_of_a_top_up_and_a_box_and_a_balanced_ledger(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    user = User(phone_e164="+919800077001")
    db_session.add(user)
    await db_session.flush()
    await paid_topup(db_session, user.id, 50_000)
    await wallet.debit_for_order(db_session, user.id, uuid.uuid4(), 42_150, date(2026, 12, 16))

    code = await demo_ledger.report(await db_session.connection(), "+919800077001", 3)

    out = capsys.readouterr().out
    assert code == 0
    assert "debit  razorpay_clearing" in out and "credit wallet (customer)" in out
    assert "debit  wallet (customer)" in out and "credit unearned_revenue" in out
    assert "₹421.50" in out
    assert "Wallet balance: ₹78.50" in out
    assert "entries that do not sum to zero: 0" in out and "  balanced" in out


async def test_an_unknown_phone_says_so(
    db_session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await demo_ledger.report(await db_session.connection(), "+919800077999", 3)
    assert code == 1
    assert "No user with phone +919800077999" in capsys.readouterr().out


def test_rupees_formats_paise() -> None:
    assert demo_ledger.rupees(4_21_50) == "₹421.50"
    assert demo_ledger.rupees(1_23_456_07) == "₹1,23,456.07"
    assert demo_ledger.rupees(-5) == "-₹0.05"
    assert demo_ledger.rupees(99_999_99) == "₹99,999.99"
    assert demo_ledger.rupees(10_00_00_000_00) == "₹10,00,00,000.00"
