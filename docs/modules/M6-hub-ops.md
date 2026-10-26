# M6 — Hub Operations

**Status:** Draft v3 (with [ADR-0026](../adr/0026-operations-contract.md) and [ADR-0027](../adr/0027-operations-contract-amendments.md)). §3.1 (hubs, service areas, vendors) and §3.2 (packing) are frozen for S4 and built. §3.3 (procurement plans and purchase orders), §3.4 (goods receipt, lots and stock) and §3.5 (waste, the surplus cascade and kept items) were refined on 2026-10-09 and are **frozen on approval**; §3.5's kept items and returned bags are built with M7's redelivery PR (ADR-0027 §2). §3.6 (shortages today, for the packer app) was drafted on 2026-10-25, frozen on approval.
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P3 / S4
**Postgres schema:** `hub_ops` · **Python package:** `gharbasket.modules.hub_ops`

## 1. Purpose
Everything inside the hub (the franchise fulfilment centre): **hubs** and their service areas (M6 is the owner of hub ids, ADR-0026), vendors, procurement plans (committed demand plus a forecast buffer), purchase orders, goods receipt with QC grading, inventory lots with expiry, **packing tasks** with bag labels and shortage handling, waste logging and the surplus cascade. M6 moves orders only through `orders.api` (M4 owns the order and its states), takes forecasts from M10 through `intel_gateway.api`, and hands crates to M7. It does not route, pay anyone or list products for sale.

