"""The API-side scrub (M13 §3, DPDP): realistic Hinglish, Hindi, Marathi and English messages
lose phone numbers, e-mails, Aadhaar numbers, PIN codes, addresses and the household's own
names before they are stored or leave for the ML service; quantities and prices stay readable.
Also the history window the model gets."""

import uuid

import pytest

from gharbasket.modules.assistant.models import Message
from gharbasket.modules.assistant.scrub import scrub
from gharbasket.modules.assistant.service import HISTORY, MAX_CONTENT, window

NAMES = ("Sunita Patil", "Sunita", "Patil", "सुनीता")
PLACES = ("B-1204", "b")


@pytest.mark.parametrize(
    ("said", "clean"),
    [
        # Phones, Indian formats, mixed into Hinglish.
        ("mera number 98200 12345 hai, bhindi mat do", "mera number [phone] hai, bhindi mat do"),
        ("+91-98200-12345 pe call karo", "[phone] pe call karo"),
        ("(+91) 9820012345 ya 09820012345", "[phone] ya [phone]"),
        ("ghar ka landline 022-2754 1234 hai", "ghar ka landline [phone] hai"),
        ("माझा नंबर ९८२००१२३४५ आहे, केळी नको", "माझा नंबर [phone] आहे, केळी नको"),
        # E-mail and Aadhaar in any grouping.
        ("bill sunita.patil@gmail.com pe bhejo", "bill [email] pe bhejo"),
        ("aadhaar 2345 6789 0123 hai", "aadhaar [id] hai"),
        ("आधार 2345-6789-0123", "आधार [id]"),
        ("234567890123 likh lo", "[id] likh lo"),
        # PIN codes and addresses (English, Hindi, Marathi).
        ("Vashi 400703 mein delivery", "Vashi [pin] mein delivery"),
        ("Kharghar 410 210", "Kharghar [pin]"),
        ("Flat No. 302, Sea Breeze CHS, Sector 17", "[address], [address], [address]"),
        ("flat 1204 tower C mein bhejo", "[address] [address] mein bhejo"),
        ("मकान नंबर 45, फ्लैट 302 में भेजना", "[address], [address] में भेजना"),
        ("सदनिका क्र. ३०२, इमारत ५ मध्ये द्या", "[address], [address] मध्ये द्या"),
        ("A 1102 Palm Beach Residency", "[address] [address]"),
        # The household's own name and flat (from M1), in Latin or Devanagari.
        ("Main Sunita Patil bol rahi hoon", "Main [name] bol rahi hoon"),
        ("सुनीता ने कहा दूध 2 पैकेट", "[name] ने कहा दूध 2 पैकेट"),
        ("B-1204 mein bhejna", "[address] mein bhejna"),
    ],
)
def test_personal_data_is_scrubbed(said: str, clean: str) -> None:
    assert scrub(said, NAMES, PLACES) == clean


@pytest.mark.parametrize(
    "said",
    [
        "is hafte bhindi mat bhejna",
        "dahi 2 packet aur 12 eggs bhejo",
        "₹ 150000 nahi, Rs 500 ka paneer",
        "agle hafte skip kar do, 13 Oct ko bahar hain",
        "दूध दो ही पैकेट भेजना",
        "या आठवड्यात केळी नको",
        "L2 ko 7 kar do, 500 g paneer",
        "My society captain is great; the box came at 7:30",
    ],
)
def test_quantities_prices_and_plain_words_stay(said: str) -> None:
    assert scrub(said, NAMES, PLACES) == said


def test_short_private_terms_never_scrub_ordinary_words() -> None:
    # A one-letter tower ("b") and a two-digit flat would otherwise eat every word or quantity.
    assert scrub("bhindi b 12 eggs", (), ("b", "12")) == "bhindi b 12 eggs"


def _messages(roles: str) -> list[Message]:
    chat = uuid.uuid4()
    return [
        Message(session_id=chat, role={"u": "user", "a": "assistant"}[r], content=f"{r}{i}")
        for i, r in enumerate(roles)
    ]


def test_the_window_starts_with_the_person_and_alternates() -> None:
    # 12 exchanges and the new question: 25 messages; the last 20 start with a reply.
    sent = window(_messages("ua" * 12 + "u"))
    assert len(sent) == HISTORY - 1 and sent[0] == {"role": "user", "content": "u6"}
    assert [m["role"] for m in sent] == ["user", "assistant"] * 9 + ["user"]

    # A reply lost in between (two questions in a row) is joined, never sent as two.
    joined = window(_messages("uau" + "u"))
    assert [m["role"] for m in joined] == ["user", "assistant", "user"]
    assert joined[-1]["content"] == "u2\n\nu3"

    long = [Message(session_id=uuid.uuid4(), role="user", content="x" * (MAX_CONTENT + 10))]
    assert len(window(long)[0]["content"]) == MAX_CONTENT
