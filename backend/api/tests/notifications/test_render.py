"""Rendering templates (spec §3.2): placeholders formatted per language, in IST."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.notifications.kinds import KINDS
from gharbasket.modules.notifications.models import Template
from gharbasket.modules.notifications.render import clock, money, placeholders_of, render


@pytest.mark.parametrize(
    ("paise", "shown"),
    [
        (0, "₹0"),
        (69_000, "₹690"),
        (123_456_789, "₹12,34,567.89"),
        (100_000_000, "₹10,00,000"),
        (-5_050, "-₹50.50"),
    ],
)
def test_money_uses_indian_grouping_and_shows_paise_only_when_there_are_any(
    paise: int, shown: str
) -> None:
    assert money(paise) == shown


def test_clock_reads_in_ist_with_the_part_of_the_day_in_hindi_and_marathi() -> None:
    cutoff = datetime(2026, 10, 12, 15, 30, tzinfo=UTC)  # 21:00 IST
    assert (clock(cutoff, "en"), clock(cutoff, "hi"), clock(cutoff, "mr")) == (
        "9 PM",
        "रात 9 बजे",
        "रात्री 9",
    )
    morning = datetime(2026, 10, 13, 2, 15, tzinfo=UTC)  # 07:45 IST
    assert (clock(morning, "en"), clock(morning, "hi")) == ("7:45 AM", "सुबह 7:45 बजे")
    assert clock(datetime(2026, 10, 12, 18, 30, tzinfo=UTC), "mr") == "रात्री 12"  # 00:00 IST


def test_render_formats_each_placeholder_and_blanks_what_it_does_not_know() -> None:
    kind = KINDS["subscriptions.box_ready"]
    params = {"total": 69_000, "cutoff_day": "2026-10-12T15:30:00+00:00"}
    text = "{total} by {cutoff_day} {cutoff_time}{unknown}"
    assert render(text, kind, params, "en") == "₹690 by Monday "
    assert render(text, kind, params, "hi") == "₹690 by सोमवार "


@pytest.mark.anyio
@pytest.mark.services
async def test_every_kind_has_seeded_copy_in_three_locales_using_only_its_placeholders(
    db_session: AsyncSession,
) -> None:
    rows = (await db_session.execute(select(Template).where(Template.active))).scalars().all()
    seeded = {(t.kind, t.channel, t.locale) for t in rows}
    assert seeded == {
        (kind, "app", locale) for kind in KINDS for locale in ("en-IN", "hi-IN", "mr-IN")
    }
    for template in rows:
        used = placeholders_of(template.title) | placeholders_of(template.body)
        assert used <= set(KINDS[template.kind].placeholders), (template.kind, template.locale)
