# M2 — Catalog & Pricing

**Status:** Draft v1 — refine at phase start (spec-first)
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

## 4. Tables owned
| Table | Key columns |
|---|---|
| `catalog.products` | id, kind (veg, fruit, staple, kit, tiffin, salad), names jsonb, unit, shelf_life_h, nutrition jsonb, tags[], embedding vector(1024), active |
| `catalog.categories` | id, names jsonb, parent_id |
| `catalog.recipes` | id, names jsonb, cuisine, cook_minutes, servings, rules jsonb, steps jsonb, embedding |
| `catalog.recipe_ingredients` | recipe_id, product_id, qty_per_serving, optional |
| `catalog.kits` | product_id, recipe_id, servings |
| `catalog.tiffin_menus` | id, hub_id, partner_id, date, meal, recipe_ids[], price_paise, status |
| `catalog.substitutions` | product_id, substitute_id, score, source (nutrition, cooccur, embedding, learned) |
| `catalog.price_lists` / `catalog.prices` | id, hub_id, valid_from / price_list_id, product_id, price_paise, mrp_paise |
| `catalog.festival_calendar` | date, region, code, rules jsonb |
| `catalog.search_terms` | product_id, term, lang |

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
- Data quality: every recipe ingredient references an active product; every product has en/hi/mr names before publishing.

## 10. Dependencies
M0. Embeddings via `intel_gateway.api` (M10). No dependency on M3/M4 (they depend on M2).

## 11. Open questions
- Source of nutrition data (IFCT 2017 tables) and licensing for the report.
- Initial recipe count for the pilot (target ≥ 120 recipes across cuisines).
