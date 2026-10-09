"""Quiet hours (spec §3.1): clock times in IST, start included and end not, and a window may
cross midnight (22:00 to 07:00 is the default). A message that falls inside one waits until
the window ends.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def held_until(now: datetime, start: time, end: time) -> datetime | None:
    """When the quiet window `now` falls in ends (in UTC), or None when `now` is outside it."""
    local = now.astimezone(IST)
    clock, day = local.time(), local.date()
    if start < end:  # within one day, e.g. 13:00 to 15:00
        inside = start <= clock < end
    else:  # across midnight: the evening part ends tomorrow, the morning part today
        inside = clock >= start or clock < end
        if clock >= start:
            day += timedelta(days=1)
    if not inside:
        return None
    return datetime.combine(day, end, IST).astimezone(UTC)
