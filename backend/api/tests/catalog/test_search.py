"""Multilingual search (M2 US-1, §3.1): every spelling people use finds the product in the top 3."""

from typing import Any

import httpx2
import pytest

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("catalog")]

# (what a person types, the product they mean). At least 30 pairs (spec §9).
QUERIES = [
    # English
    ("okra", "bhindi"),
    ("lady finger", "bhindi"),
    ("spinach", "palak"),
    ("tomato", "tomato"),
    ("cauliflower", "cauliflower"),
    ("sweet lime", "mosambi"),
    ("gram flour", "besan"),
    ("peanuts", "groundnuts"),
    ("curd", "curd"),
    ("eggs", "eggs"),
    # Hinglish (romanised Hindi or Marathi)
    ("bhindi", "bhindi"),
    ("bhendi", "bhindi"),
    ("pyaaz", "onion"),
    ("kanda", "onion"),
    ("aloo", "potato"),
    ("batata", "potato"),
    ("tamatar", "tomato"),
    ("dhania", "coriander"),
    ("kothimbir", "coriander"),
    ("toor dal", "toor-dal"),
    ("arhar dal", "toor-dal"),
    ("shevga", "drumstick"),
    ("doodhi", "bottle-gourd"),
    ("sabudana", "sabudana"),
    ("gul", "jaggery"),
    # Devanagari: Hindi and Marathi
    ("भिंडी", "bhindi"),
    ("भेंडी", "bhindi"),
    ("प्याज", "onion"),
    ("कांदा", "onion"),
    ("आलू", "potato"),
    ("बटाटा", "potato"),
    ("कोथिंबीर", "coriander"),
    ("तूर डाळ", "toor-dal"),
    ("दही", "curd"),
    # Small typos and partial words
    ("bindi", "bhindi"),
    ("tamater", "tomato"),
    ("spinch", "palak"),
    ("cauli", "cauliflower"),
    ("pome", "pomegranate"),
]


async def _customer(api: httpx2.AsyncClient, google: FakeGoogle) -> dict[str, str]:
    return bearer((await sign_in(api, google))["access_token"])


async def _search(api: httpx2.AsyncClient, headers: dict[str, str], **params: Any) -> list[Any]:
    response = await api.get("/v1/search", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def test_every_spelling_finds_its_product_in_the_top_3(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await _customer(api, google)
    assert len(QUERIES) >= 30

    misses = []
    for query, slug in QUERIES:
        top = [item["slug"] for item in (await _search(api, headers, q=query, limit=3))]
        if slug not in top:
            misses.append((query, slug, top))

    assert misses == []


async def test_a_result_says_which_spelling_matched_in_the_asked_language(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = {**await _customer(api, google), "Accept-Language": "hi-IN,hi;q=0.9"}

    response = await api.get("/v1/search", params={"q": "bhendi"}, headers=headers)

    first = response.json()["items"][0]
    assert first["slug"] == "bhindi"
    assert first["matched"] == "bhendi"
    assert first["name"] == "भिंडी"
    assert first["step"] == {"qty": 250, "unit": "g", "label": "250 ग्राम"}
    assert first["price_paise"] is None  # prices arrive with the price lists
    assert response.headers["content-language"] == "hi"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("query", "first", "matched"),
    [
        ("bindi", "bhindi", "bhindi"),  # a typo still ranks the right product first
        ("भिंडी", "bhindi", "भिंडी"),  # the spelling as typed, not its Hinglish form
        ("tur dal", "toor-dal", "tur dal"),
    ],
)
async def test_the_best_product_and_spelling_come_first(
    clients: ClientFactory, google: FakeGoogle, query: str, first: str, matched: str
) -> None:
    api = clients()

    items = await _search(api, await _customer(api, google), q=query)

    assert (items[0]["slug"], items[0]["matched"]) == (first, matched)


async def test_nonsense_finds_nothing_and_blank_is_refused(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await _customer(api, google)

    nothing = await _search(api, headers, q="xqzv")
    blank = await api.get("/v1/search", params={"q": ""}, headers=headers)
    too_long = await api.get("/v1/search", params={"q": "a" * 81}, headers=headers)

    assert nothing == []
    assert blank.status_code == too_long.status_code == 422


async def test_search_needs_sign_in(clients: ClientFactory) -> None:
    response = await clients().get("/v1/search", params={"q": "okra"})

    assert response.status_code == 401
