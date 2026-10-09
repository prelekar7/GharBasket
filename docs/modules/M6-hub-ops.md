# M6 — Hub Operations

**Status:** Draft v2 (with [ADR-0026](../adr/0026-operations-contract.md)). §3.1 (hubs, service areas, vendors) and §3.2 (packing) are frozen for S4 when this spec and ADR-0026 are approved; §3.3 (procurement), §3.4 (receiving and inventory) and §3.5 (waste and surplus) are outlines, refined in the PR that builds each.
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P3 / S4
**Postgres schema:** `hub_ops` · **Python package:** `gharbasket.modules.hub_ops`

## 1. Purpose
Everything inside the hub (the franchise fulfilment centre): **hubs** and their service areas (M6 is the owner of hub ids, ADR-0026), vendors, procurement plans (committed demand plus a forecast buffer), purchase orders, goods receipt with QC grading, inventory lots with expiry, **packing tasks** with bag labels and shortage handling, waste logging and the surplus cascade. M6 moves orders only through `orders.api` (M4 owns the order and its states), takes forecasts from M10 through `intel_gateway.api`, and hands crates to M7. It does not route, pay anyone or list products for sale.

**Build order for MS-B:** (a) hubs, service areas, vendors → (b) packing → (c) procurement plan and purchase orders → (d) goods receipt and lots → (e) waste and surplus cascade. (a) and (b) are frozen below; (c)–(e) wait for inputs that are still moving (the forecast from PRs #118/#122, the inventory model, M8/M9 consumers).

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As an admin, I want to create a hub with its service area so that societies, prices, slots and staff can point at a real hub. | Hub with location, polygon and day timings; `hub_ops.hub_created`; the seed hub exists. |
| US-2 | As a hub manager, I want a vendor list so that purchase orders go to the right people. | Vendors per hub; one default vendor; deactivate instead of delete. |
| US-3 | As a packer, I want a packing queue in loading order so that crates are loaded in the right sequence. | Tasks built at T 05:00; ordered by route stop when routes exist, else by slot, society, tower, flat. |
| US-4 | As a packer, I want to scan a bag, pick each line and flag shortages so that customers are treated fairly. | Start → pick → complete moves the order `locked → picking → packed`; a shortage refunds or substitutes through `orders.api.mark_shortage`. |
| US-5 | As a packer on a weak network, I want my taps saved and sent later so that I never pack twice. | Every action has an `Idempotency-Key` made when tapped; replays return the first answer. |
| US-6 | As a hub manager, I want a procurement plan right after cut-off so that I buy only what's needed. | At T-1 21:10 a plan lists committed qty, forecast q50/qτ (or the rule-based buffer), order qty and vendor per item. |
| US-7 | As a hub manager, I want to adjust lines and send purchase orders so that vendors know what to deliver. | Edits audited; one PO per vendor with a WhatsApp-ready text. |
| US-8 | As a hub manager, I want to record goods received with weight and grade so that inventory and vendor scores are accurate. | A goods receipt creates lots with expiry; rejects feed vendor scores. |
| US-9 | As a hub manager, I want surplus routed through the cascade so that waste is minimised. | 12:30 job offers lots to Imperfect Sabzi, kitchens, NGOs, then compost; each step logged. |
| US-10 | As an analyst, I want kg wasted per 100 kg sold so that the zero-waste claim is measurable. | Computed per hub and day from waste logs and packed quantities. |

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
| GET | `/v1/hubs/{id}/procurement-plans?date=` | hub_manager, franchise_owner (hub) | — | §3.3 (outline) |
| POST | `/v1/hubs/{id}/procurement-plans/build` | hub_manager (hub) | Required | §3.3 |
| PATCH | `/v1/procurement-plans/{id}/lines/{product_id}` | hub_manager (hub) | `If-Match` | §3.3 |
| POST | `/v1/procurement-plans/{id}/purchase-orders` | hub_manager (hub) | Required | §3.3 |
| GET | `/v1/hubs/{id}/purchase-orders?date=` | hub_manager, franchise_owner (hub) | — | §3.3 |
| POST | `/v1/hubs/{id}/goods-receipts` | hub_manager, packer (hub) | Required | §3.4 (outline) |
| GET | `/v1/hubs/{id}/inventory` | hub_manager, packer (hub) | — | §3.4 |
| GET / POST | `/v1/hubs/{id}/waste-logs` | hub_manager, packer (hub) | Required (POST) | §3.5 (outline) |
| GET | `/v1/hubs/{id}/waste-summary?from=&to=` | hub_manager, franchise_owner (hub), admin | — | §3.5 |
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
        "version": 1, "created_at", "updated_at" }
