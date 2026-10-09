"""The console's product editor (M2 §3.3): create with validation, edit with If-Match, immutable
fields, recipe rules recomputed on a tag change, deactivation refused while a recipe needs the
product, history from the audit log, the lists with their filters and paging, and who may do
what."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Category, Product, Recipe, RecipeIngredient
from gharbasket.platform.models import AuditEntry, OutboxEvent

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

PRIYA = "+919800001001"  # customer
VIKRAM = "+919800001008"  # hub manager
NEHA = "+919800001011"  # admin
REKHA = "+919800001013"  # catalog editor
PRODUCTS = "/v1/admin/products"


async def _as(api: httpx2.AsyncClient, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed_in = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed_in["access_token"])


async def _product(db: AsyncSession, slug: str) -> Product:
    return (await db.execute(select(Product).where(Product.slug == slug))).scalar_one()


async def _id(db: AsyncSession, slug: str) -> uuid.UUID:
    """A product's id (a refused request rolls the session back and expires loaded rows)."""
    return (await db.execute(select(Product.id).where(Product.slug == slug))).scalar_one()


async def _category_id(db: AsyncSession, slug: str = "leafy-greens") -> str:
    return str((await db.execute(select(Category.id).where(Category.slug == slug))).scalar_one())


async def _body(db: AsyncSession, **overrides: Any) -> dict[str, Any]:
    return {
        "slug": "chawli-leaves",
        "kind": "veg",
        "category_id": await _category_id(db),
        "names": {"en": "Amaranth (chawli)", "hi": "चौलाई", "mr": "चवळी"},
        "step": {"qty": 1, "unit": "bunch"},
        "shelf_life_h": 48,
        "tags": ["leafy"],
        "nutrition": None,
        "synonyms": [{"term": "chaulai", "lang": "hinglish"}],
        **overrides,
    }


async def _create(api: httpx2.AsyncClient, auth: dict[str, str], body: dict) -> httpx2.Response:
    return await api.post(
        PRODUCTS, json=body, headers={**auth, "Idempotency-Key": uuid.uuid4().hex}
    )


async def _patch(
    api: httpx2.AsyncClient, auth: dict[str, str], product_id: Any, body: dict, version: Any = 1
) -> httpx2.Response:
    headers = {**auth, "If-Match": f'"{version}"'} if version is not None else auth
    return await api.patch(f"{PRODUCTS}/{product_id}", json=body, headers=headers)


def _code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json()["code"]


