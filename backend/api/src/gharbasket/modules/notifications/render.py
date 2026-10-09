"""Rendering a template (spec §3.2): plain text with `{placeholders}`, no template engine.

A template may use only its kind's placeholders (`kinds.py`); each raw value is formatted for the
reader's language here, so one stored message reads right in English, Hindi and Marathi.
"""

import string
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from gharbasket.modules.notifications.kinds import Kind, PlaceholderType
from gharbasket.platform.locale import Language

IST = ZoneInfo("Asia/Kolkata")

WEEKDAYS: dict[Language, tuple[str, ...]] = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "hi": ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"),
    "mr": ("सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"),
}
# The part of the day said before a clock time in Hindi and Marathi, by the hour it starts at.
DAY_PARTS: dict[Language, tuple[tuple[int, str], ...]] = {
    "hi": ((0, "रात"), (5, "सुबह"), (12, "दोपहर"), (16, "शाम"), (19, "रात")),
    "mr": ((0, "रात्री"), (5, "सकाळी"), (12, "दुपारी"), (16, "संध्याकाळी"), (19, "रात्री")),
}


def placeholders_of(text: str) -> set[str]:
    """The `{names}` a template uses; raises ValueError on a malformed brace."""
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def money(paise: int) -> str:
    """₹ with Indian digit grouping (₹1,23,456), and paise only when there are any."""
    rupees, rest = divmod(abs(paise), 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = [head[max(0, i - 2) : i] for i in range(len(head), 0, -2)][::-1]
        digits = ",".join([*groups, tail])
    sign = "-" if paise < 0 else ""
    return f"{sign}₹{digits}" + (f".{rest:02d}" if rest else "")


def clock(moment: datetime, lang: Language) -> str:
    """'9 PM' / 'रात 9 बजे' / 'रात्री 9', in IST; minutes only when not on the hour. Marathi
    leaves out वाजता so templates can write '{time} पर्यंत'."""
    local = moment.astimezone(IST)
    hour12 = local.hour % 12 or 12
    shown = f"{hour12}:{local.minute:02d}" if local.minute else str(hour12)
    if lang == "en":
        return f"{shown} {'AM' if local.hour < 12 else 'PM'}"
    part = next(name for start, name in reversed(DAY_PARTS[lang]) if local.hour >= start)
    return f"{part} {shown} बजे" if lang == "hi" else f"{part} {shown}"


def format_value(kind: PlaceholderType, raw: Any, lang: Language) -> str:
    """One raw value for the reader; ValueError or TypeError when it is not that type."""
    match kind:
        case "money":
            return money(int(raw))
        case "weekday":  # a date, or a timestamp read in IST
            text = str(raw)
            day = (
                date.fromisoformat(text)
                if len(text) == 10
                else datetime.fromisoformat(text).astimezone(IST).date()
            )
            return WEEKDAYS[lang][day.weekday()]
        case "time":
            return clock(datetime.fromisoformat(str(raw)), lang)


def render(text: str, kind: Kind, params: dict[str, Any], lang: Language) -> str:
    """The template with each placeholder formatted. Templates are checked when saved, so an
    unknown placeholder or a missing value only shows as empty rather than failing a read."""
    values = {
        name: format_value(kind.placeholders[name], params[name], lang)
        if name in kind.placeholders and name in params
        else ""
        for name in placeholders_of(text)
    }
    return text.format_map(values)
