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


MEALS: dict[Language, dict[str, str]] = {
    "en": {"lunch": "lunch", "dinner": "dinner"},
    "hi": {"lunch": "दोपहर का खाना", "dinner": "रात का खाना"},
    "mr": {"lunch": "दुपारचे जेवण", "dinner": "रात्रीचे जेवण"},
}


# M5's reconciliation kinds (M5 §3.4), short: the console's Reconciliation page says the rest.
RECON_KINDS: dict[Language, dict[str, str]] = {
    "en": {
        "captured_not_credited": "paid, wallet not credited",
        "credited_not_captured": "credited, not paid",
        "amount_mismatch": "amounts differ",
        "unknown_payment": "unknown payment",
        "refund_status_mismatch": "refund status differs",
        "refund_unknown": "unknown refund",
        "refund_stuck": "refund stuck",
    },
    "hi": {
        "captured_not_credited": "भुगतान हुआ, वॉलेट में नहीं जुड़ा",
        "credited_not_captured": "वॉलेट में जुड़ा, भुगतान नहीं",
        "amount_mismatch": "रकम अलग",
        "unknown_payment": "अनजान भुगतान",
        "refund_status_mismatch": "रिफ़ंड की स्थिति अलग",
        "refund_unknown": "अनजान रिफ़ंड",
        "refund_stuck": "रिफ़ंड अटका",
    },
    "mr": {
        "captured_not_credited": "पेमेंट झाले, वॉलेटमध्ये जमा नाही",
        "credited_not_captured": "वॉलेटमध्ये जमा, पेमेंट नाही",
        "amount_mismatch": "रक्कम वेगळी",
        "unknown_payment": "अनोळखी पेमेंट",
        "refund_status_mismatch": "रिफंडची स्थिती वेगळी",
        "refund_unknown": "अनोळखी रिफंड",
        "refund_stuck": "रिफंड अडकला",
    },
}


def recon_kinds(raw: Any, lang: Language) -> str:
    """A list of reconciliation kinds, each in the reader's words. A kind M5 adds later shows as
    its code rather than losing the admins' message; anything but a list of strings raises."""
    items: list[Any] = raw if isinstance(raw, list) else []  # pyright: ignore[reportUnknownVariableType]
    codes = [k for k in items if isinstance(k, str)]
    if not isinstance(raw, list) or len(codes) != len(items):
        raise TypeError("expected a list of reconciliation kinds")
    names = RECON_KINDS[lang]
    return ", ".join(names.get(k, k) for k in codes)


def number(count: int) -> str:
    """A whole number with Indian digit grouping (12,34,567)."""
    return money(count * 100).replace("₹", "")


def clock_of_day(text: str, lang: Language) -> str:
    """An IST 'HH:MM' shown as `clock` shows a moment: '6:30 AM'."""
    hour, minute = text.split(":")
    return clock(datetime(2000, 1, 1, int(hour), int(minute), tzinfo=IST), lang)


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
        case "clock":  # an IST 'HH:MM', as M7 publishes departures and ETAs
            return clock_of_day(str(raw), lang)
        case "number":
            return number(int(raw))
        case "percent":  # a fraction: 0.2345 becomes 23%
            return f"{round(float(raw) * 100)}%"
        case "recon_kinds":
            return recon_kinds(raw, lang)
        case "text":
            return str(raw)
        case "meal":
            return MEALS[lang][str(raw)]  # KeyError for anything but lunch and dinner


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


def ordered_values(text: str, kind: Kind, params: dict[str, Any], lang: Language) -> list[str]:
    """The template's placeholders formatted, in the order they appear: a WhatsApp template's
    {{1}}, {{2}} … parameters (spec §3.2)."""
    return [
        render(f"{{{name}}}", kind, params, lang)
        for _, name, _, _ in string.Formatter().parse(text)
        if name
    ]
