# M2 — Catalog & Pricing

**Status:** Draft v1.3. §3.1 (products, search, prices, substitutes) and §3.2 (recipes, festivals) are frozen for S1; §3.3 (catalog admin) drafted 2026-10-09, frozen on approval; kits and tiffin menus are refined when they are built (spec-first).
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
| GET | `/v1/categories` | Authenticated | — | §3.3 |
| GET / POST / PATCH | `/v1/admin/products[/{id}]`, `…/{id}/history` | admin (write); admin, hub_manager (read) | Required (POST) | §3.3; `If-Match` on PATCH |
| GET / POST / PATCH | `/v1/admin/recipes[/{id}]`, `…/{id}/history` | admin (write); catalog_editor (PATCH of names and the fasting and diabetic flags only); admin, hub_manager, catalog_editor (read) | Required (POST) | §3.3; `If-Match` on PATCH |
| POST | `/v1/hubs/{id}/price-lists` | hub_manager (hub), admin | Required | Bulk upload (§3.1) |
| GET / PUT / DELETE | `/v1/hubs/{id}/price-lists[/{list_id}]` | hub_manager (hub), admin | — | List, read, replace today's or a future list, delete a future one (§3.3) |
| GET / POST / PATCH / DELETE | `/v1/admin/festivals[/{date}/{code}/{region}]` | admin | Required (POST) | §3.3 |

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

### 3.2 Recipes and festival contract (frozen for S1; `sides` and `is_side` added for S3)
**Addition for S3 (planner v1, M10 §3.2):** a recipe may carry `"sides": ["chapati"]` (slugs of side recipes it is eaten with) and `"is_side": true` for recipes that are never planned alone (chapati, rice). `RecipeOut` and the seed gain both fields; `GET /v1/recipes?is_side=` filters. Nothing else changes.

The meal planner (M10 M-C, spike S-4), the Week screen's dish names and the console read these. Same language, sign-in and price rules as §3.1.

**Rules a recipe follows.**
- **Calculated from its ingredients when it is saved**, so they cannot drift:
  - `diet`: `egg` if an ingredient is tagged `egg`, else `veg`;
  - `no_onion_garlic`: no ingredient tagged `onion_garlic`;
  - `jain_ok`: also no ingredient tagged `root`, and no egg.
- **Curated, because customs differ:** `fasting_ok` and `diabetic_ok` (reviewed by Rhythm Thakur and Manish Raje).

```
RecipeSummary : {
  "id": "<uuid>", "slug": "sabudana-khichdi",
  "name": "Sabudana khichdi", "names": { "en", "hi", "mr" },
  "cuisine": "maharashtrian" | "gujarati" | "north_indian" | "south_indian" | "bengali" | "common",
  "slots": ["breakfast", "lunch", "dinner"],         when it is usually eaten (at least one)
  "cook_minutes": 25, "servings": 4,
  "diet": "veg" | "egg",
  "rules": { "jain_ok": false, "no_onion_garlic": true, "fasting_ok": true, "diabetic_ok": false },
  "tags": ["one_pot", "light"] }
RecipeOut : RecipeSummary & {
  "ingredients": [ { "product_id", "slug", "name", "qty": 50, "unit": "g" | "ml" | "piece" | "bunch",
                     "label": "50 g", "optional": false } ],      qty per serving
  "cost_per_serving_paise": 1840 | null }   from the caller's hub prices (§3.1 hub rule); null if any
                                            ingredient has no price there

GET /v1/recipes?cuisine=&slot=&diet=&jain_ok=&fasting_ok=&cursor=&limit=
  200 : Page[RecipeSummary]    alphabetical by English name; limit ≤ 100, default 50; active only
GET /v1/recipes/{id}?hub_id=   200 RecipeOut | 404 "catalog.recipe_not_found"

GET /v1/festivals?from=&to=&region=
  from defaults to today (Asia/Kolkata), to to from + 30 days; at most 92 days ("request.invalid")
  region: "all" (default: every region) or a state code such as "maharashtra"
  200 : { "items": [ { "date": "2026-10-11", "code": "navratri", "name": "Navratri",
                       "region": "all",
                       "rules": { "fasting": true, "no_onion_garlic": true },
                       "verified": false } ] }     ordered by date; verified = checked against a
                                                   panchang (unverified dates are shown, flagged)
```
Weekly fasts that need no calendar (`tuesdays` in M1 `fasting_rules`) are applied by the planner from the weekday.