HubSummary : { "id", "slug", "name", "status" }

GET /v1/hubs                    200 { "items": [ HubSummary ] }   admin: every hub; hub staff: the
                                hubs in their grants; by name
GET /v1/hubs/{id}               200 Hub (ETag = version) | 404 "hub_ops.hub_not_found"
POST /v1/admin/hubs             admin; Idempotency-Key
  request : { "slug", "name", "address", "location", "service_area"?, "timings"?,
              "procurement_buffer_pct"? }       slug: lower-case letters, digits and "-", 3 to 40,
                                                unique, never changes; name 3 to 80; address up to 300
  201 : Hub (status active; timings default to the values above); publishes hub_ops.hub_created
PATCH /v1/admin/hubs/{id}       admin; If-Match (the hub's version)
  request : any of { "name", "address", "location", "service_area" (null clears it), "status",
                     "timings", "procurement_buffer_pct" }
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

### 3.3 Procurement plan and purchase orders (outline; refined at its PR)
The forecast arrives with the ML chain (PRs #118/#122): `intel_gateway.api.hub_forecast(session, hub_id, delivery_date)` returns `items[{ product_id, committed, uncommitted_q50, uncommitted_qtau, order_qty, model }]` in **steps** (M10 §3.6), or `None` when the job has not run.

```
ProcurementLine : { "product_id", "slug", "name", "unit": "kg",
  "committed_qty": 42.5,        Σ locked order lines for the date, in the operations unit
  "at_risk_qty": 1.5,           Σ payment_failed lines (still in the 2 h grace)
  "forecast_q50": 3.0 | null, "forecast_qtau": 5.0 | null,   uncommitted, converted from steps
  "on_hand_qty": 0.0,           usable lots expiring after the delivery day (0 until §3.4)
  "suggested_qty": 47.5,        see below
  "order_qty": 50.0,            suggested, or the hub manager's edit
  "vendor": { "id", "name" } | null, "unit_cost_paise": 3200 | null,   per operations unit
  "note": "…" | null, "edited": false }
ProcurementPlan : { "id", "hub_id", "delivery_date", "status": "draft" | "ordered" | "closed",
  "source": "ml" | "fallback", "model": "AutoETS" | null, "built_at", "version",
  "lines": [ ProcurementLine ], "total_paise": 152300 }
PurchaseOrder : { "id", "plan_id", "vendor": { "id", "name", "phone" }, "delivery_date",
  "status": "sent" | "received" | "cancelled",
  "lines": [ { "product_id", "name", "qty", "unit", "unit_cost_paise", "amount_paise" } ],
  "total_paise", "share_text": "…", "created_at" }
```

- **Quantity.** With a forecast: `suggested = committed + forecast_qtau − on_hand` (the newsvendor rule; τ is M10's). **Rule-based fallback** when `hub_forecast` returns `None` or the product has no forecast line: `suggested = committed × (1 + procurement_buffer_pct / 100)` (default 10 %) `− on_hand`. Never below `committed − on_hand`, never negative; products with `shelf_life_h` under 48 h get no buffer beyond 5 %. Rounded up to 0.1 kg or l, or to whole pieces, bunches and packs. `source` says which rule built the plan, and the console shows it.
- **When.** `build_procurement_plan` at T-1 21:10 for delivery day T. A consumer of `intel.forecast_ready` rebuilds a **draft** plan when the forecast lands; `refresh_procurement_plan` at T-1 23:05 (after M4's `payment_grace_expiry`) updates `committed_qty` and `at_risk_qty`. A rebuild keeps edited lines' `order_qty`.
- **Edit** (`PATCH`, `If-Match`): `order_qty` (≥ 0), `vendor_id`, `note`; only while `draft` (409 `hub_ops.plan_not_editable`); each edit goes to `platform.audit_log` with old and new values.
- **Purchase orders** (`POST …/purchase-orders`, Idempotency-Key): one PO per vendor for lines with `order_qty > 0`; lines without a vendor → 422 `hub_ops.vendor_missing` (`product_ids`); the plan becomes `ordered`. `unit_cost_paise` is the last accepted receipt's cost for that product at the hub, else null; `amount_paise = round(qty × unit_cost_paise)` (half up). `share_text` is a plain-text PO for WhatsApp or print; nothing is sent automatically in v1. Event `hub_ops.purchase_orders_sent`.
- **Vendor suggestion:** the vendor of the product's last purchase order at the hub, else the hub's default vendor.

### 3.4 Goods receipt and inventory lots (outline; refined at its PR)
- `POST /v1/hubs/{id}/goods-receipts` (Idempotency-Key): `{ "purchase_order_id" | null, "vendor_id", "lines": [ { "product_id", "received_qty", "accepted_qty", "grade": "A" | "B" | "C", "reject_reason"?, "unit_cost_paise", "expires_at"? } ] }` in operations units; `accepted_qty ≤ received_qty`; the rest goes back to the vendor (not waste) and counts against the vendor's score. Each accepted line becomes an **inventory lot** (`expires_at` defaults to received + `shelf_life_h`); grade B lots are first in line for Imperfect Sabzi. The PO becomes `received`. Event `hub_ops.goods_received`.
- **Packing consumes lots** first-expiry-first-out at `complete` (steps × step size); a pick with no lot left is recorded as unlotted, never refused, so packing never waits for receiving data.
- `compute_vendor_scores` (weekly): freshness (share of grade A) 40 %, price against the hub's median 20 %, reliability (received ÷ ordered, on time) 20 %, defects (rejected share) 20 %.

### 3.5 Waste and the surplus cascade (outline; refined at its PR)
- `POST /v1/hubs/{id}/waste-logs` (Idempotency-Key): `{ "lot_id" | null, "product_id", "qty", "reason": "spoilage" | "damage" | "surplus", "cascade_to": "imperfect" | "kitchen" | "ngo" | "bin" }`; it reduces the lot. Event `hub_ops.waste_logged`.
- `surplus_cascade` at T 12:30: every lot with stock left after packing is offered in order: shelf life ≥ 24 h → **Imperfect Sabzi** add-on for the next day; usable for cooking today → **kitchens** as ingredient credit; fit for donation → **NGO**; otherwise **compost** (`bin`). Each offer is a `hub_ops.surplus_offers` row and an event `hub_ops.surplus_offered`; the consumers (M2/M4 for the Imperfect listing, M8 for kitchen credits, M9 for NGO hand-offs) come later and report the outcome through `hub_ops.api.settle_surplus(offer_id, outcome)`. Until then the hub manager settles offers in the console. Every outcome is a waste log with `cascade_to`.
- **KPI:** kg wasted per 100 kg sold = Σ waste logs with `cascade_to ∈ {ngo, bin}` ÷ Σ packed quantity × 100, per hub and day (`waste-summary`; M12 charts it).

### Facade for other modules (`gharbasket.modules.hub_ops.api`)
- `get_hub(session, hub_id) -> HubRef | None` — `{ id, slug, name, status, lat, lng, timings }`.
- `active_hub_ids(session) -> list[UUID]` — for jobs that run per hub.
- `society_loads(session, hub_id, delivery_date) -> list[SocietyLoad]` — `{ society_id, bags, packed_bags, crates }` per society, for M7's vehicle loads and the captain's handover counts.
- `bag_for_order(session, order_id) -> BagRef | None` — `{ bag_qr, bags, crate_no, status }`, for M7's door scan.
- `settle_surplus(session, offer_id, outcome)` — §3.5, later.

## 4. Tables owned
| Table | Key columns |
|---|---|
| `hub_ops.hubs` | id (uuidv7), slug (unique), name, address, location geography(Point, 4326), service_area geography(Polygon, 4326) null, status (active, paused), timings jsonb, procurement_buffer_pct smallint, data_source, version, created_at, updated_at |
| `hub_ops.vendors` | id, hub_id → hubs, name (unique per hub, case-insensitive), kind (apmc_trader, fpo, farmer, wholesaler), phone_e164 null, whatsapp, is_default (partial unique per hub), notes, active, version, created_at |
| `hub_ops.packing_tasks` | id, hub_id → hubs, order_id (unique), order_kind, delivery_date, slot_id, society_id, tower_id, flat, seq, route_id null, stop_seq null, status (queued, picking, packed), packer_user_id null, bags, crate_no null, started_at, packed_at, version, created_at; index (hub_id, delivery_date, status, seq) |
| `hub_ops.packing_lines` | task_id → packing_tasks, product_id (the ordered product), step_qty, step_unit, qty, picked_qty, short_qty, substitute_product_id null, updated_at; primary key (task_id, product_id) |
| `hub_ops.packing_events` | id, task_id, action (start, pick, shortage, complete, release), user_id, payload jsonb, client_at null, at |
| `hub_ops.procurement_plans` / `procurement_lines` | (§3.3) id, hub_id, delivery_date (unique per hub), status, source, model, built_at, version / plan_id, product_id, unit, committed_qty, at_risk_qty, forecast_q50, forecast_qtau, on_hand_qty, suggested_qty, order_qty `NUMERIC(10,3)`, vendor_id, unit_cost_paise, note, edited |
| `hub_ops.purchase_orders` / `purchase_order_lines` | (§3.3) id, plan_id, hub_id, vendor_id, delivery_date, status, total_paise, share_text, created_by, created_at / po_id, product_id, qty, unit, unit_cost_paise, amount_paise |
| `hub_ops.goods_receipts` / `goods_receipt_lines` | (§3.4) id, hub_id, vendor_id, purchase_order_id null, received_by, received_at / receipt_id, product_id, received_qty, accepted_qty, grade, reject_reason, unit_cost_paise |
| `hub_ops.inventory_lots` | (§3.4) id, hub_id, product_id, receipt_line_id null, unit, qty_received, qty_on_hand, grade, cost_paise (per unit), received_at, expires_at |
| `hub_ops.waste_logs` | (§3.5) id, hub_id, lot_id null, product_id, qty, unit, reason, cascade_to, logged_by, at |
| `hub_ops.surplus_offers` | (§3.5) id, hub_id, lot_id, product_id, qty, unit, offered_to (imperfect, kitchen, ngo), status (open, taken, expired), expires_at, settled_at |
| `hub_ops.vendor_scores` | (§3.4) vendor_id, week, freshness, price, reliability, defects, score |

Foreign keys stay inside `hub_ops`; `order_id`, `slot_id`, `society_id`, `product_id` and user ids are plain UUIDs (ADR-0026 §5).

## 5. Events
**Published:**
- `hub_ops.hub_created { hub_id, slug, name }` · `hub_ops.hub_updated { hub_id, changed }` (§3.1)
- `hub_ops.procurement_plan_ready { plan_id, hub_id, delivery_date, source, lines_count, total_paise }` (§3.3)
- `hub_ops.purchase_orders_sent { plan_id, hub_id, delivery_date, purchase_order_ids, total_paise }` (§3.3)
- `hub_ops.goods_received { receipt_id, hub_id, vendor_id, purchase_order_id, accepted_lines, rejected_lines }` (§3.4)
- `hub_ops.waste_logged { waste_log_id, hub_id, product_id, qty, unit, reason, cascade_to }` (§3.5)
- `hub_ops.surplus_offered { offer_id, hub_id, lot_id, product_id, qty, unit, offered_to, expires_at }` (§3.5)

Packing publishes nothing of its own: M4 publishes `orders.packed` and `orders.shortage_substituted` when M6 calls `orders.api` (the old `hub_ops.shortage_detected` is dropped).

**Consumed:** `intel.forecast_ready` (rebuild draft plans, §3.3), `logistics.route_published` (re-sequence queued tasks, §3.2).

## 6. Jobs
| Job | Trigger (IST) | Notes |
|---|---|---|
| `build_packing_tasks` | T 05:00 (cron-job.org → `POST /v1/internal/jobs/build_packing_tasks`) | Every active hub; idempotent per order; §3.2 |
| `build_procurement_plan` | T-1 21:10 (cron-job.org) | Every active hub; forecast or rule-based fallback; §3.3 |
| `refresh_procurement_plan` | T-1 23:05 (cron-job.org) | Draft plans only: committed and at-risk after the payment grace |
| `surplus_cascade` | T 12:30 (cron-job.org) | §3.5 |
| `compute_vendor_scores` | Monday 03:33 (GitHub Actions) | §3.4 |

Each job is idempotent, takes its advisory lock and uses `scheduled_for` (the delivery day follows from it), not the run time (ADR-0021, ADR-0025).

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `hubs:read` | packer, rider, kitchen_partner, hub_manager, franchise_owner (hub); admin |
| `hubs:write` | admin |
| `vendors:read` · `vendors:write` | hub_manager (hub), admin · franchise_owner reads |
| `packing:read` | packer, hub_manager, franchise_owner (hub), admin |
| `packing:write` | packer (hub), hub_manager (hub) |
| `packing:manage` | hub_manager (hub), admin (build, release) |
| `procurement:read` · `procurement:write` | hub_manager (hub), admin · franchise_owner reads |
| `receiving:write` | hub_manager, packer (hub) |
| `waste:write` | hub_manager, packer (hub) |

## 8. Screens
**Mobile (packer):** Queue (with "Next task"), Pick/Pack (scan the bag label, step through lines, shortage sheet with substitutes), offline indicator for queued actions. The packer route on `main` is a placeholder (`FieldShell`), so no mock shapes need mapping yet. **Console:** Ops board (today: packing summary per society, plan and PO status), Packing board, Hubs admin (map with terra-draw for the service area), Vendors, Procurement, Receiving/QC, Waste and cascade, bag label sheet (printed from the task list).

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
- Procurement (§3.3, at its PR): fallback quantity with and without buffer; forecast conversion from steps; rebuild keeps edits; PO totals in paise.
- E2E (MS-B): seeded hub → cut-off → plan → receipt → packing → `orders.packed` for every order, on the simulator seed.

## 10. Dependencies
M4 (`orders.api`: `orders_for_delivery` with `kinds`, `transition` with the new actors, `mark_shortage` with refunds and substitutes; ADR-0026 §2, §6), M2 (`catalog.api`: products, steps, shelf life, substitutes, prices), M1 (`identity.api.delivery_labels`), M9 (`community.api`: society names and tower labels), M10 (`intel_gateway.api.hub_forecast`, PRs #118/#122), M7 (`logistics.route_published`; reads `society_loads`), M8 and M9 later for surplus offers.

## 11. Open questions and decisions
- **Decided (ADR-0026):** M6 owns hubs; hub ids elsewhere have no foreign key; crates are counted, not tracked; operations quantities in kg, l, piece, bunch, pack; packing does not wait for routes.
- **Decided (this spec):** a substitute replaces a whole line only, at a price no higher than the line's; partial shortages are refunded.
- **Decided by the owner (2026-10-09, ADR-0026):** substitute by default, but never for households that chose "never substitute, refund" (M1 preference, read through `identity.api`); routes are solved at T-1 23:15, so packing at 05:00 normally follows route order; one captain per society; a missed hold window gets one free redelivery the next morning (M7).
- Real vendor list and APMC Vashi timings for the pilot (Rhythm Thakur, Manish Raje).
- Labels: printed sheet from the console in the prototype; a thermal label printer at the pilot.
