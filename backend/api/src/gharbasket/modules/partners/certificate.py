"""The income certificate (spec §3.7): the months it covers, the facts it prints and the PDF.

The money comes from M5's `billing.api.partner_months`; M8 adds who she is (her name from M1, her
records, the hubs' names from M6) and renders the page with M0's `platform.pdf`. Nothing here
reads a phone, an address, a KYC number or a bank account: they are never on the certificate.
"""

import calendar
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.partners.models import KINDS, Partner
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.pdf import Bold, PdfDocument, indian, rupees

IST = ZoneInfo("Asia/Kolkata")
MAX_MONTHS = 12
DEFAULT_MONTHS = 6
MONTH = re.compile(r"^[12]\d{3}-(0[1-9]|1[0-2])$")
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
MONTH_NAMES = [m for m in calendar.month_name][1:]
LABELS = {
    "kitchen": "Home kitchen partner",
    "captain": "Society Captain",
    "rider": "Delivery rider",
    "packer": "Packer",
}
# What a kind's units are, singular and plural ("Work done in the period").
WORK = {
    "kitchen": ("tiffin portion cooked", "tiffin portions cooked"),
    "captain": ("bag delivered", "bags delivered"),
    "rider": ("order picked up for delivery", "orders picked up for delivery"),
    "packer": ("order packed", "orders packed"),
}
SIMULATED = "PROTOTYPE: payouts are simulated and no money was paid. Not valid as proof of income."
FOOTER = (
    "Computer-generated document; no signature is needed. "
    "To verify it, quote the reference to GharBasket."
)


def period_invalid() -> ProblemError:
    return ProblemError(
        422,
        "partners.certificate_period_invalid",
        "Choose months as YYYY-MM: the first no later than the last, the last no later than "
        f"this month, at most {MAX_MONTHS} months.",
        extra={"max_months": MAX_MONTHS},
    )


def _index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:]) - 1


def _label(index: int) -> str:
    return f"{index // 12}-{index % 12 + 1:02d}"


def parse_period(first: str | None, last: str | None, now: datetime) -> tuple[str, str]:
    """The months `from` and `to` as `YYYY-MM` (IST), with the defaults of §3.7: neither, or only
    `to`, is six months ending with it (the current month); only `from` runs to the current one."""
    current = now.astimezone(IST).strftime("%Y-%m")
    if any(given is not None and not MONTH.match(given) for given in (first, last)):
        raise period_invalid()
    end = last or current
    start = first or _label(_index(end) - (DEFAULT_MONTHS - 1))
    if (
        not MONTH.match(start)
        or not start <= end <= current
        or _index(end) - _index(start) >= (MAX_MONTHS)
    ):
        raise period_invalid()
    return start, end


def new_reference() -> str:
    """`GBIC-` and 20 Crockford base32 characters (100 random bits) in groups of four; not derived
    from any id."""
    bits = secrets.randbits(100)
    chars = "".join(CROCKFORD[(bits >> (5 * n)) & 31] for n in range(20))
    return "GBIC-" + "-".join(chars[n : n + 4] for n in range(0, 20, 4))


@dataclass(frozen=True)
class Line:
    kind: str
    hub: str | None
    since: str  # "May 2026"


@dataclass(frozen=True)
class Facts:
    name: str
    lines: list[Line]
    first: str
    last: str
    months: billing_api.PartnerMonths
    earned: dict[tuple[str, str], int]  # (month, kind) -> paise
    units: dict[str, int]  # kind -> units of work in the whole period

    @property
    def kinds(self) -> list[str]:
        return [line.kind for line in self.lines]

    @property
    def earned_paise(self) -> int:
        return sum(m.earned_paise for m in self.months.months)

    @property
    def paid_out_paise(self) -> int:
        return sum(m.paid_out_paise for m in self.months.months)


def month_name(month: str) -> str:
    return f"{MONTH_NAMES[int(month[5:]) - 1]} {month[:4]}"


def day_name(day: datetime) -> str:
    return f"{day.day} {MONTH_NAMES[day.month - 1][:3]} {day.year}"


