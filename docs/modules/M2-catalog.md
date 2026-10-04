# M2 — Catalog & Pricing

**Status:** Draft v1.1. §3.1 (products, search, prices, substitutes) is frozen for S1; recipes, festivals, kits and tiffin menus are refined when they are built (spec-first).
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje (also curate translations and search synonyms)
**Phase / Sprint:** P2 / S1
**Postgres schema:** `catalog` · **Python package:** `gharbasket.modules.catalog`

## 1. Purpose
Everything that can be planned or sold: vegetables, fruits, staples, cut-vegetable kits, tiffin menus, salads, recipes and their ingredients, nutrition data, the substitution graph, hub price lists, the festival/fasting calendar, multilingual names and search. M2 does not hold stock (M6) or customer orders (M4).

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a customer, I want to search "bhindi", "bhendi" or "okra" and find the same item so that language doesn't block me. | Search over FTS + trigram + synonyms returns the product in the top 3 for all variants. |
| US-2 | As a customer, I want to see three good substitutes for an item so that I can swap quickly. | `/products/{id}/substitutes` returns ranked items with price delta and reason. |
| US-3 | As a hub manager, I want to publish today's price list so that boxes are priced correctly. | Price list versioned by `valid_from`; prices in paise; audit-logged. |
| US-4 | As an admin, I want to manage recipes with ingredients, cook time and rules (Jain, fasting, diabetic) so that the meal planner can use them. | Recipe saved with rules JSON; ingredients reference products. |
| US-5 | As the meal planner, I want a festival/fasting calendar so that plans respect Navratri, Shravan, Ekadashi, Ramzan. | Calendar rows per date/region with rules. |
| US-6 | As a customer, I want product names in my language so that the app feels native. | Names stored as `{en, hi, mr}`; API returns the requested locale with fallback. |
| US-7 | As an admin, I want kits and tiffin menus defined from recipes so that kits stay consistent with ingredients. | Kit lines generated from recipe ingredients × servings. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/products` | Authenticated | — | Filters: kind, tags, hub; cursor pagination |
| GET | `/v1/products/{id}` | Authenticated | — | |
| GET | `/v1/search?q=` | Authenticated | — | Multilingual |
| GET | `/v1/products/{id}/substitutes` | Authenticated | — | Ranked, with price delta |
| GET | `/v1/recipes`, `/v1/recipes/{id}` | Authenticated | — | |
| GET | `/v1/prices?hub_id=&date=` | Authenticated | — | Current price list |
| GET | `/v1/festivals?from=&to=&region=` | Authenticated | — | |
| POST / PATCH | `/v1/admin/products[/{id}]` | admin | Required (POST) | |
| POST / PATCH | `/v1/admin/recipes[/{id}]` | admin | Required (POST) | |
| POST | `/v1/hubs/{id}/price-lists` | hub_manager (hub) | Required | Bulk upload |
| POST / PATCH | `/v1/admin/festivals` | admin | Required (POST) | |

### 3.1 Catalog contract (frozen for S1)
The Box, Substitute and add-on search screens and the backend build against these shapes. Changing them needs a PR reviewed by both Prathamesh and Yash. Every endpoint needs sign-in (Bearer or the web cookie, M1 §3.1).

**Language.** Names come back in the language of `Accept-Language` (`en`, `hi` or `mr`, with or without `-IN`), falling back to English. `names` always carries all three for screens that show more than one. Catalog reads answer with `Content-Language` and `Vary: Accept-Language`, and they are cacheable (`Cache-Control: private, max-age=300`), because the PWA's service worker caches `/v1/products` (`frontend/mobile/src/sw.js`). Prices and search are never cached.

**Hub and price.** Prices differ by hub. Unless `hub_id` is given, the caller's hub is their household's (M1). With no household and no `hub_id`, `price` is `null`. A price is for **one step** (the unit you add or remove in the app, e.g. one 250 g pack), in integer paise.

```
ProductOut : {
  "id": "<uuid>", "slug": "bhindi",
  "kind": "veg" | "fruit" | "staple" | "kit" | "tiffin" | "salad",
  "category": { "id": "<uuid>", "slug": "leafy-greens", "name": "Leafy greens" },
  "name": "Bhindi",                                   in the requested language
  "names": { "en": "Okra (bhindi)", "hi": "भिंडी", "mr": "भेंडी" },
  "step": { "qty": 250, "unit": "g", "label": "250 g" },
                                                      unit: g | kg | ml | l | piece | bunch | pack;
                                                      label is localised ("250 ग्राम")
  "tags": ["green", "high_fibre"],                    codes, not text
  "shelf_life_h": 72,
  "image_url": "<url> | null",
  "price": { "price_paise": 2000, "mrp_paise": 2400 | null, "hub_id": "<uuid>",
             "valid_from": "2026-11-02" } | null
}

