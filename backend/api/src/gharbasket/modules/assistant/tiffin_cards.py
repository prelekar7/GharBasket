"""`book_tiffin` (M13 §3.1): an intent from the model becomes a card the person confirms, or a short
question; confirming books the tiffin through M4. The model names a date, a meal and a menu
handle; the menu, the portions, the price and the total are the API's. Nothing is booked, and no
money moves, until the person confirms.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.assistant.tools import ToolContext
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language

MEALS = ("lunch", "dinner")
SAY: dict[str, dict[str, str]] = {
    "en": {
        "which_day": "Which day should I book the tiffin for?",
        "which_meal": "Lunch or dinner?",
        "closed": "Tiffins are booked until 21:00 the evening before, up to a week ahead.",
        "none": "There is no tiffin menu for {meal} on {date}.",
        "booked": "You already have a tiffin booked for {meal} on {date}.",
        "which": "Which one: {options}?",
    },
    "hi": {
        "which_day": "किस दिन का टिफ़िन बुक करूँ?",
        "which_meal": "दोपहर का या रात का?",
        "closed": "टिफ़िन एक रात पहले 21:00 बजे तक और एक हफ़्ते आगे तक बुक होते हैं।",
        "none": "{date} को {meal} का कोई टिफ़िन मेन्यू नहीं है।",
        "booked": "{date} को {meal} का टिफ़िन आपने पहले ही बुक किया है।",
        "which": "कौन-सा: {options}?",
    },
    "mr": {
        "which_day": "कोणत्या दिवसाचा डबा बुक करू?",
        "which_meal": "दुपारचा की रात्रीचा?",
        "closed": "डबे आदल्या रात्री 21:00 पर्यंत आणि आठवडाभर पुढपर्यंत बुक होतात.",
        "none": "{date} रोजी {meal} साठी डब्याचा मेन्यू नाही.",
        "booked": "{date} रोजी {meal} साठी डबा तुम्ही आधीच बुक केला आहे.",
        "which": "कोणता: {options}?",
    },
}
MEAL_WORD = {
    "en": {"lunch": "lunch", "dinner": "dinner"},
    "hi": {"lunch": "दोपहर का", "dinner": "रात का"},
    "mr": {"lunch": "दुपारचा", "dinner": "रात्रीचा"},
}
SUMMARY = {
    "en": "Book the {meal} tiffin for {date}: {dishes}, {portions} x ₹{price} = ₹{total}. "
    "Paid at the 21:00 cut-off; free to cancel until then.",
    "hi": "{date}: {meal} टिफ़िन बुक करें: {dishes}, {portions} x ₹{price} = ₹{total}। "
    "भुगतान 21:00 के कट-ऑफ़ पर; तब तक मुफ़्त रद्द।",
    "mr": "{date}: {meal} टिफिन बुक करा: {dishes}, {portions} x ₹{price} = ₹{total}. "
    "पेमेंट 21:00 च्या कट-ऑफला; तोपर्यंत मोफत रद्द.",
}


@dataclass(frozen=True)
class TiffinCard:
    payload: dict[str, Any]
    summary: str


def _rupees(paise: int) -> str:
    return str(paise // 100) if paise % 100 == 0 else f"{paise / 100:.2f}"


def _day(value: Any, today: date) -> date | None:
    """`today`, `tomorrow` or an ISO date; None for anything else."""
    word = str(value or "").strip().lower()
    if word in ("today", "tomorrow"):
        return today + timedelta(days=word == "tomorrow")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", word):
        try:
            return date.fromisoformat(word)
        except ValueError:
            return None
    return None


def _say(lang: Language, key: str, **values: str) -> str:
    return SAY.get(lang, SAY["en"])[key].format(**values)


def _options(choice: orders_api.TiffinChoice) -> str:
    return "; ".join(f"{', '.join(m.dishes)} (₹{_rupees(m.price_paise)})" for m in choice.menus)[
        :300
    ]


async def resolve(
    session: AsyncSession,
    context: ToolContext,
    household_id: uuid.UUID,
    lang: Language,
    today: date,
    intent: dict[str, Any],
) -> TiffinCard | str:
    """One `book_tiffin` intent as a card, or what to ask the person. The window, the clock and the
    household's booked meals are checked against what the API itself sent (and the clock now)."""
    meal = intent.get("meal")
    if meal not in MEALS:
        return _say(lang, "which_meal")
    day = _day(intent.get("date"), today)
    if day is None:
        return _say(lang, "which_day")
    if not today + timedelta(days=1) <= day <= today + timedelta(days=orders_api.TIFFIN_MENU_DAYS):
        return _say(lang, "closed")
    words = f"{day.day} {day:%b}"
    named = {"meal": MEAL_WORD.get(lang, MEAL_WORD["en"])[meal], "date": words}
    choice = next((c for c in context.choices if c.on_date == day and c.meal == meal), None)
    if choice is None:
        return _say(lang, "none", **named)
    if choice.booked:
        return _say(lang, "booked", **named)
    if datetime.now(UTC) >= choice.order_by:
        return _say(lang, "closed")
    handle = str(intent.get("menu") or "").strip().upper()
    found = context.menus.get(handle) if handle else None
    if found is not None and found[0] is choice:
        menu = found[1]
    elif not handle and len(choice.menus) == 1:
        menu = choice.menus[0]
    else:  # no choice made, or a handle that is not this date and meal's: ask, never guess
        return _say(lang, "which", options=_options(choice))
    portions = await orders_api.default_tiffin_portions(session, household_id)
    total = portions * menu.price_paise
    summary = SUMMARY.get(lang, SUMMARY["en"]).format(
        meal=named["meal"],
        date=words,
        dishes=", ".join(menu.dishes)[:50],
        portions=portions,
        price=_rupees(menu.price_paise),
        total=_rupees(total),
    )
    payload = {
        "menu_id": str(menu.id),
        "date": day.isoformat(),
        "meal": meal,
        "portions": portions,
        "unit_price_paise": menu.price_paise,
        "total_paise": total,
        "dishes": list(menu.dishes),
    }
    return TiffinCard(payload, summary[:200])


async def book(
    session: AsyncSession, user_id: uuid.UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Confirm: M4 books it with its own rules (one per date and meal, closed after `order_by`).
    If the total is no longer what the card showed, nothing is kept."""
    order = await orders_api.book_tiffin(
        session, user_id, uuid.UUID(str(payload["menu_id"])), int(payload["portions"])
    )
    if order.total_paise != int(payload["total_paise"]):
        raise ProblemError(
            409, "assistant.price_changed", "The price changed since this card; ask again."
        )
    return {
        "order_id": str(order.id),
        "status": order.status,
        "delivery_date": order.delivery_date.isoformat(),
        "meal": payload["meal"],
        "portions": order.portions,
        "total_paise": order.total_paise,
    }
