"""Quiet hours (spec §3.1): IST clock times, start included and end not, across midnight."""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from gharbasket.modules.notifications.quiet import held_until


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment in October 2026 given in IST (UTC+5:30), as UTC."""
    total = hour * 60 + minute - 330
    d, rest = divmod(total, 24 * 60)
    return datetime(2026, 10, day + d, rest // 60, rest % 60, tzinfo=UTC)


NIGHT = (time(22), time(7))
AFTERNOON = (time(13), time(15, 30))


@pytest.mark.parametrize(
    ("now", "window", "until"),
    [
        (ist(12, 21, 59), NIGHT, None),  # just before it starts
        (ist(12, 22), NIGHT, ist(13, 7)),  # the start is inside
        (ist(12, 23, 45), NIGHT, ist(13, 7)),  # evening: ends tomorrow morning
        (ist(13, 0), NIGHT, ist(13, 7)),  # midnight
        (ist(13, 6, 59), NIGHT, ist(13, 7)),  # morning: ends today
        (ist(13, 7), NIGHT, None),  # the end is outside
        (ist(13, 12), NIGHT, None),
        (ist(13, 12, 59), AFTERNOON, None),
        (ist(13, 13), AFTERNOON, ist(13, 15, 30)),  # a window within one day
        (ist(13, 15, 29), AFTERNOON, ist(13, 15, 30)),
        (ist(13, 15, 30), AFTERNOON, None),
        (ist(13, 23), (time(23, 45), time(0, 15)), None),
        (ist(13, 23, 50), (time(23, 45), time(0, 15)), ist(14, 0, 15)),
        (ist(14, 0, 5), (time(23, 45), time(0, 15)), ist(14, 0, 15)),
    ],
)
def test_held_until_the_end_of_the_window_now_is_in(
    now: datetime, window: tuple[time, time], until: datetime | None
) -> None:
    assert held_until(now, *window) == until


def test_the_answer_is_in_utc_whatever_zone_now_is_given_in() -> None:
    local = datetime(2026, 10, 12, 23, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    until = held_until(local, *NIGHT)
    assert until is not None and until.tzinfo == UTC
    assert until == datetime(2026, 10, 13, 1, 30, tzinfo=UTC)
