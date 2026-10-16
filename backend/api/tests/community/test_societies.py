"""Society search, details and unlock (M9 §3.1, US-2): the contract the onboarding screens use."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.service import SocietyData, save_society
from gharbasket.platform.pagination import encode_cursor
from gharbasket.tools.seed import SEED_SOCIETY_ID, SOCIETIES

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import customer

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("societies")]

SUMMARY_FIELDS = {"id", "slug", "name", "area", "status", "distance_m"}
# The centre of Shree Ganesh CHS (seed): Sunrise Towers is ~600 m away, Green Valley ~2 km.
NEAR_SHREE_GANESH = "19.0430,73.0720"
KHARGHAR = ["shree-ganesh-chs-kharghar", "sunrise-towers-kharghar", "green-valley-heights-kharghar"]


async def _search(api: httpx2.AsyncClient, headers: dict[str, str], **params: Any) -> list[Any]:
    response = await api.get("/v1/societies", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _slugs(items: list[dict[str, Any]]) -> list[str]:
    return [item["slug"] for item in items]


async def test_search_by_name_finds_the_society_and_never_shows_a_captain(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)

    items = await _search(api, headers, q="sunrise")

    assert _slugs(items) == ["sunrise-towers-kharghar"]
    assert items[0] == {
        "id": str(SEED_SOCIETY_ID),
        "slug": "sunrise-towers-kharghar",
        "name": "Sunrise Towers",
        "area": "Sector 20, Kharghar",
        "status": "live",
        "distance_m": None,
    }


async def test_search_forgives_typos_case_and_accents(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)

    assert set(_slugs(await _search(api, headers, q="KHARGAR"))) == set(KHARGHAR)
    assert _slugs(await _search(api, headers, q="Sunríse")) == ["sunrise-towers-kharghar"]
    assert _slugs(await _search(api, headers, q="  krishna   kunj ")) == ["krishna-kunj-new-panvel"]
    assert await _search(api, headers, q="zzqx") == []


async def test_the_best_match_comes_first(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await save_society(
        db_session,
        SocietyData(
            id=uuid.uuid4(),
            slug="sunrise-park-kamothe",
            name="Sunrise Park",
            area="Sector 9, Kamothe",
            boundary=None,
            towers=[],
            flats=None,
            unlock_threshold=20,
            status="prospect",
            hub_id=None,
        ),
    )
    api = clients()
    headers = await customer(api, google)

    items = await _search(api, headers, q="sunrise towers")

    assert _slugs(items)[0] == "sunrise-towers-kharghar"


async def test_equal_matches_are_ordered_by_distance(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)

    items = await _search(api, headers, q="kharghar", near=NEAR_SHREE_GANESH)

    assert _slugs(items) == KHARGHAR
    distances = [item["distance_m"] for item in items]
    assert distances[0] == 0  # inside its boundary
    assert 300 < distances[1] < 900 and 1_500 < distances[2] < 3_000


async def test_near_alone_lists_the_nearest_first_and_undrawn_societies_last(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)

    items = await _search(api, headers, near="19.0005,73.1185")

    assert _slugs(items)[0] == "krishna-kunj-new-panvel"
    assert items[0]["distance_m"] == 0
    assert _slugs(items)[-1] == "sai-darshan-apartments-panvel"
    assert items[-1]["distance_m"] is None
    known = [item["distance_m"] for item in items[:-1]]
    assert known == sorted(known)


async def test_without_q_or_near_the_list_is_alphabetical(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)

    items = await _search(api, headers)

    assert [item["name"] for item in items] == sorted(society.name for society in SOCIETIES)
    assert all(set(item) == SUMMARY_FIELDS for item in items)


@pytest.mark.parametrize(
    "params",
    [{}, {"near": NEAR_SHREE_GANESH}, {"q": "kharghar", "near": NEAR_SHREE_GANESH}],
    ids=["alphabetical", "nearest", "best-match"],
)
async def test_pages_join_up_to_the_full_list(
    clients: ClientFactory, google: FakeGoogle, params: dict[str, str]
) -> None:
    api = clients()
    headers = await customer(api, google)
    everything = await _search(api, headers, **params)

    paged: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(10):
        page = await api.get(
            "/v1/societies",
            params={**params, "limit": 2, **({"cursor": cursor} if cursor else {})},
            headers=headers,
        )
        assert page.status_code == 200, page.text
        paged += page.json()["items"]
        cursor = page.json()["next_cursor"]
        if cursor is None:
            break

    assert paged == everything
    assert len(everything) >= 3


async def test_a_cursor_from_another_kind_of_search_is_refused(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)
    first = await api.get("/v1/societies", params={"limit": 1}, headers=headers)

    reused = await api.get(
        "/v1/societies",
        params={"limit": 1, "near": NEAR_SHREE_GANESH, "cursor": first.json()["next_cursor"]},
        headers=headers,
    )
    garbage = await api.get("/v1/societies", params={"cursor": "bm9wZQ"}, headers=headers)
    tampered = await api.get(
        "/v1/societies",
        params={"cursor": encode_cursor({"order": "name", "after": [1.5, str(SEED_SOCIETY_ID)]})},
        headers=headers,
    )

    for refused in (reused, garbage, tampered):
        assert refused.status_code == 422
        assert refused.json()["code"] == "request.invalid_cursor"


@pytest.mark.parametrize("near", ["abc", "19.03", "0,0", "51.5,-0.12", "19.03,73.07,1", "nan,73"])
async def test_near_must_be_a_point_in_india(
    clients: ClientFactory, google: FakeGoogle, near: str
) -> None:
    api = clients()
    headers = await customer(api, google)

    response = await api.get("/v1/societies", params={"near": near}, headers=headers)

    assert response.status_code == 422
    assert response.json()["code"] == "community.near_invalid"


async def test_page_size_is_at_most_50(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    headers = await customer(api, google)

    response = await api.get("/v1/societies", params={"limit": 51}, headers=headers)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/v1/societies",
        f"/v1/societies/{SEED_SOCIETY_ID}",
        f"/v1/societies/{SEED_SOCIETY_ID}/unlock",
    ],
)
async def test_society_endpoints_need_sign_in(clients: ClientFactory, path: str) -> None:
    response = await clients().get(path)

    assert response.status_code == 401


async def test_society_details_have_towers_and_no_captain_yet(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)

    response = await api.get(f"/v1/societies/{SEED_SOCIETY_ID}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == SUMMARY_FIELDS | {"towers", "flats", "captain"}
    assert body["towers"] == [
        {"id": "a", "label": "A wing"},
        {"id": "b", "label": "B wing"},
        {"id": "c", "label": "C wing"},
    ]
    assert body["flats"] == 240
    assert body["captain"] is None


@pytest.mark.parametrize("suffix", ["", "/unlock"])
async def test_an_unknown_society_is_404(
    clients: ClientFactory, google: FakeGoogle, suffix: str
) -> None:
    api = clients()
    headers = await customer(api, google)

    response = await api.get(f"/v1/societies/{uuid.uuid4()}{suffix}", headers=headers)

    assert response.status_code == 404
    assert response.json()["code"] == "community.society_not_found"


async def test_unlock_shows_the_threshold_and_who_has_joined(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)

    response = await api.get(f"/v1/societies/{SEED_SOCIETY_ID}/unlock", headers=headers)

    # No household in this test, so nobody has joined; seeded live, so delivery is free (§3.6).
    body = response.json()
    assert body["live_at"] is not None
    assert body == {
        "society_id": str(SEED_SOCIETY_ID),
        "status": "live",
        "joined": 0,
        "threshold": 20,
        "live_at": body["live_at"],
        "threshold_source": "default",
        "box_fee_paise": 0,
    }