async def test_an_admin_adds_a_product_that_search_finds(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    key = {**neha, "Idempotency-Key": uuid.uuid4().hex}
    body = await _body(db_session)

    created = await api.post(PRODUCTS, json=body, headers=key)
    replayed = await api.post(PRODUCTS, json=body, headers=key)

    assert created.status_code == 201, created.text
    out = created.json()
    assert out["version"] == 1 and created.headers["etag"] == '"1"'
    assert out["active"] is True and out["data_source"] == "real"
    assert out["updated_by"]["name"] == "Neha Iyer"
    assert out["step"] == {"qty": 1, "unit": "bunch", "label": "1 bunch"}
    assert out["synonyms"] == [{"term": "chaulai", "lang": "hinglish"}]
    assert out["used_by_recipes"] == 0
    assert replayed.json() == out
    found = await api.get("/v1/search", params={"q": "chaulai"}, headers=neha)
    assert found.json()["items"][0]["slug"] == "chawli-leaves"
    read = await api.get(f"{PRODUCTS}/{out['id']}", headers=neha)
    assert read.headers["etag"] == '"1"' and read.json() == out
    events = await db_session.execute(
        select(OutboxEvent.payload).where(OutboxEvent.event_type == "catalog.product_changed")
    )
    [event] = [e for e in events.scalars() if e["product_id"] == out["id"]]
    assert event["active"] is True


async def test_a_new_product_needs_three_names_a_free_slug_and_known_codes(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    await db_session.commit()
    names = {"en": "Amaranth", "hi": " ", "mr": None}

    async def refused(**overrides: Any) -> httpx2.Response:
        return await _create(api, neha, await _body(db_session, **overrides))

    missing = await refused(names=names)
    assert _code(missing) == (422, "catalog.names_incomplete")
    assert missing.json()["missing"] == ["hi", "mr"]
    assert _code(await refused(slug="bhindi")) == (409, "catalog.slug_taken")
    assert _code(await refused(slug="Bad_Slug")) == (422, "request.invalid")
    assert _code(await refused(kind="kit")) == (422, "catalog.kind_not_editable")
    unknown = await refused(tags=["leafy", "spicy"])
    assert _code(unknown) == (422, "catalog.tag_unknown") and unknown.json()["tags"] == ["spicy"]
    assert _code(await refused(category_id=str(uuid.uuid4()))) == (422, "catalog.category_unknown")
    assert _code(await refused(step={"qty": 1, "unit": "dozen"})) == (422, "request.invalid")
    assert _code(await refused(step={"qty": 0, "unit": "g"})) == (422, "request.invalid")
    bad_nutrition = {"kcal": -1, "protein_g": 1, "fat_g": 1, "carb_g": 1, "fibre_g": 1}
    assert _code(await refused(nutrition={**bad_nutrition, "source": "manual"}))[0] == 422


async def test_an_edit_needs_the_current_version_and_never_changes_slug_kind_or_step(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    okra = await _id(db_session, "bhindi")
    await db_session.commit()
    names = {"en": "Okra", "hi": "भिंडी", "mr": "भेंडी"}

    assert _code(await _patch(api, neha, okra, {"names": names}, version=None)) == (
        428,
        "request.precondition_required",
    )
    assert _code(await _patch(api, neha, okra, {"names": names}, version=7)) == (
        412,
        "catalog.version_conflict",
    )
    immutable = await _patch(api, neha, okra, {"slug": "okra", "step": {"qty": 500, "unit": "g"}})
    assert _code(immutable) == (422, "catalog.field_immutable")
    assert immutable.json()["fields"] == ["slug", "step"]
    assert _code(await _patch(api, neha, uuid.uuid4(), {"names": names})) == (
        404,
        "catalog.product_not_found",
    )

    saved = await _patch(api, neha, okra, {"names": names, "shelf_life_h": 60})
    again = await _patch(api, neha, okra, {"shelf_life_h": 60}, version=2)

    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2 and saved.headers["etag"] == '"2"'
    assert saved.json()["name"] == "Okra" and saved.json()["recipes_recomputed"] == []
    assert again.json()["version"] == 2  # nothing changed, nothing saved
    [entry] = (
        await db_session.execute(
            select(AuditEntry).where(
                AuditEntry.action == "catalog.product_updated",
                AuditEntry.entity_id == str(okra),
            )
        )
    ).scalars()
    assert entry.before == {
        "names": {"en": "Okra (bhindi)", "hi": "भिंडी", "mr": "भेंडी"},
        "shelf_life_h": 72,
    }
    assert entry.after == {"names": names, "shelf_life_h": 60}
    assert (await _product(db_session, "bhindi")).updated_by is not None
    found = await api.get("/v1/search", params={"q": "okra"}, headers=neha)
    assert found.json()["items"][0]["slug"] == "bhindi"


async def test_a_restricted_tag_recomputes_the_rules_of_every_recipe_using_the_product(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    cabbage = await _id(db_session, "cabbage")
    using = select(RecipeIngredient.recipe_id).where(RecipeIngredient.product_id == cabbage)
    recipes = {
        r.slug: (r.version, r.rules["no_onion_garlic"])
        for r in (await db_session.execute(select(Recipe).where(Recipe.id.in_(using)))).scalars()
    }
    await db_session.commit()
    # Kobi bhaji and cabbage poriyal have no onion or garlic; the mixed veg soup has.
    affected = sorted(slug for slug, (_, ok) in recipes.items() if ok)
    assert affected == ["cabbage-poriyal", "kobi-bhaji"] and len(recipes) == 3

    tagged = await _patch(api, neha, cabbage, {"tags": ["onion_garlic"]})

    assert tagged.status_code == 200, tagged.text
    assert tagged.json()["recipes_recomputed"] == affected
    for slug in affected:
        recipe = (await db_session.execute(select(Recipe).where(Recipe.slug == slug))).scalar_one()
        await db_session.refresh(recipe)
        assert recipe.rules["no_onion_garlic"] is False and recipe.rules["jain_ok"] is False
        assert recipe.version == recipes[slug][0] + 1
    changed = await db_session.execute(
        select(OutboxEvent.payload).where(OutboxEvent.event_type == "catalog.recipe_changed")
    )
    assert len([p for p in changed.scalars() if p["changed"] == ["rules"]]) >= len(affected)
    audited = await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == "catalog.recipe_updated")
    )
    assert len(list(audited.scalars())) == len(affected)

    untagged = await _patch(api, neha, cabbage, {"tags": []}, version=2)
    assert untagged.json()["recipes_recomputed"] == affected


async def test_a_product_an_active_recipe_needs_cannot_be_deactivated(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    palak, mint = await _id(db_session, "palak"), await _id(db_session, "mint")
    await db_session.commit()

    in_use = await _patch(api, neha, palak, {"active": False})
    assert _code(in_use) == (409, "catalog.product_in_use")
    assert set(in_use.json()["recipes"]) >= {"palak-dal", "palak-paneer"}

    off = await _patch(api, neha, mint, {"active": False})
    assert off.status_code == 200 and off.json()["active"] is False
    assert (await api.get(f"/v1/products/{mint}", headers=neha)).status_code == 404
    inactive = await api.get(PRODUCTS, params={"active": "false"}, headers=neha)
    assert [p["slug"] for p in inactive.json()["items"]] == ["mint"]
    on = await _patch(api, neha, mint, {"active": True}, version=2)
    assert on.json()["active"] is True and on.json()["version"] == 3
    assert (await api.get(f"/v1/products/{mint}", headers=neha)).status_code == 200


async def test_the_history_comes_from_the_audit_log_newest_first(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    created = (await _create(api, neha, await _body(db_session))).json()
    await _patch(api, neha, created["id"], {"shelf_life_h": 24})
    await _patch(api, neha, created["id"], {"synonyms": []}, version=2)

    history = await api.get(f"{PRODUCTS}/{created['id']}/history", headers=neha)

    assert history.status_code == 200, history.text
    items = history.json()["items"]
    assert [i["action"] for i in items] == [
        "catalog.product_updated",
        "catalog.product_updated",
        "catalog.product_created",
    ]
    assert items[0]["changed"] == {
        "synonyms": {"from": [{"term": "chaulai", "lang": "hinglish"}], "to": []}
    }
    assert items[1]["changed"] == {"shelf_life_h": {"from": 48, "to": 24}}
    assert items[2]["changed"]["slug"] == {"from": None, "to": "chawli-leaves"}
    assert items[0]["by"]["name"] == "Neha Iyer"


async def test_only_admins_add_or_edit_products(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, vikram = await _as(api, google, PRIYA), await _as(api, google, VIKRAM)
    rekha = await _as(api, google, REKHA)
    okra = await _id(db_session, "bhindi")
    await db_session.commit()
    names = {"en": "Okra", "hi": "भिंडी", "mr": "भेंडी"}

    history = f"{PRODUCTS}/{okra}/history"
    assert _code(await api.get(history, headers=priya)) == (403, "auth.forbidden")
    for caller in (priya, vikram, rekha):
        if caller is not priya:
            assert (await api.get(history, headers=caller)).status_code == 200
        refused = (
            await _patch(api, caller, okra, {"names": names}),
            await _create(api, caller, await _body(db_session)),
        )
        assert [_code(answer) for answer in refused] == [(403, "auth.forbidden")] * 2


async def test_the_admin_list_filters_and_pages(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    await db_session.execute(
        Product.__table__.update().where(Product.slug == "mint").values(active=False)  # pyright: ignore[reportAttributeAccessIssue]
    )

    okra = await api.get(PRODUCTS, params={"q": "okra"}, headers=neha)
    fruit = await api.get(PRODUCTS, params={"kind": "fruit", "limit": 3}, headers=neha)
    rest = await api.get(
        PRODUCTS,
        params={"kind": "fruit", "limit": 3, "cursor": fruit.json()["next_cursor"]},
        headers=neha,
    )
    greens = await api.get(PRODUCTS, params={"category": "leafy-greens"}, headers=neha)
    inactive = await api.get(PRODUCTS, params={"active": "false"}, headers=neha)

    assert okra.status_code == 200, okra.text
    assert okra.headers["cache-control"] == "no-store"
    [bhindi] = okra.json()["items"]
    assert bhindi["slug"] == "bhindi" and bhindi["used_by_recipes"] == 3
    assert bhindi["nutrition"]["source"] == "ifct2017"
    assert bhindi["version"] == 1 and bhindi["updated_by"] is None  # never edited in the console
    assert {"term": "bhendi", "lang": "hinglish"} in bhindi["synonyms"]
    first, second = fruit.json()["items"], rest.json()["items"]
    assert len(first) == 3 and {p["kind"] for p in first + second} == {"fruit"}
    assert not {p["id"] for p in first} & {p["id"] for p in second}
    assert "mint" in [p["slug"] for p in greens.json()["items"]]  # inactive ones too
    assert [p["slug"] for p in inactive.json()["items"]] == ["mint"]


async def test_one_product_answers_with_its_version_as_the_etag(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    okra = await _id(db_session, "bhindi")
    await db_session.commit()

    one = await api.get(f"{PRODUCTS}/{okra}", headers=neha)
    missing = await api.get(f"{PRODUCTS}/{uuid.uuid4()}", headers=neha)

    assert one.status_code == 200 and one.headers["etag"] == '"1"'
    assert one.json()["names"]["en"] == "Okra (bhindi)" and one.json()["active"] is True
    assert _code(missing) == (404, "catalog.product_not_found")


async def test_who_may_read_the_admin_lists(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, vikram = await _as(api, google, PRIYA), await _as(api, google, VIKRAM)
    rekha = await _as(api, google, REKHA)
    await db_session.commit()

    categories = await api.get("/v1/categories", headers={**priya, "Accept-Language": "mr"})
    assert categories.status_code == 200
    assert categories.json()["items"][0]["slug"] == "leafy-greens"
    assert categories.json()["items"][0]["name"] == "पालेभाज्या"
    assert _code(await api.get(PRODUCTS, headers=priya)) == (403, "auth.forbidden")
    for reader in (vikram, rekha):
        assert (await api.get(PRODUCTS, headers=reader)).status_code == 200