**Build order for MS-B:** (a) hubs, service areas, vendors → (b) packing → (c) procurement plan and purchase orders → (d) goods receipt and lots → (e) waste and surplus cascade. (a) and (b) are frozen and built; (c)–(e) are frozen on approval below. They do not wait for the ML chain: with no forecast every plan line uses the rule-based buffer, and the forecast (PRs #118/#122) changes a line's `basis`, never a shape. Surplus is settled by the hub manager until M8 and M9 consume offers.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As an admin, I want to create a hub with its service area so that societies, prices, slots and staff can point at a real hub. | Hub with location, polygon and day timings; `hub_ops.hub_created`; the seed hub exists. |
| US-2 | As a hub manager, I want a vendor list so that purchase orders go to the right people. | Vendors per hub; one default vendor; deactivate instead of delete. |
| US-3 | As a packer, I want a packing queue in loading order so that crates are loaded in the right sequence. | Tasks built at T 05:00; ordered by route stop when routes exist, else by slot, society, tower, flat. |
| US-4 | As a packer, I want to scan a bag, pick each line and flag shortages so that customers are treated fairly. | Start → pick → complete moves the order `locked → picking → packed`; a shortage refunds or substitutes through `orders.api.mark_shortage`. |
| US-5 | As a packer on a weak network, I want my taps saved and sent later so that I never pack twice. | Every action has an `Idempotency-Key` made when tapped; replays return the first answer. |
| US-6 | As a hub manager, I want a procurement plan right after cut-off so that I buy only what's needed. | At T-1 21:10 a plan lists committed qty, forecast q50/qτ (or the rule-based buffer), order qty and vendor per item. |
| US-7 | As a hub manager, I want to adjust lines and send purchase orders so that vendors know what to deliver. | Edits need a reason and are audited; one PO per vendor, sent by a WhatsApp link or printed. |
| US-8 | As a hub manager, I want to record goods received with weight and grade so that inventory and vendor scores are accurate. | A goods receipt creates lots with a best-before; rejects go back to the vendor and feed vendor scores. |
| US-9 | As a hub manager, I want surplus routed through the cascade so that waste is minimised. | At 12:30 lots that cannot be packed tomorrow are offered to kitchens, then NGOs, then compost; each step logged. |
| US-10 | As an analyst, I want kg wasted per 100 kg sold so that the zero-waste claim is measurable. | Computed per hub and day from waste logs and packed quantities. |
| US-11 | As a hub manager, I want the non-perishables of a box missed twice kept for the family so that they ride on its next delivery (ADR-0027). | Scanning the returned bag keeps them per household; packing picks them from the shelf; perishables go to the cascade. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/hubs` | hub staff (their hubs), admin | — | §3.1 |
| GET | `/v1/hubs/{id}` | hub staff (that hub), admin | — | §3.1; ETag |
| POST | `/v1/admin/hubs` | admin | Required | §3.1 |
| PATCH | `/v1/admin/hubs/{id}` | admin | `If-Match` | §3.1 |
| GET / POST | `/v1/hubs/{id}/vendors` | hub_manager (hub), admin | Required (POST) | §3.1 |
| PATCH | `/v1/vendors/{id}` | hub_manager (hub), admin | `If-Match` | §3.1 |
| GET | `/v1/hubs/{id}/packing-tasks?date=&status=&society_id=` | packer, hub_manager (hub) | — | §3.2; cursor |
| GET | `/v1/hubs/{id}/packing-summary?date=` | packer, hub_manager, franchise_owner (hub) | — | §3.2 |
| POST | `/v1/hubs/{id}/packing-tasks/build` | hub_manager (hub) | Required | §3.2; same work as the 05:00 job |
| POST | `/v1/hubs/{id}/packing-tasks/next` | packer (hub) | Required | §3.2; claims the next task |
| GET | `/v1/packing-tasks/{id}` | packer, hub_manager (hub) | — | §3.2 |
| POST | `/v1/packing-tasks/{id}/start` · `/pick` · `/shortage` · `/complete` | packer (hub), hub_manager | Required | §3.2; offline-safe |
| POST | `/v1/packing-tasks/{id}/release` | hub_manager (hub) | Required | §3.2 |
| GET | `/v1/hubs/{id}/shortages?date=` | packer, hub_manager, franchise_owner (hub), admin | — | §3.6; items short today and the bags they fall on |
| GET | `/v1/hubs/{id}/procurement-plans?date=` · `/v1/procurement-plans/{id}` | hub_manager, franchise_owner (hub), admin | — | §3.3; ETag |
| POST | `/v1/hubs/{id}/procurement-plans/build` | hub_manager (hub), admin | Required | §3.3; same work as the 21:10 job |
| PATCH | `/v1/procurement-plans/{id}/lines/{product_id}` | hub_manager (hub), admin | `If-Match` | §3.3; reason required for a quantity |
| POST | `/v1/procurement-plans/{id}/purchase-orders` | hub_manager (hub), admin | Required | §3.3; (re)generates draft POs |
| GET | `/v1/hubs/{id}/purchase-orders?date=&status=` · `/v1/purchase-orders/{id}` | hub_manager, franchise_owner (hub), admin | — | §3.3; ETag |
| POST | `/v1/purchase-orders/{id}/send` · `/cancel` | hub_manager (hub), admin | Required | §3.3 |
| POST | `/v1/hubs/{id}/goods-receipts` | hub_manager, packer (hub) | Required | §3.4 |
| GET | `/v1/hubs/{id}/goods-receipts?date=` · `/v1/goods-receipts/{id}` | hub_manager, packer, franchise_owner (hub), admin | — | §3.4 |
| GET | `/v1/hubs/{id}/inventory` · `/v1/lots/{id}` | hub_manager, packer, franchise_owner (hub), admin | — | §3.4 |
| POST | `/v1/lots/{id}/count` | hub_manager (hub), admin | Required | §3.4; absolute stock count |
| GET / POST | `/v1/hubs/{id}/waste-logs` | hub_manager, packer (hub); franchise_owner, admin read | Required (POST) | §3.5 |
| GET | `/v1/hubs/{id}/surplus-offers?date=&status=` | hub_manager, packer, franchise_owner (hub), admin | — | §3.5 |
| POST | `/v1/surplus-offers/{id}/settle` | hub_manager (hub), admin | Required | §3.5 |
| POST | `/v1/hubs/{id}/surplus/run` | hub_manager (hub), admin | Required | §3.5; same work as the 12:30 job |
| POST | `/v1/hubs/{id}/returned-bags` | hub_manager, packer (hub) | Required | §3.5; a bag back after a second miss |
| GET | `/v1/hubs/{id}/kept-items?status=` | hub_manager, packer, franchise_owner (hub), admin | — | §3.5 |
| POST | `/v1/hubs/{id}/kept-items/{item_id}/lost` | hub_manager (hub), admin | Required | §3.5; refunds the item (M4 §3.8) |
| GET | `/v1/hubs/{id}/waste-summary?from=&to=` | hub_manager, packer, franchise_owner (hub), admin | — | §3.5 |
| POST | `/v1/internal/jobs/build_packing_tasks` · `build_procurement_plan` · `refresh_procurement_plan` · `surplus_cascade` · `compute_vendor_scores` | operator token | — | §6 |

**For every section.** Every endpoint needs sign-in (M1 §3.1). Hub staff act in one hub: the grant `packer:<hub_id>` or `hub_manager:<hub_id>` (`X-Active-Role`, M0 permissions); a request for another hub is 403 `auth.forbidden`. Dates are IST calendar dates (`date` defaults to today in IST and may be today − 7 … today + 2, else 422 `hub_ops.date_out_of_range`); clock times are IST `HH:MM`; timestamps are UTC. Money is integer paise. Quantities shown to packers are **steps** (as on the order line); operations quantities (§3.3–§3.5) are `NUMERIC(10,3)` in the product's **operations unit** (`kg`, `l`, `piece`, `bunch` or `pack`; ADR-0026 §1). Reads send `Cache-Control: no-store`.

### 3.1 Hubs, service areas and vendors (frozen for S4)
The console's Hubs admin and Vendors screens, the hub switcher, and every module that stores a hub id build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
GeoPolygon : { "type": "Polygon", "coordinates": [ [ [73.05, 19.02], …, [73.05, 19.02] ] ] }
             GeoJSON, WGS 84, [lng, lat]; one closed ring (first point = last), 4 to 500 points,
             no holes, not self-intersecting (ST_IsValid)

Hub : { "id": "<uuid>", "slug": "kharghar", "name": "Kharghar hub",
        "address": "Plot 12, Sector 19, Kharghar, Navi Mumbai 410210",
        "location": { "lat": 19.0330, "lng": 73.0297 },
        "service_area": GeoPolygon | null,              null until an admin draws it
        "status": "active" | "paused",                   paused: no jobs run for it
        "timings": { "receiving": "04:00", "packing": "05:00",
                     "dispatch": "06:30", "surplus": "12:30" },   the hub's day (IST); display and
                                                         job planning only; the cut-off is not here
        "procurement_buffer_pct": 10,                    0 to 50; the rule-based buffer (§3.3)
        "pre_live_box_fee_paise": 3000,                  0 to 10,000; the delivery fee on each box
                                                         while a society is not live (added
                                                         2026-10-10, M9 §3.6 and §11)
        "version": 1, "created_at", "updated_at" }
HubSummary : { "id", "slug", "name", "status" }

GET /v1/hubs                    200 { "items": [ HubSummary ] }   admin: every hub; hub staff: the
                                hubs in their grants; by name
GET /v1/hubs/{id}               200 Hub (ETag = version) | 404 "hub_ops.hub_not_found"
POST /v1/admin/hubs             admin; Idempotency-Key
  request : { "slug", "name", "address", "location", "service_area"?, "timings"?,
              "procurement_buffer_pct"?, "pre_live_box_fee_paise"? }   slug: lower-case letters, digits and "-", 3 to 40,
                                                unique, never changes; name 3 to 80; address up to 300
  201 : Hub (status active; timings default to the values above); publishes hub_ops.hub_created
PATCH /v1/admin/hubs/{id}       admin; If-Match (the hub's version)
  request : any of { "name", "address", "location", "service_area" (null clears it), "status",
                     "timings", "procurement_buffer_pct", "pre_live_box_fee_paise" }
  200 : Hub (version + 1); publishes hub_ops.hub_updated { hub_id, changed: ["service_area", …] }

Vendor : { "id", "hub_id", "name": "Shinde Traders",
           "kind": "apmc_trader" | "fpo" | "farmer" | "wholesaler",
           "phone": "+919800002001" | null,             E.164
           "whatsapp": true,                            POs can be shared on WhatsApp
           "is_default": false,                         at most one per hub: suggested for any
                                                        product with no purchase history
           "notes": "…" | null, "active": true,
           "score": 0.82 | null,                        last weekly score (§3.4); null until scored
           "version": 1, "created_at" }

GET  /v1/hubs/{id}/vendors?active=true      200 { "items": [ Vendor ] }   by name
POST /v1/hubs/{id}/vendors                  Idempotency-Key
  request : { "name", "kind", "phone"?, "whatsapp"?, "is_default"?, "notes"? }   name 2 to 80,
            unique per hub (case-insensitive); notes up to 500
  201 : Vendor                              is_default: true clears it on the hub's other vendor
PATCH /v1/vendors/{id}                      If-Match; any field above, plus "active"
  200 : Vendor                              vendors are deactivated, never deleted
```

**The seed hub.** `just seed` upserts the Kharghar hub with the fixed id `0199a000-0000-7000-8000-00000000a001` (`SEED_HUB_ID`), a service area around the seeded societies and one default vendor, so the hub ids already in identity, community, catalog, subscriptions and orders point at a real row.

**Hub ids elsewhere** (ADR-0026 §5) are plain UUIDs with no foreign key; M6 publishes `hub_ops.hub_created` and serves `hub_ops.api.get_hub`. A society's polygon outside its hub's service area is allowed (the console warns); no address is refused because of a hub's area in v1.

**As built (PR a, hubs and vendors).** Details the shapes above leave open, settled in the code:
- `timings` may be sent in part (create and `PATCH`); the missing times keep their current or default values, and the merged day must still be in order.
- A `PATCH` that changes nothing (every value equal to the stored one) keeps the version and publishes nothing; `hub_updated.changed` lists only fields whose value changed. A field that may not be null (everything except a hub's `service_area` and a vendor's `phone` and `notes`) sent as `null` is 422 `request.invalid`.
- A body that is not even the GeoJSON shape (`type` other than `"Polygon"`, coordinates not nested number arrays) is 422 `request.invalid`; everything else in the `GeoPolygon` rules (ring count, 4 to 500 points, `[lng, lat]` in India, closed, `ST_IsValid`) is `hub_ops.area_invalid`.
- An inactive vendor is never the default: deactivating the default vendor clears `is_default` (the hub then has none until one is chosen), and `is_default: true` on an inactive vendor is ignored. Making a vendor the default bumps the previous default's version too.
- Hub staff of another hub get 404 `hub_ops.hub_not_found` on `GET /v1/hubs/{id}`, 403 `auth.forbidden` on that hub's vendor list and `POST`, and 404 `hub_ops.vendor_not_found` on `PATCH` of its vendors. `POST` and `PATCH` answers carry the `ETag` as well. Hub creates and changes go to `platform.audit_log`.
- `score` is `null` until vendor scores exist (§3.4).
- The seed hub is `kharghar`, service area a box around the seed societies (lng 73.02–73.13, lat 18.99–19.05), default vendor "Shinde Traders" (`apmc_trader`).

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `hub_ops.hub_not_found` | Unknown hub id, or a hub outside the caller's grants |
| 404 | `hub_ops.vendor_not_found` | Unknown vendor, or another hub's |
| 409 | `hub_ops.slug_taken` | A hub with that slug exists |
| 409 | `hub_ops.vendor_name_taken` | A vendor of that hub has the name |
| 412 | `request.version_mismatch` | `If-Match` is not the current version |
| 422 | `hub_ops.area_invalid` | Not a GeoJSON polygon as above |
| 422 | `hub_ops.timings_invalid` | Not `HH:MM`, or not in the order receiving < packing < dispatch < surplus |
| 422 | `hub_ops.date_out_of_range` | A `date` outside today − 7 … today + 2 (§3.2–§3.5) |
| 403 | `auth.forbidden` | A role without the permission, or hub staff of another hub |
| 400 / 422 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.invalid` | As in M0 |

### 3.2 Packing (frozen for S4)
The packer screens (Queue, Pick/Pack, Shortage) in the mobile app, the console's packing board and the backend build against this. Same review rule as §3.1.

**What a task is.** One task per order to deliver: box and add-on orders (`kind` `box` or `addon`) in `locked` for the hub and date; tiffins are cooked by kitchens (M8) and never packed here. A task has one or more bags (one by default), all with the same label.

```
PackingTask : {
  "id", "hub_id", "order_id", "order_kind": "box" | "addon", "delivery_date",
  "slot": { "id", "label", "start", "end" },
  "society": { "id", "name" },
  "flat_label": "B-402",                    tower label + flat (identity.api.delivery_labels,
                                            community.api); no customer name or phone
  "seq": 14,                                position in the hub's queue for the date
  "route": { "route_id", "stop_seq": 3 } | null,   set when M7 publishes routes
  "status": "queued" | "picking" | "packed",
  "packer": { "user_id", "name" } | null,   who holds the task
  "bag_qr": "GB1:0199a0000000700080000000000000ab",  printed on the bag label: GB1: + the order
                                            id without hyphens; the captain scans it at the door
  "bags": 1, "crate_no": 2 | null,          crate within the society, set at complete
  "lines_total": 12, "lines_done": 7,
  "started_at": "…" | null, "packed_at": "…" | null, "version": 4 }

PackingLine : {
  "product_id", "slug", "name",             the ordered product; name in Accept-Language
  "step": { "qty": 250, "unit": "g", "label": "250 g" },
  "qty": 2,                                 steps ordered
  "picked_qty": 2,                          steps in the bag (of the substitute, if any)
  "short_qty": 0,                           steps refunded
  "substitute": { "product_id", "slug", "name", "step" } | null,
  "done": true }                            picked_qty + short_qty = qty

PackingTaskDetail : PackingTask & { "lines": [ PackingLine ] }   lines by catalog category, then name

GET /v1/hubs/{id}/packing-tasks?date=&status=&society_id=&cursor=&limit=
  200 : Page[PackingTask]                   by seq; limit ≤ 200, default 50
GET /v1/hubs/{id}/packing-summary?date=
  200 : { "date", "queued": 40, "picking": 6, "packed": 94,
          "societies": [ { "society_id", "name", "bags": 31, "packed_bags": 22, "crates": 8 } ] }
GET /v1/packing-tasks/{id}           200 PackingTaskDetail | 404 "hub_ops.task_not_found"

POST /v1/hubs/{id}/packing-tasks/build     hub_manager; Idempotency-Key
  request : { "date": "2026-12-15" }
  200 : { "created": 12, "existing": 128 }  the 05:00 job's work, on demand (§6)

POST /v1/hubs/{id}/packing-tasks/next      packer; Idempotency-Key
  200 : PackingTaskDetail                   the caller's own task in picking if any, else the
                                            queued task with the lowest seq, started for them
  204                                       nothing queued
POST /v1/packing-tasks/{id}/start          packer or hub_manager; Idempotency-Key
  request : { "bag_qr"?: "GB1:…", "client_at"?: "…" }   bag_qr, when scanned, must match
  200 : PackingTaskDetail                   status picking, packer = caller; the order moves
                                            locked → picking (orders.api.transition, actor packer)
POST /v1/packing-tasks/{id}/pick           the task's packer or hub_manager; Idempotency-Key
  request : { "product_id", "picked_qty": 2, "client_at"? }   absolute, not a delta: 0 … qty − short_qty
  200 : PackingTaskDetail
POST /v1/packing-tasks/{id}/shortage       the task's packer or hub_manager; Idempotency-Key
  request : { "product_id", "short_qty": 1, "substitute_product_id": "<uuid>" | null, "client_at"? }
  200 : PackingTaskDetail                   calls orders.api.mark_shortage in the same transaction
POST /v1/packing-tasks/{id}/complete       the task's packer or hub_manager; Idempotency-Key
  request : { "bags"?: 1, "crate_no"?: 2, "client_at"? }   bags 1 to 5; crate_no 1 to 99
  200 : PackingTaskDetail                   status packed; the order moves picking → packed
                                            (actor packer) and M4 publishes orders.packed
POST /v1/packing-tasks/{id}/release        hub_manager; Idempotency-Key
  200 : PackingTaskDetail                   packer cleared (status stays picking, the order too);
                                            any packer of the hub may start it again
```

**Rules**
- **Building (job and `build`).** `orders.api.orders_for_delivery(hub, date, statuses=("locked",), kinds=("box", "addon"))`; one task per order (unique `order_id`), so a re-run only adds orders that have none. Lines are copied from the order lines (steps and step size). `seq` follows the published route (route start, stop order, then tower and flat) when M7 has published one for the date (`logistics.route_published`), else slot start, society name, tower, flat. Queued tasks are re-sequenced when routes arrive; started ones keep their place.
- **One packer per task.** `start` takes a row lock on the task; `next` picks with `FOR UPDATE SKIP LOCKED`, so two packers never get the same task. Another packer's task is 409 `hub_ops.task_taken`; a hub manager can `release` it. A hub manager may also pick, record shortages and complete (actor `hub` on the order).
- **Shortages** (ADR-0026 §6). One shortage per line, recorded once (409 `hub_ops.shortage_recorded` afterwards, because the refund has been made):
  - **No substitute:** `short_qty` 1 … qty − picked_qty steps are refunded to the wallet by M4 (`mark_shortage`, reason `short_<product hex>`), and the line is done when picked + short = qty.
  - **Substitute:** only for the whole line (`short_qty = qty`, nothing picked yet); the substitute must be one of `catalog.api.substitute_ids(product, hub)`, not already on the order, and its current price per step at the hub no higher than the line's unit price (else 422 `hub_ops.substitute_invalid`). The packer then picks `qty` steps of the substitute; M4 changes the line's product, sets `substituted_from` and refunds any price difference. The customer is told through `orders.shortage_substituted` (M11).
- **Complete** needs every line done (else 409 `hub_ops.lines_open` with `product_ids`). Without `crate_no`, the bag goes into the society's current crate, three bags to a crate (ADR-0026 §6: crates are counted, not tracked).
- **Offline outbox (packer app).** Each tap is stored on the phone with an `Idempotency-Key` made **when tapped**, and sent in order, one task's queue at a time. A replay with the same key answers what the first call answered (M0; keys live 24 h, longer than a packing morning). `pick` is absolute, so a repeated pick under a new key changes nothing. On a 409 or 422 the app stops that task's queue and shows the conflict; other tasks continue. `client_at` (when the packer tapped) is stored in the task's events for audit and productivity; the server's time decides everything else.
- **Order status follows the task**: `queued` = order `locked`, `picking` = `picking`, `packed` = `packed`. M6 never writes `orders.*`; an illegal move (for example an order already `packed` by a hub manager in the console) comes back as 409 `orders.illegal_transition` and the task is not changed.

**As built (PR b1, packing tasks and the floor's reads).** Details the contract leaves open, settled in the code:
- **Queue order.** The first build numbers tasks by slot start, society name, tower label and flat (digit runs compare as numbers, so `A-101` comes before `A-1203`), then order id. A later build (or job run) appends the orders that have no task yet after the highest `seq`, sorted the same way; existing tasks keep their place. `existing` in the build answer counts the tasks the date already had. Builds of one hub run one after the other (the hub row is locked).
- **Routes are not consumed yet.** M7 is not built, so `route` is always `null` and nothing re-sequences queued tasks; the consumer of `logistics.route_published` comes with M7 and sets `route_id`/`stop_seq` on queued tasks.
- **`flat_label`** is `"<tower label>, <flat>"` (`"B wing, B-402"`), or the flat alone when the tower is unknown, or `null` without an address. Tower and flat are copied from `identity.api.delivery_labels` when the task is built; society names and tower labels come from `community.api.society_labels`, the packer's name from `identity.api.user_names`.
- **Lines** are ordered by the catalog category's `sort_order` (then category slug) and the name in the request's language; `catalog.api.LineProduct` gained `shelf` (category sort order, category slug) for this.
- **Summary.** `crates` is the highest crate number packed so far in the society (0 before the first bag).
- **Who.** The franchise owner and admins read the queue (`packing:read`); hub managers and admins build (`packing:manage`). A task of another hub is 404 `hub_ops.task_not_found`; another hub's list, summary or build is 403 `auth.forbidden`.
- **Facade.** `hub_ops.api.society_loads` and `hub_ops.api.bag_for_order` are built as listed below.

**As built (PR b2, the packer's moves).**
- **"Never substitute, refund" (ADR-0026 §6, M1 §3.4).** Packing reads the household's preference through `identity.api.shortage_preferences` when the shortage is recorded (`hub_ops.packing.household_allows_substitutes`), so a change applies at once; a substitute for a household that chose `refund` is 422 `hub_ops.substitute_invalid` with `reason: "refund_preferred"` (the packer then records a refund). M4's `mark_shortage` checks the same rule again.
- **`next`** works on today's tasks (IST). Besides queued tasks it offers started tasks a hub manager released (status `picking`, no packer), by `seq`. A task whose order M4 refuses to move (packed in the console) is passed over and stays `queued`. `204` keeps no `Idempotency-Key` (nothing happened), so a retry asks again.
- **`start`** by the packer who holds the task returns it unchanged; on a released task it only sets the packer (the order is already `picking`). `bag_qr` is checked before anything else, also on a packed task (`hub_ops.task_packed` wins).
- **Shortage codes.** M6 checks the ranges first (`hub_ops.qty_invalid`: no substitute, 1 … qty − picked; `hub_ops.substitute_invalid` with `reason: "partial"` for a substitute that is not the whole line or after a pick). M4's refusals are passed on as `hub_ops.substitute_invalid` with M4's `reason` (`not_a_substitute`, `on_order`, `pricier`, `no_price`, or `unknown` for an inactive product); `billing.wallet_limit` and `orders.not_editable` are passed on as they are. A substituted line keeps `short_qty` 0 (nothing was refunded by steps; M4 refunds the price difference) and is done once `qty` steps of the substitute are picked; `pick` takes either the ordered or the substitute's product id.
- **Complete.** `bags` defaults to the task's (1). Without `crate_no` the crate is `⌊bags already packed in the society today ÷ 3⌋ + 1` (at most 99); a multi-bag task gets the crate of its first bag. Packed tasks keep their packer.
- **Who.** A hub manager acting on a task is actor `hub` on the order (`actor_user_id` = the manager); a packer is `packer`. Admins may release (`packing:manage`) but not pack (`packing:write` is packer and hub manager only). Another hub's `next` is 403 `auth.forbidden`; its tasks are 404 `hub_ops.task_not_found`.
- **Events.** Each change writes a `hub_ops.packing_events` row (`start` with the scanned QR, `pick` with the absolute quantity, `shortage`, `complete` with bags and crate, `release` with the previous packer) and `client_at`; a `pick` that changes nothing writes none and keeps the version.
- **Not tested here:** two packers calling `next` at the same instant on separate database connections (the test database rolls each test back inside one connection); sequential claims by two packers are tested, and the claim uses `FOR UPDATE SKIP LOCKED`.

**Error codes** (in addition to §3.1)

| Status | Code | When |
|---|---|---|
| 404 | `hub_ops.task_not_found` | Unknown task, or another hub's |
| 404 | `hub_ops.line_not_found` | A product that is not on the task |
| 403 | `hub_ops.not_your_task` | A packer acting on a task another packer holds (pick, shortage, complete) |
| 409 | `hub_ops.task_taken` | `start` on a task another packer holds |
| 409 | `hub_ops.task_not_started` | `pick`, `shortage` or `complete` on a queued task |
| 409 | `hub_ops.task_packed` | Any change to a packed task |
| 409 | `hub_ops.lines_open` | `complete` with lines not done (`product_ids`) |
| 409 | `hub_ops.shortage_recorded` | A second shortage on the same line |
| 422 | `hub_ops.qty_invalid` | `picked_qty` or `short_qty` out of range |
| 422 | `hub_ops.substitute_invalid` | Not a listed substitute, already on the order, dearer, or not a whole-line shortage |
| 422 | `hub_ops.bag_mismatch` | The scanned `bag_qr` is not this task's |
| 409 | `orders.illegal_transition` | The order is not in the status the step needs (from M4) |

### 3.3 Procurement plans and purchase orders (frozen on approval)
The console's Procurement screen (plan table, line edit sheet, purchase orders with the WhatsApp link and print view) and the backend build against this. Same review rule as §3.1.

**What M6 buys.** Products of kind `veg`, `fruit`, `staple` and `salad` (the **stocked kinds**). Tiffins are cooked by kitchens (M8) and kits are not built yet (M2), so neither gets a plan line. All quantities below are operations quantities (ADR-0026 §1): `NUMERIC(10,3)` in the product's operations unit, sent as JSON numbers with at most 3 decimals (more is 422 `hub_ops.qty_invalid`); `piece`, `bunch` and `pack` quantities are whole numbers. Steps convert as `steps × step_qty`, divided by 1000 for `g` and `ml`.

```
ProcurementLine : {
  "product_id", "slug", "name",            name in Accept-Language
  "unit": "kg",                            the operations unit
  "shelf_life_h": 48 | null,               from the catalog
  "committed_qty": 42.5,                   paid demand for the date (rules)
  "at_risk_qty": 1.5,                      payment_failed orders still in the grace; shown, not bought
  "forecast_q50": 3.0 | null,              M10's uncommitted demand, converted from steps
  "forecast_qtau": 5.0 | null,
  "basis": "forecast" | "buffer",          which rule made buffer_qty
  "buffer_qty": 4.3,
  "on_hand_qty": 0.0,                      usable stock for the date (§3.4)
  "suggested_qty": 46.8,                   committed + buffer − on hand, rounded up
  "order_qty": 50.0,                       suggested, or the hub manager's edit
  "edited": true, "edit_reason": "Vendor sells in 10 kg crates" | null,
  "vendor": { "id", "name" } | null,
  "unit_cost_paise": 3200 | null,          expected cost per operations unit
  "amount_paise": 160000 | null,           order_qty × unit_cost_paise, rounded half up
  "purchase_order": { "id", "number", "status" } | null,   the line's PO (not cancelled)
  "note": "…" | null }

ProcurementPlan : {
  "id", "hub_id", "delivery_date",
  "status": "draft" | "ordered" | "closed",   ordered once any PO is sent; closed at T 12:30 (§3.5)
  "source": "ml" | "fallback",              ml when M10 had a forecast for the hub and date
  "model": "AutoETS" | null,                the forecast's model
  "buffer_pct": 10,                         the hub's procurement_buffer_pct when built
  "totals": { "lines": 38, "order_paise": 152300, "unpriced_lines": 4 },
  "lines": [ ProcurementLine ],             by catalog category (shelf), then name
  "built_at", "refreshed_at", "version" }

GET /v1/hubs/{id}/procurement-plans?date=   200 ProcurementPlan (ETag = version)
                                            | 404 "hub_ops.plan_not_found" (not built yet)
GET /v1/procurement-plans/{id}              200 ProcurementPlan | 404 "hub_ops.plan_not_found"
POST /v1/hubs/{id}/procurement-plans/build  hub_manager; Idempotency-Key
  request : { "date": "2026-12-15" }        today … today + 2
  200 : ProcurementPlan                     builds it, or rebuilds it (rules); the 21:10 job's work
PATCH /v1/procurement-plans/{id}/lines/{product_id}     hub_manager; If-Match (the plan's version)
  request : any of { "order_qty": 50.0 | null,      null: follow suggested_qty again (edited false)
                     "vendor_id": "<uuid>" | null,
                     "unit_cost_paise": 3100 | null, 0 to 10,000,000
                     "note": "…" | null,            up to 200
                     "reason": "…" }                3 to 200; required when order_qty changes
  200 : ProcurementPlan (version + 1)

PurchaseOrder : {
  "id", "number": "KHARGHAR-261215-02" | null,    given when sent: hub slug, YYMMDD, a number per hub and day
  "plan_id", "hub_id", "delivery_date",
  "vendor": { "id", "name", "phone": "+919800002001" | null, "whatsapp": true },
  "status": "draft" | "sent" | "partly_received" | "received" | "cancelled",
  "lines": [ { "product_id", "slug", "name", "unit", "qty": 50.0,
               "unit_cost_paise": 3200 | null, "amount_paise": 160000 | null,
               "received_qty": 0.0, "accepted_qty": 0.0 } ],     sums of the receipts (§3.4)
  "total_paise": 98000, "unpriced_lines": 1,
  "share_text": "…",                         plain text for WhatsApp or print (below)
  "whatsapp_url": "https://wa.me/919800002001?text=…" | null,   when the vendor has a phone and WhatsApp
  "stale": false,                            a draft whose plan changed after it was generated
  "cancel_reason": "…" | null,
  "created_at", "sent_at" | null, "closed_at" | null, "version" }

POST /v1/procurement-plans/{id}/purchase-orders    hub_manager; Idempotency-Key; no body
  200 : { "purchase_orders": [ PurchaseOrder ] }   every PO of the plan that is not cancelled
GET  /v1/hubs/{id}/purchase-orders?date=&status=   200 { "items": [ PurchaseOrder ] } by vendor name
GET  /v1/purchase-orders/{id}                      200 PurchaseOrder (ETag) | 404 "hub_ops.po_not_found"
POST /v1/purchase-orders/{id}/send                 hub_manager; Idempotency-Key; no body
  200 : PurchaseOrder                              draft → sent; number given; publishes
                                                   hub_ops.purchase_order_sent
POST /v1/purchase-orders/{id}/cancel               hub_manager; Idempotency-Key
  request : { "reason": "…" }                      3 to 200
  200 : PurchaseOrder                              draft, or sent with nothing received → cancelled
```

**Quantity rules** (per line; the plan keeps every input, so the console can show the sum)
- **Committed:** `orders.api.orders_for_delivery(hub, date, statuses=("locked", "picking", "packed"), kinds=("box", "addon"))`, Σ `(qty − shortage_qty) × step` of each line's current product (a substituted line counts the substitute). Carried-over lines (`carried_from_order_id`, §3.5) are left out: they are packed from kept items, not bought. **At risk:** the same over `payment_failed` orders. A line exists for every stocked product with committed, at-risk or forecast quantity above 0.
- **Buffer.** When `intel_gateway.api.hub_forecast(hub, date)` returns a forecast and it has an item for the product: `basis: "forecast"`, `buffer_qty = max(0, uncommitted_qtau) × step` (M10 §3.6; M10's `committed` and `order_qty` are ignored, because the cut-off gives the exact count). Otherwise, or when `hub_forecast` returns `None`: `basis: "buffer"`, `buffer_qty = committed × buffer_pct / 100` (the hub's `procurement_buffer_pct`, default 10). **Short-life cap:** for a product with `shelf_life_h ≤ 48` (leafy greens and milk in the seed catalog) `buffer_qty` is at most `committed × min(buffer_pct, 5) / 100`, whatever the basis. `source` is `ml` when a forecast was found for the hub and date (even if some lines use the buffer), else `fallback`.
- **Suggested:** `max(0, committed + buffer − on_hand)`, rounded **up** to 0.1 for `kg` and `l` and to a whole number otherwise. It is never below `committed − on_hand`, because the buffer is never negative.
- **Order quantity** follows `suggested_qty` until the hub manager sets it (`edited: true`); then rebuilds keep it until `order_qty: null` is sent.
- **Vendor and cost** for a new line: the vendor of the product's last purchase order at the hub that was not cancelled, if still active, else the hub's default vendor, else null; `unit_cost_paise` from the product's latest goods receipt line at the hub with a cost (§3.4), else null. Rebuilds keep a line's vendor, cost and note. Money: `amount_paise = round_half_up(order_qty × unit_cost_paise)`; lines with no cost are counted in `unpriced_lines` and left out of the totals. Costs are recorded for the hub's books and vendor scores only; M6 posts no ledger entries and pays no vendor (vendors are paid outside the platform in the prototype, §11).

**Building, rebuilding and refreshing.** One plan per hub and delivery date (unique). `build_procurement_plan` (T-1 21:10, §6) creates the plan for T for every active hub, or rebuilds it if it exists. M10's forecast job runs at the same minute, so the 21:10 build normally uses the forecast stored by the previous evening's run (it covers 7 days ahead) or the buffer; the consumer of **`intel.forecast_ready`** then rebuilds the listed dates that already have a plan that is not closed. `refresh_procurement_plan` (T-1 23:05, after M4's `payment_grace_expiry`) rebuilds existing plans only. A rebuild recomputes `committed_qty`, `at_risk_qty`, the forecast, `buffer_qty`, `on_hand_qty` and `suggested_qty` of every line; `order_qty` follows only on lines that are not edited and not on a sent purchase order; new products get lines; a line whose committed, at-risk and forecast quantities all fell to 0 is removed unless it is edited or on a sent PO. A rebuild that changes anything bumps `version` and sets `refreshed_at`. Builds of one hub run one after the other (the hub row is locked). A closed plan is never rebuilt.

**Editing.** A line may be changed while the plan is not `closed` (409 `hub_ops.plan_not_editable`) and the line is not on a sent, partly received or received PO (409 `hub_ops.line_ordered`; cancel the PO first). `vendor_id` must be an active vendor of the hub (404 `hub_ops.vendor_not_found`, 422 `hub_ops.vendor_inactive`). Every change goes to `platform.audit_log` with the old and new values and the reason. A product not on the plan is 404 `hub_ops.line_not_found` (lines are not added by hand in v1).

**Purchase orders.**
- **Generate** (`POST …/purchase-orders`): one **draft** PO per vendor for the lines with `order_qty > 0` that are not on a sent PO, at the lines' `order_qty`, vendor and cost; the plan's earlier drafts are replaced (drafts never left the hub, so they are deleted). Lines without a vendor → 422 `hub_ops.vendor_missing` with `product_ids`, and nothing changes; no line to order → 409 `hub_ops.nothing_to_order`.
- **Send** marks that the hub manager is sending it now: she opens `whatsapp_url` (or prints `share_text`); nothing is sent automatically in v1. It needs a draft (409 `hub_ops.po_not_draft`) generated from the plan's current version (409 `hub_ops.po_stale`: regenerate), and an active vendor (422 `hub_ops.vendor_inactive`). The PO gets its `number`, the plan becomes `ordered`, and `hub_ops.purchase_order_sent` is published.
- **Lifecycle:** `draft → sent → partly_received → received`; `draft` or `sent` (nothing received) `→ cancelled` (409 `hub_ops.po_not_cancellable` otherwise). Receipts move it (§3.4): every line's `received_qty ≥ qty` → `received`, else `partly_received`. The 12:30 run on the delivery day closes the day's buying (§3.5): `partly_received → received` and `sent → cancelled` with `cancel_reason: "not_delivered"`, which counts against the vendor's reliability. A plan whose sent POs are all cancelled returns to `draft`.
- **`share_text`** (English in v1), for example:

```
GharBasket Kharghar hub · PO KHARGHAR-261215-02
Deliver by 04:00 on Tue 15 Dec 2026 to Plot 12, Sector 19, Kharghar
1. Tomato: 50 kg @ ₹32.00/kg = ₹1,600.00
2. Palak: 40 bunch (no price agreed)
Total ₹1,600.00 + 1 item at market price
```

**As built (PR c1, plans and lines).** Details the rules above leave open, settled in the code:
- One migration creates all four §3.3 tables.
- A plan's `version` counts changes to its lines (a rebuild that changes something, a line edit). Status moves made by purchase orders do not bump it, so sending one PO never makes the plan's other drafts stale.
- A number sent as `order_qty` pins the line (`edited: true`) and needs a `reason`, unless the line is already edited at that quantity; `order_qty: null` needs none. A `PATCH` that changes nothing keeps the version and writes no audit row; otherwise `platform.audit_log` gets `procurement.line_updated` (entity `<plan id>:<product id>`) with the changed fields before and after, and the reason.
- A rebuild uses the hub's current `procurement_buffer_pct`. `buffer_qty` is rounded half up to 0.001 before `suggested_qty` is rounded up. Lines are listed by the catalog category's sort order, then by name in Accept-Language.
- `POST …/build` on a closed plan is 409 `hub_ops.plan_not_editable`; the jobs skip it and report how many plans were `built`, `rebuilt`, `unchanged` or `closed`. `refresh_procurement_plan` and the `intel.forecast_ready` consumer never create a plan.
- Until the sections they read exist, three hooks stand in: the forecast (`_forecast_for`, wired to `intel_gateway.api.hub_forecast` when M10's forecast PRs land; none today, so `source` is `fallback` and every line uses the buffer), usable stock (`_on_hand_for`, §3.4 lots; 0 today) and the last receipt cost (`_last_costs`, §3.4; null today). Carried-over lines are left out once M4's order lines carry `carried_from_order_id`.
- Hub staff of another hub get 403 `auth.forbidden` on its `/v1/hubs/{id}/…` paths and 404 `hub_ops.plan_not_found` for its plan ids.
- `just seed` adds "Kharghar Kisan FPO" (`fpo`, leafy greens) and "Patil Wholesale" (`wholesaler`, staples) beside the default "Shinde Traders".

**As built (PR c2, purchase orders).**
- `share_text` reads "PO draft" until the PO is sent, then carries its number; it uses the hub's full address and `timings.receiving`, and lists the lines by English name. `whatsapp_url` is made on each read from the vendor's current phone and WhatsApp flag.
- A number counts every numbered PO of the hub and delivery date (a cancelled one keeps its number), so numbers are never reused; sending locks the hub, then the plan, then the PO.
- A cancelled draft stays, `cancelled` with its reason; regenerating deletes the plan's drafts instead.
- Hub staff of another hub get 403 `auth.forbidden` on its `/v1/hubs/{id}/purchase-orders` and 404 `hub_ops.po_not_found` for its PO ids.

**Error codes** (in addition to §3.1 and §3.2)

| Status | Code | When |
|---|---|---|
| 404 | `hub_ops.plan_not_found` | No plan for the hub and date, unknown id, or another hub's |
| 404 | `hub_ops.po_not_found` | Unknown purchase order, or another hub's |
| 404 | `hub_ops.line_not_found` | A product that is not on the plan |
| 409 | `hub_ops.plan_not_editable` | Building, editing or generating POs for a closed plan |
| 409 | `hub_ops.line_ordered` | Editing a line that is on a sent, partly received or received PO |
| 409 | `hub_ops.nothing_to_order` | No line with `order_qty > 0` left to put on a PO |
| 409 | `hub_ops.po_not_draft` | `send` on a PO that is not a draft |
| 409 | `hub_ops.po_stale` | `send` on a draft generated before the plan's last change |
| 409 | `hub_ops.po_not_cancellable` | `cancel` on a PO with goods received, or already received or cancelled |
| 412 | `request.version_mismatch` | `If-Match` is not the plan's version |
| 422 | `hub_ops.vendor_missing` | Generating POs while lines with `order_qty > 0` have no vendor (`product_ids`) |
| 422 | `hub_ops.vendor_inactive` | An inactive vendor on a line or a PO being sent |
| 422 | `hub_ops.reason_required` | `order_qty` changed without a `reason` |
| 422 | `hub_ops.qty_invalid` | Negative, more than 3 decimals, over 9,999,999.999, or not whole for `piece`, `bunch`, `pack` |

### 3.4 Goods receipt, lots and stock (frozen on approval)
The console's Receiving/QC screen (on a tablet at the hub at 04:00) and Inventory screen build against this. Same review rule as §3.1.

```
POST /v1/hubs/{id}/goods-receipts       hub_manager, packer; Idempotency-Key
  request : { "purchase_order_id": "<uuid>" | null,   null: an ad hoc purchase (a market buy)
              "vendor_id": "<uuid>",                   required without a PO; with one, omitted or the PO's
              "received_at"?: "…",                     default now; at most 24 h back, never ahead
              "lines": [ {                             1 to 100, one per product
                "product_id",
                "received_qty": 52.0,                  weighed or counted at the dock, > 0
                "accepted_qty": 50.0,                  0 … received_qty; the rest goes back to the vendor
                "grade": "A" | "B" | "C",              of the accepted part (rules)
                "reject_reason"?: "rotten" | "damaged" | "undersized" | "wrong_item" | "other",
                                                       required when accepted < received
                "unit_cost_paise"?: 3100 | null,       per operations unit; default the PO line's
                "best_before"?: "…",                   default received_at + shelf_life_h
                "note"? } ] }                          up to 200
  201 : GoodsReceipt                                   publishes hub_ops.goods_received

GoodsReceipt : {
  "id", "hub_id", "vendor": { "id", "name" },
  "purchase_order": { "id", "number", "status" } | null,   status after this receipt
  "received_at", "received_by": { "user_id", "name" },
  "lines": [ { "product_id", "slug", "name", "unit",
               "received_qty", "accepted_qty", "rejected_qty", "grade", "reject_reason",
               "unit_cost_paise" | null, "amount_paise" | null,   accepted_qty × cost, half up
               "ordered": true,                     false: not on the PO (or no PO)
               "lot_id": "<uuid>" | null,           null when nothing was accepted
               "best_before" } ],
  "total_paise", "unpriced_lines", "created_at" }
GET /v1/hubs/{id}/goods-receipts?date=     200 { "items": [ GoodsReceipt ] }   newest first
GET /v1/goods-receipts/{id}                200 GoodsReceipt | 404 "hub_ops.receipt_not_found"

Lot : {
  "id", "hub_id", "product_id", "slug", "name", "unit",
  "source": "receipt" | "returned",          returned: from a bag back after a second miss (§3.5)
  "grade": "A" | "B" | "C",
  "qty_in": 50.0, "qty_on_hand": 12.5,
  "unit_cost_paise": 3100 | null,
  "received_at", "best_before",
  "receipt_id": "<uuid>" | null, "vendor": { "id", "name" } | null }
StockMove : { "kind": "receive" | "return" | "pack" | "waste" | "offer" | "count",
              "qty": -3.0, "ref_id", "by": { "user_id", "name" } | null, "at" }

GET /v1/hubs/{id}/inventory?product_id=&expiring_before=
  200 : { "as_of",
          "items": [ { "product_id", "slug", "name", "unit", "shelf_life_h",
                       "on_hand_qty": 12.5,          Σ lots' qty_on_hand
                       "packed_not_drawn_qty": 3.0,  packed and not yet drawn down (rules)
                       "lots": [ Lot ] } ] }         lots with stock, by best_before
GET  /v1/lots/{id}                         200 Lot & { "moves": [ StockMove ] } | 404 "hub_ops.lot_not_found"
POST /v1/lots/{id}/count                   hub_manager; Idempotency-Key
  request : { "qty_on_hand": 10.0, "reason": "…" }   absolute; reason 3 to 200
  200 : Lot                                 a count move for the difference; audited
```

**Rules**
- **Receipts are facts.** A receipt is never edited or deleted; a wrong quantity is corrected by a stock count on the lot. Each product must be a stocked kind and active (422 `hub_ops.product_not_stocked`). Quantities follow §3.3's rules (`hub_ops.qty_invalid`); `accepted_qty > received_qty`, a missing `reject_reason`, a product twice, a `vendor_id` that is not the PO's, `received_at` in the future or more than 24 h back, or `best_before` not after `received_at` or more than a year after it are 422 `hub_ops.receipt_invalid` with `reason`. With a PO: it must be the hub's and `sent` or `partly_received` (409 `hub_ops.po_not_receivable`); several receipts may follow one PO (a vendor delivering in two trips); a product not on the PO is accepted with `ordered: false`; each PO line adds the receipt's `received_qty` and `accepted_qty`, and the PO's status follows (§3.3). Without a PO the vendor must be an active vendor of the hub (404 `hub_ops.vendor_not_found`, 422 `hub_ops.vendor_inactive`).
- **Rejected goods** go back to the vendor: they are neither stock nor waste, and count only in the vendor's score.
- **Lots.** Each line with `accepted_qty > 0` creates one lot: `qty_in = qty_on_hand = accepted_qty`, the line's grade and cost, `best_before` as sent or `received_at + shelf_life_h` (a product with no shelf life: one year). **Grades:** `A` fresh, `B` packable with cosmetic faults (both are packed and netted in plans; B only lowers the vendor's freshness score), `C` accepted for kitchens or donation only: never packed or netted, offered by the next surplus run (§3.5).
- **Stock moves.** Every change to a lot is a `hub_ops.stock_moves` row (`receive`, `return`, `pack`, `waste`, `offer`, `count`) in the same transaction, and `qty_on_hand` (never below 0) equals the sum of its lot's moves. Moves are unique per kind, source row, product and lot, so a replayed job or request never moves stock twice.
- **Usable stock for a date D** (the plan's `on_hand_qty`): Σ `qty_on_hand` of the product's grade A and B lots at the hub whose `best_before` is after the end of D (IST), so what is packed on D lasts the day.
- **Packing draws stock down once a day, not at `complete`.** The 12:30 run (§3.5, also `POST …/surplus/run`) first draws down every packed task of the hub not drawn yet (`packing_tasks.drawn_at` null, any date up to today): for each line, the picked steps of the picked product (the substitute when there is one) in the operations unit, from that product's grade A and B lots with stock, **first expiry first out** (`best_before`, then `received_at`, then id). What no lot covers is a `pack` move without a lot (**unlotted**), so packing never waits for receiving data and the gap shows on the inventory screen. Carried lines draw from kept items instead (§3.5). The task's `drawn_at` is set in the same transaction. Packing itself (§3.2) is unchanged and never checks stock: a shortage is what the packer finds on the shelf. `packed_not_drawn_qty` shows what is packed and not yet drawn down.
- **Counts** (`POST /v1/lots/{id}/count`) set a lot's stock after a physical count (hub manager only) and write a `count` move with the difference and the reason, plus `platform.audit_log`.

**Vendor scores** (`compute_vendor_scores`, Monday 03:33 IST for the IST week Monday to Sunday before it; one `hub_ops.vendor_scores` row per vendor with a sent PO or a receipt that week; `Vendor.score` shows the latest):
- **freshness** (weight 40 %) = accepted grade A lines ÷ accepted lines;
- **price** (20 %) = mean over the vendor's receipt lines with a cost of `min(1, median ÷ cost)`, where the median is the hub's median cost of that product that week over every vendor (a lone vendor scores 1);
- **reliability** (20 %) = ½ × the fill rate (mean over its POs' lines of `min(1, received_qty ÷ qty)`, a cancelled `not_delivered` PO counting 0) + ½ × the share of its POs whose first receipt came by the hub's `timings.packing` on the delivery day;
- **defects** (stored as the rejected share: the mean over its receipt lines of rejected ÷ received; it counts 20 % as `1 − defects`);
- `score` = the weighted sum over the components that have data, divided by their weights; rounded to 2 decimals.

**As built (PR d1, receipts, lots and the stock ledger).** Details the rules above leave open, settled in the code:
- One migration creates all five §3.4 tables (`vendor_scores` too, filled by PR d2). `stock_moves.kept_item_id` arrives with kept items (§3.5).
- `received_at` and `best_before` must carry a time zone (else 422 `request.invalid`) and are stored in UTC; a `received_at` up to one minute ahead is taken as now (a tablet's clock). A receipt without a PO and without `vendor_id` is 422 `request.invalid`; a PO of another hub is 404 `hub_ops.po_not_found`; a received quantity of 0 is `hub_ops.qty_invalid`.
- A line that leaves `unit_cost_paise` out takes the PO line's cost; one that sends `null` has no cost. A `reject_reason` sent with nothing rejected is dropped (the table checks that a reason is stored exactly when something went back).
- A receipt locks the hub, then (with a PO) the plan, the PO and its lines, the procurement lock order. A PO that becomes `received` gets `closed_at`. The receipt is audited (`goods_receipts.created`, with the request).
- The 201 answer shows the PO's status after the receipt; `GET` shows its current status. `GET …/goods-receipts?date=` takes any IST day (default today), newest `received_at` first; lines are listed by name.
- Every stock change goes through one function that inserts the move with `ON CONFLICT DO NOTHING` and applies it to the (row-locked) lot only when it was written, so the lot's `qty_on_hand` stays the sum of its moves.
- Hub staff of another hub get 403 `auth.forbidden` on its `/v1/hubs/{id}/goods-receipts` and 404 `hub_ops.receipt_not_found` for its receipt ids.

**As built (PR d2, inventory, counts, vendor scores and the plan hooks).**
- Inventory items are listed by shelf, then name; `expiring_before` must carry a time zone. `packed_not_drawn_qty` is 0 until the 12:30 draw-down (§3.5) exists (it does now: see §3.5's "As built (PR e2)"). `GET /v1/lots/{id}` lists moves oldest first, each with its `reason` (a count's; additive).
- A count needs `inventory:manage` (the hub's manager, admin). Each count is its own source (a fresh `ref_id`); a count that matches the lot writes no move but is still audited (`inventory.counted`, the quantity before and after, the reason). Another hub's lot is 404 `hub_ops.lot_not_found`.
- Plans now read stock (§3.3's hooks): `on_hand_qty` sums grade A and B lots with stock whose `best_before` is after the next IST midnight; a new line's cost is the latest receipt line at the hub (by `received_at`, then id) with a cost and something accepted, from any vendor.
- Vendor scores take the purchase orders delivered in the week that were sent; one cancelled by hand after sending is left out, one still `sent` counts like one not delivered. A receipt line whose product had a single vendor at the hub that week (or cost 0) scores 1 on price. Components are stored to 3 decimals, the score to 2 (half up); a rerun rewrites the week's rows (`computed_at`). `Vendor.score` is the latest week's score. The job runs from `jobs-staging.yml` at `3 22 * * 0` (Monday 03:33 IST).

**Error codes** (in addition to §3.1–§3.3)

| Status | Code | When |
|---|---|---|
| 404 | `hub_ops.receipt_not_found`, `hub_ops.lot_not_found` | Unknown id, or another hub's |
| 409 | `hub_ops.po_not_receivable` | A receipt against a PO that is a draft, received or cancelled |
| 422 | `hub_ops.product_not_stocked` | A product that is unknown, inactive, or not of a stocked kind |
| 422 | `hub_ops.receipt_invalid` | As in the rules, with `reason` (`accepted_over_received`, `reject_reason_missing`, `duplicate_product`, `vendor_mismatch`, `received_at`, `best_before`) |

### 3.5 Waste, the surplus cascade and kept items (frozen on approval)
The console's Waste and cascade screen and the packer app's "Returned bag" scan build against this. Same review rule as §3.1.

```
WasteLog : {
  "id", "hub_id", "lot_id": "<uuid>" | null, "product_id", "slug", "name",
  "qty": 2.5, "unit": "kg",
  "kg": 2.5 | null,                         weight for the KPI (rules); null when unknown
  "reason": "spoiled" | "damaged" | "expired" | "pest" | "surplus" | "returned" | "other",
  "cascade_to": "kitchen" | "ngo" | "bin",
  "offer_id": "<uuid>" | null,
  "taken_by": { "kind": "kitchen", "partner_id", "name" } | { "kind": "ngo", "name" } | null,
  "note": "…" | null, "logged_by": { "user_id", "name" } | null,   null: the 12:30 job
  "at" }
GET  /v1/hubs/{id}/waste-logs?from=&to=&cascade_to=&cursor=&limit=   200 Page[WasteLog], newest first
POST /v1/hubs/{id}/waste-logs             hub_manager, packer; Idempotency-Key
  request : { "lot_id": "<uuid>" } or { "product_id": "<uuid>" },   exactly one
            & { "qty": 2.5, "reason": "spoiled" | "damaged" | "expired" | "pest" | "other", "note"? }
  201 : WasteLog                            cascade_to "bin"; publishes hub_ops.waste_logged

SurplusOffer : {
  "id", "hub_id", "lot_id", "product_id", "slug", "name", "unit", "grade", "best_before",
  "qty": 6.0,
  "offered_to": "kitchen" | "ngo",
  "status": "open" | "taken" | "declined" | "expired",
  "taken_qty": 4.0 | null, "taken_by": … | null,      as on WasteLog
  "previous_offer_id": "<uuid>" | null,              the offer it follows in the cascade
  "expires_at",                                      the next day's 12:30 run
  "created_at", "settled_at" | null }
GET  /v1/hubs/{id}/surplus-offers?date=&status=      200 { "items": [ SurplusOffer ] }
POST /v1/surplus-offers/{id}/settle                  hub_manager; Idempotency-Key
  request : { "outcome": "taken" | "declined",
              "qty"?: 4.0,                           taken only; default all of it
              "taken_by"?: { "kind": "kitchen", "partner_id" } | { "kind": "ngo", "name" },
              "note"? }
  200 : { "offer": SurplusOffer, "next": SurplusOffer | null, "waste_logs": [ WasteLog ] }
POST /v1/hubs/{id}/surplus/run                       hub_manager; Idempotency-Key; no body
  200 : { "drawn_tasks": 140, "closed_purchase_orders": 3, "offers": 9, "binned": 2 }   the 12:30 run

KeptItem : {
  "id", "carry_id", "from_order_id", "society": { "id", "name" }, "flat_label": "B wing, B-402",
  "product_id", "slug", "name", "step": { "qty": 5, "unit": "kg", "label": "5 kg" },
  "qty": 1,                                  steps
  "status": "kept" | "packed" | "released" | "lost",
  "kept_at", "packed_task_id": "<uuid>" | null, "settled_at" | null }
GET  /v1/hubs/{id}/kept-items?status=kept           200 { "items": [ KeptItem ] }   by society, flat
POST /v1/hubs/{id}/returned-bags                    packer, hub_manager; Idempotency-Key
  request : { "bag_qr": "GB1:…" }
  200 : { "order_id", "society", "flat_label", "kept": [ KeptItem ], "lots": [ Lot ] }
                                                    the same answer again for the same bag
POST /v1/hubs/{id}/kept-items/{item_id}/lost        hub_manager; Idempotency-Key; audited
  request : { "note": "…" }                         1–500 chars
  200 : KeptItem (status "lost")                    M4 refunds the item at its paid price, no fee
  409 hub_ops.kept_item_settled (packed or released) · 409 orders.carry_not_refundable (its box is packed); both with `current_status`
```

**Waste logs.** Every quantity that leaves stock other than by packing is a waste log: written off by hub staff (`spoiled`, `damaged`, `expired`, `pest`, `other`; always `cascade_to: "bin"`) or settled by the cascade (`surplus`, or `returned` for a returned lot; `cascade_to` = where it went). A log with `lot_id` writes a `waste` move on the lot (`qty` above the lot's stock is 409 `hub_ops.qty_exceeds_stock`); one with only `product_id` is unlotted (stock that was never received in the system). `kg` is the weight: the quantity itself for `kg` and `l` (1 l counted as 1 kg), else `qty × unit_g ÷ 1000` from the catalog's nutrition data, else null. Each log publishes `hub_ops.waste_logged`.

**The 12:30 run** (`surplus_cascade` at T 12:30 for every active hub, and `POST …/surplus/run` for one hub on demand; idempotent):
1. **Draw down packing** (§3.4).
2. **Close the day's buying:** today's `partly_received` POs → `received`, `sent` POs → `cancelled` (`not_delivered`); today's plan → `closed`.
3. **Open offers whose `expires_at` has passed** (made by an earlier run and not settled): status `expired`, and their quantity moves on (step 5).
4. **New surplus:** every lot with stock that is grade C, or whose `best_before` is before the end of tomorrow (IST), so it cannot be packed tomorrow (the same line as §3.4's usable stock). Its whole `qty_on_hand` leaves the lot (an `offer` move) and moves on (step 5); it never returns to stock.
5. **Next destination:** a quantity past its lot's `best_before` goes to **bin** (a waste log, reason `expired`). Otherwise new surplus is offered to **kitchens**; what leaves a kitchen offer is offered to an **NGO**; what leaves an NGO offer goes to **bin** (reason `surplus`, or `returned` for a returned lot). Each offer publishes `hub_ops.surplus_offered`; `expires_at` is the next day's 12:30.

**Settling.** `taken` writes a waste log with `cascade_to` = the offer's `offered_to` and `taken_by`, and any quantity not taken moves on as in step 5; `declined` moves the whole quantity on at once (so a declined NGO offer is binned). Only an open offer can be settled (409 `hub_ops.offer_settled`). A kitchen taker must be an active kitchen partner of the hub (`partners.api.get_partner`; else 422 `hub_ops.taker_invalid`); an NGO is a name (up to 80) until M9 lists NGOs through `community.api`. In v1 the **hub manager settles** every offer in the console, after a phone call; M8 (kitchens) and M9 (NGOs) later consume `hub_ops.surplus_offered` and call `hub_ops.api.settle_surplus` with the same rules. Surplus is given free in v1: no ingredient credit, no money (§11).

**As built (PR e2, the 12:30 run and the cascade).** Details the rules above leave open, settled in the code:
- **One run per hub at a time.** The run locks the hub row (`FOR NO KEY UPDATE`, as every hub lock in M6: it serialises the hub's writers but lets through the foreign-key checks of rows that refer to the hub, so a settle or a write-off holding its offer or lot can still insert its log and never deadlocks with a run waiting for that offer or lot), then (step 1) the packed tasks not drawn, the lots it draws by id, (step 2) today's plan and its POs, (step 3) the expiring offers by id, and (step 4) the surplus lots by id. `now` is the job's `scheduled_for` (the endpoint: the request time); "today" and "tomorrow" are its IST days. The job runs every active hub in its own savepoint; a hub that fails is logged and listed as `failed_hubs`, and the others' runs stand. Its summary is `{ date, hubs, drawn_tasks, closed_purchase_orders, offers, binned }`; a paused hub is skipped by the job, but `POST …/surplus/run` runs any hub.
- **The draw-down** takes packed tasks with `drawn_at` null and a delivery date up to today, in `packed_at` order. Per task it adds up the paid lines' picked steps by the product in the bag: a substitute by its own catalog step (the packing line keeps the ordered product's), in the operations unit. It draws from the product's grade A and B lots with stock in that unit, `best_before`, then `received_at`, then id, whatever their best-before (the bag is packed, so the stock is gone). The rest is one unlotted `pack` move. Moves are keyed by the task (`ref_id` = task id), apart from carried lines' moves (keyed by the kept item or carry). `packed_not_drawn_qty` in the inventory counts the paid lines of every packed task not drawn yet, any date.
- **Closing the buying** touches only POs and the plan of today's delivery date: `partly_received → received` and `sent → cancelled` (`not_delivered`), each with `closed_at` and a version bump; drafts are left as they are; the plan becomes `closed` (no version bump, as for other status moves). `closed_purchase_orders` counts the POs moved.
- **A run before the surplus time keeps the buying open.** Step 2 happens only when the run's moment (IST) is at or after the hub's `timings["surplus"]` (default 12:30). A manager's run at 03:00 still draws down, expires and offers, but reports `closed_purchase_orders: 0` and leaves the POs and the plan alone, so the sent POs are not cancelled before the 04:00 receiving. The scheduled `surplus_cascade` job always closes the buying, whatever the hub's `surplus` time (`surplus.run(…, close_buying=True)`; M6 §3.5 runs it at T 12:30 for every hub). The manual endpoint leaves `close_buying` unset and follows the time rule, so for a hub whose `surplus` time is 13:00 a manual run at 12:45 keeps the buying open while the job at 12:30 closes it.
- **First deploy: old packed tasks start drawn.** The migration that adds `packing_tasks.drawn_at` sets it to `packed_at` (or `now()` when that is null) for every task already `packed`, so the first run does not draw down against today's lots what packing had already used before the draw-down existed. Nothing extra on downgrade (the column is dropped). `tests/test_db.py` covers it.
- **Open offers for M12.** `hub_ops.api.open_surplus_offers(session, *, hub_id) -> (count of open offers, oldest created_at or None)` feeds the today board's `surplus_open` attention row.
- **New surplus** is a lot with stock that is grade C or whose `best_before` is not after the end of tomorrow (IST): the exact complement of §3.4's usable stock for tomorrow, so a lot whose best-before is exactly tomorrow's midnight is surplus. "Past its best_before" means `best_before ≤ now`.
- **Stock leaves a lot once.** A new offer takes the lot's whole stock with an `offer` move (`ref_id` = the offer); a lot already past its best-before goes straight to the bin with a waste log (`reason: "expired"`, no offer) and a `waste` move (`ref_id` = the log). Whatever later happens to an offer (taken, declined, expired, moved on) changes no stock: those logs carry `lot_id` and `offer_id` but write no move.
- **Offers and logs of the cascade.** `expires_at` is the next IST day's 12:30 after the moment the offer is made (by a run or by a settle), and `created_at` that moment. A kitchen offer's rest becomes an NGO offer with `previous_offer_id`; an NGO offer's rest is binned (`returned` for a returned lot, else `surplus`); anything past its best-before when it moves on is binned as `expired`, from either offer. A taken log has `reason` `surplus` (or `returned`). Logs of the run have `logged_by` null; logs of a settle carry the hub manager and the settle's `note` (to 300). Each offer publishes `hub_ops.surplus_offered`, each log `hub_ops.waste_logged`. An expired offer has `settled_at` = the run, `settled_by` null; a declined one has `taken_qty` null.
- **Idempotent per hub and day:** drawn tasks are marked, closed POs and settled offers are not read again, and surplus lots are left empty, so a second run writes nothing. The database keeps it so: one follower per offer (a partial unique index on `previous_offer_id`), one log per offer and `cascade_to` (a partial unique index on `waste_logs (offer_id, cascade_to)`), and `taken_qty ≤ qty`.
- **Settling.** `taken_by` is required on `taken` (422 `hub_ops.taker_invalid` with `reason`: `missing`, `kind_mismatch` for a kitchen on an NGO offer or the reverse, `not_a_kitchen` for a partner that is not an active kitchen of the offer's hub, and `declined` for a taker on a decline). The kitchen's name in the log is its partner `display_name`. `qty` follows §3.3's quantity rules (`hub_ops.qty_invalid`: above the offer, more than 3 decimals, or not whole for `piece`, `bunch`, `pack`; and `reason: "declined"` for a `qty` sent with a decline); 0 is 422 `request.invalid`. The facade raises `ValueError` for an `outcome` other than `taken` or `declined`. `409 hub_ops.offer_settled` carries `current_status`. The offer is row-locked; another hub's offer is 404 `hub_ops.offer_not_found` for its staff. The endpoint and `hub_ops.api.settle_surplus` run the same code (`surplus.settle`); the facade takes `taken_by` as `hub_ops.api.Taker("kitchen", partner_id=…)` or `Taker("ngo", name=…)`, an optional `note`, and returns `SettleResult { offer, next, waste_logs }` of plain refs; the caller commits.
- **`GET …/surplus-offers?date=&status=`** lists the offers made on that IST day (default today, today − 7 … today + 2), oldest first; an offer's `grade` and `best_before` are its lot's, and `taken_by` is that of its taken log.
- **The KPI** needed no change: `rescued_kg` already sums the logs with `cascade_to` kitchen or NGO, by the IST day of `at` (the run's or the settle's time).
- **Races on separate connections** (`test_surplus_races.py`, committed and cleaned up): a settle holding an expired offer, and a write-off holding its lot, while the run holds the hub and waits for them. Not tested there: two runs of one hub at the same instant; the hub row lock serialises them, and the stock moves' unique key would refuse a second draw.

**Imperfect Sabzi is not offered in v1.** Listing a lot for sale as a discounted add-on for the next day needs a discounted product and price in M2 and add-on rules in M4 (add-ons ride only on a box). Until then the cascade starts at kitchens; the handbook's first step is a later option (§11).

**Kept items and returned bags** (ADR-0027 §2 and §6; built with M7's redelivery PR):
- **Returned bag.** After a box's second miss, M7 refunds its perishables (`undelivered → refunded`) and calls `orders.api.carry_over` with its non-perishable lines; the bag reaches the hub with the van or on a return-leg pickup. Hub staff scan its label at `POST …/returned-bags`. The order must be the hub's box or add-on order with a packed task (else 404 `hub_ops.bag_unknown`) and `refunded` (else 409 `hub_ops.bag_not_returned` with the order's `current_status`: a bag after a first miss waits for the next van and nothing is unpacked). The items M4 carried (`orders.api.carried_lines(order_id)`, M4 §3.8: carried from this order, or carried in it and back after its own second miss; each with `carry_id`, product and steps) become **kept items** of the household, one per carry row and bag, labelled with its society and flat (`identity.api.delivery_labels`); everything else in the bag (the task's picked steps minus the carried steps) becomes one **returned lot** per product: grade C, no cost, `best_before` = the task's `packed_at` + `shelf_life_h`. Returned food is never packed for another customer; the next 12:30 run offers it. A second scan of the same bag answers the first scan's result.
- **Packing a carried line.** When M4 attaches a carried item to the household's next box (`OrderSummary.carried`, with `carry_id` and `carried_from_order_id`; M4 §3.8), the packing task copies it as its own line with `"carried": true` (an additive field on §3.2's `PackingLine`, and part of the line's key, so it never merges with a paid line of the same product). The app shows "Kept item: from the shelf, B-402"; the packer picks it as usual. A carried line takes no shortage (422 `hub_ops.qty_invalid`, `reason: "carried"`); a lost kept item is refunded by the hub manager (`POST …/kept-items/{item_id}/lost` → `orders.api.refund_carry`; the item becomes `lost`); this works until the box is packed, also while it is being picked, and M6 takes the item off the task (a picked one too), so the box completes without it. `complete` marks the `kept` item of that `carry_id` `packed` (with `packed_task_id`) and writes its `pack` move; with none (the bag was never scanned), the move is unlotted. Pack moves of carried lines carry `kept_item_id` (or are unlotted with the carry id); the 12:30 draw-down and any unlotted-packing view count paid lines only. `pick` and `shortage` take an optional `"carried": true` to mean the kept item's line; without it the paid line is meant, or the carried line when the product has no paid line.
- **Expiry.** On `orders.carry_over_expired { order_id, household_id, hub_id, lines: [ { carry_id, … } ] }` (M4's daily job after 14 days, ADR-0027 §6; M4 §3.8) the kept items of those `carry_id`s still `kept` become `released`, each into a returned lot for the next 12:30 run. M1's erase hook releases a household's kept items the same way.

**KPI: kg wasted per 100 kg sold.**

```
GET /v1/hubs/{id}/waste-summary?from=&to=    at most 31 days; default the last 7 up to yesterday
  200 : { "hub_id", "from", "to",
          "days": [ { "date",
                      "sold_kg": 412.6,                    packed that day
                      "wasted_kg": 6.1,                    cascade_to bin
                      "rescued_kg": { "kitchen": 8.0, "ngo": 3.5 },
                      "waste_per_100kg": 1.48 | null,      null when nothing was sold
                      "stockout_pct": 0.9 | null,          steps short or substituted ÷ steps ordered
                      "unweighed_lines": 3 } ],            lines with no weight, left out
          "totals": { "sold_kg", "wasted_kg", "rescued_kg", "waste_per_100kg", "stockout_pct",
                      "unweighed_lines" } }
```

- **Sold** = the picked steps of the packed tasks with that delivery date, weighed as for waste logs. **Wasted** = the `kg` of waste logs with `cascade_to: "bin"` logged that IST day. **Rescued** (kitchen, NGO) is reported but is not waste: food eaten by people is the cascade working. `waste_per_100kg = wasted_kg ÷ sold_kg × 100`, rounded to 2 decimals. `stockout_pct` is the stock-out metric of M-E (handbook 08 §8.3, §8.5).
- **Details.** Sold counts the picked steps of the day's `packed` tasks, a substituted line by its substitute's own step and weight (the packing line keeps the ordered product's step), and leaves out carried lines (sold on an earlier day). `stockout_pct` counts all of the day's tasks: short steps plus the whole of a substituted line, over the steps ordered (carried lines left out). `unweighed_lines` counts picked lines and waste logs (any `cascade_to`) with no weight. `from` and `to` are IST days, inclusive; more than 31 days or `from` after `to` is 422 `hub_ops.date_out_of_range`, and the same code answers a log list with `from` after `to`. A write-off of a product (no lot) also writes an unlotted `waste` move; a product the hub does not stock (a kit or tiffin, or unknown) is 422 `hub_ops.product_not_stocked`.
- The same numbers are served to M10's `monitor_drift` and M12's KPI tiles through `hub_ops.api.waste_summary`.

**Error codes** (in addition to §3.1–§3.4)

| Status | Code | When |
|---|---|---|
| 404 | `hub_ops.offer_not_found` | Unknown offer, or another hub's |
| 404 | `hub_ops.bag_unknown` | Not a `GB1:` code, or not a packed box or add-on order of this hub |
| 409 | `hub_ops.qty_exceeds_stock` | A waste log above the lot's stock |
| 409 | `hub_ops.offer_settled` | Settling an offer that is not open |
| 409 | `hub_ops.bag_not_returned` | A returned-bag scan for an order that is not `refunded` (`current_status`) |
| 422 | `hub_ops.taker_invalid` | A kitchen that is not an active kitchen partner of the hub; `taken_by` on a decline; missing on a take, or of the other kind than the offer (`reason`) |
| 422 | `hub_ops.qty_invalid` | Also: taken `qty` above the offer, a `qty` on a decline (`reason: "declined"`), or a shortage on a carried line (`reason: "carried"`) |

### 3.6 Shortages today (drafted 2026-10-25, frozen on approval)
The packer app's Shortages screen (`routes/_auth/packer/short.jsx`, `useShortages`) and its day summary build against this. Today it calls the path as a mock-only proposal (`mocks/packer.js`). It is one read. The packer still records each bag's shortage with `POST /v1/packing-tasks/{id}/shortage` (§3.2), through the offline outbox as today. Same sign-in, date and review rules as §3.1.

```
GET /v1/hubs/{hub_id}/shortages?date=
    packer, hub_manager, franchise_owner (hub); admin (packing:read). Cache-Control: no-store
    date as §3 (default today, IST)
  200 { "hub_id", "date", "as_of",
        "items": [ ShortItem ] }           largest short_by first, then the reported ones, by name

ShortItem : {
  "product_id", "slug", "name", "step": { "qty", "unit", "label" },   name in Accept-Language
  "needed": 30,         steps of this product on the date's packing lines (carried lines left out)
  "received": 26 | null,  steps the hub has for the date: lots on hand (grade A or B, not past
                        best_before, §3.4) plus what the date's packed tasks already drew; null
                        when the step does not convert to the operations unit (§3.3's conversion)
  "short_by": 4,        needed − received, at least 0; 0 when received is null
  "bags": [ ShortBag ],     the open bags the gap falls on: the last in the queue first (highest
                            seq), until their open steps cover short_by
  "reported": [ ShortBag ] }  bags with a shortage already recorded for this product today

ShortBag : { "task_id", "bag_qr", "society": "Sunrise Towers", "flat_label": "B wing, B-402" | null,
             "qty": 1,                       steps open on the line (bags) or short (reported)
             "outcome": "substitute" | "refund",   bags: what §3.2 would allow (the household's
                                             shortage preference, M1 §3.4, and a whole line not
                                             yet picked); reported: what was recorded
             "substitute": { "product_id", "slug", "name", "step" } | null,
                                             bags: the first of catalog.api.substitute_ids in stock,
                                             not on the order and no dearer (§3.2's rule);
                                             reported: the substitute packed
             "refund_paise": 2000 }          the open (or short) steps × the line's paid unit price;
                                             0 for a substitute that costs the same
```

- **Which items.** A product is listed when `short_by > 0`, or when any of the date's bags has a shortage recorded for it (a short at a bag that the stock did not predict, `received` may then be enough). Tiffins are never here (§3.2).
- **Nothing is decided here.** The list says where the gap falls and what each family's rule allows. The packer still records it bag by bag, and §3.2's checks apply again at that moment, so a stale list can never refund twice or substitute against a household's choice.
- **Data it reads.** Packing lines and tasks (§3.2) and lots (§3.4) are M6's own. Prices come from `orders.api.orders_for_delivery` (already read when the tasks are built); preferences from `identity.api.shortage_preferences`; substitutes from `catalog.api.substitute_ids` and today's prices. No new facade.
- **Personal data.** As the packing queue: the society and `flat_label`, never a name or a phone.

| Status | Code | When |
|---|---|---|
| 403 | `auth.forbidden` | Another hub |
| 422 | `hub_ops.date_out_of_range` | A `date` outside today − 7 … today + 2 |

| Mock today (`mocks/packer.js`) | Contract |
|---|---|
| `GET /v1/hubs/{id}/shortages` → `{ items: ShortItem[] }` | Same path and `items`, plus `hub_id`, `date` and `as_of`; each item gains `short_by` |
| `needed`, `received` (steps), null for an item reported at a bag | Same names and unit; `received` is null only when the step does not convert, and an item reported at a bag carries both numbers |
| `bags` (the bags the gap falls on), `reported` | Same, in the same order rule (the end of the queue first) |
| `outcome: "swap" \| "refund"` | `"substitute" \| "refund"`, M1's preference words |
| `society`, `flat_label`, `qty`, `substitute`, `refund_paise`, `task_id`, `bag_qr` | Same |

**As built (the read, `hub_ops/shortages.py`).** Details the contract leaves open:
- **Needed and received.** `needed` counts every non-carried packing line of the product on the date, a line with a shortage or a substitute recorded included (so the gap stays visible while the packer works down it). `received` is the grade A and B lots on hand not past their best-before (now for today, else the start of the IST day), plus what the date's packed and already drawn-down tasks took (`stock.drawn_picks`), floored to whole steps. It is null when the step's unit is unknown or the product's stock is counted in another unit than the step's operations unit (a lot in pieces for a step in grams).
- **Bags.** Open bags are tasks not yet packed, from the highest `seq` down, whose line of the product has nothing short or substituted and steps still open; their open steps add up until they cover what is still short, and the bag that crosses it is included. What is still short is what the date's lines still take of the product (each line's steps less those reported short; a substituted line takes its substitute instead) less `received`, so a shortage already recorded is not laid on a second bag; it equals `short_by` until something is reported. A line with a step already picked is still listed (with its open steps) but can only be `refund`. `reported` lists every bag with a shortage or substitute recorded for the product, also from the end of the queue, packed or not.
- **Outcome.** `substitute` when the household's preference is not `refund`, nothing is picked on the line, and the catalog's ranked list has a product that is not on the order, has a price at the hub no higher than the line's paid price, and whose free stock covers the line's steps: the substitute's `received`, counted the same way, less what the date's lines already take of it (its own orders and the bags it already went into), used up further as the listed bags take it; else `refund` for the open steps. A substitute's `refund_paise` is the line's steps times the price difference. For a reported substitute the paid price on the order is already the substitute's and M4 keeps no copy of the original's (the exact difference is only in M5's ledger, which M6 does not read, §10), so the original's price is today's price at the hub (0 if it has none): an estimate, exact while the price has not changed since the order locked.
- **Order.** Largest `short_by` first, then name, so the reported-only items (short_by 0) come last.
- **Errors.** As the packing queue: 403 `auth.forbidden` for another hub, 422 `hub_ops.date_out_of_range`. The path is `GET /v1/hubs/{id}/shortages` with `packing:read`, so a packer, hub manager, franchise owner or admin reads it.

### Facade for other modules (`gharbasket.modules.hub_ops.api`)
- `get_hub(session, hub_id) -> HubRef | None` — `{ id, slug, name, status, lat, lng, timings }`.
- `active_hub_ids(session) -> list[UUID]` — for jobs that run per hub.
- `society_loads(session, hub_id, delivery_date) -> list[SocietyLoad]` — `{ society_id, bags, packed_bags, crates }` per society, for M7's vehicle loads and the captain's handover counts.
- `bag_for_order(session, order_id) -> BagRef | None` — `{ bag_qr, bags, crate_no, status }`, for M7's door scan.
- `settle_surplus(session, offer_id, outcome, *, qty=None, taken_by=None, actor_user_id=None) -> SettleResult` — `{ offer, next, waste_logs }` with §3.5's rules and errors; for M8 (kitchens) and M9 (NGOs) when they consume `hub_ops.surplus_offered`; the console's settle endpoint calls it today.
- `waste_summary(session, hub_id, date_from, date_to) -> list[WasteDay]` — §3.5's daily rows (`sold_kg`, `wasted_kg`, `rescued_kg`, `waste_per_100kg`, `stockout_pct`), for M10's `monitor_drift` and M12's KPI tiles.

## 4. Tables owned
| Table | Key columns |
|---|---|
| `hub_ops.hubs` | id (uuidv7), slug (unique), name, address, location geography(Point, 4326), service_area geography(Polygon, 4326) null, status (active, paused), timings jsonb, procurement_buffer_pct smallint, pre_live_box_fee_paise bigint (default 3000, 0 to 10,000), data_source, version, created_at, updated_at |
| `hub_ops.vendors` | id, hub_id → hubs, name (unique per hub, case-insensitive), kind (apmc_trader, fpo, farmer, wholesaler), phone_e164 null, whatsapp, is_default (partial unique per hub), notes, active, version, created_at |
| `hub_ops.packing_tasks` | id, hub_id → hubs, order_id (unique), order_kind, delivery_date, slot_id, society_id, tower_id, flat, seq, route_id null, stop_seq null, status (queued, picking, packed), packer_user_id null, bags, crate_no null, started_at, packed_at, drawn_at null (§3.4, added with the draw-down), version, created_at; index (hub_id, delivery_date, status, seq) |
| `hub_ops.packing_lines` | task_id → packing_tasks, product_id (the ordered product), carried (bool, default false; §3.5, added with kept items), step_qty, step_unit, qty, picked_qty, short_qty, substitute_product_id null, updated_at; primary key (task_id, product_id), becoming (task_id, product_id, carried) with kept items |
| `hub_ops.packing_events` | id, task_id, action (start, pick, shortage, complete, release), user_id, payload jsonb, client_at null, at |
| `hub_ops.procurement_plans` | (§3.3) id (uuidv7), hub_id → hubs, delivery_date, unique (hub_id, delivery_date), status (draft, ordered, closed), source (ml, fallback), model null, buffer_pct smallint, built_at, refreshed_at null, version, created_at |
| `hub_ops.procurement_lines` | (§3.3) plan_id → procurement_plans, product_id, unit, shelf_life_h null, committed_qty, at_risk_qty, forecast_q50 null, forecast_qtau null, basis (forecast, buffer), buffer_qty, on_hand_qty, suggested_qty, order_qty (all `NUMERIC(10,3)`, ≥ 0), edited, edit_reason null, vendor_id → vendors null, unit_cost_paise BIGINT null, note null, updated_at; primary key (plan_id, product_id) |
| `hub_ops.purchase_orders` | (§3.3) id, number null (unique; set when sent), plan_id → procurement_plans, hub_id → hubs, vendor_id → vendors, delivery_date, status (draft, sent, partly_received, received, cancelled), plan_version, total_paise BIGINT, unpriced_lines, share_text, cancel_reason null, created_by, created_at, sent_at null, sent_by null, closed_at null, version; partial unique (plan_id, vendor_id) where status = 'draft' |
| `hub_ops.purchase_order_lines` | (§3.3) po_id → purchase_orders, product_id, unit, qty, unit_cost_paise BIGINT null, amount_paise BIGINT null, received_qty, accepted_qty; primary key (po_id, product_id) |
| `hub_ops.goods_receipts` | (§3.4) id, hub_id → hubs, vendor_id → vendors, purchase_order_id → purchase_orders null, received_at, received_by, total_paise BIGINT, unpriced_lines, created_at; index (hub_id, received_at) |
| `hub_ops.goods_receipt_lines` | (§3.4) id, receipt_id → goods_receipts, product_id, unit, received_qty, accepted_qty (≤ received_qty), grade (A, B, C), reject_reason null (rotten, damaged, undersized, wrong_item, other; set when accepted < received), unit_cost_paise BIGINT null, amount_paise BIGINT null, best_before, ordered, note null; unique (receipt_id, product_id) |
| `hub_ops.inventory_lots` | (§3.4) id, hub_id → hubs, product_id, unit, source (receipt, returned), receipt_line_id → goods_receipt_lines null, order_id null (returned), grade (A, B, C), qty_in, qty_on_hand (≥ 0), unit_cost_paise BIGINT null, received_at, best_before; partial index (hub_id, product_id, best_before) where qty_on_hand > 0 |
| `hub_ops.stock_moves` | (§3.4) id, hub_id, lot_id → inventory_lots null (null: unlotted), kept_item_id → kept_items null, product_id, unit, qty (signed), kind (receive, return, pack, waste, offer, count), ref_id (receipt line, packing task, waste log, offer or count), reason null, by_user_id null, at; unique (kind, ref_id, product_id, lot_id) nulls not distinct |
| `hub_ops.waste_logs` | (§3.5) id, hub_id → hubs, lot_id → inventory_lots null, product_id, qty, unit, kg `NUMERIC(10,3)` null, reason (spoiled, damaged, expired, pest, surplus, returned, other), cascade_to (kitchen, ngo, bin), offer_id → surplus_offers null, taken_by_kind null, taken_by_partner_id null, taken_by_name null, note null, logged_by null, at; index (hub_id, at); partial unique (offer_id, cascade_to) where offer_id is not null |
| `hub_ops.surplus_offers` | (§3.5) id, hub_id → hubs, lot_id → inventory_lots, product_id, qty, unit, offered_to (kitchen, ngo), status (open, taken, declined, expired), taken_qty null, previous_offer_id → surplus_offers null (partial unique: one follower per offer), expires_at, settled_by null, settled_at null, created_at; taken_qty ≤ qty; index (hub_id, status) |
| `hub_ops.kept_items` | (§3.5) id, hub_id → hubs, household_id, carry_id (M4's `orders.carry_overs` row), from_order_id, bag_order_id (the scanned bag), society_id, tower_id null, flat null, product_id, step_qty, step_unit, qty (steps), status (kept, packed, released, lost), packed_task_id → packing_tasks null, kept_at, settled_at null; unique (carry_id, bag_order_id) |
| `hub_ops.returned_bags` | (§3.5) order_id (primary key), hub_id → hubs, scanned_by, scanned_at: one unpacking per bag |
| `hub_ops.vendor_scores` | (§3.4) vendor_id → vendors, week (the Monday), freshness, price, reliability, defects `NUMERIC(4,3)` null each, score `NUMERIC(4,3)`, pos_count, receipt_lines_count; primary key (vendor_id, week) |

Foreign keys stay inside `hub_ops`; `order_id`, `slot_id`, `society_id`, `household_id`, `product_id`, partner ids and user ids are plain UUIDs (ADR-0026 §5). Operations quantities are `NUMERIC(10,3)` everywhere above; money is `BIGINT` paise.

## 5. Events
**Published:**
- `hub_ops.hub_created { hub_id, slug, name }` · `hub_ops.hub_updated { hub_id, changed }` (§3.1)
- `hub_ops.procurement_plan_ready { plan_id, hub_id, delivery_date, source, model, lines_count, order_paise, unpriced_lines, version }` (§3.3) — when a plan is built, and again when a rebuild changes it; M11 may tell the hub manager (nobody consumes it yet)
- `hub_ops.purchase_order_sent { purchase_order_id, number, plan_id, hub_id, vendor_id, delivery_date, lines_count, total_paise }` (§3.3) — one per PO (replaces the outline's `purchase_orders_sent`)
- `hub_ops.goods_received { receipt_id, hub_id, vendor_id, purchase_order_id, lines_count, rejected_lines, total_paise }` (§3.4)
- `hub_ops.waste_logged { waste_log_id, hub_id, lot_id, product_id, qty, unit, kg, reason, cascade_to }` (§3.5) — M10 (waste in `monitor_drift`), M12
- `hub_ops.surplus_offered { offer_id, hub_id, lot_id, product_id, qty, unit, grade, best_before, offered_to, expires_at }` (§3.5) — M8 (kitchens) and M9 (NGOs) later

Packing publishes nothing of its own: M4 publishes `orders.packed` and `orders.shortage_substituted` when M6 calls `orders.api` (the old `hub_ops.shortage_detected` is dropped). Stock moves, counts and kept items publish nothing.

**Consumed:** `intel.forecast_ready { hub_id, delivery_dates, model, data_source }` (rebuild the listed dates' plans that are not closed, §3.3), `logistics.route_published` (re-sequence queued tasks, §3.2), `orders.carry_over_expired` (release kept items, §3.5; with M4's carry-over), `logistics.pickup_completed` (each bag in `bag_order_ids` goes through the returned-bag intake of §3.5 with the rider as `scanned_by`; a bag the intake refuses is logged and skipped; M7 §3.6).

## 6. Jobs
| Job | Trigger (IST) | Notes |
|---|---|---|
| `build_packing_tasks` | T 05:00 (cron-job.org → `POST /v1/internal/jobs/build_packing_tasks`) | Every active hub; idempotent per order; §3.2 |
| `build_procurement_plan` | T-1 21:10 (cron-job.org) | Every active hub: builds or rebuilds T's plan with the stored forecast or the buffer; §3.3 |
| `refresh_procurement_plan` | T-1 23:05 (cron-job.org) | Rebuilds T's existing plans that are not closed, after `payment_grace_expiry` (23:00); §3.3 |
| `surplus_cascade` | T 12:30 (cron-job.org) | Every active hub: draws down packing (§3.4), closes the day's POs and plan, moves open offers on, offers new surplus (§3.5) |
| `compute_vendor_scores` | Monday 03:33 (GitHub Actions) | The previous IST week; §3.4 |

Each job is idempotent, takes its advisory lock and uses `scheduled_for` (the delivery day follows from it), not the run time (ADR-0021, ADR-0025).

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `hubs:read` | packer, rider, kitchen_partner, hub_manager, franchise_owner (hub); admin |
| `hubs:write` | admin |
| `vendors:read` · `vendors:write` | hub_manager (hub), admin · franchise_owner reads |
| `packing:read` | packer, hub_manager, franchise_owner (hub), admin (also the shortages read, §3.6) |
| `packing:write` | packer (hub), hub_manager (hub) |
| `packing:manage` | hub_manager (hub), admin (build, release) |
| `procurement:read` · `procurement:write` | hub_manager (hub), admin · franchise_owner reads (plans, lines, purchase orders) |
| `inventory:read` | packer, hub_manager, franchise_owner (hub), admin (receipts, lots, waste logs, offers, kept items, waste summary) |
| `receiving:write` | hub_manager, packer (hub) (goods receipts, returned bags) |
| `inventory:manage` | hub_manager (hub), admin (counts, surplus run, settling offers) |
| `waste:write` | hub_manager, packer (hub) |

## 8. Screens
**Mobile (packer):** Queue (with "Next task"), Pick/Pack (scan the bag label, step through lines, shortage sheet with substitutes), offline indicator for queued actions; with kept items, a "Kept item: from the shelf" hint on carried lines and a "Returned bag" scan. The packer route on `main` is a placeholder (`FieldShell`), so no mock shapes need mapping yet. **Console:** Ops board (today: packing summary per society, plan and PO status, open offers), Packing board, Hubs admin (map with terra-draw for the service area), Vendors (with the weekly score), **Procurement** (the plan table with `basis` and at-risk chips, the line sheet with a required reason, "Generate POs", each PO's "Send on WhatsApp" link and print view), **Receiving/QC** (tablet-sized: pick a sent PO or "ad hoc", weigh, grade, reject reason, cost), **Inventory** (lots by product, expiring soon, unlotted packing, count), **Waste and cascade** (write-off form, open offers with settle, the waste summary), kept items shelf list, bag label sheet (printed from the task list). Receiving stays in the console in v1 (a tablet at the dock); a mobile receiving screen is a later option.

## 9. Tests required
- Hubs (§3.1): slug unique and immutable; polygon validation (open ring, self-intersection, holes); `If-Match`; hub staff see only their hubs; `hub_created` once per hub; the seed creates `SEED_HUB_ID` and every hub id in the seed world exists.
- Vendors: name unique per hub; one default per hub; deactivate keeps history.
- Packing (§3.2):
  - building twice creates no duplicate task; tiffin orders are skipped; sequence by route when published, else slot, society, tower, flat;
  - two packers calling `next` at once get different tasks (concurrent sessions);
  - start → pick → complete moves the order `locked → picking → packed` with actor `packer` and the packer's user id; an order already moved by a hub manager returns `orders.illegal_transition` and leaves the task unchanged;
  - shortage without substitute refunds exactly once (replay and a second shortage); whole-line substitute only from the listed substitutes, never dearer; partial substitute refused;
  - complete with open lines refused; crate numbering three bags to a crate;
  - offline replay: the same key returns the same answer, a changed body is `request.idempotency_mismatch`, an absolute pick under a new key changes nothing.
- Procurement (§3.3):
  - committed counts locked, picking and packed box and add-on lines at `qty − shortage_qty`, the substitute's product for a substituted line, never tiffins, kits or carried lines; at-risk counts `payment_failed` and is not bought;
  - the buffer basis at 0 %, 10 % and 50 %; the short-life cap (`shelf_life_h` 48 capped at 5 %, 49 not); the forecast basis converts steps to kg (`g` steps), l (`ml` steps) and pieces; a product missing from the forecast falls back to the buffer while `source` stays `ml`; `hub_forecast` returning `None` gives `fallback`;
  - on-hand netting uses only grade A/B lots lasting past the delivery day; suggested never negative; rounding up to 0.1 kg and to whole pieces (hypothesis: suggested ≥ committed − on hand, and ≤ committed × (1 + buffer) − on hand + one rounding unit);
  - a rebuild (job twice, `intel.forecast_ready`, the 23:05 refresh) keeps edited quantities, vendors, costs and sent lines and creates no duplicate plan; `order_qty: null` un-edits; an edit without a reason, on a sent line or a closed plan is refused; every edit is audited;
  - PO generation groups by vendor, replaces drafts, refuses lines without a vendor (`product_ids`) and changes nothing then; a stale draft cannot be sent; amounts round half up (`0.1 kg × 3205` → 321 paise) and unpriced lines stay out of totals; `whatsapp_url` only with a phone and WhatsApp; the same Idempotency-Key replays.
- Receiving and stock (§3.4):
  - a receipt against a PO adds to its lines and moves it to `partly_received`, then `received`; a second receipt, an unordered product and an ad hoc receipt; every `hub_ops.receipt_invalid` reason; rejected goods create no lot;
  - each accepted line makes one lot with the default or given `best_before`; grade C is never netted or drawn;
  - the draw-down is FEFO across lots, draws a substitute's product, records unlotted quantity, sets `drawn_at` and never moves stock twice when the run repeats; `qty_on_hand` always equals the sum of its moves and never goes below 0 (property test over random receipts, packs, waste and counts);
  - vendor scores on a hand-made week: each component, a lone vendor's price score of 1, a `not_delivered` PO, missing components re-weighted.
- Waste and surplus (§3.5):
  - a write-off with and without a lot; above stock refused; `kg` from kg, l and `unit_g`;
  - the run closes the day's POs (`not_delivered`) and plan, offers grade C and short-dated lots exactly once, bins what is past `best_before`, and moves yesterday's open offers on (kitchen → NGO → bin); a repeated run changes nothing;
  - settle: taken in full and in part (the rest moves on), declined, a settled offer refused, a kitchen taker that is not the hub's active kitchen partner refused;
  - the KPI on a hand-made day: bin counts as waste, kitchen and NGO as rescued, unweighed lines counted, no sales gives `null`.
  - kept items (with M7's PR): a refunded bag splits into kept items (M4's carried lines) and grade C returned lots; a bag after a first miss is refused; a second scan replays; a carried line is packed from the household's kept item and takes no shortage; `orders.carry_over_expired` releases kept items into returned lots.
- E2E (MS-B): seeded hub → cut-off → 21:10 plan (buffer basis) → generate and send POs → goods receipt against them → packing → `orders.packed` for every order → 12:30 run draws stock down and offers the leftovers → the waste summary shows the day, on the simulator seed.

## 10. Dependencies
M4 (`orders.api`: `orders_for_delivery` with `kinds`, `transition` with the new actors, `mark_shortage` with refunds and substitutes; ADR-0026 §2, §6; for §3.5 `carried_lines`, `carried_from_order_id` on summary lines and `orders.carry_over_expired`, ADR-0027), M2 (`catalog.api`: products, steps, kinds, shelf life, `unit_g`, substitutes, prices; `stock_products` below), M1 (`identity.api.delivery_labels`; the erase hook for kept items), M9 (`community.api`: society names and tower labels; NGOs later), M10 (`intel_gateway.api.hub_forecast` and `intel.forecast_ready`, PRs #118/#122; optional: plans fall back to the buffer without them), M7 (`logistics.route_published`; reads `society_loads`; the second miss that leads to returned bags), M8 (`partners.api.get_partner` to check a kitchen taker; later a consumer of `hub_ops.surplus_offered`), M9 later for NGO offers. **Not** M5: purchase orders and receipts record costs but post no ledger entries.

## 11. Open questions and decisions
- **Decided (ADR-0026):** M6 owns hubs; hub ids elsewhere have no foreign key; crates are counted, not tracked; operations quantities in kg, l, piece, bunch, pack; packing does not wait for routes.
- **Decided (this spec):** a substitute replaces a whole line only, at a price no higher than the line's; partial shortages are refunded.
- **Decided by the owner (2026-10-09, ADR-0026):** substitute by default, but never for households that chose "never substitute, refund" (M1 preference, read through `identity.api`); routes are solved at T-1 23:15, so packing at 05:00 normally follows route order; one captain per society; a missed hold window gets one free redelivery the next morning (M7).
- **Decided (this spec, §3.3–§3.5, 2026-10-09; the owner may change any of them before approval):**
  - **Plan lines carry their own basis.** A line uses M10's `uncommitted_qtau` when the forecast has the product, else the hub's buffer; the cut-off's exact count replaces M10's `committed`. MS-B runs on the buffer; MS-C's "forecast-driven procurement" only changes `basis`.
  - **At-risk demand is shown, not bought.** Orders in the payment grace are listed (`at_risk_qty`); the 23:05 refresh moves the paid ones into `committed_qty` on lines not yet sent. This keeps the outline's rule (locked lines × (1 + buffer) − stock).
  - **Purchase orders start as drafts** that the hub manager sends herself (WhatsApp link or print); nothing reaches a vendor automatically, and a draft made before the plan's last change cannot be sent.
  - **Packing draws stock down once a day, in the 12:30 run, not at `complete`** (FEFO, unlotted when no lot covers it). It leaves the built packing code (§3.2) untouched, keeps lot row locks out of the packing rush, and still gives the 12:30 cascade and the 21:10 plan the right stock. The cost: on-hand during the morning is "received minus written off", with `packed_not_drawn_qty` beside it.
  - **Every stock change is a stock move** (a small stock ledger), so on-hand is auditable and replays never move stock twice.
  - **Grade C and returned food are never packed for a customer**; they go to kitchens, NGOs or compost.
  - **The cascade in v1 is kitchens → NGO → compost, settled by the hub manager.** Offers wait until the next day's run, which moves them on.
  - **The KPI counts only compost (`bin`) as waste**; kitchen and NGO quantities are "rescued" and reported beside it. This changes the outline, which counted NGO hand-offs as waste.
  - **Costs without money movement.** Purchase orders and receipts record expected and actual costs in paise for the books and vendor scores; no ledger entry, no vendor payout.
- **Decided by the owner (2026-10-09)** (every recommendation of this section's open questions, accepted as written):
  1. ~~Real vendors and APMC Vashi timings: which traders, FPOs and wholesalers, when they deliver, whether they take WhatsApp POs?~~ Decided: seed three vendors for the demo: an APMC trader for vegetables and fruit (the seed's "Shinde Traders", §3.1), an FPO for leafy greens and a wholesaler for staples (both added to `just seed` in the §3.3 PR). Receiving stays 04:00–05:00. Rhythm Thakur and Manish Raje confirm the real vendors, their delivery times and WhatsApp POs on the hub visit before the pilot.
  2. ~~Does packing draw down lots in the prototype, and when?~~ Decided: yes, once a day in the 12:30 run (§3.4, §3.5), not at `complete`. Not drawing down would leave sold stock on the books, so the plan would net it out and under-buy; drawing at `complete` would change the built packing code and lock lots during packing.
  3. ~~The short-life cap line: the outline said "under 48 h", but the seed's shortest shelf life is exactly 48 h (palak, methi, coriander, milk), so the cap would never apply.~~ Decided: `shelf_life_h ≤ 48`, as written in §3.3.
  4. ~~What the forecast means after the cut-off, now that add-ons ride only on boxes (M4, 2026-10-09) and tiffins are not bought by the hub.~~ Decided: keep this contract (each plan line carries its own `basis`), and M10 redefines the M-E target for MS-C, for example as the gap between ordered and accepted quantity (receiving yield and stock-outs, §3.4) or as total demand for staples bought ahead (M10 follow-up below).
  5. ~~Imperfect Sabzi?~~ Decided: left out of the prototype, since it needs a discounted product per lot in M2 and an add-on rule in M4. It is added as the cascade's first step at the pilot if the KPI shows enough grade B surplus to sell (§3.5).
  6. ~~Kitchen surplus: free, or an ingredient credit?~~ Decided: free in the prototype (§3.5). A credit needs a price, an M5 ledger account and M8's rate card, which no flow needs before the pilot.
  7. ~~Paying vendors?~~ Decided: outside the platform in the prototype (cash or UPI by the hub, as APMC trade works); purchase orders and receipts record costs only (§3.3, §3.4). Vendor payables and inventory accounting in M5 are a pilot-phase decision.
  8. ~~Labels?~~ Decided: a printed sheet from the console in the prototype, and a thermal label printer at the pilot.
- **Follow-ups for other specs** (made in the PRs that need them, reviewed by both owners):
  - ~~**M4:** `orders.api.carried_lines`, `carried_from_order_id`, a carried line never merged with a paid one and refused by `mark_shortage`, `orders.carry_over_expired`, how a hub manager refunds a lost kept item (ADR-0027 §2, §6).~~ Specified in M4 §3.8 (2026-10-20): carried items are M4 rows (`orders.carry_overs`) shown as `OrderSummary.carried`, not order lines, so they never reach `mark_shortage`, totals or procurement; a lost item is `orders.api.refund_carry`.
  - **M2:** `catalog.api.stock_products(session, product_ids) -> {id: StockProduct}` with `kind`, `step_qty`, `step_unit`, `shelf_life_h`, `unit_g` (from nutrition), names and shelf; additive. When kits are built: whether the hub assembles them from ingredient lots (then plans buy the ingredients).
  - **M10:** the endpoint table's `/forecast/run` row says the API writes `hub_ops.procurement_lines`; M6 builds its own lines from `hub_forecast` (M10 §3.6 already says so), so that row should drop it. `monitor_drift`'s "waste waits for M6" reads `hub_ops.api.waste_summary`. M10 redefines the M-E target for MS-C (decision 4 above).
  - **M7:** §3.6 says M6 consumes `logistics.pickup_completed` for returned bags; M6 instead relies on the hub's scan (`POST /v1/hubs/{id}/returned-bags`), so M7's consumer list should drop M6.
  - **M8, M9:** consumers of `hub_ops.surplus_offered` calling `hub_ops.api.settle_surplus` (later); M9 `community.api` NGO lookup so `taken_by` can name a listed NGO.
  - **M1:** the erase hook releases a household's kept items (§3.5).
  - ~~**M5:** `lost` when the wallet is full answers 422 `billing.wallet_limit` and the item stays kept, until M5's per-order refund to the payment source.~~ Done by M5 §3.3 (2026-10-21): a full wallet sends the refund to the payment source. `billing.wallet_limit` is left only for a refund the customer's top-ups cannot take either.
  - **Handbook:** 05 §5.5 (the `hub_ops` ERD: `best_before`, stock moves, purchase orders, receipts, offers, kept items; the schema is `hub_ops`, not `ops`); 06 (purchase order and lot lifecycles); 07 W4 (the 21:10 plan and its rebuild, draft and sent POs, receiving at 04:00) and W9 (kitchens → NGO → compost in v1, the draw-down at 12:30, Imperfect Sabzi later, compost-only waste in the KPI).