### 3.3 Catalog admin contract (drafted 2026-10-09, frozen on approval)
The console's Catalog page (products, recipes, festivals tabs) and Prices page, and the backend, build against this. Today `(console)/(admin)/catalog/page.jsx` is a placeholder and `/prices` is a "soon" item in `lib/nav.js`, so no mock has to change. Changing this needs a PR reviewed by both Prathamesh and Yash. Every endpoint needs sign-in with `X-Active-Role` (M1 §3.1). Writes are refused with 403 `auth.forbidden` unless the caller holds the permission in §7. Catalog data is not personal data, so the audit rows keep full before/after values.

**Versioning and history (all editable rows).**
- **Optimistic locking.** `catalog.products`, `catalog.recipes` and `catalog.price_lists` gain `version` (starts at 1) and `updated_by`. `GET` of one row sends `ETag: "<version>"`. A `PATCH` or `PUT` needs `If-Match`: without it the answer is 428 `request.precondition_required`, and a stale one is 412 `catalog.version_conflict`.
- **Audit.** Every write is audit-logged in the same transaction (`platform.audit`, action `catalog.<entity>_<created|updated|deleted>`, with the changed fields before and after).
- **History.** The console's History drawer reads `GET /v1/admin/products/{id}/history` and `GET /v1/admin/recipes/{id}/history`. Each answers `{ "items": [ { "at", "by": { "id", "name" }, "action", "changed": { "<field>": { "from", "to" } } } ] }`, newest first and at most 100. They are read from the audit log, so there is no separate history table.
- **Price lists.** Their history is their `valid_from` dates: a past list never changes.

**The seed and the console** (owner, 2026-10-09). Once the console ships, `just seed` only adds rows that are missing (by slug for products and recipes) and skips every row edited in the console (`updated_by` set): from the first console edit, the console is the source of truth for that row, and a later change to its seed file does not reach the database. New recipes keep arriving by file in the recipe-batch PRs. Festivals follow their own rule below (`source: manual` or `verified: true` are never overwritten).

**The `catalog_editor` role** (owner, 2026-10-09). A staff role without scope, granted by an admin (`POST /v1/admin/user-roles`, M1), for Rhythm Thakur and Manish Raje, who curate recipes. With it a caller may read the admin lists (`catalog:admin_read`) and `PATCH /v1/admin/recipes/{id}` with only `names`, `fasting_ok` and `diabetic_ok` (`recipes:curate`, §7). A body with any other field gets 403 `auth.forbidden` with `fields`, and nothing is saved. Every other write stays with admins.

**Change events.**
- **`catalog.product_changed { product_id, changed: [fields], active }`** is published on every create or update. Its consumers (§6) rebuild search terms and embeddings.
- **`catalog.recipe_changed { recipe_id, changed: [fields], active }`** is new. M10's planner catalog cache drops the recipe.
- **`catalog.prices_published`** is sent again when a list is replaced (`replaced: true`).

