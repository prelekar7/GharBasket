"""The refresh windows in IST (M12 §3.2) and the `as_of`/`stale` rule."""

from datetime import UTC, date, datetime, timedelta

import pytest

from gharbasket.modules.analytics import freshness, refresh

TUESDAY = date(2026, 11, 3)


@pytest.mark.parametrize(
    ("at", "today"),
    [
        (datetime(2026, 11, 2, 18, 29, tzinfo=UTC), date(2026, 11, 2)),  # 23:59 IST Monday
        (datetime(2026, 11, 2, 18, 30, tzinfo=UTC), TUESDAY),  # 00:00 IST Tuesday
        (datetime(2026, 11, 2, 21, 10, tzinfo=UTC), TUESDAY),  # the nightly run, 02:40 IST
    ],
)
def test_today_is_the_ist_date(at: datetime, today: date) -> None:
    assert refresh.ist_today(at) == today


def test_the_windows_are_those_of_the_spec() -> None:
    assert refresh.refresh_window(TUESDAY) == (date(2026, 10, 31), date(2026, 11, 4))
    assert refresh.nightly_window(TUESDAY) == (date(2026, 9, 29), TUESDAY)
    # The last 6 ISO weeks: Mondays from 28 Sep to 2 Nov 2026.
    assert refresh.partner_weeks(TUESDAY) == (date(2026, 9, 28), date(2026, 11, 2))
    assert refresh.partner_weeks(date(2026, 11, 8)) == (date(2026, 9, 28), date(2026, 11, 2))
    assert refresh.monday(date(2026, 11, 8)) == date(2026, 11, 2)
    assert refresh.iso_week(date(2026, 11, 2)) == "2026-W45"
    assert refresh.iso_week(date(2026, 12, 28)) == "2026-W53"


def test_chunks_cover_the_range_in_seven_day_spans() -> None:
    spans = refresh.chunks(date(2026, 10, 1), date(2026, 10, 20))
    assert spans == [
        (date(2026, 10, 1), date(2026, 10, 7)),
        (date(2026, 10, 8), date(2026, 10, 14)),
        (date(2026, 10, 15), date(2026, 10, 20)),
    ]
    assert refresh.chunks(TUESDAY, TUESDAY) == [(TUESDAY, TUESDAY)]
    assert refresh.chunks(TUESDAY, TUESDAY - timedelta(days=1)) == []


def test_stale_after_45_minutes_for_daily_views_and_26_hours_for_nightly_ones() -> None:
    now = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)
    daily, nightly = "mv_daily_hub_kpis", "mv_cohorts"
    assert freshness.judge(daily, now - timedelta(minutes=45), now).stale is False
    assert freshness.judge(daily, now - timedelta(minutes=46), now).stale is True
    assert freshness.judge(nightly, now - timedelta(hours=26), now).stale is False
    assert freshness.judge(nightly, now - timedelta(hours=26, minutes=1), now).stale is True
    assert freshness.judge(nightly, None, now) == freshness.Freshness(None, True)
    assert freshness.jobs_refreshing("mv_impact_monthly") == (
        refresh.REFRESH,
        refresh.NIGHTLY,
        refresh.REBUILD,
    )
    assert freshness.jobs_refreshing("mv_weekly_unit_economics") == (
        refresh.NIGHTLY,
        refresh.REBUILD,
    )
    with pytest.raises(ValueError, match="not an analytics view"):
        freshness.jobs_refreshing("orders")
