# M7 — Logistics

**Status:** Draft v2 (with [ADR-0026](../adr/0026-operations-contract.md)). §3.1 (vehicles), §3.2 (route plans: solve, fallback, publish), §3.3 (rider execution, GPS and live tracking), §3.4 (handover, door proof, undelivered, hold and redelivery) and §3.5 (the customer's delivery view) are frozen for S5 when this spec is approved; §3.6 (return-leg pickups) and §3.7 (tiffin routes) are outlines, refined in the PR that builds each.
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P3 / S5
**Postgres schema:** `logistics` · **Python package:** `gharbasket.modules.logistics`

## 1. Purpose
Getting bags from the hub to each society and each flat: vehicles, route plans (solved by M10's OR-Tools CVRPTW through `intel_gateway.api.solve_routes`, with a fallback in M7 when the ML service is down), rider execution, live location, the **Society Captain handover**, the last 50 metres with proof, failed deliveries (hold, one redelivery, refund) and return-leg pickups. M7 moves orders only through `orders.api` (M4 owns the order and its states), reads the captain of a society only through `community.api` (M9 is the source of truth, ADR-0026 §4) and reads bag counts from `hub_ops.api` (M6). It does not pack, pay anyone or send messages.

**Build order for MS-B** ("OR-Tools routes → captain handover → delivered"): (a) vehicles and route plans → (b) rider execution with GPS and live tracking → (c) handover, door proof, undelivered, hold and redelivery → (d) return-leg pickups (with M9 donations, P5) and tiffin routes (with M8, S6). (a)–(c) are frozen below.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a hub manager, I want the next morning's routes ready the night before so that packing follows route order and vans leave at 06:30. | At T-1 23:15 a plan is solved per hub; published automatically when the checker finds no problems; solver stats stored. |
| US-2 | As a hub manager, I want to re-solve and publish on demand so that a late change or a broken van does not stop the morning. | Manual solve (async) and publish until the first route starts. |
| US-3 | As a hub manager, I want vehicles with capacities and default riders so that the solver uses the real fleet. | Vehicles per hub; deactivate instead of delete. |
| US-4 | As a rider, I want my route with stops, crates and a navigation link so that I deliver without calling anyone. | Route visible the night before; works offline after start; every tap replay-safe. |
| US-5 | As a customer, I want to see the van coming so that I know when my bag reaches my captain. | SSE position every 15 s until my society's handover; no rider details. |
| US-6 | As a Society Captain, I want to take the crates by count and code so that the handover is accountable. | Crate and bag count + handover code or photo → orders `at_society`. |
| US-7 | As a Society Captain, I want a flat checklist and to scan each bag at the door so that deliveries are confirmed. | Bag scan → `delivered`; customer notified (M11). |
| US-8 | As a captain, I want to hold a bag when nobody is home so that the family can collect it the same evening. | Held until 20:00; at 20:30 → `undelivered`; one free redelivery the next morning, then a refund of the perishables minus a fee. |
| US-9 | As a rider, I want empties and donations on my return leg so that no extra trip is needed. | Pickup counts per stop; `logistics.pickup_completed` (§3.6). |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/hubs/{id}/vehicles` | hub_manager, franchise_owner (hub), admin | — | §3.1 |
| POST | `/v1/hubs/{id}/vehicles` | hub_manager (hub), admin | Required | §3.1 |
| PATCH | `/v1/vehicles/{id}` | hub_manager (hub), admin | `If-Match` | §3.1 |
| POST | `/v1/hubs/{id}/route-plans` | hub_manager (hub) | Required | §3.2; manual solve or re-solve, async (202) |
| GET | `/v1/hubs/{id}/route-plans?date=&kind=` | hub_manager, franchise_owner (hub) | — | §3.2 |
| GET | `/v1/route-plans/{id}` | hub_manager, franchise_owner (hub) | — | §3.2; poll after a solve |
| POST | `/v1/route-plans/{id}/publish` | hub_manager (hub) | Required | §3.2 |
| GET | `/v1/hubs/{id}/routes?date=&kind=&status=` | hub_manager, franchise_owner, packer (hub) | — | §3.3 |
| GET | `/v1/routes/{id}` | hub staff (hub), rider (own) | — | §3.3; ETag |
| PATCH | `/v1/routes/{id}` | hub_manager (hub) | `If-Match` | §3.3; rider and vehicle, before start |
| GET | `/v1/riders/me/routes?date=` | rider | — | §3.3 |
| POST | `/v1/routes/{id}/start` · `/complete` | rider (own), hub_manager | Required | §3.3 |
| POST | `/v1/stops/{id}/arrive` · `/skip` | rider (own route), hub_manager | Required | §3.3 |
| PATCH | `/v1/stops/{id}` | hub_manager (hub) | `If-Match` (route version) | §3.4; `rider_mode` |
| POST | `/v1/riders/me/location` | rider | — (deduplicated) | §3.3; batched pings, rate-limited |
| GET | `/v1/routes/{id}/live` | rider (own), hub_manager (hub), captain (her stop), customer (own order) | — | §3.3; SSE |
| GET | `/v1/captains/me/today?date=` | captain | — | §3.4 |
| GET | `/v1/stops/{id}/deliveries` | rider (rider-mode stop, own route), hub staff | — | §3.4 |
| POST | `/v1/stops/{id}/handover` | captain (society), rider (rider-mode stop), hub_manager | Required | §3.4 |
| POST | `/v1/deliveries/{order_id}/proof` | captain (society), rider (rider mode, tiffins), hub_manager | Required | §3.4 |
| POST | `/v1/deliveries/{order_id}/undelivered` | captain (society), rider (rider mode, tiffins), hub_manager | Required | §3.4 |
| POST | `/v1/deliveries/{order_id}/resolve` | hub_manager (hub) | Required | §3.4; triage |
| GET | `/v1/hubs/{id}/deliveries?date=&status=` | hub_manager, franchise_owner (hub) | — | §3.4; exceptions board; cursor |
| GET | `/v1/deliveries/{order_id}` | customer (own household), hub staff | — | §3.5 |
| POST | `/v1/stops/{id}/pickups` | rider (own route) | Required | §3.6 (outline) |
| GET | `/v1/hubs/{id}/pickups?date=` | hub_manager (hub) | — | §3.6 (outline) |
| POST | `/v1/internal/jobs/solve_routes` · `persist_gps_pings` · `hold_expiry` · `schedule_redeliveries` · `close_routes` · `purge_gps_pings` | operator token | — | §6 |

**For every section.** Every endpoint needs sign-in (M1 §3.1). Hub staff and riders act in one hub (`X-Active-Role` `hub_manager:<hub>`, `rider:<hub>`, …); a captain acts for one society (`captain:<society>`), **and** M7 checks on every write that `community.api.captain_for_society(society_id).user_id` is the caller (the role grant and the M9 assignment must agree; ADR-0026 §4). A request outside the caller's scope is 403 `auth.forbidden`. Dates are IST calendar dates (`date` defaults to today in IST; allowed today − 7 … today + 1, else 422 `logistics.date_out_of_range`); clock times (windows, ETAs) are IST `HH:MM`; timestamps are UTC. Money is integer paise. Reads send `Cache-Control: no-store`. **Field writes** (rider and captain) follow M6's offline rule: the `Idempotency-Key` is made when the person taps, the phone's outbox sends taps in order, a replay answers what the first call answered (M0, keys live 24 h), and `client_at` (when the tap happened) is stored for audit while the server's time decides everything else. M7 never writes `orders.*`; every order move is `orders.api.transition(…, actor=<kind>, actor_user_id=<caller>)`, and an illegal move comes back as 409 `orders.illegal_transition` with nothing changed in M7.

### 3.1 Vehicles (frozen for S5)
The console's Vehicles screen and the solver input build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
Vehicle : { "id", "hub_id", "label": "EV-1",                 unique per hub (case-insensitive), 1 to 20
            "kind": "cargo_3w" | "two_wheeler",              boxes and add-ons ride cargo_3w; tiffins
                                                             two_wheeler (ADR-0026 §6)
            "registration": "MH46AB1234" | null,
            "capacity_kg": 400, "capacity_crates": 24,        defaults by kind: cargo_3w 400 kg / 24
            "capacity_tiffins": 0,                            crates / 0; two_wheeler 40 kg / 0 / 30
            "default_rider": { "user_id", "name" } | null,    a rider of this hub; set on new routes
            "active": true, "version": 1, "created_at" }

GET   /v1/hubs/{id}/vehicles?active=true&kind=    200 { "items": [ Vehicle ] }   by label
POST  /v1/hubs/{id}/vehicles                      Idempotency-Key
  request : { "label", "kind", "registration"?, "capacity_kg"?, "capacity_crates"?,
              "capacity_tiffins"?, "default_rider_user_id"? }
            capacity_kg 10 to 2000; capacity_crates 0 to 100; capacity_tiffins 0 to 100
  201 : Vehicle
PATCH /v1/vehicles/{id}                           If-Match; any field above, plus "active"
  200 : Vehicle                                   vehicles are deactivated, never deleted; a
                                                  change applies to the next solve, never to a
                                                  route already published
```

- `default_rider_user_id` must hold an active `rider:<hub>` grant (`identity.api`), else 422 `logistics.rider_invalid`. The rider's name is shown to hub staff only (§Privacy).
- **The fleet a solve uses** is the hub's active vehicles of the plan's kind. The v0 solver takes one capacity per fleet (`RouteVans`), so a mixed fleet is solved with the smallest capacity of each measure; the console warns when capacities differ.

### 3.2 Route plans: solve, fallback and publish (frozen for S5)
A **plan** is one solve for a hub, a delivery date and a kind (`box` in S5; `tiffin` in §3.7). It holds **routes**; a route is one trip of one vehicle (the two-wave solver can give a van a second trip: `trip: 2`). A **stop** is a society and slot (the captain takes the bags), a food-bank drop (`bank`, with donations, P5) or a kitchen pickup (`kitchen`, tiffins). The console's Routes screen and the packing sequence (M6 §3.2) build against this.

```
RoutePlan : {
  "id", "hub_id", "delivery_date", "kind": "box" | "tiffin",
  "status": "queued" | "solving" | "solved" | "published" | "failed" | "superseded",
  "trigger": "job" | "manual",
  "source": "ml" | "fallback_previous" | "fallback_nearest" | null,   null until solved
  "distance_source": "osrm" | "haversine" | null,  how the distances were priced
  "model": "cvrptw-waves-v0" | null,
  "summary": { "stops": 18, "orders": 212, "vehicles": 4, "routes": 6, "km": 61.2,
               "cost_paise": 412000, "on_time": 1.0, "late_min": 0.0,
               "problems": [ "van 2: over capacity leaving the hub" ] } | null,
  "seconds": 7.9 | null, "error": "…" | null,    error: why a plan failed
  "routes": [ RouteSummary ],
  "requested_by": { "user_id", "name" } | null, "created_at", "published_at": "…" | null }

POST /v1/hubs/{id}/route-plans        hub_manager; Idempotency-Key
  request : { "date": "2026-12-29", "kind": "box" }    date: today or tomorrow
  202 : RoutePlan (status queued)      the solve runs as an outbox consumer (ADR-0021), so the
                                       request returns at once; poll GET /v1/route-plans/{id}
GET  /v1/hubs/{id}/route-plans?date=&kind=   200 { "items": [ RoutePlan without routes ] } newest first
GET  /v1/route-plans/{id}                    200 RoutePlan | 404 "logistics.plan_not_found"
POST /v1/route-plans/{id}/publish     hub_manager; Idempotency-Key
  request : { "accept_problems"?: false }
  200 : RoutePlan (published)          the routes become published; the date's previously
                                       published plan becomes superseded and its routes cancelled
```

**What a solve sends** (M7 builds `intel_gateway.api.solve_routes`' `RouteRequest`, M10 §3.4):
- **Orders:** `orders.api.orders_for_delivery(hub, date, statuses=("locked", "packed"), kinds=("box", "addon"))`, plus the redeliveries M7 has scheduled for that date (§3.4). At 23:15 the set is fixed: the cut-off was at 21:00 and `payment_grace_expiry` at 23:00.
- **Stops:** one per (society, slot) with orders, `id = "<society_id>/<slot_id>"`; location from `community.api.society_points` (the gate, else a point inside the boundary; a society without either is left out and named in `problems`, so the plan cannot auto-publish); window = the slot's start and end (M4 `slot_ref`); `orders` and bags = the number of orders (one bag per order, M6); `crates = ceil(bags / 3)` (M6's three bags to a crate; the actual counts come from `hub_ops.api.society_loads` at route start); `kg_tenths` from the order lines through `catalog.api.grams_of` (0.5 kg a step when a product has no weight); `empties` = crates handed over at that society on its last delivery and not yet collected (0 until §3.6); `donation_kg_tenths` = 0 until §3.6.
- **Vans:** the fleet above; `depart_s` = the hub's `timings.dispatch` (06:30, M6 §3.1); `return_by_s` = the latest window end + 90 min; `time_limit_s` 20 (two waves of 10 s); service, cost and lateness at the M10 defaults.
- **Mapping the answer:** each element of `routes[]` is a trip; van index *i* is the *i*-th vehicle by label; trips of one van are numbered by start time. ETAs are `eta_s` as IST clock times. The route's rider is the vehicle's `default_rider` (or null). The whole answer is kept in `intel.predictions` by the gateway and its summary on the plan.

**Fallback (ML never blocks, M10 §3.4).** When the gateway returns `None` (service down, slow or not configured), M7 builds the plan itself, in the API, in under a second:
1. **`fallback_previous`:** the stop order of the hub's last published plan of the same kind in the past 7 days; societies that were not in it are inserted at their nearest neighbour; trips are filled in that order up to the vehicle's kg and crate capacity, early-window stops first.
2. **`fallback_nearest`:** with no previous plan, nearest neighbour from the hub by haversine distance × 1.3, early-window stops first, filling each vehicle to capacity.

ETAs then come from haversine × 1.3 at 22 km/h × the traffic factor (1.5), with the same service times. M7 runs its own structural check on every plan, from the ML service or not: every stop on exactly one route, kg and crates within capacity leaving the hub, no more routes per vehicle than two, and every route back by `return_by_s`; its findings join `summary.problems`. A solve that fails before any plan exists (no active vehicle, no orders with a location) ends `failed` with `error`.

**Rules**
- **One plan in flight.** A second solve for the same hub, date and kind while one is `queued` or `solving` → 409 `logistics.solve_running`. A plan with no orders is `solved` with no routes (and auto-publishes; nothing is sent to M10).
- **Auto-publish.** A plan from the `solve_routes` job is published at once when `summary.problems` is empty; otherwise it stays `solved` and M7 publishes `logistics.plan_needs_review` (M11 tells the hub managers). A manual solve is never auto-published.
- **Publish** needs `summary.problems` empty, or `accept_problems: true` (recorded in `platform.audit_log` with the problems), else 409 `logistics.plan_has_problems`. A plan that leaves an order's stop off every route can never be published: 409 `logistics.plan_incomplete` with `stop_ids`. Publishing a plan with a vehicle that has no rider is allowed; the hub manager sets riders with `PATCH /v1/routes/{id}`.
- **Re-solve** (a new manual plan, then publish) replaces the published plan only while none of its routes has started; afterwards 409 `logistics.routes_started`. The old plan's routes become `cancelled` (`logistics.route_cancelled`), so riders and M6's packing sequence follow the new one (M6 re-sequences queued tasks on `logistics.route_published`).
- Each published route publishes `logistics.route_published`; its stops tell M6 the loading order and M11 whom to tell (§5).

### 3.3 Rider execution, GPS and live tracking (frozen for S5)
The rider screens (Today, Route map, Stop detail) and the console's live map build against this. Same review rule as §3.1.

```
RouteSummary : { "id", "plan_id", "hub_id", "delivery_date", "kind": "box" | "tiffin", "trip": 1,
  "status": "draft" | "published" | "in_progress" | "completed" | "cancelled",   draft: in a
                                                  plan not yet published
  "vehicle": { "id", "label", "kind" },
  "rider": { "user_id", "name" } | null,          null until a rider is set
  "depart": "06:30", "return_by": "11:30",        IST
  "stops_total": 6, "stops_done": 2, "crates": 18, "bags": 52, "km": 14.8,
  "started_at": "…" | null, "completed_at": "…" | null, "version": 3 }

Stop : { "id", "seq": 1, "kind": "drop" | "bank" | "kitchen",
  "society": { "id", "name", "location": { "lat", "lng" } } | null,
  "slot": { "id", "label", "start", "end" } | null,
  "eta": "07:12", "late_min": 0,                  planned; "eta_now" adds the route's current delay
  "eta_now": "07:20" | null,
  "status": "pending" | "arrived" | "handed_over" | "skipped",
  "crates": 3, "bags": 9,                          planned; from M6 society_loads after start
  "redelivery_bags": 1,                            bags of redeliveries for this stop (§3.4)
  "empties_expected": 2,                           §3.6
  "captain": { "display_name", "photo_url" } | null,   null: no active captain (rider mode)
  "rider_mode": false,                             true: the rider does the handover and the doors
  "handover_code": "4821" | null,                  only to the route's rider and hub staff, while
                                                   in progress; the captain types it (§3.4)
  "navigate_url": "https://www.google.com/maps/dir/?api=1&destination=19.03,73.03",
  "arrived_at": "…" | null, "handed_over_at": "…" | null }

RouteDetail : RouteSummary & { "stops": [ Stop ], "source": "ml" | "fallback_previous" | "fallback_nearest",
                               "left_behind": [ "<order_id>" ] }   orders not packed at start

GET   /v1/hubs/{id}/routes?date=&kind=&status=   200 { "items": [ RouteSummary ] }  by depart, label
GET   /v1/riders/me/routes?date=                 200 { "items": [ RouteSummary ] }  the caller's
                                                 published, in-progress and completed routes;
                                                 date today or tomorrow (riders see them the night before)
GET   /v1/routes/{id}                            200 RouteDetail (ETag = version) | 404 "logistics.route_not_found"
PATCH /v1/routes/{id}                            hub_manager; If-Match
  request : { "rider_user_id"?: "<uuid>" | null, "vehicle_id"?: "<uuid>" }   before start only
  200 : RouteDetail                              a new rider gets logistics.route_published again
                                                 (with previous_rider_user_id)

POST /v1/routes/{id}/start       the route's rider or hub_manager; Idempotency-Key
  request : { "client_at"?, "leave_behind"?: false }
  200 : RouteDetail (in_progress)
POST /v1/stops/{id}/arrive       the route's rider; Idempotency-Key   { "client_at"? }  200 Stop
POST /v1/stops/{id}/skip         the route's rider or hub_manager; Idempotency-Key
  request : { "reason": "captain_absent" | "no_access" | "vehicle_issue" | "other", "note"?, "client_at"? }
  200 : Stop (skipped)
POST /v1/routes/{id}/complete    the route's rider or hub_manager; Idempotency-Key
  request : { "client_at"?, "empties_returned"?: 6 }
  200 : RouteDetail (completed)
```

**Rules**
- **Start** needs the route `published`, its date today (IST; else 409 `logistics.route_not_today`), the caller as its rider (403 `logistics.not_your_route`; hub managers may start for a rider) and no other route of that rider `in_progress` (409 `logistics.rider_busy`). It moves every order of its stops that is `packed`, and every redelivery booked on it, to `dispatched` (actor `rider`; ADR-0026 §2) and reads the actual crates and bags per stop from `hub_ops.api.society_loads`. Orders still `locked` or `picking` → 409 `logistics.bags_not_packed` with `order_ids`, unless `leave_behind: true`: they stay off the route (listed in `left_behind`) and appear on the hub's exceptions board. A second trip starts only after the first is completed.
- **Arrive** is optional (the handover also marks the stop): it records the arrival, recomputes `eta_now` for the remaining stops (planned ETA + this stop's delay; no live traffic in v1) and publishes `logistics.stop_arrived`, which M11 turns into a push to the captain ("The van is at the gate").
- **Skip** leaves the stop's orders `dispatched`; they ride back to the hub.
- **Complete** needs every stop `handed_over` or `skipped` (409 `logistics.stops_open` with `stop_ids`). Orders of the route still `dispatched` (a skipped stop, bags not handed over) become `undelivered` (actor `rider`, reason `returned_to_hub`) and go through §3.4's redelivery. Publishes `logistics.route_completed`. The phone stops sending location.
- **Actions before start** (`arrive`, `skip`, `handover`, …) → 409 `logistics.route_not_started`; any action on a completed or cancelled route → 409 `logistics.route_closed`.
- **Offline.** After start the rider app keeps `RouteDetail` on the phone and works without a network; taps go through the outbox (§3, "Field writes").

**GPS pings**

```
POST /v1/riders/me/location      rider; no Idempotency-Key (pings are deduplicated)
  request : { "route_id", "pings": [ { "at": "2026-12-29T01:41:15Z", "lat": 19.0331, "lng": 73.0298,
                                       "accuracy_m": 12, "speed_mps": 6.1 | null, "heading": 90 | null } ] }
            1 to 20 pings, oldest first
  202 : { "accepted": 4, "dropped": 1 }
```
- Accepted only for the caller's route while it is `in_progress` (409 `logistics.no_active_route`); the app sends location only between start and complete, every 15 s, batched (a backlog after a dead zone goes in batches of 20).
- A ping is dropped (counted in `dropped`, never an error) when it is more than 10 minutes old or more than 1 minute in the future, less than 5 s after the previous one, outside India's bounding box, or less accurate than 200 m.
- **Rate limit:** 12 requests a minute per rider (fixed window in Valkey) → 429 `logistics.location_rate_limited` with `Retry-After`. If Valkey is down, requests are allowed.
- **Storage:** each accepted ping is appended to a Valkey stream and the route's latest position is set (`gb:loc:<route_id>`, expires after 15 minutes) and published on `gb:live:<route_id>`; `persist_gps_pings` moves the stream into `logistics.gps_pings` every minute (unique `(rider_user_id, at)`, so a replay inserts nothing). If Valkey is down, pings are inserted into Postgres at once and live tracking reads the latest row. A lost Valkey loses at most the last minute of the trail, never an order state (handbook 05 keeps live rider positions in Valkey).

**Live tracking (SSE)**

```
GET /v1/routes/{id}/live         text/event-stream; Cache-Control: no-store; X-Accel-Buffering: no
  event: snapshot   data: { "route_id", "status", "position": Position | null, "stops": [ StopLive ] }
  event: position   data: Position = { "at", "lat", "lng", "heading": 90 | null, "age_s": 8 }
  event: stop       data: StopLive = { "seq", "status", "eta_now" }
  event: end        data: { "reason": "handed_over" | "route_completed" | "access_ended" | "reconnect" }
  : keep-alive comment every 20 s
```
- **Who sees what.** The route's rider and the hub's hub managers: every stop and every position. A **captain**: her own stop only (`stops` holds one entry, plus `"stops_before": 2`) and the position until her stop is handed over. A **customer**: only while an order of their household is on this route and its stop is not yet handed over; one entry with the stop's `eta_now` and `stops_before`, never other societies; positions rounded to 4 decimals (about 11 m) and at most every 15 s. Anyone else → 403 `auth.forbidden`; a customer whose stop is done gets `end` (`handed_over`) and the stream closes.
- The first event is always `snapshot`, so a reconnect needs no `Last-Event-ID`. A stream closes after 30 minutes with `end` (`reconnect`); at most 3 open streams per user (429 `logistics.too_many_streams`). The app reads the stream with a fetch-based SSE reader because `EventSource` cannot send `Authorization`; the console's browser sends cookies.
- Positions come from the Valkey channel; when Valkey is down the stream polls the latest persisted ping every 15 s. A position older than 2 minutes is sent with its `age_s` so the app can show "last seen".

### 3.4 Handover, door proof, undelivered, hold and redelivery (frozen for S5)
The captain screens (Today, Handover, Flat checklist, Exceptions), the rider's rider-mode stop and the console's exceptions board build against this. Same review rule as §3.1.

```
CaptainDay : { "date", "society": { "id", "name" },
  "stops": [ { "stop_id", "route_id", "slot": { "label", "start", "end" }, "eta_now": "07:20",
               "status": "pending" | "arrived" | "handed_over" | "skipped",
               "expected": { "crates": 5, "bags": 14, "redelivery_bags": 1 } } ],
  "flats": [ CaptainFlat ],                          by tower, then flat
  "counts": { "on_the_way": 0, "with_you": 9, "held": 1, "delivered": 4, "undelivered": 0 } }
CaptainFlat : { "order_id", "flat_label": "B-402",   tower label + flat (identity.api.delivery_labels);
                "kind": "box" | "addon" | "tiffin",  no name, no phone
                "bags": 1, "redelivery": false,
                "status": "on_the_way" | "with_you" | "held" | "delivered" | "undelivered",
                "hold_until": "20:00" | null }

GET  /v1/captains/me/today?date=      captain; the society of X-Active-Role; date today or yesterday
  200 : CaptainDay
GET  /v1/stops/{id}/deliveries        the rider of a rider-mode stop while the route is in progress,
                                      or hub staff
  200 : { "items": [ CaptainFlat ] }

POST /v1/stops/{id}/handover          captain of the stop's society, the rider (rider-mode stop),
                                      or hub_manager; Idempotency-Key
  request : { "crates": 5, "bags": 14,                counted by the captain
              "scanned_bags"?: [ "GB1:0199a0…" ],     needed only when the counts do not match
              "handover_code"?: "4821",               exactly one of handover_code and photo_key;
              "photo_key"?: "delivery_photo/<user_id>/<uuid>.jpg",   rider mode needs neither
              "note"?, "client_at"? }
  200 : { "handover_id", "stop": Stop, "at_society": [ "<order_id>" ],
          "still_dispatched": [ "<order_id>" ], "mismatch": false }

POST /v1/deliveries/{order_id}/proof  captain of the order's society, the rider (rider-mode stop or
                                      tiffin on their route), or hub_manager; Idempotency-Key
  request : { "bag_qr": "GB1:0199a0000000700080000000000000ab",   always; must be this order's
              "proof": "scan" | "otp" | "photo",
              "delivery_code"?: "5170",            with otp: the code in the customer's app (§3.5)
              "photo_key"?: "delivery_photo/…",    with photo: the bag at the door
              "client_at"? }
  200 : Delivery
POST /v1/deliveries/{order_id}/undelivered   same callers; Idempotency-Key
  request : { "reason": "not_home" | "refused" | "wrong_address" | "damaged" | "missing" | "other",
              "note"?, "photo_key"?, "client_at"? }      note required for other (up to 300)
  200 : Delivery
POST /v1/deliveries/{order_id}/resolve       hub_manager; Idempotency-Key
  request : { "outcome": "redeliver" | "refund", "refund_paise"?: 42000, "note" }
  200 : Delivery

Delivery : { "order_id", "attempt": 1 | 2, "delivery_date", "society_id",
  "status": "scheduled" | "on_route" | "with_captain" | "held" | "delivered" | "undelivered"
            | "redelivery" | "refunded" | "triage",
  "reason": "not_home" | … | "returned_to_hub" | "not_confirmed" | null,
  "hold_until": "20:00" | null, "proof": "scan" | "otp" | "photo" | null,
  "by": "captain" | "rider" | "hub" | "system" | null, "at": "…" | null }
```

**Handover rules**
- **Who.** The active captain of the stop's society. Where the society has no active captain, the stop is in **rider mode** from the start (`rider_mode: true`), and the route's rider hands over to himself and does the doors (ADR-0026 §2). When the captain is absent, the rider skips the stop or calls the hub; a hub manager can switch the stop to rider mode for the day (`PATCH /v1/stops/{id}` `{ "rider_mode": true }`, audited).
- **Proof.** The handover code is generated at route start and shown only on the rider's phone; the captain types it, which shows they met. A photo of the crates (`delivery_photo` upload, M0) is the alternative when the rider's phone is dead. A wrong code → 422 `logistics.code_invalid`; after 5 wrong codes for a stop → 429 `logistics.code_attempts_exceeded` and only a photo is accepted.
- **Counts** are compared with what the van carries for the stop (`society_loads` at start, plus redelivery bags that came back to the hub). Redelivery bags the captain already holds are not counted; they move to `at_society` with the handover. When crates and bags match, every order of the stop that is `dispatched` moves to `at_society` (actor `captain`, or `rider` in rider mode, or `hub`). When they do not match, the captain scans the bags she has (`scanned_bags`); without scans → 409 `logistics.count_mismatch` with the expected counts. With scans, only the scanned orders move; the rest stay `dispatched` (`still_dispatched`) and return with the van (`returned_to_hub` at complete). A mismatch is kept on the handover and shown on the exceptions board.
- One handover per stop: another one → 409 `logistics.already_handed_over` (a replay of the same key answers the first). The rider's distance from the society at that moment (from the latest ping) is stored for audit and never refuses a handover.
- Publishes `logistics.handover_completed`; M4 publishes `orders.at_society` per order.

**Door rules**
- **Proof.** `bag_qr` must be the order's (`GB1:` + the order id without hyphens, M6 §3.2; else 422 `logistics.bag_mismatch`). A captain may use `scan` alone: she is a verified partner and the bag is in her hands. A rider (rider mode or tiffin) needs `otp` or `photo` (422 `logistics.proof_required`). Five wrong delivery codes for an order → 429 `logistics.code_attempts_exceeded`, then only a photo.
- Moves `at_society → delivered` (box and add-on) or `dispatched → delivered` (tiffin), actor `captain` or `rider`. A held bag can be delivered until `hold_expiry` runs. A delivered or refunded order → 409 `logistics.delivery_closed`.

**Undelivered, hold and redelivery** (ADR-0026 §6 and the owner's decision of 2026-10-09)

| Reason | Captain (box, add-on) | Rider in rider mode | Tiffin |
|---|---|---|---|
| `not_home` | **Held**: the order stays `at_society`, status `held`, `hold_until` 20:00 IST; `logistics.delivery_held` (M11 tells the family to collect from her) | `undelivered` at once; the bag rides back | `undelivered`, then `refunded` in full at once (actor `system`; a lunch is never redelivered) |
| `damaged`, `missing` | `undelivered`, then `refunded` in full at once (our fault) | same | same |
| `refused`, `wrong_address`, `other` | `undelivered`, status `triage`: a hub manager resolves | same | same as above |

- **`hold_expiry` (T 20:30)** moves every order of the day still `at_society` to `undelivered` (actor `system`): held ones with reason `not_home`, others (never scanned or held) with `not_confirmed`, which go to triage.
- **`schedule_redeliveries` (T 20:45)** takes the day's `undelivered` box and add-on orders with reason `not_home` or `returned_to_hub`:
  - **first miss:** `undelivered → redelivery` (actor `system`) and a delivery `attempt: 2` for the next morning (T+1) in the same slot's window; M7 adds it to T+1's 23:15 solve, so the society gets a stop even if T+1 is not its delivery day. Publishes `logistics.redelivery_scheduled`. The free redelivery is once per order.
  - **second miss** (attempt 2 undelivered): `undelivered → refunded` (actor `system`) with `refund_paise` = the paid amount of its **perishable** lines (catalog shelf life ≤ 96 h, the catalog's rule) for the steps delivered (`qty − shortage_qty`) at the line's paid price, **minus a ₹20 fee (2,000 paise)**, never below 0. The bag goes back to the hub on the next return leg (§3.6, pickup kind `bag`) and its contents into M6's surplus cascade.
- **Triage** (`resolve`, hub manager): `redeliver` (only on attempt 1; `undelivered → redelivery`, actor `hub`, as above) or `refund` with `refund_paise` up to what is left of the order's paid total after earlier credits (default: all of it; 422 `logistics.refund_invalid` with `max_paise` above that). Not `undelivered` → 409 `logistics.not_undelivered`.
- Each `undelivered` move publishes M4's `orders.delivery_failed` with the reason (M11 notifies); refunds go through M4's `transition(…, "refunded", payload={"refund_paise", "reason"})`, which pays through `billing.api.refund_to_wallet` (M4 follow-up, §10).

### 3.5 The customer's delivery view (frozen for S5)
The order detail's delivery card and live map build against this. M4's `OrderOut` stays as it is; the app reads this next to it.

```
GET /v1/deliveries/{order_id}     the order's household (any member) or hub staff
  200 : { "order_id", "status": Delivery.status, "attempt": 1,
          "window": { "start": "08:00", "end": "10:00" }, "eta": "08:20" | null,
          "tracking": { "route_id" } | null,       while the order is dispatched and its stop not
                                                   yet handed over: open GET /v1/routes/{id}/live
          "captain": { "display_name", "photo_url" } | null,   the society's active captain
          "hold_until": "20:00" | null,
          "delivery_code": "5170" | null,          only to the household, only while the order is
                                                   dispatched or at_society; for a rider's otp proof
          "proof": { "kind": "scan" | "otp" | "photo", "at", "photo_url": "<5-minute link>" | null } | null,
          "redelivery": { "date", "window" } | null }
                                       before a plan exists: status scheduled, eta and tracking null
  404 "logistics.delivery_not_found"   not an order of the caller's household
```

**For the mobile mocks** (`feat/mobile-orders`, `mocks/orders.js`; `feat/mobile-box-addons`, `lib/outbox.test.js`)

| Mock today | Contract |
|---|---|
| `eta_by: "10:00"` on the order | `GET /v1/deliveries/{order_id}`: `window.end` is the promise, `eta` the live estimate |
| `captain: { name, phone_e164 }` on the order | `captain: { display_name, photo_url }` from this endpoint; **no phone** (M9 §3.1: a captain's phone and flat are never returned). The app offers "Call the hub" instead |
| No live tracking | `tracking.route_id` → `GET /v1/routes/{id}/live` (SSE, fetch-based reader) until the handover |
| Outbox test posts `/v1/stops/1/handover` with `{ bags: 3 }` | Same path; the body needs `crates`, `bags` and `handover_code` or `photo_key` |

### 3.6 Return-leg pickups (outline; refined at its PR)
- Every drop stop can carry pickups: **empties** (crates handed over earlier at that society), **donations** (M9 `community.donation_scheduled`, P5; donation weight goes into `donation_kg_tenths` and the food bank becomes a `bank` stop on the same van, as in M10 §3.4) and **bags** (bags going back after a second miss, §3.4).
- `POST /v1/stops/{id}/pickups` (rider, Idempotency-Key): `{ "empties": 4, "donations": [ { "donation_id", "status": "collected" | "not_ready" } ], "bags": [ "GB1:…" ], "client_at"? }` → `logistics.pickup_completed { stop_id, route_id, hub_id, society_id, empties, donation_ids, bag_order_ids }`; M9 consumes it for donations, M6 for returned bags.
- Empties expected per society = crates handed over minus crates collected since; the count is a number per society, never per crate (ADR-0026 §6).

### 3.7 Tiffin routes (outline; refined at the M8 tiffin PR, S6)
- A `tiffin` plan per hub at T 10:30, from the day's kitchen assignments, so riders know their kitchens before READY (11:30): two-wheeler routes from M8's assignments (`partners.api`), `kitchen` stops first, then societies, window 12:30–13:30; ordered by nearest neighbour in M7 (the v0 solver has no kitchen pickups).
- At the kitchen the rider scans each tiffin's QR (`GB1:<order_id>`): `packed → dispatched` (actor `rider`). At the society the rider hands tiffins to the captain or delivers them; `dispatched → delivered` by `captain` or `rider` with the door rules above. A failed tiffin is refunded in full at once.

### Facade for other modules (`gharbasket.modules.logistics.api`)
- `published_stop_order(session, hub_id, delivery_date) -> dict[(society_id, slot_id), StopRef]` — `{ route_id, trip, stop_seq, depart }` of the published plan, for M6's packing sequence at build time.
- `route_for_order(session, order_id) -> StopRef | None` — the route and stop an order rides on (M12 boards).
- `delivery_status(session, order_id) -> DeliveryRef | None` — `{ status, attempt, reason, proof, at }` (M12 triage, M13 assistant).
- `export_for_user(session, user_id) -> dict` — the M1 data export section `logistics`: the household's deliveries (status, proof kind, times; no photos) and, for a rider, their routes and pings of the last 90 days.
- `erase_user(session, user_id)` — on account deletion: deletes the rider's pings and nulls user ids on handovers and deliveries (the order history keeps the actor kind).

### Privacy
- **Captains** see flat labels (tower and flat) only for their own society's orders of today and yesterday, and never a customer's name or phone. A captain's phone and flat are never returned to anyone (M9 §3.1); riders and customers see her display name and photo.
- **Riders** see flat labels only for rider-mode stops of their own route while it is in progress, and the captain's display name only. A rider's name and phone are never shown to customers or captains; hub staff see the rider's name here and contact details through M8.
- **Customers** see the van only for their own order, only until their society's handover, and never other societies or stops.
- **Location** is collected only while a route is in progress, kept 90 days (handbook 13 §13.5), visible live only to the route's own audience above; trails are for disputes and kilometres, never shown to customers. Coordinates are not written to logs.
- **Codes and photos.** Handover and delivery codes never appear in logs or events. Proof photos are private (`delivery_photo`, M0), read through 5-minute signed links by the household (its own door photo) and hub staff, and deleted 90 days after the delivery.

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `logistics.vehicle_not_found`, `logistics.plan_not_found`, `logistics.route_not_found`, `logistics.stop_not_found`, `logistics.delivery_not_found` | Unknown id, or outside the caller's scope |
| 403 | `logistics.not_your_route` | A rider acting on another rider's route |
| 403 | `logistics.not_society_captain` | A captain role whose M9 assignment is not active for that society |
| 409 | `logistics.vehicle_label_taken` | A vehicle of that hub has the label |
| 409 | `logistics.solve_running` | A solve for the hub, date and kind is queued or running |
| 409 | `logistics.plan_has_problems` | Publish without `accept_problems` while `problems` is not empty |
| 409 | `logistics.plan_incomplete` | A stop with orders is on no route (`stop_ids`) |
| 409 | `logistics.plan_not_publishable` | Publishing a plan that is not `solved` |
| 409 | `logistics.routes_started` | Re-solving or publishing over a plan whose route has started |
| 409 | `logistics.route_not_today` · `logistics.rider_busy` · `logistics.bags_not_packed` | Start rules (§3.3) |
| 409 | `logistics.route_not_started` · `logistics.route_closed` · `logistics.stops_open` | Route state (§3.3) |
| 409 | `logistics.no_active_route` | Location without an in-progress route |
| 409 | `logistics.count_mismatch` · `logistics.already_handed_over` | Handover (§3.4) |
| 409 | `logistics.delivery_closed` · `logistics.not_undelivered` | Door and triage (§3.4) |
| 422 | `logistics.rider_invalid` · `logistics.vehicle_too_small` · `logistics.capacity_invalid` | Vehicles and route edits |
| 422 | `logistics.code_invalid` · `logistics.bag_mismatch` · `logistics.proof_required` · `logistics.photo_key_invalid` | Proof (§3.4) |
| 422 | `logistics.reason_invalid` · `logistics.refund_invalid` | Undelivered and triage |
| 422 | `logistics.date_out_of_range` | A date outside the allowed range |
| 429 | `logistics.location_rate_limited` · `logistics.code_attempts_exceeded` · `logistics.too_many_streams` | Limits |
| 409 | `orders.illegal_transition` | The order is not in the status the step needs (from M4) |
| 403 | `auth.forbidden` | A role without the permission, or outside its scope |
| 400 / 422 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.invalid` | As in M0 |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `logistics.vehicles` | id (uuidv7), hub_id, label (unique per hub, case-insensitive), kind (cargo_3w, two_wheeler), registration null, capacity_kg, capacity_crates, capacity_tiffins, default_rider_user_id null, active, version, created_at |
| `logistics.route_plans` | id, hub_id, delivery_date, kind (box, tiffin), status, trigger (job, manual), source null, distance_source null, model null, summary jsonb, seconds, error null, prediction_id null (`intel.predictions`), requested_by null, published_by null, published_at, created_at; one `published` plan per (hub_id, delivery_date, kind) (partial unique) |
| `logistics.routes` | id, plan_id → route_plans, hub_id, delivery_date, kind, trip, vehicle_id → vehicles, rider_user_id null, status (draft, published, in_progress, completed, cancelled), depart_time, return_by, planned_km, started_at, completed_at, empties_returned, version; RLS by hub (handbook 13 §13.4) |
| `logistics.route_stops` | id, route_id → routes, seq (unique per route), kind (drop, bank, kitchen), society_id null, slot_id null, window_start, window_end, eta, late_s, delay_s, status (pending, arrived, handed_over, skipped), rider_mode, handover_code, code_attempts, crates, bags, empties_expected, arrived_at, skip_reason null |
| `logistics.handovers` | id, stop_id (unique) → route_stops, society_id, by_actor (captain, rider, hub), user_id null, partner_id null, crates_expected, crates_counted, bags_expected, bags_counted, mismatch, proof (code, photo, rider_mode), photo_key null, distance_m null, note, client_at, at |
| `logistics.deliveries` | id, order_id, attempt (1, 2), hub_id, society_id, order_kind, delivery_date (the attempt's day), slot_id, stop_id null, status, reason null, hold_until null, proof (scan, otp, photo) null, photo_key null, delivery_code, code_attempts, by_actor null, user_id null, refund_paise null, client_at, at; unique (order_id, attempt) |
| `logistics.field_events` | id, route_id, stop_id null, order_id null, action (start, arrive, skip, handover, proof, undelivered, hold, resolve, complete, pickup, rider_mode), user_id, payload jsonb, client_at null, at |
| `logistics.pickups` | (§3.6) id, stop_id, society_id, kind (empties, donation, bag), ref_id null, qty, status (expected, collected, not_ready), at |
| `logistics.gps_pings` | rider_user_id, route_id, at, location geography(Point, 4326), accuracy_m, speed_mps null, heading null; partitioned by month on `at`, BRIN on `at`, unique (rider_user_id, at) |

Foreign keys stay inside `logistics`; hub, order, society, slot and user ids are plain UUIDs (ADR-0026 §5).

## 5. Events
**Published** (no names, phones, flats, codes or coordinates in any payload):
- `logistics.route_published { route_id, plan_id, hub_id, delivery_date, kind, trip, vehicle_id, rider_user_id, previous_rider_user_id, depart, stops: [ { seq, kind, society_id, slot_id, eta, bags, captain_user_id } ] }` — M6 packing sequence, M11 (the rider: "Tomorrow: EV-1, 6 stops, leave 06:30"; each captain: "14 bags tomorrow, van about 07:20").
- `logistics.route_cancelled { route_id, plan_id, hub_id, delivery_date, rider_user_id, reason: "superseded" }` — M6 clears route references, M11 tells the rider.
- `logistics.plan_needs_review { plan_id, hub_id, delivery_date, kind, problems }` — M11 tells the hub's managers.
- `logistics.stop_arrived { stop_id, route_id, hub_id, society_id, captain_user_id, at }` — M11 tells the captain.
- `logistics.handover_completed { handover_id, stop_id, route_id, hub_id, society_id, by_actor, user_id, partner_id, crates, bags, order_ids, mismatch }` — M8 captain commission (at its PR), M12.
- `logistics.delivery_held { order_id, household_id, society_id, hold_until }` — M11 tells the family to collect.
- `logistics.redelivery_scheduled { order_id, household_id, society_id, delivery_date, window }` — M11.
- `logistics.route_completed { route_id, hub_id, delivery_date, rider_user_id, vehicle_id, stops_done, stops_skipped, km, started_at, completed_at }` — M8 rider pay (at its PR), M12.
- `logistics.pickup_completed` (§3.6) — M9, M6.
- Order events (`orders.dispatched`, `orders.at_society`, `orders.delivered`, `orders.delivery_failed` with `reason`, `billing.refund_issued`) are published by M4 and M5 when M7 calls their facades.

**Consumed:** `logistics.solve_requested` (its own: runs a solve outside the request), `community.donation_scheduled` (§3.6, P5).

**For M11's event map** (added when M7 lands; categories and push rules are M11's): `logistics.route_published` (rider, captain), `logistics.route_cancelled` (rider), `logistics.plan_needs_review` (hub managers), `logistics.stop_arrived` (captain), `logistics.delivery_held` (customer, category `delivery`, link `/orders`), `logistics.redelivery_scheduled` (customer, `delivery`, `/orders`). Not mapped: `handover_completed`, `route_completed`, `pickup_completed`.

## 6. Jobs
| Job | Trigger (IST) | Notes |
|---|---|---|
| `solve_routes` | T-1 23:15 (cron-job.org → `POST /v1/internal/jobs/solve_routes`) | Every active hub: queues a `box` plan for T (`logistics.solve_requested`); auto-publish rule in §3.2. Re-solves on demand through `POST /v1/hubs/{id}/route-plans` (the old T 07:00 solve) |
| `persist_gps_pings` | Every minute (cron-job.org) | Valkey stream → `logistics.gps_pings`; idempotent on (rider, at) |
| `hold_expiry` | T 20:30 (cron-job.org) | Still `at_society` → `undelivered` (actor `system`): `not_home` for held bags, `not_confirmed` for the rest (§3.4) |
| `schedule_redeliveries` | T 20:45 (cron-job.org) | First miss → `redelivery` for T+1; second miss → `refunded` minus the fee (§3.4) |
| `close_routes` | T 16:00 (cron-job.org) | Completes routes still `in_progress` (actor `system` on the orders, reason `returned_to_hub`) and stops their location |
| `purge_gps_pings` | Daily 03:43 (GitHub Actions) | Deletes pings older than 90 days; drops a month's partition once it is empty |

Each job is idempotent, takes its advisory lock and uses `scheduled_for` (the delivery day follows from it), not the run time (ADR-0021, ADR-0025).

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `vehicles:read` · `vehicles:write` | hub_manager, franchise_owner (hub), admin · hub_manager (hub), admin |
| `routes:read` | hub_manager, franchise_owner, packer (hub), admin; rider (own routes) |
| `routes:write` | hub_manager (hub): solve, publish, edit routes and stops, start or complete for a rider |
| `routes:execute` | rider (own route): start, arrive, skip, complete, location, pickups |
| `routes:live` | rider (own), hub_manager (hub), captain (her society's stop), customer (own order, until handover) |
| `handover:write` | captain (society, active in M9), rider (rider-mode stop of own route), hub_manager (hub) |
| `deliveries:read` | customer (own household), captain (her society), rider (rider-mode stop), hub staff (hub) |
| `deliveries:write` | captain (society), rider (rider-mode stops and tiffins on own route), hub_manager (hub) |
| `deliveries:resolve` | hub_manager (hub), admin |

## 8. Screens
**Mobile:** Rider — Today (routes, tonight's and today's), Route map with stops, Stop detail (handover code, crates, navigation, rider-mode flat list with proof), Pickups, offline indicator. Captain — Today (van ETA, expected crates and bags), Handover (counts, code or photo, bag scan on mismatch), Flat checklist (scan at the door, hold, undelivered), Exceptions. Customer — delivery card and live map in the order detail (§3.5). The rider and captain routes on `main` are placeholders (`FieldShell`). **Console:** Route plans (solve, problems, publish), Routes live map (MapLibre + deck.gl), Deliveries exceptions board with triage, Vehicles.

## 9. Tests required
- Vehicles (§3.1): label unique per hub; default rider must be a rider of the hub; a change does not touch published routes.
- Route plans (§3.2):
  - the request built from orders, slots, society points and the fleet matches a golden fixture; stop ids round-trip to (society, slot); van index → vehicle by label; trips numbered per van;
  - gateway `None` → `fallback_previous` from the last published plan, and `fallback_nearest` with none; both respect capacity and serve every stop; the structural check catches a missing stop and an over-capacity trip;
  - auto-publish only with no problems, else `plan_needs_review`; publish with problems needs `accept_problems` and writes the audit log; `plan_incomplete` always refused; re-solve refused after a route starts; superseded routes cancelled with events;
  - two concurrent solves → one `solve_running`.
- Rider (§3.3): start moves packed orders and redeliveries to `dispatched` with actor `rider` and the rider's user id; `bags_not_packed` and `leave_behind`; `rider_busy`; complete with open stops refused; complete turns undelivered orders `returned_to_hub`.
- GPS: pings outside an in-progress route refused; drop rules; rate limit; replayed batch inserts nothing; `persist_gps_pings` empties the stream; Valkey down → pings in Postgres and live reads them.
- SSE: the audience rules (rider, hub manager, captain, customer, stranger 403); the customer stream ends at handover; positions rounded for customers; snapshot first; 3-stream limit.
- Handover (§3.4): matching counts move every order with actor `captain`; mismatch without scans refused, with scans moves only scanned ones; wrong code and the attempts limit; rider mode for a society without a captain; a captain whose M9 assignment ended is refused; replay returns the first answer.
- Door and exceptions: bag QR must match; rider needs otp or photo; hold keeps `at_society` until `hold_expiry`; `hold_expiry` reasons; first miss → one redelivery on T+1's plan; second miss → refund of perishables minus ₹20, never negative; damaged/missing refunded in full; tiffin failure refunded at once; triage refund bounded by what is left of the paid total.
- Privacy: captain views never contain names or phones and only her society; no event payload carries a code, phone, flat or coordinate; data export and erasure.
- Solver integration (with M10): golden routes from fixed seeds are feasible; the 26 s gateway timeout leads to the fallback, never a missing plan.
- E2E (MS-B): seeded hub → cut-off → 23:15 plan published → packing in route order → start → handover → every flat delivered, plus one held bag that becomes a redelivery and is delivered the next morning; on the simulator seed.
- Device: background location on a low-end Android (Capacitor background geolocation plugin), pings stop at complete; offline handover and door scans replay once.

## 10. Dependencies
- **M4** (`orders.api`), changes from ADR-0026 made in the M6 packing PR: the field actors and `actor_user_id` on `transition`, the per-kind state machine, `kinds` on `orders_for_delivery`, `system` on `at_society → undelivered`, `kind` in fulfilment events. **Further M4 changes this spec needs** (in the M7 PR, reviewed by both owners; they extend ADR-0026 §2's table): `system` on `undelivered → redelivery` (the automatic free redelivery); `dispatched → undelivered` for box and add-on orders by `rider`, `system` and `hub` (bags that ride back to the hub: a skipped stop, a count mismatch, `close_routes`); `rider` on `dispatched → at_society` and `at_society → delivered / undelivered` also where a hub manager switched the stop to rider mode for an absent captain (M7 checks it); `transition(…, "refunded")` takes `payload.refund_paise` (default: what is left of the paid total) and `payload.reason`; `orders.delivery_failed` carries `reason`; `orders_for_delivery` accepts `statuses=("locked", "packed")`.
- **M9** captains PR (before M7): `community.captains` with one active captain per society, `community.api.captain_for_society`, the `captain_assigned`/`captain_ended` events. **Also needed from M9:** `community.api.society_points(society_ids) -> {id: (lat, lng)}` (a nullable gate point on societies, else a point inside the boundary) and tower labels (already needed by M6).
- **M6** (`hub_ops.api.society_loads`, `bag_for_order`; consumes `logistics.route_published` and `route_cancelled`), **M1** (`identity.api.delivery_labels`, rider grants for a hub, the data export section `logistics`, the erase hook), **M0** (a new upload purpose `delivery_photo`: JPEG/WebP, 5 MB, 90 days after the delivery), **M2** (`catalog.api.grams_of` and shelf life per product), **M10** (`intel_gateway.api.solve_routes`, PR on `feat/M10-route-solve`), **M5** (refunds through M4), **M11** (the event map above), **M8** (tiffin assignments in §3.7; captain commission and rider pay from M7 events at its PR).
- **Docs to update with this spec:** handbook 07 §7.1 and W5 (solve at T-1 23:15 per hub, not T 07:00 per slot; crates by count and handover code, not crate QR; hold until 20:00, one redelivery, then the refund rule; the fallback is the previous day's order or nearest neighbour), handbook 10 §10.4 (`solve_routes` T-1 23:15; the new M7 jobs) and §10.3 (the M7 events), handbook 05 §5.5 ERD for `logistics`, handbook 06 §6.2 (hold and redelivery triggers), M10 §3.4 (stop ids and the 23:15 trigger), M11 §3.1 event map (the six events above and a category for partner messages).

## 11. Open questions and decisions
- **Decided (ADR-0026, owner 2026-10-09):** captain from M9 (one per society), rider as the last-50-m fallback, crates counted not tracked, bag QR `GB1:<order_id>`, solve at T-1 23:15, hold until 20:00 with `hold_expiry` at 20:30, one free redelivery the next morning then the perishables refunded minus a fee, electric cargo three-wheeler (400 kg, 24 crates) for boxes and two-wheelers for tiffins.
- **Decided (this spec):** the fee is ₹20; perishable means catalog shelf life ≤ 96 h; ML down → previous day's order, else nearest neighbour; no live traffic in v1 ETAs.
- **Open (owner):** where a held bag spends the night before its redelivery. This spec assumes it stays with the captain and her society gets a stop on T+1's routes (the van brings nothing for it if the society has no other orders that day). The alternative, an M4 move `redelivery → at_society` by the captain without a van, saves that stop but changes ADR-0026's decided path.
- **Open (owner):** a box missed twice: this spec refunds only the perishables (as decided) and sends the non-perishables back with the bag to the hub's surplus cascade. Should non-perishables (atta, oil) be refunded too, or ride on the family's next delivery?
- Pilot data (Rhythm Thakur, Manish Raje): real vehicles and their speed against OSRM (GPS traces), captains' real windows, orders per crate.