```
GET /v1/categories                    Authenticated; { "items": [ { "id", "slug", "name", "names", "parent_id" } ] }
                                      by sort order (filters in the app and the editor's picker)

AdminProduct : ProductOut (§3.1) without "price" & {
  "active": true, "version": 3,
  "nutrition": { "kcal", "protein_g", "fat_g", "carb_g", "fibre_g", "unit_g"?, "source" } | null,
  "synonyms": [ { "term": "lady finger", "lang": "en" | "hi" | "mr" | "hinglish" } ],
  "used_by_recipes": 4,                 active recipes with this product as an ingredient
  "data_source": "real" | "synthetic", "updated_at", "updated_by": { "id", "name" } | null }
ProductIn : { "slug", "kind": "veg" | "fruit" | "staple" | "salad", "category_id",
              "names": { "en", "hi", "mr" }, "step": { "qty": 250, "unit": "g" },
              "shelf_life_h": 72 | null, "tags": ["green"], "nutrition": … | null, "synonyms": [ … ] }

GET   /v1/admin/products?q=&kind=&category=&active=&cursor=&limit=    Page[AdminProduct]; inactive
                                      included unless active=true; q matches slug and the three names
GET   /v1/admin/products/{id}         200 AdminProduct (+ ETag) | 404 "catalog.product_not_found"
POST  /v1/admin/products              Idempotency-Key; ProductIn → 201 AdminProduct (version 1)
PATCH /v1/admin/products/{id}         If-Match; any ProductIn field except slug, kind and step, plus
                                      "active": false | true → 200 AdminProduct (version + 1)

AdminRecipe : RecipeOut (§3.2) without "cost_per_serving_paise" & {
  "active", "version", "is_side", "sides": ["chapati"],
  "steps": [ { "n": 1, "text": { "en", "hi"?, "mr"? } } ] | null,
  "reviews": { "fasting_ok": { "by": { "id", "name" }, "at" } | null,
               "diabetic_ok": { "by", "at" } | null },          who last set the curated flags
  "updated_at", "updated_by" }
RecipeIn : { "slug", "names", "cuisine", "slots": ["lunch"], "cook_minutes": 25, "servings": 4,
             "tags", "is_side": false, "sides": ["chapati"], "fasting_ok": true, "diabetic_ok": false,
             "ingredients": [ { "product_id", "qty": 50, "unit": "g", "optional": false } ],
             "steps": [ … ] | null }
GET   /v1/admin/recipes?q=&cuisine=&slot=&active=&is_side=&cursor=&limit=   Page[AdminRecipe] (no steps)
GET   /v1/admin/recipes/{id}          200 AdminRecipe (+ ETag) | 404 "catalog.recipe_not_found"
POST  /v1/admin/recipes               Idempotency-Key; RecipeIn → 201 AdminRecipe
PATCH /v1/admin/recipes/{id}          If-Match; any RecipeIn field except slug ("ingredients" and
                                      "steps" are replaced whole), plus "active" → 200 AdminRecipe

AdminFestival : { "date", "code", "region", "names", "rules": { "fasting", "no_onion_garlic" },
                  "verified": false, "source": "panchang" | "manual", "updated_at", "updated_by" }
GET    /v1/admin/festivals?from=&to=&region=       { "items": [AdminFestival] }; at most 366 days
POST   /v1/admin/festivals                         Idempotency-Key; { date, code, region, names, rules,
                                                   verified } → 201 (source "manual")
PATCH  /v1/admin/festivals/{date}/{code}/{region}  { names?, rules?, verified? } → 200; no If-Match
                                                   (a row is a few fields; last write wins, audited)
DELETE /v1/admin/festivals/{date}/{code}/{region}  204; only for dates after today

PriceListSummary : { "id", "hub_id", "valid_from", "items_count", "version",
                     "published_by": { "id", "name" } | null, "created_at", "updated_at",
                     "editable": true }                 valid_from today or later (IST)
GET    /v1/hubs/{hub_id}/price-lists?from=&to=     { "items": [PriceListSummary] } newest first;
                                                   default the last 30 days and every future list
GET    /v1/hubs/{hub_id}/price-lists/{id}          200 PriceListSummary & { "items": [ { "product_id",
                                                   "slug", "name", "step", "price_paise", "mrp_paise" } ] }
                                                   (+ ETag)
PUT    /v1/hubs/{hub_id}/price-lists/{id}          If-Match; { "items": [ … ] } as in the §3.1 POST;
                                                   replaces every item → 200 (version + 1)
DELETE /v1/hubs/{hub_id}/price-lists/{id}          204; only a list whose valid_from is after today
```

