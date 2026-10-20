"""The personal-data scrub of M13 (spec §3, handbook 13 §13.6): what a person types is cleaned
here, in the API, before it is stored or sent to the ML service (hosted outside India, ADR-0025).

Covered: e-mail addresses; Indian phone numbers (mobile with or without +91 / 0, spaced or
hyphenated, and landlines with an STD code); Aadhaar-like 12-digit numbers in any grouping; PIN
codes; flat, house, plot, room, wing, tower, building and sector numbers and housing-society
names, in English, Hindi and Marathi; and the household's own private terms (the owner's name,
flat and tower, from M1's facade). Each is replaced by a bracketed tag, so the model still knows
something was there. Devanagari digits come back as ASCII digits. Over-scrubbing is the safe
side, but quantities ("2 kilo", "12 eggs", "500 g") and prices ("Rs 500") are left alone.
"""

import re
from collections.abc import Iterable

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 12 digits, any grouping: "2345 6789 0123", "2345-6789-0123", "234567890123", "23 4567 890123".
AADHAAR = re.compile(r"(?<![\d+])\d(?:[ -]?\d){11}(?!\d)")
# Mobiles: +91 98200 12345, +91-98200-12345, 091 9820012345, 09820012345, 98200-12345,
# 982 001 2345, (+91) 9820012345.
MOBILE = re.compile(r"(?<!\d)(?:\(?\+?91\)?[ -]?|0[ -]?)?[6-9]\d(?:[ -]?\d){8}(?!\d)")
# Landlines with an STD code: 022-2754 1234, 022 27541234, (022) 2754-1234, 0251-2345678.
LANDLINE = re.compile(r"(?<!\d)\(?0\d{2,4}\)?[ -]\d{3,4}[ -]?\d{4}(?!\d)")
# Six-digit PIN codes, also "400 703" and "400-703"; not a rupee amount.
PIN = re.compile(
    r"(?<![\d₹.,])(?<!₹ )(?<!rs )(?<!rs\. )(?<!rs)[1-9]\d{2}[ -]?\d{3}(?![\d,.]\d)(?!\d)", re.I
)
UNIT = r"[A-Za-z]?[-/]?\d{1,5}[A-Za-z]?(?:[-/]\d{1,5}[A-Za-z]?)?|[A-Za-z]\b"
ADDRESS_WORDS = (
    # English and Hinglish
    r"flat|flt|house|h\.?\s?no|room|rm|plot|wing|tower|bldg|building|block|sector|sec|"
    r"apartment|apt|door|shop|gala|chawl|"
    # Hindi
    r"फ्लैट|फ़्लैट|मकान|कमरा|रूम|प्लॉट|विंग|टावर|बिल्डिंग|सेक्टर|"
    # Marathi
    r"सदनिका|खोली|इमारत"
)
ADDRESS = re.compile(
    rf"(?:(?<=\W)|^)(?:{ADDRESS_WORDS})\.?\s*(?:no\.?|number|num|#|नंबर|नं\.?|क्र\.?|क्रमांक)?"
    rf"\s*[:.-]?\s*(?:{UNIT})(?!\w)",
    re.I,
)
# A wing and flat written together: "B-302", "C/1204", "A 1102".
WING_FLAT = re.compile(r"(?<!\w)[A-Z]\s?[-/]\s?\d{2,4}(?!\w)|(?<!\w)[A-Z] \d{3,4}(?!\w)")
# Devanagari digits are read as ASCII first, so "९८२००१२३४५" is a phone number too.
DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
SOCIETY = re.compile(
    r"(?<!\w)(?:[A-Z][\w.'-]*\s){1,4}(?:CHS(?:L)?|C\.H\.S\.?|Co-?op(?:erative)?\.?\s+Housing"
    r"\s+Society|Housing\s+Society|Society|Apartments|Towers|Heights|Residency|Nagar|Complex)"
    r"(?!\w)"
)


def _own(terms: Iterable[str], shortest: int) -> re.Pattern[str] | None:
    """The household's own words as one pattern, longest first. Short ones are left out: one
    letter (a tower "B") would scrub every word, a two-digit flat every "12 eggs"."""
    cleaned = sorted(
        {t.strip() for t in terms if t and len(t.strip()) >= shortest}, key=len, reverse=True
    )
    if not cleaned:
        return None
    alternatives = "|".join(re.escape(t) for t in cleaned)
    return re.compile(rf"(?<!\w)(?:{alternatives})(?!\w)", re.I)


def scrub(text: str, names: Iterable[str] = (), places: Iterable[str] = ()) -> str:
    """The text with personal data replaced: [email], [phone], [id], [pin], [address], [name].
    `names` and `places` are the household's own (M1: the owner's name; flat and tower)."""
    text = EMAIL.sub("[email]", text.translate(DIGITS))
    text = MOBILE.sub("[phone]", text)
    text = LANDLINE.sub("[phone]", text)
    text = AADHAAR.sub("[id]", text)
    text = SOCIETY.sub("[address]", text)
    text = ADDRESS.sub("[address]", text)
    text = WING_FLAT.sub("[address]", text)
    text = PIN.sub("[pin]", text)
    for terms, shortest, tag in ((places, 3, "[address]"), (names, 2, "[name]")):
        own = _own(terms, shortest)
        if own is not None:
            text = own.sub(tag, text)
    return text