GET /v1/products?kind=&category=<slug>&tag=&hub_id=&cursor=&limit=
  200 : Page[ProductOut]    alphabetical by the English name; limit ≤ 100, default 50;
                            only active products
GET /v1/products/{id}?hub_id=        200 ProductOut | 404 "catalog.product_not_found"
                                     {id} is the uuid; slugs are for URLs and fixtures

GET /v1/search?q=&hub_id=&limit=     q: 1 to 80 characters; limit ≤ 20, default 10
  200 : { "items": [ SearchItem ] }  best match first; no cursor (a search is one screen)
  SearchItem : { "product_id": "<uuid>", "slug": "bhindi", "name": "Bhindi",
                 "matched": "bhendi",               the spelling that matched, to highlight
                 "step": { "qty": 250, "unit": "g", "label": "250 g" },
                 "price_paise": 2000 | null }
  Matches English, Hindi and Marathi names, Devanagari and romanised spellings
  ("bhindi", "भिंडी", "bhendi", "okra", "lady finger"), ignoring case, accents and small typos.

GET /v1/products/{id}/substitutes?hub_id=&limit=      limit 1 to 10, default 3
  200 : { "items": [ Substitute ] }  best first
  Substitute : { "product_id", "slug", "name", "step",
                 "price_paise": 1800 | null,
                 "price_delta_paise": -200 | null,   substitute price − original price, per step
                 "score": 0.82,                       0 to 1
                 "reason_code": "similar_use" | "same_family" | "cheaper" | "in_season",
                 "reason_params": { } }               e.g. { "saves_paise": 200 } for cheaper
  404 "catalog.product_not_found"

GET /v1/prices?hub_id=&date=          hub_id required; date defaults to today (Asia/Kolkata)
  200 : { "id": "<uuid>", "hub_id", "valid_from": "2026-11-02",
          "items": [ { "product_id", "price_paise", "mrp_paise": 2400 | null } ] }
        the list with the latest valid_from on or before date; not paginated
  404 "catalog.price_list_not_found"

POST /v1/hubs/{hub_id}/price-lists    hub_manager of that hub, or admin; Idempotency-Key required
  request : { "valid_from": "2026-11-02",           today or later (Asia/Kolkata)
              "items": [ { "product_id": "<uuid>", "price_paise": 2000,
                           "mrp_paise": 2400 | null } ] }       1 to 1000 items, unique products
  201 : { "id", "hub_id", "valid_from", "items_count": 58 }
  publishes catalog.prices_published { price_list_id, hub_id, valid_from, items_count };
  audit-logged
```

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `catalog.product_not_found` | Unknown or inactive product id |
| 404 | `catalog.price_list_not_found` | No price list for the hub on or before the date |
| 409 | `catalog.price_list_exists` | The hub already has a list for that `valid_from` (publish a new date, or fix it in the console later) |
| 422 | `catalog.product_unknown` | A price-list item names a product that doesn't exist or isn't active; the problem carries `"product_ids"` |
| 422 | `catalog.price_above_mrp` | `price_paise` is greater than `mrp_paise` |
| 422 | `catalog.valid_from_past` | `valid_from` is before today in Asia/Kolkata |
| 403 | `auth.forbidden` | A hub manager publishing for another hub |
| 400 / 422 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.invalid` | As in M0 |