**Validation rules**
- **Slug.** `^[a-z0-9]+(-[a-z0-9]+)*$`, 2 to 60 characters, unique per table; otherwise 409 `catalog.slug_taken`. Slugs, `kind` and the product's `step` **never change** after creation, and sending them in a `PATCH` gets 422 `catalog.field_immutable` with `fields`:
  - slugs name fixtures, URLs and recipe `sides`;
  - prices, cycle lines and order lines are all per step, so a different step is a different product. Create the new product and deactivate the old one.
- **Names.** `en`, `hi` and `mr` are all required, 1 to 80 characters each; otherwise 422 `catalog.names_incomplete` with `missing`. This turns the §9 data-quality rule into an API rule.
- **Kinds.** The admin API creates `veg`, `fruit`, `staple` and `salad`. Kits come from recipes, and tiffins from kitchens' menus (M8), each in their own flow. Any other kind gets 422 `catalog.kind_not_editable`.
- **Vocabularies.**
  - `step.unit` is one of `g, kg, ml, l, piece, bunch, pack` (ADR-0026 §1), and `step.qty` is 1 to 100,000.
  - `tags` come from `catalog.TAGS` (the codes the seed uses, including the restricted `root`, `onion_garlic`, `egg` and `dairy`); otherwise 422 `catalog.tag_unknown` with `tags`.
  - `category_id` must exist; otherwise 422 `catalog.category_unknown`.
  - `shelf_life_h` is 1 to 8,760 or null.
  - `nutrition` values are ≥ 0, and `source` is one of `ifct2017`, `nin_nvif`, `label`, `manual`.
  - `synonyms`: at most 30 per product, each term 1 to 60 characters. They are stored as `catalog.search_terms` rows with `source: synonym`, and `build_search_terms` regenerates the `name` and `transliteration` rows.
- **Restricted tags and recipes.** Changing a product's restricted tags recomputes, in the same transaction, the derived rules (`diet`, `no_onion_garlic`, `jain_ok`, §3.2) of every recipe that uses it. Each recipe whose rules change gets version + 1 and a `catalog.recipe_changed`. The answer carries `"recipes_recomputed": ["<slug>", …]` so the editor can show it.
- **Deactivating a product.** It is refused with 409 `catalog.product_in_use` (with `recipes`) while the product is a non-optional ingredient of an active recipe. Once inactive, the product disappears from `GET /v1/products`, search, substitutes, new price lists and the planner. Cycles and orders that already hold it keep their lines.
- **Recipe ingredients.**
  - 1 to 30 distinct active products; otherwise 422 `catalog.product_unknown` with `product_ids`.
  - `qty` per serving is > 0 with at most 2 decimals.
  - The unit must fit the product's step: `g` for `g`/`kg`, `ml` for `ml`/`l`, `piece` for `piece`/`pack`, `bunch` for `bunch`; otherwise 422 `catalog.unit_mismatch` with `product_ids`.
- **Recipe rules.**
  - The derived rules (`diet`, `no_onion_garlic`, `jain_ok`) are never accepted in the body (422 `request.invalid`).
  - The curated `fasting_ok` and `diabetic_ok` record the caller and time in `reviews` whenever their value changes. Admins and catalog editors set them.
  - `cook_minutes` is 5 to 240, `servings` 1 to 12, and `slots` 1 to 3 of `breakfast, lunch, dinner`.
- **Sides.** `sides` must name active recipes with `is_side: true`, and a side has no sides of its own; otherwise 422 `catalog.side_invalid`. Deactivating a side that an active recipe lists is 409 `catalog.recipe_in_use` with `recipes`.
- **Festivals.**
  - `code` is `^[a-z0-9_]{2,40}$`; `region` is `all` or a state code from `catalog.REGIONS`; `date` is within two years of today.
  - A duplicate (date, code, region) is 409 `catalog.festival_exists`.
  - Rows with `source: manual` or `verified: true` are never overwritten by the panchang generator or `just seed`.
  - Deleting today or an earlier date is 409 `catalog.festival_past`: plans may already rest on it.
