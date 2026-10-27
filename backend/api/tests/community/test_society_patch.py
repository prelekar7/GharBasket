"""PATCH /v1/admin/societies/{id}, the map editor's half (M9 §3.9): set or clear the boundary,
replace the towers; the same permission as the unlock settings, the audit row, and that
`community.api.contains` follows the boundary."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.platform.models import AuditEntry

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_staff_societies import NEHA, SOCIETIES, VIKRAM, _as, _key, _manager_of, code

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("kindness_world")]

SQUARE = [[73.02, 19.0], [73.04, 19.0], [73.04, 19.02], [73.02, 19.02], [73.02, 19.0]]
BOW_TIE = [[73.02, 19.0], [73.04, 19.02], [73.04, 19.0], [73.02, 19.02], [73.02, 19.0]]
ZIGZAG = [[73.0 + i / 1000, 19.0 + (i % 2) / 1000] for i in range(500)] + [[73.0, 19.0]]
INSIDE, OUTSIDE = (19.01, 73.03), (19.05, 73.09)  # (lat, lng)


def polygon(*rings: list[list[float]]) -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": list(rings)}


async def _new_society(
    api: httpx2.AsyncClient, headers: dict[str, str], name: str = "Lake View", **body: Any
) -> str:
    made = await api.post(SOCIETIES, json={"name": name, **body}, headers={**headers, **_key()})
    assert made.status_code == 201, made.text
    return made.json()["id"]


async def _listed(api: httpx2.AsyncClient, headers: dict[str, str], society_id: str) -> Any:
    items = (await api.get(f"{SOCIETIES}?q=lake", headers=headers)).json()["items"]
    return next(s for s in items if s["id"] == society_id)


async def _audit(db: AsyncSession) -> list[AuditEntry]:
    rows = await db.execute(
        select(AuditEntry)
        .where(AuditEntry.action == "community.society_updated")
        .order_by(AuditEntry.id)
    )
    return list(rows.scalars())


async def test_the_map_editor_sets_and_clears_the_boundary_and_contains_follows(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    society_id = await _new_society(api, vikram)
    path = f"{SOCIETIES}/{society_id}"
    sid = uuid.UUID(society_id)

    assert await community_api.contains(db_session, sid, *OUTSIDE) is True  # nothing drawn yet
    drawn = await api.patch(path, json={"boundary": polygon(SQUARE)}, headers=vikram)

    assert drawn.status_code == 200, drawn.text
    assert (await _listed(api, vikram, society_id))["boundary"] == polygon(SQUARE)
    assert await community_api.contains(db_session, sid, *INSIDE) is True
    assert await community_api.contains(db_session, sid, *OUTSIDE) is False

    cleared = await api.patch(path, json={"boundary": None}, headers=vikram)

    assert cleared.status_code == 200, cleared.text
    assert (await _listed(api, vikram, society_id))["boundary"] is None
    assert await community_api.contains(db_session, sid, *OUTSIDE) is True
    audited = await _audit(db_session)
    assert [(a.before or {}).get("boundary_vertices") for a in audited] == [None, 4]
    assert [(a.after or {}).get("boundary_vertices") for a in audited] == [4, None]
    assert [(a.after or {}).get("changed") for a in audited] == [["boundary"], ["boundary"]]
    assert "73.02" not in str(audited[0].after) + str(audited[0].before)  # a count, not the polygon


async def test_towers_replace_the_whole_list_and_fill_missing_ids(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    society_id = await _new_society(api, vikram, towers=[{"label": "Old"}])
    path = f"{SOCIETIES}/{society_id}"

    replaced = await api.patch(
        path,
        json={"towers": [{"label": "  A  wing "}, {"id": "a2", "label": "B wing"}, {"label": "C"}]},
        headers=vikram,
    )

    assert replaced.status_code == 200, replaced.text
    assert (await _listed(api, vikram, society_id))["towers"] == [
        {"id": "a", "label": "A wing"},
        {"id": "a2", "label": "B wing"},
        {"id": "b", "label": "C"},
    ]
    other_fields = await api.patch(path, json={"unlock_threshold": 30}, headers=vikram)
    assert len((await _listed(api, vikram, society_id))["towers"]) == 3  # untouched when not sent
    emptied = await api.patch(path, json={"towers": []}, headers=vikram)
    assert other_fields.status_code == emptied.status_code == 200
    assert (await _listed(api, vikram, society_id))["towers"] == []
    changed = [(a.after or {})["changed"] for a in await _audit(db_session)]
    assert changed == [["towers"], ["unlock_threshold"], ["towers"]]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"towers": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]}, "request.invalid"),
        ({"towers": None}, "request.invalid"),
        ({"towers": [{"label": " "}]}, "request.invalid"),
        ({"towers": [{"label": "A"}] * 51}, "request.invalid"),
        ({"slug": "other"}, "request.invalid"),
        ({"boundary": {"type": "Point", "coordinates": [73.0, 19.0]}}, "request.invalid"),
        ({"boundary": polygon(SQUARE[:-1])}, "community.boundary_invalid"),  # not closed
        ({"boundary": polygon(SQUARE[:2] + SQUARE[:1])}, "community.boundary_invalid"),  # 3 points
        ({"boundary": polygon(SQUARE, SQUARE)}, "community.boundary_invalid"),  # a hole
        ({"boundary": polygon(BOW_TIE)}, "community.boundary_invalid"),  # crosses itself
        (
            {"boundary": polygon([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]])},
            "community.boundary_invalid",  # outside India
        ),
        ({"boundary": polygon([[73.0, 19.0, 5.0]] * 4)}, "community.boundary_invalid"),
        ({"boundary": polygon([*ZIGZAG, [73.0, 19.0]])}, "community.boundary_invalid"),  # 502
    ],
)
async def test_a_bad_boundary_or_tower_list_changes_nothing(
    clients: ClientFactory, google: FakeGoogle, body: dict[str, Any], expected: str
) -> None:
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    society_id = await _new_society(api, vikram, towers=[{"label": "A wing"}])

    refused = await api.patch(f"{SOCIETIES}/{society_id}", json=body, headers=vikram)

    assert code(refused) == (422, expected), refused.text
    listed = await _listed(api, vikram, society_id)
    assert listed["boundary"] is None and listed["towers"] == [{"id": "a", "label": "A wing"}]


async def test_another_hubs_society_is_not_found_and_an_admin_may_draw_a_hubless_one(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, neha = await _as(api, google, VIKRAM), await _as(api, google, NEHA)
    other = await _manager_of(api, google, db_session, uuid.uuid4())
    mine = await _new_society(api, vikram)
    hubless = await _new_society(api, neha, name="Hubless View")
    body = {"boundary": polygon(SQUARE)}

    foreign = await api.patch(f"{SOCIETIES}/{mine}", json=body, headers=other)
    managers_hubless = await api.patch(f"{SOCIETIES}/{hubless}", json=body, headers=vikram)
    admin = await api.patch(f"{SOCIETIES}/{hubless}", json=body, headers=neha)

    assert code(foreign) == (404, "community.society_not_found")
    assert code(managers_hubless) == (404, "community.society_not_found")
    assert admin.status_code == 200, admin.text
    assert (await _audit(db_session))[-1].entity_id == hubless