**For the mobile mocks** (current shapes → contract)

| Mock today | Contract |
|---|---|
| `product_id: "methi"` (slug) in box lines, search and substitutes | `product_id` is a UUID; the slug is in `slug` |
| `SearchItem.step` as a string ("250 g") | `step: { qty, unit, label }`; show `step.label` |
| `Substitute.qty_label` | `step.label` |
| Substitute without a score | `score` (0 to 1) added; keep sorting by the order returned |
| Language chosen by a mock parameter | `Accept-Language` from the app's language |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `catalog.products` | id (uuidv7), slug (unique), kind (veg, fruit, staple, kit, tiffin, salad), category_id, names jsonb `{en, hi, mr}`, step_qty, step_unit, shelf_life_h, nutrition jsonb null, tags[], image_key null, embedding vector(1024) null (with the ML service), active, data_source |
| `catalog.categories` | id, slug (unique), names jsonb, parent_id null |
| `catalog.recipes` | id, names jsonb, cuisine, cook_minutes, servings, rules jsonb, steps jsonb, embedding |
| `catalog.recipe_ingredients` | recipe_id, product_id, qty_per_serving, optional |
| `catalog.kits` | product_id, recipe_id, servings |
| `catalog.tiffin_menus` | id, hub_id, partner_id, date, meal, recipe_ids[], price_paise, status |
| `catalog.substitutions` | product_id, substitute_id, score, source (nutrition, cooccur, embedding, learned) |
| `catalog.price_lists` / `catalog.prices` | id, hub_id (no FK until M6), valid_from, unique (hub_id, valid_from), data_source / price_list_id, product_id, price_paise BIGINT (per step), mrp_paise BIGINT null |
| `catalog.festival_calendar` | date, region, code, rules jsonb |
| `catalog.search_terms` | product_id, term, lang (en, hi, mr, hinglish), source (name, synonym, transliteration); trigram index on the unaccented term |

## 5. Events
**Published:** `catalog.prices_published`, `catalog.product_changed`. **Consumed:** `intel.substitutions_learned` (refresh learned scores).

## 6. Jobs
| Job | Trigger | Notes |
|---|---|---|
| `embed_catalog` | On product/recipe change (batched) | bge-m3 embeddings via ML service |
| `build_search_terms` | On product change | indic-transliteration variants |

Both jobs are outbox consumers of `catalog.product_changed` (ADR-0021).

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `catalog:read` | All authenticated |
| `catalog:write` | admin |
| `prices:write` | hub_manager (hub), admin |
| `tiffin_menus:write` | kitchen_partner (own), hub_manager (approve) |

## 8. Screens
**Mobile:** product detail sheet, search, substitute sheet (shared with M3), tiffin menu browsing. **Console:** catalog and recipes editor, price lists, festival calendar, tiffin menu approvals.

## 9. Tests required
- Unit: locale fallback; kit line generation.
- Integration: multilingual search relevance fixtures (≥ 30 query/expected pairs); price list effective-date selection.
- Contract (§3.1): search finds every listed spelling in the top 3; a price is per step and in paise; substitutes never include the product itself; a hub manager cannot publish for another hub; the same Idempotency-Key replays a price list.
- Data quality: every recipe ingredient references an active product; every product has en/hi/mr names before publishing.

## 10. Dependencies
M0. The caller's hub comes from their household through `identity.api` (M1). Embeddings via `intel_gateway.api` (M10). No dependency on M3/M4 (they depend on M2).

## 11. Open questions
- Source of nutrition data (IFCT 2017 tables) and licensing for the report.
- Initial recipe count for the pilot (target ≥ 120 recipes across cuisines).