- **Price lists.**
  - **Same rules as the §3.1 POST.** That covers unique and active products, price ≤ MRP, and a hub manager only for their own hub.
  - **Past lists are locked.** A list whose `valid_from` is before today is never changed (409 `catalog.price_list_locked`), and so cannot be deleted either. Orders and cycles were priced from it.
  - **Editing today's list.** It changes only prices read after the save: lines already on a cycle or an order keep their price (M3/M4 freeze them).
  - **CSV.** The console reads the CSV in the browser and sends JSON, so there is no CSV endpoint.

**Error codes** (in addition to §3.1)

| Status | Code | When |
|---|---|---|
| 404 | `catalog.product_not_found`, `catalog.recipe_not_found`, `catalog.festival_not_found`, `catalog.price_list_not_found` | Unknown id or key |
| 409 | `catalog.slug_taken` | The slug is used |
| 409 | `catalog.product_in_use`, `catalog.recipe_in_use` | Deactivating something an active recipe needs (`recipes`) |
| 409 | `catalog.festival_exists`, `catalog.festival_past` | Duplicate; deleting a day that has started |
| 409 | `catalog.price_list_locked` | Changing or deleting a list that took effect before today |
| 412 | `catalog.version_conflict` | Stale `If-Match` |
| 422 | `catalog.field_immutable` | slug, kind or step in a `PATCH` (`fields`) |
| 422 | `catalog.names_incomplete` | A language missing (`missing`) |
| 422 | `catalog.kind_not_editable`, `catalog.tag_unknown`, `catalog.category_unknown`, `catalog.unit_mismatch`, `catalog.side_invalid` | As above |
| 428 | `request.precondition_required` | `PATCH`/`PUT` without `If-Match` |

**For the console** (pages → endpoints)

| Page (console) | Reads | Writes |
|---|---|---|
| Catalog → Products (table, filters, edit drawer, History) | `/v1/admin/products`, `/v1/categories`, `…/history` | `POST`/`PATCH /v1/admin/products` |
| Catalog → Recipes (ingredient editor with product search, rules shown read-only, review flags) | `/v1/admin/recipes`, `/v1/admin/products?active=true` | `POST`/`PATCH /v1/admin/recipes` |
| Catalog → Festivals (calendar grid, "verified" toggle) | `/v1/admin/festivals` | `POST`/`PATCH`/`DELETE /v1/admin/festivals/…` |
| Prices (hub picker for admins; a hub manager sees their hub; CSV import, copy from the latest list) | `/v1/hubs/{id}/price-lists`, `/v1/prices` | `POST`/`PUT`/`DELETE …/price-lists` |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `catalog.products` | id (uuidv7), slug (unique), kind (veg, fruit, staple, kit, tiffin, salad), category_id, names jsonb `{en, hi, mr}`, step_qty, step_unit, shelf_life_h, nutrition jsonb `{kcal, protein_g, fat_g, carb_g, fibre_g, unit_g?, source}` per 100 g (IFCT 2017), tags[], image_key null, embedding vector(1024) null (with the ML service), active, data_source |
| `catalog.categories` | id, slug (unique), names jsonb, parent_id null |
| `catalog.recipes` | id (uuidv7), slug (unique), names jsonb `{en, hi, mr}`, cuisine, slots[], cook_minutes, servings, diet, rules jsonb `{jain_ok, no_onion_garlic, fasting_ok, diabetic_ok}`, tags[], steps jsonb null, embedding null (later), active, data_source |
| `catalog.recipe_ingredients` | recipe_id, product_id, qty_per_serving, unit (g, ml, piece, bunch), optional; primary key (recipe_id, product_id) |
| `catalog.kits` | product_id, recipe_id, servings |
| `catalog.tiffin_menus` | id, hub_id, partner_id, date, meal, recipe_ids[], price_paise, status |
| `catalog.substitutions` | product_id, substitute_id, score, source (nutrition, cooccur, embedding, learned) |
| `catalog.price_lists` / `catalog.prices` | id, hub_id (no FK until M6), valid_from, unique (hub_id, valid_from), data_source / price_list_id, product_id, price_paise BIGINT (per step), mrp_paise BIGINT null |
| `catalog.festival_calendar` | date, code, region (primary key together), names jsonb, rules jsonb `{fasting, no_onion_garlic}`, verified |
| `catalog.search_terms` | product_id, term, lang (en, hi, mr, hinglish), source (name, synonym, transliteration); trigram index on the unaccented term |