async def collect(
    session: AsyncSession, records: list[Partner], first: str, last: str, now: datetime
) -> Facts:
    """The certificate's facts for the records of one person, in the order of `KINDS`."""
    records = sorted(records, key=lambda p: KINDS.index(p.kind))
    kind_of = {p.id: p.kind for p in records}
    months = await billing_api.partner_months(session, list(kind_of), first, last, until=now)
    earned: dict[tuple[str, str], int] = {}
    units: dict[str, int] = {}
    for found in months.months:
        for line in found.by_basis:
            kind = kind_of[line.partner_id]
            key = (found.month, kind)
            earned[key] = earned.get(key, 0) + line.amount_paise
            units[kind] = units.get(kind, 0) + line.units
    user_id = records[0].user_id
    names = await identity_api.user_names(session, [user_id])
    oldest = min(records, key=lambda p: p.created_at)
    hubs = {}
    for hub_id in {p.hub_id for p in records}:
        hub = await hub_ops_api.get_hub(session, hub_id)
        hubs[hub_id] = hub.name if hub else None
    return Facts(
        name=names.get(user_id) or oldest.display_name,
        lines=[
            Line(
                p.kind, hubs[p.hub_id], month_name(p.verified_at.astimezone(IST).strftime("%Y-%m"))
            )
            for p in records
        ],
        first=first,
        last=last,
        months=months,
        earned=earned,
        units=units,
    )


def render(
    facts: Facts, *, reference: str, issued_at: datetime, issuer: str, simulated: bool
) -> bytes:
    """The certificate as a PDF (CPU work: call it in `asyncio.to_thread`)."""
    at = issued_at.astimezone(IST)
    doc = PdfDocument(
        title="GharBasket income certificate",
        author="GharBasket",
        created=issued_at,
        footer=FOOTER,
        banner=SIMULATED if simulated else None,
    )
    doc.heading("GharBasket", size=20)
    doc.heading("Income certificate", size=15)
    doc.paragraph("Reference: ", Bold(reference))
    doc.paragraph(f"Issued on {day_name(at)} at {at:%H:%M} IST")
    doc.paragraph(issuer, size=9)
    doc.paragraph("This is to certify that ", Bold(facts.name), " has worked with GharBasket as:")
    for line in facts.lines:
        hub = f", {line.hub} hub" if line.hub else ""
        doc.paragraph(f"  •  {LABELS[line.kind]}{hub}, since {line.since}")
    # The period: from the 1st of the first month to the end of the last, or to today.
    last_year, last_month = int(facts.last[:4]), int(facts.last[5:])
    to_date = facts.last == at.strftime("%Y-%m")
    end_day = at.day if to_date else calendar.monthrange(last_year, last_month)[1]
    end = f"{end_day} {MONTH_NAMES[last_month - 1][:3]} {last_year}"
    doc.heading("Period", size=12)
    doc.paragraph(
        f"1 {MONTH_NAMES[int(facts.first[5:]) - 1][:3]} {facts.first[:4]} to {end}"
        + (" (to date)" if to_date else "")
    )
    doc.heading("Earnings by month", size=12)
    kinds = facts.kinds
    rows = [
        [
            month_name(m.month),
            *[rupees(facts.earned.get((m.month, k), 0)) for k in kinds],
            rupees(m.earned_paise),
            rupees(m.paid_out_paise),
        ]
        for m in facts.months.months
    ]
    doc.table(
        ["Month", *[f"Earned: {LABELS[k]}" for k in kinds], "Total earned", "Paid out"],
        rows,
        totals=[
            "Total",
            *[rupees(sum(v for (_, kind), v in facts.earned.items() if kind == k)) for k in kinds],
            rupees(facts.earned_paise),
            rupees(facts.paid_out_paise),
        ],
        widths=[1.3, *[1.5] * (len(kinds) + 2)],
        align=["LEFT", *["RIGHT"] * (len(kinds) + 2)],
    )
    if facts.earned_paise == 0:
        doc.paragraph("No earnings were recorded in this period.")
    doc.heading("Work done in the period", size=12)
    for kind in kinds:
        count = facts.units.get(kind, 0)
        doc.paragraph(f"{indian(count)} {WORK[kind][0 if count == 1 else 1]}")
    doc.heading("Earned but not yet paid out", size=12)
    doc.paragraph(
        f"Earned but not yet paid out on {end}: ",
        Bold(rupees(facts.months.owed_paise)),
        ". GharBasket pays partners every Monday.",
    )
    doc.heading("Notes", size=12)
    for note in (
        "Earned is the fee for work done in the month, before tax. Paid out is the money sent to "
        "the partner's bank account in the month; a transfer that failed and came back is not "
        "counted.",
        "The amounts are fees before tax. GharBasket deducts no TDS.",
        "The figures come from GharBasket's double-entry ledger.",
    ):
        doc.paragraph(note, size=9)
    return doc.output()


def new_key(user_id: uuid.UUID, file_id: uuid.UUID) -> str:
    return f"income_certificate/{user_id}/{file_id}.pdf"
