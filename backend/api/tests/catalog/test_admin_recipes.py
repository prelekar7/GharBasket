"""The console's recipe editor (M2 §3.3): create with validation, rules derived from the
ingredients, edits with If-Match, the catalog editor's limits and reviews, sides that active
recipes need, history from the audit log, the lists, and who may do what."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product, Recipe
from gharbasket.platform.models import AuditEntry, OutboxEvent

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

PRIYA = "+919800001001"  # customer
VIKRAM = "+919800001008"  # hub manager
NEHA = "+919800001011"  # admin
REKHA = "+919800001013"  # catalog editor
RECIPES = "/v1/admin/recipes"
NAMES = {"en": "Bhindi egg bhurji", "hi": "भिंडी अंडा भुर्जी", "mr": "भेंडी अंडा भुर्जी"}


async def _as(api: httpx2.AsyncClient, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed_in = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed_in["access_token"])


async def _product(db: AsyncSession, slug: str) -> str:
    return str((await db.execute(select(Product.id).where(Product.slug == slug))).scalar_one())


async def _recipe(db: AsyncSession, slug: str) -> uuid.UUID:
    return (await db.execute(select(Recipe.id).where(Recipe.slug == slug))).scalar_one()


async def _line(db: AsyncSession, slug: str, qty: Any, unit: str, **more: Any) -> dict[str, Any]:
    return {"product_id": await _product(db, slug), "qty": qty, "unit": unit, **more}


async def _body(db: AsyncSession, **overrides: Any) -> dict[str, Any]:
    return {
        "slug": "bhindi-egg-bhurji",
        "names": NAMES,
        "cuisine": "maharashtrian",
        "slots": ["breakfast", "dinner"],
        "cook_minutes": 20,
        "servings": 2,
        "tags": ["quick", "egg"],
        "is_side": False,
        "sides": ["chapati"],
        "fasting_ok": False,
        "diabetic_ok": True,
        "ingredients": [
            await _line(db, "bhindi", 100, "g"),
            await _line(db, "eggs", 1.5, "piece"),
            await _line(db, "onion", 30, "g", optional=True),
        ],
        "steps": [{"n": 1, "text": {"en": "Fry the okra."}}, {"n": 2, "text": {"en": "Add eggs."}}],
        **overrides,
    }


async def _create(api: httpx2.AsyncClient, auth: dict[str, str], body: dict) -> httpx2.Response:
    return await api.post(RECIPES, json=body, headers={**auth, "Idempotency-Key": uuid.uuid4().hex})


async def _patch(
    api: httpx2.AsyncClient, auth: dict[str, str], recipe_id: Any, body: dict, version: Any = 1
) -> httpx2.Response:
    headers = {**auth, "If-Match": f'"{version}"'} if version is not None else auth
    return await api.patch(f"{RECIPES}/{recipe_id}", json=body, headers=headers)


def _code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json()["code"]


async def test_an_admin_adds_a_recipe_whose_rules_come_from_its_ingredients(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    key = {**neha, "Idempotency-Key": uuid.uuid4().hex}
    body = await _body(db_session)

    created = await api.post(RECIPES, json=body, headers=key)
    replayed = await api.post(RECIPES, json=body, headers=key)

    assert created.status_code == 201, created.text
    out = created.json()
    assert out["version"] == 1 and created.headers["etag"] == '"1"'
    assert out["diet"] == "egg" and out["active"] is True
    assert out["rules"] == {
        "jain_ok": False,
        "no_onion_garlic": False,  # an optional onion still counts
        "fasting_ok": False,
        "diabetic_ok": True,
    }
    assert out["reviews"]["fasting_ok"]["by"]["name"] == "Neha Iyer"
    assert out["updated_by"]["name"] == "Neha Iyer"
    assert out["sides"] == ["chapati"] and out["steps"][1] == {
        "n": 2,
        "text": {"en": "Add eggs.", "hi": None, "mr": None},
    }
    eggs = next(i for i in out["ingredients"] if i["slug"] == "eggs")
    assert eggs["qty"] == 1.5 and eggs["label"] == "1.5 pc"
    assert replayed.json() == out
    read = await api.get(f"{RECIPES}/{out['id']}", headers=neha)
    assert read.headers["etag"] == '"1"' and read.json() == out
    public = await api.get(f"/v1/recipes/{out['id']}", headers=neha)
    assert public.status_code == 200 and public.json()["rules"] == out["rules"]
    listed = await api.get(RECIPES, params={"q": "bhindi-egg"}, headers=neha)
    assert [r["steps"] for r in listed.json()["items"]] == [None]
    events = await db_session.execute(
        select(OutboxEvent.payload).where(OutboxEvent.event_type == "catalog.recipe_changed")
    )
    [event] = [e for e in events.scalars() if e["recipe_id"] == out["id"]]
    assert event["active"] is True and "ingredients" in event["changed"]


async def test_a_new_recipe_is_checked_before_it_is_saved(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    await db_session.execute(update(Product).where(Product.slug == "mint").values(active=False))
    mint, okra = await _product(db_session, "mint"), await _product(db_session, "bhindi")
    body = await _body(db_session)
    await db_session.commit()

    async def refused(**overrides: Any) -> httpx2.Response:
        return await _create(api, neha, {**body, **overrides})

    missing = await refused(names={"en": "Bhurji", "hi": ""})
    assert _code(missing) == (422, "catalog.names_incomplete")
    assert missing.json()["missing"] == ["hi", "mr"]
    assert _code(await refused(slug="palak-paneer")) == (409, "catalog.slug_taken")
    ghost = str(uuid.uuid4())
    unknown = await refused(
        ingredients=[{"product_id": ghost, "qty": 1, "unit": "g"}, {**body["ingredients"][0]}]
    )
    assert _code(unknown) == (422, "catalog.product_unknown")
    assert unknown.json()["product_ids"] == [ghost]
    inactive = await refused(ingredients=[{"product_id": mint, "qty": 1, "unit": "bunch"}])
    assert inactive.json()["product_ids"] == [mint]
    wrong_unit = await refused(ingredients=[{"product_id": okra, "qty": 5, "unit": "ml"}])
    assert _code(wrong_unit) == (422, "catalog.unit_mismatch")
    assert wrong_unit.json()["product_ids"] == [okra]
    not_a_side = await refused(sides=["palak-paneer", "chapati"])
    assert _code(not_a_side) == (422, "catalog.side_invalid")
    assert not_a_side.json()["sides"] == ["palak-paneer"]
    assert _code(await refused(is_side=True)) == (422, "catalog.side_invalid")
    for invalid in (
        {"diet": "veg"},  # derived, never sent
        {"rules": {"jain_ok": True}},
        {"slug": "Bad_Slug"},
        {"cook_minutes": 4},
        {"servings": 13},
        {"slots": []},
        {"ingredients": []},
        {"ingredients": [body["ingredients"][0]] * 2},
        {"ingredients": [{"product_id": okra, "qty": 0.125, "unit": "g"}]},
        {"steps": [{"n": 2, "text": {"en": "Out of order"}}]},
    ):
        assert _code(await refused(**invalid)) == (422, "request.invalid"), invalid


async def test_an_edit_needs_the_current_version_and_never_changes_the_slug(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    thepla = await _recipe(db_session, "methi-thepla")
    await db_session.commit()

    assert _code(await _patch(api, neha, thepla, {"servings": 3}, version=None)) == (
        428,
        "request.precondition_required",
    )
    assert _code(await _patch(api, neha, thepla, {"servings": 3}, version=4)) == (
        412,
        "catalog.version_conflict",
    )
    immutable = await _patch(api, neha, thepla, {"slug": "thepla"})
    assert _code(immutable) == (422, "catalog.field_immutable")
    assert immutable.json()["fields"] == ["slug"]
    assert _code(await _patch(api, neha, thepla, {"names": None})) == (422, "request.invalid")
    assert _code(await _patch(api, neha, uuid.uuid4(), {"servings": 3})) == (
        404,
        "catalog.recipe_not_found",
    )

    saved = await _patch(
        api, neha, thepla, {"servings": 3, "steps": [{"n": 1, "text": {"en": "Knead."}}]}
    )
    again = await _patch(api, neha, thepla, {"servings": 3}, version=2)

    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2 and saved.headers["etag"] == '"2"'
    assert saved.json()["servings"] == 3 and saved.json()["reviews"]["fasting_ok"] is None
    assert again.json()["version"] == 2  # nothing changed, nothing saved
    [entry] = (
        await db_session.execute(
            select(AuditEntry).where(
                AuditEntry.action == "catalog.recipe_updated",
                AuditEntry.entity_id == str(thepla),
            )
        )
    ).scalars()
    assert entry.after == {"servings": 3, "steps": [{"n": 1, "text": {"en": "Knead."}}]}
    assert entry.before is not None and entry.before["steps"] is None


async def test_new_ingredients_recompute_the_derived_rules(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    khichdi = await _recipe(db_session, "sabudana-khichdi")
    lines = [
        await _line(db_session, "sabudana", 60, "g"),
        await _line(db_session, "onion", 20, "g"),
    ]
    await db_session.commit()

    saved = await _patch(api, neha, khichdi, {"ingredients": lines})

    assert saved.status_code == 200, saved.text
    out = saved.json()
    assert out["rules"]["no_onion_garlic"] is False and out["rules"]["jain_ok"] is False
    assert out["rules"]["fasting_ok"] is True  # curated, untouched
    assert sorted(i["slug"] for i in out["ingredients"]) == ["onion", "sabudana"]
    events = await db_session.execute(
        select(OutboxEvent.payload).where(OutboxEvent.event_type == "catalog.recipe_changed")
    )
    [event] = [e for e in events.scalars() if e["recipe_id"] == str(khichdi)]
    assert event["changed"] == ["ingredients", "rules"]
    vrat = await api.get("/v1/recipes", params={"jain_ok": "true", "limit": 100}, headers=neha)
    assert "sabudana-khichdi" not in [r["slug"] for r in vrat.json()["items"]]


async def test_a_catalog_editor_changes_only_names_and_the_curated_flags(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    rekha, neha = await _as(api, google, REKHA), await _as(api, google, NEHA)
    vikram = await _as(api, google, VIKRAM)
    kobi = await _recipe(db_session, "kobi-bhaji")
    await db_session.commit()
    names = {"en": "Cabbage bhaji", "hi": "पत्तागोभी", "mr": "कोबीची भाजी"}

    reviewed = await _patch(api, rekha, kobi, {"names": names, "fasting_ok": True})
    others = await _patch(
        api, rekha, kobi, {"fasting_ok": False, "cook_minutes": 30, "tags": []}, 2
    )

    assert reviewed.status_code == 200, reviewed.text
    out = reviewed.json()
    assert out["rules"]["fasting_ok"] is True and out["name"] == "Cabbage bhaji"
    assert out["reviews"]["fasting_ok"]["by"]["name"] == "Rekha Joshi"
    assert out["reviews"]["diabetic_ok"] is None  # unchanged, so not reviewed
    assert out["updated_by"]["name"] == "Rekha Joshi"
    assert _code(others) == (403, "auth.forbidden")
    assert others.json()["fields"] == ["cook_minutes", "tags"]
    assert (await api.get(f"{RECIPES}/{kobi}", headers=rekha)).json()["version"] == 2
    assert _code(await _create(api, rekha, await _body(db_session))) == (403, "auth.forbidden")
    assert _code(await _patch(api, vikram, kobi, {"fasting_ok": False}, 2)) == (
        403,
        "auth.forbidden",
    )
    # An admin may change anything, and their change of a flag is recorded too.
    flagged = await _patch(api, neha, kobi, {"diabetic_ok": False, "cook_minutes": 30}, 2)
    assert flagged.json()["reviews"]["diabetic_ok"]["by"]["name"] == "Neha Iyer"
    assert flagged.json()["reviews"]["fasting_ok"]["by"]["name"] == "Rekha Joshi"


async def test_a_side_that_active_recipes_list_cannot_be_deactivated(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    chapati, kachumber = (
        await _recipe(db_session, "chapati"),
        await _recipe(db_session, "kachumber"),
    )
    palak = await _recipe(db_session, "palak-paneer")
    await db_session.commit()

    in_use = await _patch(api, neha, chapati, {"active": False})
    assert _code(in_use) == (409, "catalog.recipe_in_use")
    assert {"kobi-bhaji", "palak-paneer"} <= set(in_use.json()["recipes"])
    assert _code(await _patch(api, neha, chapati, {"is_side": False})) == (
        409,
        "catalog.recipe_in_use",
    )
    assert _code(await _patch(api, neha, chapati, {"sides": ["plain-rice"]})) == (
        422,
        "catalog.side_invalid",
    )

    off = await _patch(api, neha, kachumber, {"active": False})
    assert off.status_code == 200 and off.json()["active"] is False
    assert (await api.get(f"/v1/recipes/{kachumber}", headers=neha)).status_code == 404
    inactive = await api.get(RECIPES, params={"active": "false"}, headers=neha)
    assert [r["slug"] for r in inactive.json()["items"]] == ["kachumber"]
    listing = await _patch(api, neha, palak, {"sides": ["chapati", "kachumber"]})
    assert _code(listing) == (422, "catalog.side_invalid")
    assert listing.json()["sides"] == ["kachumber"]
    on = await _patch(api, neha, kachumber, {"active": True}, version=2)
    assert on.json()["active"] is True and on.json()["version"] == 3
    paired = await _patch(api, neha, palak, {"sides": ["chapati", "kachumber"]})
    assert paired.status_code == 200 and paired.json()["sides"] == ["chapati", "kachumber"]


async def test_a_recipe_using_a_deactivated_product_cannot_come_back_unchanged(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    thepla = await _recipe(db_session, "methi-thepla")
    await db_session.execute(update(Recipe).where(Recipe.id == thepla).values(active=False))
    await db_session.execute(update(Product).where(Product.slug == "methi").values(active=False))
    methi = await _product(db_session, "methi")
    await db_session.commit()

    back = await _patch(api, neha, thepla, {"active": True})

    assert _code(back) == (422, "catalog.product_unknown")
    assert back.json()["product_ids"] == [methi]


async def test_the_history_comes_from_the_audit_log_newest_first(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha, rekha = await _as(api, google, NEHA), await _as(api, google, REKHA)
    created = (await _create(api, neha, await _body(db_session))).json()
    await _patch(api, neha, created["id"], {"cook_minutes": 25})
    await _patch(api, rekha, created["id"], {"fasting_ok": True}, version=2)

    history = await api.get(f"{RECIPES}/{created['id']}/history", headers=rekha)

    assert history.status_code == 200, history.text
    items = history.json()["items"]
    assert [i["action"] for i in items] == [
        "catalog.recipe_updated",
        "catalog.recipe_updated",
        "catalog.recipe_created",
    ]
    assert items[0]["by"]["name"] == "Rekha Joshi"
    assert items[0]["changed"]["rules"]["to"]["fasting_ok"] is True
    assert items[1]["changed"] == {"cook_minutes": {"from": 20, "to": 25}}
    assert items[2]["changed"]["slug"] == {"from": None, "to": "bhindi-egg-bhurji"}
    priya = await _as(api, google, PRIYA)
    assert _code(await api.get(f"{RECIPES}/{created['id']}/history", headers=priya)) == (
        403,
        "auth.forbidden",
    )


async def test_the_admin_list_filters_and_pages(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha, vikram = await _as(api, google, NEHA), await _as(api, google, VIKRAM)
    priya = await _as(api, google, PRIYA)
    await db_session.commit()

    sides = await api.get(RECIPES, params={"is_side": "true", "limit": 4}, headers=vikram)
    rest = await api.get(
        RECIPES,
        params={"is_side": "true", "limit": 4, "cursor": sides.json()["next_cursor"]},
        headers=vikram,
    )
    found = await api.get(RECIPES, params={"q": "पोळी"}, headers=neha)
    bengali = await api.get(RECIPES, params={"cuisine": "bengali", "slot": "lunch"}, headers=neha)

    assert sides.status_code == 200, sides.text
    assert sides.headers["cache-control"] == "no-store"
    first, second = sides.json()["items"], rest.json()["items"]
    assert len(first) == 4 and {r["is_side"] for r in first + second} == {True}
    assert not {r["id"] for r in first} & {r["id"] for r in second}
    chapati = next(r for r in found.json()["items"] if r["slug"] == "chapati")
    assert chapati["names"]["mr"] == "पोळी" and chapati["version"] == 1
    assert chapati["updated_by"] is None and chapati["reviews"]["fasting_ok"] is None
    assert len(chapati["ingredients"]) == 3
    assert {r["cuisine"] for r in bengali.json()["items"]} == {"bengali"}
    assert _code(await api.get(RECIPES, headers=priya)) == (403, "auth.forbidden")
    assert _code(await api.get(f"{RECIPES}/{uuid.uuid4()}", headers=neha)) == (
        404,
        "catalog.recipe_not_found",
    )