Columns added by §3.3 (one migration): `version` and `updated_by` on `products`, `recipes` and `price_lists` (plus `updated_at` on `price_lists`); `fasting_reviewed_by`, `fasting_reviewed_at`, `diabetic_reviewed_by` and `diabetic_reviewed_at` on `recipes`; `source` (panchang, manual), `updated_by` and `updated_at` on `festival_calendar`.

## 5. Events
**Published:** `catalog.prices_published` (gains `replaced: bool`, §3.3), `catalog.product_changed { product_id, changed, active }`, `catalog.recipe_changed { recipe_id, changed, active }` (new, §3.3). **Consumed:** `intel.substitutions_learned` (refresh learned scores).

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
| `recipes:curate` | catalog_editor, admin (a recipe's `names`, `fasting_ok` and `diabetic_ok`; §3.3) |
| `prices:write` | hub_manager (hub), admin |
| `tiffin_menus:write` | kitchen_partner (own), hub_manager (approve) |
| `catalog:admin_read` | admin, hub_manager (the admin lists in §3.3, to build price lists), catalog_editor |

## 8. Screens
**Mobile:** product detail sheet, search, substitute sheet (shared with M3), tiffin menu browsing. **Console:** catalog and recipes editor, price lists, festival calendar, tiffin menu approvals.

## 9. Tests required
- Unit: locale fallback; kit line generation.
- Integration: multilingual search relevance fixtures (≥ 30 query/expected pairs); price list effective-date selection.
- Contract (§3.1): search finds every listed spelling in the top 3; a price is per step and in paise; substitutes never include the product itself; a hub manager cannot publish for another hub; the same Idempotency-Key replays a price list.
- Data quality: every recipe ingredient references an active product; every product has en/hi/mr names before publishing.

- Catalog admin (§3.3): every write is audited and bumps `version`; stale `If-Match` 412, missing 428; slug, kind and step are immutable; a missing language is refused; a restricted tag change recomputes the rules of every recipe using the product, in one transaction; deactivating an ingredient of an active recipe is refused; a past price list cannot be replaced or deleted, a future one can; a hub manager cannot touch another hub's lists or any product; a catalog editor can change a recipe's names and fasting and diabetic flags and nothing else (403 with `fields`); the seed adds missing rows and never overwrites a console-edited row or a manual/verified festival.
## 10. Dependencies
M0. The caller's hub comes from their household through `identity.api` (M1); M1 adds the `catalog_editor` staff role (§3.3). Embeddings via `intel_gateway.api` (M10). No dependency on M3/M4 (they depend on M2).

## 11. Open questions
- **Decided (Oct 2026):** nutrition per 100 g comes from IFCT 2017 (ICMR-NIN) through the `@ifct2017/compositions` transcription, mapped by hand per product (`data/external/nutrition/`); five foods IFCT lacks use the NIN Nutritive Value of Indian Foods. Daily targets per age band are ICMR-NIN RDA 2020 (`rda.json`, a hypothesis until ratified). `ProductOut.nutrition` and the planner catalog's `nutrition_per_serving` carry it.
- Initial recipe count for the pilot (target ≥ 120 recipes across cuisines).
- **Decided by the owner (2026-10-09):**
  - ~~Catalog admin (§3.3): would `just seed` overwrite console edits?~~ Decided: `just seed` only adds missing rows and skips rows edited in the console; the console is the source of truth once a row is edited (§3.3).
  - ~~Who may set the curated `fasting_ok` / `diabetic_ok` flags?~~ Decided: a `catalog_editor` role for Rhythm Thakur and Manish Raje, who set a recipe's fasting and diabetic flags and its names (§3.3, §7).
  - ~~Product images in §3.3?~~ Decided: later. `image_url` stays null and the console has no image field until a public bucket (or CDN path) and a `catalog_image` purpose exist in M0.
