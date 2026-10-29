# M7 — Logistics

**Status:** Draft v2 (with [ADR-0026](../adr/0026-operations-contract.md) and its amendments in [ADR-0027](../adr/0027-operations-contract-amendments.md)). §3.1 (vehicles), §3.2 (route plans: solve, fallback, publish), §3.3 (rider execution, GPS and live tracking), §3.4 (handover, door proof, undelivered, hold and redelivery) and §3.5 (the customer's delivery view) are frozen for S5 when this spec is approved; §3.6 (return-leg pickups) was refined on 2026-10-25, is frozen on approval and is built; §3.7 (tiffin routes) was refined on 2026-10-28, is frozen on approval and is built (lunch; "As built" notes in §3.7).
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P3 / S5
**Postgres schema:** `logistics` · **Python package:** `gharbasket.modules.logistics`

## 1. Purpose
Getting bags from the hub to each society and each flat: vehicles, route plans (solved by M10's OR-Tools CVRPTW through `intel_gateway.api.solve_routes`, with a fallback in M7 when the ML service is down), rider execution, live location, the **Society Captain handover**, the last 50 metres with proof, failed deliveries (hold, one redelivery, refund) and return-leg pickups. M7 moves orders only through `orders.api` (M4 owns the order and its states), reads the captain of a society only through `community.api` (M9 is the source of truth, ADR-0026 §4) and reads bag counts from `hub_ops.api` (M6). It does not pack, pay anyone or send messages.

**Build order for MS-B** ("OR-Tools routes → captain handover → delivered"): (a) vehicles and route plans → (b) rider execution with GPS and live tracking → (c) handover, door proof, undelivered, hold and redelivery → (d) return-leg pickups (with M9 donations, P5) and tiffin routes (with M8, S6). (a)–(c) are frozen below; (d)'s pickups are specified in §3.6 and its tiffin routes in §3.7.

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
| US-8 | As a captain, I want to hold a bag when nobody is home so that the family can collect it the same evening. | Held until 20:00; at 20:30 → `undelivered`; the bag stays with her overnight and one free redelivery comes the next morning; if that is missed too, the perishables are refunded minus a fee and the non-perishables come with the family's next delivery. |
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
| POST | `/v1/stops/{id}/pickups` | rider (own route) | Required | §3.6; empties, donations and bags taken back at a stop |
| POST | `/v1/stops/{id}/collect` | rider (own route) | Required | §3.7; the kitchen's `GB1A:` scan: its ready tiffins `packed → dispatched` |
| GET | `/v1/hubs/{id}/pickups?date=` | hub_manager (hub), admin | — | §3.6 |
| POST | `/v1/internal/jobs/solve_routes` · `solve_tiffin_routes` · `persist_gps_pings` · `hold_expiry` · `schedule_redeliveries` · `close_routes` · `purge_gps_pings` | operator token | — | §6 |

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
  "source": "ml" | "ml_fallback" | "fallback_previous" | "fallback_nearest" | null,   null until
                                   solved; ml_fallback: M10 answered with its own nearest neighbour
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
  202 : RoutePlan (status queued)      the API solves it in a background task right after the
                                       request (no worker, ADR-0021; an outbox consumer may not
                                       call the ML service); poll GET /v1/route-plans/{id}. A
                                       plan queued or solving for 10 min is failed (abandoned)
GET  /v1/hubs/{id}/route-plans?date=&kind=   200 { "items": [ RoutePlan without routes ] } newest first
GET  /v1/route-plans/{id}                    200 RoutePlan | 404 "logistics.plan_not_found"
POST /v1/route-plans/{id}/publish     hub_manager; Idempotency-Key
  request : { "accept_problems"?: false }
  200 : RoutePlan (published)          the routes become published; the date's previously
                                       published plan becomes superseded and its routes cancelled
```

**What a solve sends** (M7 builds `intel_gateway.api.solve_routes`' `RouteRequest`, M10 §3.4):
- **Orders:** `orders.api.orders_for_delivery(hub, date, statuses=("locked", "picking", "packed"), kinds=("box", "addon"))`, plus the redeliveries M7 has scheduled for that date (§3.4). At 23:15 the set is fixed: the cut-off was at 21:00 and `payment_grace_expiry` at 23:00.
- **Stops:** one per (society, slot) with orders, `id = "<society_id>/<slot_id>"`; location from `community.api.society_points` (the gate, else a point inside the boundary; a society without either is left out and named in `problems`, so the plan cannot auto-publish); window = the slot's start and end (M4 `slot_ref`); `orders` and bags = the number of orders (one bag per order, M6); `crates = ceil(bags / 3)` (M6's three bags to a crate; the actual counts come from `hub_ops.api.society_loads` at route start); `kg_tenths` from the order lines through `catalog.api.grams_of` (0.5 kg a step when a product has no weight); `empties` = the society's open empties (§3.6), on its earliest-window stop; `donation_kg_tenths` = 0 and no `food_bank` (donations ride back to the hub, §3.6).
- **Vans:** the fleet above; `depart_s` = the hub's `timings.dispatch` (06:30, M6 §3.1); `return_by_s` = the latest window end + 90 min; `time_limit_s` 20 (two waves of 10 s); service, cost and lateness at the M10 defaults.
- **Mapping the answer:** each element of `routes[]` is a trip; van index *i* is the *i*-th vehicle by label; trips of one van are numbered by start time. ETAs are `eta_s` as IST clock times. The route's rider is the vehicle's `default_rider` (or null). The whole answer is kept in `intel.predictions` by the gateway and its summary on the plan.

**Fallback (ML never blocks, M10 §3.4).** When the gateway returns `None` (service down, slow or not configured), M7 builds the plan itself, in the API, in under a second:
1. **`fallback_previous`:** the stop order of the hub's last published plan of the same kind in the past 7 days; societies that were not in it are inserted at their nearest neighbour; trips are filled in that order up to the vehicle's kg and crate capacity, early-window stops first.
2. **`fallback_nearest`:** with no previous plan, nearest neighbour from the hub by haversine distance × 1.3, early-window stops first, filling each vehicle to capacity.

ETAs then come from haversine × 1.3 at 22 km/h × the traffic factor (1.5), with the same service times. M7 runs its own structural check on every plan, from the ML service or not: every stop on exactly one route, kg and crates within capacity leaving the hub, no more routes per vehicle than two, no route leaving before `depart_s` or before its vehicle's previous trip is back and reloaded, and every route back by `return_by_s`; a stop id in the ML answer that is not one of the plan's is named too. Its findings join `summary.problems`. A request M10 would refuse (more than 400 stops or 60 vans) is not sent: M7 plans it alone. A solve that fails before any plan exists (no active vehicle, no orders with a location) ends `failed` with `error`.

**Rules**
- **One plan in flight.** A second solve for the same hub, date and kind while one is `queued` or `solving` → 409 `logistics.solve_running`. A plan with no orders is `solved` with no routes (and auto-publishes; nothing is sent to M10).
- **Auto-publish.** A plan from the `solve_routes` job is published at once when `summary.problems` is empty; otherwise it stays `solved` and M7 publishes `logistics.plan_needs_review` (M11 tells the hub managers). A manual solve is never auto-published.
- **Publish** needs `summary.problems` empty, or `accept_problems: true` (recorded in `platform.audit_log` with the problems), else 409 `logistics.plan_has_problems`. A plan that leaves an order's stop off every route, or puts one on two routes, can never be published: 409 `logistics.plan_incomplete` with `stop_ids`. Publishing supersedes the date's other `solved` plans too. Publishing a plan with a vehicle that has no rider is allowed; the hub manager sets riders with `PATCH /v1/routes/{id}`.
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
  "stops_total": 6, "stops_done": 2, "crates": 18, "bags": 52, "km": 14.8,   km: an estimate
                                                  (straight line x 1.3 along the stops)
  "started_at": "…" | null, "completed_at": "…" | null, "version": 3 }

Stop : { "id", "seq": 1, "kind": "drop" | "bank" | "kitchen",
  "society": { "id", "name", "location": { "lat", "lng" } } | null,
  "slot": { "id", "label", "start", "end" } | null,
  "eta": "07:12", "late_min": 0,                  planned; "eta_now" adds the route's current delay
  "eta_now": "07:20" | null,
  "status": "pending" | "arrived" | "handed_over" | "skipped",
  "crates": 3, "bags": 9,                          planned; from M6 society_loads after start
  "redelivery_bags": 1,                            redelivery bags the van carries for this stop
                                                   (they came back to the hub); bags the captain
                                                   kept overnight are not counted (§3.4)
  "empties_expected": 2,                           §3.6: planned at the solve, set again at start
  "pickups": StopPickups | null,                   §3.6: from route start; null before, and on
                                                   bank and kitchen stops
  "captain": { "display_name", "photo_url" } | null,   null: no active captain (rider mode)
  "rider_mode": false,                             true: the rider does the handover and the doors
  "handover_code": "4821" | null,                  only to the route's rider and hub staff, while
                                                   in progress; the captain types it (§3.4)
  "navigate_url": "https://www.google.com/maps/dir/?api=1&destination=19.03,73.03",
  "arrived_at": "…" | null, "handed_over_at": "…" | null }

RouteDetail : RouteSummary & { "stops": [ Stop ], "source": "ml" | "ml_fallback" | "fallback_previous" | "fallback_nearest",
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
- **Start** needs the route `published`, its date today (IST; else 409 `logistics.route_not_today`), the caller as its rider (403 `logistics.not_your_route`; hub managers may start for a rider) and no other route of that rider `in_progress` (409 `logistics.rider_busy`). It moves every order of its stops that is `packed`, and every redelivery booked on it, to `dispatched` (actor `rider`; ADR-0026 §2; also when a hub manager starts the route for its rider: the orders move as the route's rider, the manager is on the field event, [ADR-0028](../adr/0028-m7-order-moves-by-hub-managers-and-orphan-redeliveries.md), accepted) and reads the actual crates and bags per stop from `hub_ops.api.society_loads`. Orders still `locked` or `picking` → 409 `logistics.bags_not_packed` with `order_ids`, unless `leave_behind: true`: they stay off the route (listed in `left_behind`) and appear on the hub's exceptions board. A second trip starts only after the first is completed.
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
- **Who sees what.** The route's rider and the hub's hub managers: every stop and every position. A **captain**: her own stop only (`stops` holds one entry, plus `"stops_before": 2`) and the position until her stop is handed over. A **customer**: only while an order of their household (or a redelivery booked on one of this route's stops) is on this route and its stop is not yet handed over; one entry with the stop's `eta_now` and `stops_before`, never other societies; positions rounded to 4 decimals (about 11 m) and at most every 15 s. Anyone else → 403 `auth.forbidden`; a customer whose stop is done gets `end` (`handed_over`) and the stream closes.
- The first event is always `snapshot`, so a reconnect needs no `Last-Event-ID`. A stream closes after 30 minutes with `end` (`reconnect`); at most 3 open streams per user (429 `logistics.too_many_streams`). The app reads the stream with a fetch-based SSE reader because `EventSource` cannot send `Authorization`; the console's browser sends cookies.
- Positions come from the Valkey channel; when Valkey is down the stream polls the latest persisted ping every 15 s. *As built:* the stream reads the route's latest position (`gb:loc:<route_id>`) every 5 s instead of subscribing to `gb:live`, which is still published to; the events are the same. A position older than 2 minutes is sent with its `age_s` so the app can show "last seen".

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
                // as built: M6's bag-label format, "<tower label>, <flat>" (e.g. "B wing, 402")
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
  request : { "bag_qr": "GB1:0199a0000000700080000000000000ab",   box and add-on: always; must be
                                                   this order's. A tiffin sends "short_code":
                                                   "T-07" instead (§3.7)
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
- **Counts** are compared with what the van carries for the stop (`society_loads` at start, plus redelivery bags that came back to the hub). Redelivery bags the captain kept overnight (§3.4, first miss) are not counted; they move to `at_society` with the handover, so her society needs the stop even when the van carries nothing else for it (she then counts 0 crates and 0 bags and types the code). When crates and bags match, every order of the stop that is `dispatched` moves to `at_society` (actor `captain`, or `rider` in rider mode, or `hub`). When they do not match, the captain scans the bags she has (`scanned_bags`); without scans → 409 `logistics.count_mismatch` with the expected counts. With scans, only the scanned orders move; the rest stay `dispatched` (`still_dispatched`) and return with the van (`returned_to_hub` at complete). A mismatch is kept on the handover and shown on the exceptions board.
- One handover per stop: another one → 409 `logistics.already_handed_over` (a replay of the same key answers the first). The rider's distance from the society at that moment (from the latest ping) is stored for audit and never refuses a handover.
- Publishes `logistics.handover_completed`; M4 publishes `orders.at_society` per order.

**Door rules**
- **Proof.** `bag_qr` must be the order's (`GB1:` + the order id without hyphens, M6 §3.2; else 422 `logistics.bag_mismatch`). A captain may use `scan` alone: she is a verified partner and the bag is in her hands. A rider (rider mode or tiffin) needs `otp` or `photo` (422 `logistics.proof_required`). A tiffin has no bag QR (kitchens label it with its `short_code`, M8 §3.4): its `short_code` must be the order's (else 422 `logistics.bag_mismatch`) and anyone delivering it, captain or rider, needs `otp` or `photo`. Five wrong delivery codes for an order → 429 `logistics.code_attempts_exceeded`, then only a photo.
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
  - **Where the bag spends the night** (owner, 2026-10-09): a held bag (`not_home`) stays overnight with the captain; a `returned_to_hub` bag stays at the hub and rides on the T+1 van. Either way the order takes ADR-0026's path `redelivery → dispatched` (at route start, actor `rider`) and then `at_society` at the stop's handover; the captain delivers a bag she kept only after that handover.
  - **second miss** (attempt 2 undelivered): `undelivered → refunded` (actor `system`) with `refund_paise` = the paid amount of its **perishable** lines (catalog shelf life ≤ 96 h, the catalog's rule) for the steps delivered (`qty − shortage_qty`) at the line's paid price, **minus a ₹20 fee (2,000 paise)**, never below 0. The **non-perishable lines** (owner, 2026-10-09) are neither refunded nor surplus: they ride on the household's next delivery. The bag goes back to the hub on the next return leg (§3.6, pickup kind `bag`) when the captain holds it, or stays there when it came back with the van. At the hub M6 sends its perishables into the surplus cascade and keeps its non-perishables aside for the household. M7 calls `orders.api.carry_over(session, order_id, lines)` with the non-perishable lines and their delivered steps; M4 adds them to the household's next box or add-on order at no charge (`carried_from_order_id` on the line), and M6 packs them from the kept items (M4 and M6 follow-ups, §10). **How long they wait** (a default set by [ADR-0027](../adr/0027-operations-contract-amendments.md), which the owner may change): up to 14 days after the missed attempt; with no delivery of the household packed in that time, M4 refunds the carried lines at their paid price, without the fee, and M6 sends the kept items to the surplus cascade.
- *As built (until M4's `carry_over` and M6's kept items exist):* a second miss refunds the perishable lines minus ₹20 (never below 0) and **does not refund the non-perishable lines** (the owner's decision of 9 October stands); they are recorded, not lost: on the `resolve` field event, and in an outbox event `logistics.carry_over_pending { order_id, household_id, hub_id, society_id, lines: [{ product_id, steps, paid_paise }] }`, which M4's `carry_over` and the next packing read once they are built. Nothing carries them to the next box yet, so for now they wait unrefunded and unpacked (see §10 for what is missing). The carry-over will be built next as an M4 follow-up, contract first, before MS-B: specified in M4 §3.8 (M7 then calls `orders.api.carry_over` in the second-miss transaction and keeps the event as its record). When the wallet cannot take the refund the attempt goes to triage. A redelivery is booked on the first day whose plan is not yet solved. The evening job also takes up undelivered attempts of earlier days that never got a decision (a route `close_routes` completed late).
- *As built, who holds the bag:* an attempt that ended at the society with the bag in the captain's hands (a held `not_home`, a captain's `refused`, `wrong_address` or `other`, a bag never scanned at `hold_expiry`) sets `kept_by_captain`, and attempt 2 keeps it, so the next morning's van carries nothing for it, her society still gets a stop and the handover moves it without a count. In a rider-mode stop the bag is the rider's and rides back.
- *As built, a bag the hub left behind:* a packed order the day's route left behind goes to triage (`left_behind`) at `hold_expiry`, from where the hub can only book it again. The ride is **attempt 1 booked again** (`hub_ride`, status `scheduled`, a new date): it rides like a redelivery but is not a customer miss, so a real miss on that ride still gets the one free redelivery (attempt 2) and triage still offers `redeliver`.
- **Triage** (`resolve`, hub manager): `redeliver` (only on attempt 1; `undelivered → redelivery`, actor `hub`, as above) or `refund` with `refund_paise` up to what is left of the order's paid total after earlier credits (default: all of it; 422 `logistics.refund_invalid` with `max_paise` above that). Not `undelivered` → 409 `logistics.not_undelivered`. *As built:* a refund below all that is left needs `"final": true` (else 422 `logistics.partial_refund_not_final` with `max_paise`), because `refunded` is final and the rest of the money could never go back. A refund the wallet cannot take (it would pass the ₹10,000 cap) goes to the customer's payment source instead, inside `refund_to_wallet` (M5 §3.3, 2026-10-21). Only a refund the top-ups cannot take either still answers 422 `billing.wallet_limit` with `room_paise` and changes nothing; the attempt then waits in triage until the customer has room. A **redelivery no route picked up** (an orphan: its day passed, or its day's plan was published without a stop for it; listed in `orphans` on the board) is resolved too: `redeliver` books it again on the next solvable day (no order move), `refund` moves the order `redelivery → refunded` in full ([ADR-0028](../adr/0028-m7-order-moves-by-hub-managers-and-orphan-redeliveries.md), accepted). Two resolves at once: the second is 409 `logistics.not_undelivered`.
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

### 3.6 Return-leg pickups (drafted 2026-10-25, frozen on approval)
The rider's Pickups screen and stop detail, and the console's pickups list, build against this. Same review rule as §3.1. When the van is at a society it also takes back what the society has for the hub: **empties** (crates left there at earlier handovers), **donations** (M9 §3.4 donations scheduled for that society and day) and **bags** (a box missed twice whose bag the captain kept, §3.4). Everything rides back to the hub on the same van: the hub counts and verifies donations (M9 §3.4) before any hand-off to an NGO (M9 §3.5), so no `bank` stop is planned yet.

```
StopPickups : {                                  Stop.pickups (§3.3), from route start
  "empties_collected": 4 | null,                 crates the van took back here; null until
                                                 reported (the expected count is Stop.empties_expected)
  "donations": [ { "donation_id",
                   "items": "clothes 3 kg, toys 2",       M9's summary (community.api.pickups_for)
                   "flat_label": "B wing, 402" | null,    rider-mode stops of a route in progress only
                   "status": "expected" | "collected" | "not_ready" } ],
  "bags": [ { "order_id", "flat_label": "B wing, 402" | null,   same rule
              "status": "expected" | "collected" | "not_ready" } ] }

POST /v1/stops/{id}/pickups       the route's rider; Idempotency-Key
  request : { "empties"?: 4,                     0 to 100: the stop's count so far (replaces it)
              "donations"?: [ { "donation_id", "status": "collected" | "not_ready" } ],
              "bags"?: [ "GB1:0199a0000000700080000000000000ab" ],   scanned: collected
              "client_at"? }                     at least one of empties, donations, bags; no id twice
  200 : Stop                                     with its pickups as they are now

GET  /v1/hubs/{id}/pickups?date=  hub_manager (hub), admin; Cache-Control: no-store
  200 : { "hub_id", "date",
          "totals": { "empties": { "expected": 12, "collected": 10 },
                      "donations": { "expected": 0, "collected": 2, "not_ready": 1 },   by status
                      "bags": { "expected": 0, "collected": 1, "not_ready": 0 } },
          "items": [ { "stop_id", "route_id", "vehicle": { "id", "label" }, "seq",
                       "society": { "id", "name" }, "stop_status": "pending" | …,
                       "empties_expected": 3,
                       "pickups": StopPickups } ] }      flat labels always (hub staff);
                                                         by departure, vehicle label, seq
      date: today − 7 … today + 1 (IST), default today; [] before the day's routes start
  404 "hub_ops.hub_not_found" · 403 "auth.forbidden" (another hub)
```

**Expected pickups** are made when the route starts (§3.3), as `logistics.pickups` rows, for every `drop` stop with a society. Like the crate counts, they are a snapshot of that moment:
- **Empties:** one row per stop (also with nothing to expect, so a report has a row to set). `qty` = the society's **open empties**: the crates counted at its handovers (`handovers.crates_counted`; a rider-mode handover leaves none, the rider keeps his crates), minus the empties collected there since, minus empties still expected on another stop, never below 0. The count is a number per society, never per crate (ADR-0026 §6). `Stop.empties_expected` takes this value at start; the solve set a first figure (§3.2).
- **Donations:** one row per donation of `community.api.pickups_for(session, society_id, delivery_date)` (M9: `scheduled`, `pickup_on` = the route's date), with M9's item summary.
- **Bags:** one row per order of the society whose attempt 2 was `refunded` with the bag in the captain's hands (`kept_by_captain`, §3.4), on a day before the route's, and that no stop has collected.
- **One stop takes them.** A donation or bag already `expected` or `collected` on another stop is not expected again, so a society with two stops that day (two slots, or two trips) has its donations and bags on the first stop to start. A stop of a route that started before this shipped gets its rows at its first report.

**Reporting** (`POST /v1/stops/{id}/pickups`)
- **Who and when.** Only the route's rider (another rider of the hub: 403 `logistics.not_your_route`; a role without `routes:execute`: 403 `auth.forbidden`; outside the rider's hubs: 404 `logistics.stop_not_found`), while the route is in progress (409 `logistics.route_not_started` · `logistics.route_closed`), at a stop that was not skipped (409 `logistics.stop_skipped`). Arrive and handover are not required first.
- **A report may name a part.** The app taps empties, then each donation or bag, through its outbox. `empties` replaces the stop's count (a correction is allowed until the route completes). Each donation and bag is set once: naming it again with the same status changes nothing; with the other status → 409 `logistics.pickup_already_reported` (`donation_ids`, `order_ids`), nothing changed.
- **Only what is expected here.** A donation id that is not expected at this stop, or a bag code that is not `GB1:` + an order expected here → 422 `logistics.pickup_unknown` (`donation_ids`, `bags`), nothing changed. An empty report or an id named twice → 422 `request.invalid`.
- Each report that changes something writes a `pickup` field event (with `client_at`), bumps the route's version and publishes `logistics.pickup_completed` with what it changed. A replay of the same key answers the first call (M0).
- **At route complete** (and `close_routes`), rows still `expected` become `not_ready`, without an event: M9's `sweep_missed_pickups` (22:30) treats an unreported donation as not ready, and a bag not collected is expected again at the society's next stop.

**`logistics.pickup_completed { stop_id, route_id, hub_id, society_id, delivery_date, rider_user_id, empties: 4 | null, donations: [ { donation_id, status } ], bag_order_ids: [ … ], at }`** carries only what that report changed (`empties`: the stop's new count, or null when the report did not send it). No name, flat or phone. Consumers:
- **M9** (`community/jobs.py`): per donation, `community.api.record_pickup(session, donation_id, status, by_user_id=rider_user_id, on=delivery_date)`: `collected` makes it `picked_up`; `not_ready` rolls it once to the society's next delivery date, then cancels it (M9 §3.4). A donation no longer `scheduled` is skipped, so a redelivered event changes nothing.
- **M6** (`hub_ops/jobs.py`): each bag goes through §3.5's returned-bag intake (the code behind `POST /v1/hubs/{id}/returned-bags`, `scanned_by` = the rider): its carried non-perishables become kept items and the rest returned lots. The hub's scan when the van is back answers the same result. A bag the intake refuses (`hub_ops.bag_unknown`, `hub_ops.bag_not_returned`) is logged and skipped.
- Not mapped in M11; nothing else consumes it yet.

**The solve** (§3.2): a stop's `empties` in M10's request is its society's open empties (on the society's earliest-window stop when it has two). `donation_kg_tenths` stays 0 and no `food_bank` is sent: M10 refuses donations without a food bank, and donations go to the hub. The fallback planner ignores empties, and M7's structural check counts crates leaving the hub only (§3.2).

**Privacy.** Riders see flat labels only on rider-mode stops of their route while it is in progress (§Privacy); at a captain's stop the rider tells donations apart by their items. Hub staff see flat labels on the hub's list, as on M9's donations queue. `logistics.pickups` keeps the household id of a donation or bag row for the label; M1's erasure nulls it and the reporter's user id.

**Permissions** (§7): `routes:execute` (rider, own route) covers the report; `pickups:read` (new) is hub_manager (hub) and admin.

**Deferred**
- A `bank` stop and `donation_kg_tenths` in the solve: when donations go straight from a society to a food bank (M10 §3.4 supports it), which waits for M9 §3.5's hand-offs to settle what goes where.
- Pickup-only stops: a society with pickups but no delivery that day gets no stop; its donations roll (M9's sweep), and its empties and bag wait for its next stop.
- The captain's view of what the van takes back (`CaptainDay`), a hub manager reporting for a rider, a label or QR on donation bags, pickups on tiffin routes, and an M12 facade for pickups.

| Mock today (`frontend/mobile/src/mocks/logistics.js`, rider Pickups screen) | Contract |
|---|---|
| `Stop.pickups` shown before the route starts | `pickups` is null until start; `empties_expected` is there the night before |
| `pickups.donations[]`: `{ donation_id, ref: "B-402", qty, note, status }` | `{ donation_id, items, flat_label, status }`: `items` is M9's summary (replaces `qty` and `note`); `flat_label` (replaces `ref`) is null at a captain's stop, so the rider taps the donation instead of typing its flat |
| No bags | `pickups.bags[]` (`order_id`, `flat_label`, `status`); the rider scans the bag's `GB1:` QR and sends it in `bags` |
| POST answers `{ stop_id, empties, donations }`; an unknown donation is 422 `request.invalid` | The answer is the `Stop`; an unknown donation or bag is 422 `logistics.pickup_unknown`, a changed status 409 `logistics.pickup_already_reported` |

### 3.7 Tiffin routes (refined 2026-10-28, frozen on approval)
The rider's tiffin screens (Today, Route map, Kitchen stop with the scanner, Stop detail), the tiffin rows of the captain's Today and Flat checklist, the customer's delivery card for a tiffin and the console's Route plans (kind `tiffin`) build against this. Same review rule as §3.1. A tiffin day reuses §3.2's plans, §3.3's routes, stops and rider endpoints, §3.4's handover, door and undelivered endpoints and §3.5's card. The only new endpoint is the rider's **collect** at a kitchen. This section covers **lunch** only (dinner: question 6 below).

**The day (IST, delivery day T, lunch slot 12:30–13:30)**

| When | What | Who |
|---|---|---|
| 00:00 → 11:30 | Kitchens tap Start and Ready (M8 §3.4); `ready_by` = slot start − 60 min = 11:30 | kitchen |
| 09:30 | Manual assignment closes (`ready_by` − 2 h) and `refund_unassigned_tiffins` refunds tiffins nobody cooks (M8) | M8 |
| **09:35** | **`solve_tiffin_routes`**: one `tiffin` plan per active hub for T, published when M7's check is clean | M7 job |
| about 11:00 | The rider starts his route (`POST /v1/routes/{id}/start`) | rider |
| from 11:30 | At each kitchen the rider scans the kitchen's `GB1A:` code (`POST /v1/stops/{id}/collect`): its ready tiffins go `packed → dispatched` | rider |
| 12:30–13:30 | At each society: the captain takes the tiffins and delivers them, or the rider delivers them in rider mode | captain, rider |
| 16:00 | `close_routes` closes what is still open (below) | M7 job |

**The plan** (§3.2 with `kind: "tiffin"`)

What a solve reads, for hub H and day T:
- **Kitchens and their tiffins:** `partners.api.tiffin_pickups(session, H, T, "lunch") -> list[PickupRef]` (built, M8 §3.4 "As built, part 5"): the assignments that are `accepted`, `preparing` or `ready`, by `ready_by`, each with its orders not yet picked up. `PickupRef` is `{ assignment_id, partner_id, display_name, address, lat, lng, ready_by, status, order_ids }` plus two additions made in the build PR (see "Facade additions" below): `slot_id` and `orders: [PickupOrder { order_id, short_code, portions }]`. A kitchen without a location is left out and named in `problems` (M8 offers such a kitchen nothing, so this should not happen).
- **Societies:** `orders.api.summaries(session, order_ids)` gives each tiffin's `society_id` and `slot_id`; the location comes from `community.api.society_points` (as §3.2); the window is the slot's start and end (`orders.api.slot_ref`). A locked tiffin in no live assignment is not M7's: it is not routed and not a problem (M8's tiffin board lists it, and M8 refunds it at 09:30).
- **Fleet:** the hub's active `two_wheeler` vehicles, by label (§3.1). As in §3.2, the v0 rule takes one capacity per fleet: the smallest `capacity_tiffins`, counted in **portions** (default 30; question 4). Weight is not checked (30 portions are about 15 kg, below the two-wheeler's 40 kg). A hub with tiffins and no active two-wheeler ends `failed` with "no active two_wheeler vehicle at the hub". A hub with no tiffins that day gets a `solved` plan with no routes, which auto-publishes (as §3.2).
- **No ML.** M10's v0 solver has no pickup-and-delivery, so no gateway call is made and no `intel.predictions` row is written. The plan has `source: "nearest"` (a new value), `model: "tiffin-nn-v0"` and `distance_source: "haversine"`.

How the stops and trips are made (in the API, deterministic, under a second; reusing `routing.py`):
1. **Kitchen stops.** One per assignment: `kind: "kitchen"`, at the kitchen's location, window `[ready_by, ready_by + 30 min]`, `portions` = its tiffins' portions, `bags` = its tiffins (one per order). They are ordered with `routing.order_nearest` from the hub: earliest `ready_by` first, then nearest neighbour by haversine × 1.3, ties by id.
2. **Trips.** Kitchens are taken in that order into trips, each up to the capacity in portions. A kitchen is never split between trips. A kitchen with more portions than the capacity rides alone and is named in `problems` ("kitchen <assignment_id>: 34 portions, a two-wheeler carries 30").
3. **Drop stops of a trip.** One `drop` per (society, slot) among the trip's tiffins, ordered with `routing.order_nearest` starting from the trip's last kitchen. A society whose tiffins come from kitchens on two trips gets a drop on each, and each drop carries its own tiffins. So every tiffin is on exactly one drop, on the trip that holds its kitchen, after that kitchen.
4. **Trips to vehicles.** Each trip goes to the vehicle that is free first (by label), at most two trips a vehicle (as `routing.fill`). A trip starts at the later of: the vehicle being free, and the first kitchen's `ready_by` minus the travel time from the hub. ETAs come from `routing.replay` (haversine × 1.3 at 22 km/h × the traffic factor 1.5; the rider waits for a kitchen's `ready_by` and a drop's window start; 4 minutes a stop, no time per tiffin). A second trip leaves when the first is back at the hub (no reload time). `depart` is the trip's start; `return_by` is the latest window end + 60 min (14:30).
5. **Rider and captain.** The route's rider is the vehicle's `default_rider` (as §3.2). A drop's `rider_mode` follows §3.4: it is true when the society has no active captain.

**M7's check for a tiffin plan** (its findings join `summary.problems`): every tiffin is on exactly one drop, on the route of its kitchen stop and after it; every kitchen stop is on exactly one route; the portions on board never exceed the vehicle's `capacity_tiffins`; at most two routes per vehicle, the second leaving after the first is back; every route is back by `return_by`. Lateness against the window shows in `summary.on_time` and `late_min` and is not a problem (as for boxes). `summary.orders` counts tiffins and `summary.stops` counts kitchen and drop stops.

**Publish rules** are §3.2's, with these differences:
- The job's plan is published at once when `problems` is empty and every tiffin is on a route; otherwise it stays `solved` and M7 publishes `logistics.plan_needs_review` (`kind: "tiffin"`).
- The hub manager solves again with `POST /v1/hubs/{id}/route-plans { "date", "kind": "tiffin" }` (date today or tomorrow), then publishes (with `accept_problems` if needed). A new plan replaces the published one only while none of its routes has started (409 `logistics.routes_started`).
- **`plan_incomplete` for a tiffin plan** is a tiffin on no drop or on two (`order_ids`). §3.2's (society, slot) rule does not apply, because a society may have a drop on two tiffin routes.
- Each published route publishes `logistics.route_published` with `kind: "tiffin"`. Its stops include the kitchen stops (`society_id: null`) and a new `partner_id` field (the kitchen's on kitchen stops, null otherwise).

**When an assignment changes after the solve.** M7 consumes no M8 event: the plan is a snapshot taken at 09:35. M8 adds no tiffins after 09:30 (manual assignment has closed), so the only possible change is a removal:
- **A kitchen suspended or leaving** cancels her assignments (M8 §3.4). Her code then answers 422 `logistics.pickup_code_invalid` (below), so the rider skips the kitchen stop (reason `other`). Her tiffins never ride; M8 has released them.
- **Before any route starts,** the hub manager can solve again and publish, which replaces the plan.
- **A tiffin ready at the scanned kitchen but on no drop of the route** (a manual plan solved before a manual assignment that came later) is returned in `not_on_route` by collect and stays at the kitchen. The hub sees it on the exceptions board at the end of the day (`left_behind`, below).

**Rider execution** (§3.3 reused)
- `GET /v1/riders/me/routes?date=` lists the tiffin route from its publish (09:35) as a `RouteSummary` with `kind: "tiffin"`. `GET /v1/routes/{id}` gives the `RouteDetail`, whose stops carry the tiffin fields below.
- **Start** (`POST /v1/routes/{id}/start`) of a tiffin route needs its date to be today and a rider on the route: 409 `logistics.rider_required` otherwise (the hub manager sets the rider with `PATCH /v1/routes/{id}`). The rider may not have another route in progress (`logistics.rider_busy`, as §3.3), so he completes a morning box route first. Start moves **no** order (the tiffins are still at the kitchens), reads no `society_loads`, makes no expected pickups (§3.6 is deferred for tiffin routes) and ignores `leave_behind`. It sets the handover codes of captain drops and refreshes `rider_mode` (as §3.3).
- **`arrive`, `skip`, location and live** work as in §3.3. Arriving at a kitchen stop publishes `logistics.stop_arrived` with `society_id` and `captain_user_id` null, which M11 does not turn into a message. Skipping a kitchen stop leaves its tiffins uncollected; skipping a drop leaves its tiffins with the rider.
- **Complete** (`POST /v1/routes/{id}/complete`) needs every stop `handed_over` (a kitchen stop: collected) or `skipped` (409 `logistics.stops_open`). Then:
  - tiffins still `on_route` (with the rider) become `undelivered` (reason `returned_to_hub`; actor `rider`, also when a hub manager taps, ADR-0028 §1) and are refunded in full at once (below);
  - tiffins of the route's kitchen stops that were never collected get attempt 1 in `triage` with reason `left_behind` (settled by the hub, below);
  - tiffins with a captain are not touched: her taps end them, or `close_routes` does.

**Stop fields on tiffin routes** (additions to §3.3's `Stop`; null or absent on box routes)

```
Stop (tiffin routes) adds :
  "kitchen": { "partner_id", "display_name", "area": "Sector 20, Kharghar",   kitchen stops only
               "ready_by": "11:30",
               "address": "B-1103, Sunrise Towers, Sector 20" | null,   to the route's rider from
               "location": { "lat", "lng" } | null } | null,            route start until collected
                                                                        (and hub staff); null before
  "portions": 12,                                   the stop's tiffin portions
  "tiffins": [ TiffinItem ] | null                  kitchen stop: what to collect; drop: what to deliver
TiffinItem : { "order_id", "short_code": "T-07", "portions": 2,
  "status": "to_collect" | "on_route" | "with_captain" | "delivered" | "undelivered" | "left_behind",
  "flat_label": "B wing, 402" | null }              rider-mode drops of a route in progress only (§Privacy)
```
On a kitchen stop `society` and `slot` are null, `crates` is 0 and `bags` is its tiffins; `navigate_url` points at the kitchen once `address` is shown. On a tiffin drop `crates` is 0 and `bags` is its tiffins. `handover_code` is set on captain drops only.

**Collect at a kitchen** (new)

```
POST /v1/stops/{id}/collect       the route's rider; Idempotency-Key
  request : { "pickup_qr": "GB1A:0199a0000000700080000000000000ab",   scanned on the kitchen's phone
              "client_at"? }
  200 : { "stop": Stop,                                   the kitchen stop, as it is now
          "collected": [ { "order_id", "short_code", "drop_seq": 4 } ],   moved packed → dispatched now
          "already": [ "<order_id>" ],                    collected by an earlier scan
          "not_ready": [ { "order_id", "short_code" } ],  on this stop, not ready yet: scan again later
          "not_on_route": [ "<order_id>" ] }              ready here, on no drop of this route: left there
```
- **Who and when.** Only the route's rider (another rider: 403 `logistics.not_your_route`; a role without `routes:execute`, a hub manager included: 403 `auth.forbidden`; a stop outside the rider's hubs: 404 `logistics.stop_not_found`). The route must be in progress (409 `logistics.route_not_started` · `logistics.route_closed`), the stop must be a kitchen stop (422 `logistics.not_a_kitchen_stop`) and not skipped (409 `logistics.stop_skipped`). Arrive is not required first.
- **The code.** M7 calls `partners.api.pickup(session, pickup_qr, route.hub_id)`. Its 404 `partners.pickup_invalid` (malformed, unknown, or another hub's code) becomes 422 `logistics.pickup_code_invalid`. A valid code of another assignment than the stop's is 422 `logistics.pickup_wrong_kitchen`. Nothing changes in either case.
- **The moves.** For each ready order of the answer that is on this stop (`logistics.stop_orders`), M7 locks the order (`orders.api.get(…, for_update=True)`) and reads it again:
  - `packed`: `orders.api.transition(order, "dispatched", actor="rider", actor_user_id=route.rider_user_id)`. Attempt 1 opens (`on_route`, `stop_id` = its drop stop, the tiffin's `short_code`, a new delivery code). It is listed in `collected`.
  - already `dispatched`, with its attempt on this route: listed in `already`, nothing changes. M8 marks a row `picked_up` from `orders.dispatched` through the outbox, so a second scan can still see it as ready.

  Tiffins of the stop that are neither in the answer nor dispatched are listed in `not_ready`. Ready orders of the answer that are not on this stop are listed in `not_on_route` and are not moved.
- **Nothing ready.** When nothing is collected by this scan and nothing was collected before, the answer is 409 `logistics.nothing_ready` with `not_ready`, and nothing changes: the kitchen has not tapped Ready yet, so the rider waits and scans again.
- **Repeat scans** are allowed while the route is in progress; each one collects what has become ready since. The first scan that collects anything marks the stop `handed_over` (`handed_over_at`; also `arrived_at` if it was not set). `bags` becomes the number of tiffins collected so far.
- Each scan that changes something writes a `collect` field event (`{ assignment_id, collected, not_ready }`, with `client_at`) and bumps the route's version. A replay with the same key answers the first call (M0); the app sends the scan through its outbox like any field tap.
- **Events.** M7 publishes no new event. For each tiffin, M4 publishes `orders.dispatched` (`kind: "tiffin"`, actor `rider`): M8 marks the row `picked_up` and accrues the rider's ₹8 (M8 §3.6), and M11 tells the household.

**Delivering a tiffin** (§3.4 reused)
- **The attempt.** A tiffin has attempt 1 only, opened at collect. It has no attempt 2 and is never redelivered.
- **At a captain's drop** (`rider_mode: false`):
  - **Handover.** The rider gives her the tiffins. She counts them and types the handover code (or sends a photo) in `POST /v1/stops/{id}/handover` with `crates: 0` and `bags` = the tiffins. The expected count is the drop's tiffins that are still `on_route`.
    - When the counts match, those attempts become `with_captain`. There is no M4 move, because tiffins skip `at_society` (ADR-0026 §3), so no `orders.at_society` is published. `logistics.handover_completed` carries the order ids. No empties are left (crates 0).
    - When they do not match: 409 `logistics.count_mismatch`, with the expected count and the expected `short_codes`. `scanned_bags` is not accepted on a tiffin drop (422 `request.invalid`). The rider first settles a tiffin he does not have (`undelivered`, reason `missing`) or delivers it himself, and then they hand over again.
  - **At the door** she proves each delivery with `POST /v1/deliveries/{order_id}/proof { "short_code": "T-07", "proof": "otp" | "photo", "delivery_code"?, "photo_key"? }`. This is `dispatched → delivered`, actor `captain`. She earns nothing for it; this is the default set by [ADR-0027](../adr/0027-operations-contract-amendments.md) §7, unchanged here, which the owner may change. On `orders.delivered` M8 accrues the kitchen's ₹60 a portion and completes the assignment.
  - **When she cannot deliver** a tiffin, she calls `POST /v1/deliveries/{order_id}/undelivered` with any reason. That is `dispatched → undelivered` (actor `captain`), refunded at once. A tiffin is never held, even for `not_home` (a lunch is not kept for the evening, ADR-0026 §3), and nobody collects it back.
- **At a rider-mode drop**, the rider delivers each tiffin with the same proof rules. No handover is needed, because there is no `at_society`. The stop becomes `handed_over` when its last tiffin is delivered or undelivered, or the rider skips it. He sees the flat labels there (`tiffins[].flat_label`, `GET /v1/stops/{id}/deliveries`). When a captain is absent, the hub manager switches her drop to rider mode (`PATCH /v1/stops/{id}`), as for boxes. A rider may deliver any tiffin of his route that is still `on_route` (§3.4: "tiffin on their route").
- **Proof rules** (as §3.4):
  - Exactly one of `bag_qr` and `short_code` is sent (422 `request.invalid` otherwise). `short_code` is `T-` and digits, and must be the order's (422 `logistics.bag_mismatch` otherwise).
  - `scan` is never enough for a tiffin, whoever delivers it (422 `logistics.proof_required`).
  - After five wrong delivery codes: 429 `logistics.code_attempts_exceeded`, and then only a photo is accepted.
  - A delivered or refunded tiffin: 409 `logistics.delivery_closed`.
- **Undelivered means refunded at once.** Every tiffin that becomes `undelivered` is refunded in the same transaction, whatever the reason and whoever moved it (rider, captain, hub, or the rider's complete with `returned_to_hub`):
  - M7 calls `orders.api.transition(order, "refunded", actor="system", payload={"refund_paise": orders.api.refundable_paise(order), "reason": <the reason>})`. M4 pays through `billing.api.refund_to_wallet`, or to the payment source when the wallet is full (M5 §3.3).
  - The attempt becomes `refunded` with `refund_paise`, after M4's `orders.delivery_failed` (with the reason) and M5's `billing.refund_issued`.
  - If even that refund is refused (`billing.wallet_limit`), the attempt goes to triage, as in §3.4.
  - There is no redelivery: `resolve` with `redeliver` on a tiffin is 409 `logistics.no_redelivery`. `hold_expiry` and `schedule_redeliveries` never take tiffins.

**The end of the day** (`close_routes`, T 16:00)
- **Tiffin routes still in progress** are completed as in §3.3, with one difference. M4's tiffin state machine does not let `system` move `dispatched → undelivered` (ADR-0026 §2), so the job moves no tiffin:
  - every tiffin attempt of the day still `on_route` or `with_captain` goes to `triage` with reason `not_confirmed`;
  - tiffins of the route's kitchen stops that were never collected go to `triage` with reason `left_behind`, as complete does.

  The job also sends to `triage` (`not_confirmed`) the tiffins still `with_captain` on routes the rider completed earlier. See question 1.
- **The hub manager settles them** from the exceptions board (`GET /v1/hubs/{id}/deliveries?status=triage`):
  - A `not_confirmed` tiffin (still `dispatched`): once the hub has checked with the captain or the family, `POST /v1/deliveries/{order_id}/proof` (photo) or `/undelivered` (actor `hub`, then refunded at once).
  - A `left_behind` tiffin (never collected): `POST /v1/deliveries/{order_id}/resolve { "outcome": "refund", "note" }`. M7 makes the hub's moves from wherever the order is to `undelivered` (`locked → picking → packed → dispatched → undelivered`; ADR-0026 §2 allows each one to `hub`; reason `left_behind`), then refunds it in full (`system`). See question 2.

**The customer's view** (§3.5 for a tiffin)
- `status`: `scheduled` until it is collected (there is no attempt yet), then `on_route`, `with_captain`, `delivered`, `refunded` or `triage`.
- `window`: the tiffin slot.
- `eta`: the `eta_now` of the drop that holds the order on the published tiffin plan, found through `logistics.stop_orders`, not by society and slot. It is null before a plan exists.
- `tracking`: from collect, while the tiffin is `on_route` and the route is in progress. For a tiffin, §3.3's customer audience is "my tiffin is collected and on this route": the stream ends with `end` (`handed_over`) when the tiffin is handed to the captain, delivered or undelivered.
- `captain`: the society's captain card, only when the drop is not in rider mode; null otherwise.
- `delivery_code`: shown while the tiffin is `dispatched`. Every tiffin needs `otp` or `photo`, so the household gives this code at the door.
- `hold_until` and `redelivery` are always null. The cook's card stays M4's `OrderOut.kitchen` with M8's card (unchanged).

**The captain's day** (§3.4's `CaptainDay` for tiffins)
- `stops` include her society's tiffin drops that are not in rider mode, with `expected` = `{ crates: 0, bags: <tiffins>, redelivery_bags: 0 }`.
- `flats` include those tiffins: `kind: "tiffin"`, and a new `short_code` field (`"T-07"` on tiffins, null otherwise) so she can match the label to the flat.
- A tiffin's status is `on_the_way` (no attempt yet, or `on_route`), `with_you` (`with_captain`), `delivered`, or `undelivered` (undelivered, refunded or triage). It is never `held`. `counts` include the tiffins.

**Notifications.** There is no new event. The flow reuses:
- `orders.dispatched` (the household: picked up from the kitchen), `orders.delivered`, `orders.delivery_failed` and `billing.refund_issued`. M11 already maps these; tiffin wording by `kind` is an M11 follow-up.
- `logistics.route_published` with `kind: "tiffin"`: to the rider ("Lunch run: 3 kitchens, 6 societies, first pickup 11:30") and to each captain of a captain drop ("4 tiffins today, about 12:50"). Its new `partner_id` on kitchen stops lets M11 later tell a kitchen when the rider comes (optional, not in this build).
- `logistics.stop_arrived` (drops only, to the captain) and `logistics.plan_needs_review` (`kind: "tiffin"`, to the hub managers).

**Privacy**
- **Kitchen address and location** are shown to the route's rider only from route start until the stop is collected (M8 §3.4: "the rider at pickup only"), and always to hub managers and admins. Before start the rider sees the kitchen's `area` (public). Captains and customers never see them, and no event carries them.
- **Short codes** are printed on the tiffin's label. The route's rider, the captain of the drop's society (today and yesterday) and hub staff see them; no event or log carries them.
- **Flat labels** follow §Privacy unchanged: the rider sees them only on rider-mode drops of his route while it is in progress, and the captain only for her society.
- **Kitchens** learn nothing about the rider from M7. `logistics.stop_orders` holds no personal data.

**Facade additions**
- **M8 (`partners.api`):** `PickupRef` gains `slot_id` and `orders: tuple[PickupOrder, ...]`, with `PickupOrder(order_id, short_code, portions)`. For `tiffin_pickups` these are the orders not yet picked up; for `pickup`, the ready ones. `order_ids` stays. `pickup` with no ready order returns an empty `orders` (as built), and M7 answers that with `logistics.nothing_ready`.
- **M7 (`logistics.api`):** `route_for_order` returns a tiffin's drop stop, found through `logistics.stop_orders`. Nothing else changes.
- **M4:** nothing new. `summaries`, `slot_ref`, `get(for_update)`, `refundable_paise` and `transition` already exist.

**Storage** (one migration in the build PR):
- `logistics.route_stops` gains `assignment_id` null, `partner_id` null and `portions` (default 0).
- New `logistics.stop_orders` (`stop_id` → route_stops, `order_id`, `short_code`, `portions`; unique (`stop_id`, `order_id`); index on `order_id`). Each tiffin is on two rows of one route: its kitchen stop and its drop.
- `logistics.deliveries` gains `short_code` null (tiffins).
- `field_events.action` gains `collect`, and `route_plans.source` gains `nearest`.

**Jobs** (to add to §6 and `infra/jobs/schedule.yaml`):
- **`solve_tiffin_routes`**, T 09:35 IST (cron-job.org, `"35 9 * * *"`). For every active hub it solves a `tiffin` plan for **today** (the IST date of `scheduled_for`). It skips a hub that already has a published plan or a job plan (`has_job_plan`), runs each hub in its own savepoint and also solves manual tiffin plans still queued (as `solve_routes`).
  - Why 09:35: manual assignment closes and `refund_unassigned_tiffins` runs at 09:30, so nothing is added after that.
  - It shares Neon's 09:30 wake, and it is 2 hours before `ready_by`.
- **`persist_gps_pings`** gains `"30 12,14 * * *"`. During the lunch run the pings wait in the Valkey stream and are moved at the 12:30 and 14:30 wakes, which already exist; no minute-level window is added (the 8-hour Neon budget). Live tracking reads Valkey, so it is not affected.
- **`close_routes`** (T 16:00) also takes tiffin routes and tiffins, as above.

**As built, part 1** (the plan: migration `2a9bfe1bd02f`, the M8 facade additions, `tiffin_plans.py` and `solve_tiffin_routes`):
- **One more M8 facade function.** The stop view needs the kitchen's public `area`, which `PickupRef` does not carry, and a kitchen stop is shown long after the 09:35 snapshot. So `partners.api.kitchen_places(session, partner_ids) -> {partner_id: KitchenPlace { partner_id, display_name, area, address, lat, lng }}` was added; M7 decides who sees the address and the point (Privacy above).
- **`unserved` of a tiffin plan holds order ids**, not stop ids, so `plan_incomplete` names them in `order_ids`. A tiffin whose society has no location (or whose slot is unknown) is left out like a kitchen without a location: named in `problems` ("tiffin T-07: the society has no location") and unserved.
- **A trip's start** is the later of the vehicle being free and the first kitchen's `ready_by` minus the travel from the hub; the hub's `timings.dispatch` does not apply to tiffins. Problems name a trip as for boxes ("TW-1 trip 1: 34 portions on board", "… back after 14:30").
- **`PATCH /v1/routes/{id}`** also refuses (422 `logistics.vehicle_too_small`) a two-wheeler whose `capacity_tiffins` is below the portions the route's kitchen stops collect.
- **`route_for_order` is not built yet.** The facade above lists it, but no module calls it and `logistics.api` has never had it; when M12 needs it, a tiffin's stop is its drop through `logistics.stop_orders`, as written.

**As built, part 2** (the day: `tiffins.py`, collect, the tiffin handover and doors, the route's end):
- **The handover answer** of a tiffin drop gains `with_captain` (the order ids she took; they stay `dispatched`); `at_society` and `still_dispatched` are empty. A rider (rider-mode drop) or a kitchen stop is refused with 422 `request.invalid`, as are `scanned_bags`.
- **Collect** answers `already` for every tiffin of the stop that is `dispatched` with its attempt on this route, whether or not M8 still lists it as ready, and `not_ready` only for tiffins still `locked`, `picking` or `packed` (a released or refunded one is in no list). `logistics.nothing_ready` also carries `not_ready`. `bags` of the kitchen stop counts what is collected so far.
- **A rider-mode tiffin drop** becomes `handed_over` when none of its tiffins is still with the rider or uncollected; a drop whose kitchen never readied is skipped by the rider.
- **`close_routes`** completes a tiffin route without moving any order; its sweep then sends every tiffin of the day (or earlier) still `on_route` or `with_captain` to triage (`not_confirmed`). The job's summary gains `tiffins_to_triage`.
- **A `left_behind` tiffin** gets attempt 1 with its drop as `stop_id` and its short code, so the hub's board and the customer's card find it.
- **A tiffin ready at the kitchen but on no drop of the route** is recorded when it is scanned: its id goes into the route's `left_behind` (so the board lists it at once) and its short code into the `collect` event. Complete and `close_routes` give it attempt 1 in `triage` (`left_behind`, no `stop_id`) if it still has no attempt and is still `locked`, `picking` or `packed`; the hub refunds it like any other `left_behind` tiffin. A scan that finds only such a tiffin answers 200 with `not_on_route` (not `nothing_ready`) so that it is recorded.
- **A captain settles only a tiffin she holds.** M4 lets a `captain` move any `dispatched` tiffin, so M7 is the guard (`deliveries.door_actor`): for a tiffin the society's captain gets 404 `logistics.delivery_not_found` unless its attempt is `with_captain`, or `triage` after the 16:00 sweep **and** she had taken it: the sweep keeps `kept_by_captain` on a tiffin that was `with_captain` (the owner's decision), and sets nothing on one still `on_route`. A tiffin still with the rider (a rider-mode drop included), or one she never received, is refused.
- **A tiffin drop is counted again** for tiffins collected after the first count (a kitchen readied later): while the drop still has `on_route` attempts a captain may hand over again, and the stop's one `handovers` row adds up (`bags_expected`, `bags_counted`); a later count that finds nothing riding is 409 `logistics.already_handed_over`. A count when nothing rides yet (the drop has no `on_route` attempt and was not handed over) is 409 `logistics.nothing_to_hand_over`.
- **No rider position for captains and customers** while the rider's next open stop (`pending` or `arrived`, by `seq`) is a kitchen stop: the snapshot and the stream carry `position: null`. Hub managers and the rider's own stream are unaffected. A kitchen stop that has had a first scan counts as done, so a rider who waits at a half-collected kitchen is shown.
- **A kitchen stays visible to the rider** while any tiffin of his route at that kitchen is still uncollected, even after the first scan marked the stop `handed_over`.
- **A published tiffin route nobody started** is given up by `close_routes` at 16:00: its tiffins get attempt 1 in `triage` (`left_behind`, with their drop and short code), the route becomes `cancelled` (`logistics.route_cancelled`, reason `never_started`), so it cannot start later. Nothing is refunded by the job: the hub settles each tiffin from the board (`resolve`, refund in full). The summary gains `unstarted_routes` and `tiffins_left_behind`.
- **Kitchen visibility** follows who asks: the route's reads, field answers and the collect answer pass `kitchens` for hub managers and admins (address and point always; packers and franchise owners read the route but never the kitchen); the route's rider sees them from the start until every tiffin of his route at that stop is collected. `navigate_url` of a kitchen stop points at the kitchen when its point is shown.
- **The customer's stream of a tiffin** is opened by the household of a tiffin `on_route` on that route, on its drop; it ends with `handed_over` once the tiffin is no longer `on_route`.
- **E2E:** the journey's stand-ins are gone (pickup and delivery go over HTTP), but the timeline rows of the dispatch and the delivery still take the step's time (the database stamps them with its own clock), so in that step the after-commit relay waits for the sweep.

**Deferred**
- Dinner tiffins (question 6).
- Empties and donations on tiffin routes.
- A pickup-and-delivery solver in M10.
- Splitting one kitchen's tiffins between two riders.
- A hub manager collecting for a rider.
- Telling a kitchen when its rider comes.

**Questions for the owner.** Each default below is what §3.7 is written on. It holds until the owner says otherwise.
1. **Tiffins still out at 16:00.** M4 has no `system` move from `dispatched` to `undelivered` for a tiffin (ADR-0026 §2; ADR-0027 §5 added one for boxes only). So `close_routes` cannot refund a tiffin still with the rider or a captain. **Default:** it goes to triage (`not_confirmed`) and the hub manager settles it. *The alternative* is to refund automatically at 16:00, which needs a **new ADR** amending ADR-0026 §2 to give tiffins `system` on `dispatched → undelivered`. This contract does not write that ADR.
2. **A tiffin never collected** (the kitchen was late, or the stop was skipped). For a tiffin, M4 has no way from `locked`, `picking` or `packed` to `refunded`, except `locked → refunded` for `no_kitchen`. **Default:** the hub's triage refund makes the `hub` moves through `dispatched → undelivered`, then refunds. The cost is that the family's timeline shows "picked up" and then "failed", and they may get both pushes. *The alternative* is a tiffin move `picking` / `packed → refunded` (`system`), which would refund at complete without those moves; that needs a **new ADR**.
3. **Solve time.** The outline said T 10:30. **Default: T 09:35**, right after the 09:30 close of assignment changes. It gives riders 2 hours' notice and shares Neon's 09:30 wake.
4. **Capacity unit.** **Default:** `capacity_tiffins` counts **portions** (30 on a two-wheeler), because a 4-portion family order fills the bag more than a 1-portion order. The alternative is to count orders.
5. **Who delivers tiffins at a society with a captain.** **Default:** the same rule as for boxes: the captain takes them by count and code and delivers them; rider mode applies only where there is no active captain. This keeps §3.4 and the captain USP, and the rider moves on sooner while the food is hot. But the captain is unpaid for tiffins (ADR-0027 §7's default, unchanged here). *The alternative* is that the rider does every tiffin door (every tiffin drop in rider mode). That is a single rule in the solve, with no other contract change.
6. **Dinner.** **Default:** lunch only. A hub's dinner tiffins are not routed by M7 yet. When a hub opens a dinner slot, plans gain a `meal` (one published plan per hub, date, kind and meal), and `solve_tiffin_routes` gets a second run at the dinner slot's start − 2 h 55 min (5 minutes after its manual assignment closes).
7. **The kitchen's address.** **Default:** shown to the rider from route start until the stop is collected, which is our reading of M8's "the rider at pickup only". Before start he sees only the area.
8. **A hub manager collecting for a rider.** **Default:** no. Collect needs the rider at the kitchen with the scanner, as §3.6 pickups do. A route with no rider cannot start (`logistics.rider_required`).

### Facade for other modules (`gharbasket.modules.logistics.api`)
- `published_stop_order(session, hub_id, delivery_date) -> dict[(society_id, slot_id), StopRef]` — `{ route_id, trip, stop_seq, depart }` of the published plan, for M6's packing sequence at build time.
- `route_for_order(session, order_id) -> StopRef | None` — the route and stop an order rides on (M12 boards); for a tiffin, its drop stop through `logistics.stop_orders` (§3.7).
- `tentative_day(session, hub_id, day, *, expected=None) -> intel_api.SlotDay | None` (drafted 2026-10-28, for green slots, M10 §3.8). It gives a day's tentative routes: the published box plan's routes for `day`, else what this module's fallback would publish now (§3.2 `fallback_previous`, else `fallback_nearest`). The fallback runs over `expected` boxes per `(society, slot)` (M3's cycles, 8 kg and one bag a box), or over M4's orders. It includes the hub, the fleet, the times and the hub's box slots, and returns None without an active box vehicle. It is read-only and adds no routing code: it reuses `routing.order_previous`, `order_nearest` and `fill`.
- `delivery_status(session, order_id) -> DeliveryRef | None` — `{ status, attempt, reason, proof, at }` (M12 triage, M13 assistant).
- `export_for_user(session, user_id) -> dict` — the M1 data export section `logistics`: the household's deliveries (status, proof kind, times; no photos) and, for a rider, their routes and pings of the last 90 days. *Built:* also the handovers they made; the deliveries carry no photo, delivery code or note; pings are capped at 100,000 (newest) with `gps_pings_truncated`.
- `anonymise_for_user(session, user_id, household_id) -> dict[str, int]` — the M1 purge's eraser (M1 §3.5): deletes the rider's pings and the household's proof photos, and nulls user ids on handovers, deliveries and field events (the order history keeps the actor kind). *Built:* the photos are made due at once (`sweep_uploads` deletes them), the household's OTP records and notes are cleared, and user ids are also nulled on routes and vehicles; registered in `ERASERS` after M6 and in `SECTIONS` (`identity/jobs.py`).

### Privacy
- **Captains** see flat labels (tower and flat) only for their own society's orders of today and yesterday, and never a customer's name or phone. A captain's phone and flat are never returned to anyone (M9 §3.1); riders and customers see her display name and photo.
- **Riders** see flat labels only for rider-mode stops of their own route while it is in progress, and the captain's display name only. A rider's name and phone are never shown to customers or captains; hub staff see the rider's name here and contact details through M8.
- **Customers** see the van only for their own order, only until their society's handover, and never other societies or stops.
- **Location** is collected only while a route is in progress, kept 90 days (handbook 13 §13.5), visible live only to the route's own audience above; trails are for disputes and kilometres, never shown to customers. Coordinates are not written to logs.
- **Codes and photos.** Handover and delivery codes never appear in logs or events. Proof photos are private (`delivery_photo`, M0), read through 5-minute signed links by the household (its own door photo) and hub staff, and deleted 90 days after the delivery. *As built:* M7 claims each photo it keeps (`files.claim`, by `logistics`) and sets it to be deleted 90 days after the delivery date; a photo can be used as proof only once (409 `logistics.photo_already_used`), and a photo sent with a scan or a code is not kept.

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
| 409 | `logistics.route_not_published` · `logistics.routes_started` | Starting a route whose plan is not published, or one already in progress (as built) |
| 404 | `logistics.vehicle_not_found` | `PATCH /v1/routes/{id}` with an unknown vehicle or one of another hub (as built) |
| 409 | `logistics.route_not_started` · `logistics.route_closed` · `logistics.stops_open` | Route state (§3.3) |
| 409 | `logistics.no_active_route` | Location without an in-progress route |
| 409 | `logistics.count_mismatch` · `logistics.already_handed_over` | Handover (§3.4) |
| 409 | `logistics.delivery_closed` · `logistics.not_undelivered` | Door and triage (§3.4) |
| 409 | `logistics.photo_already_used` | A proof photo that a handover or a delivery already holds (as built) |
| 409 | `logistics.stop_skipped` · `logistics.pickup_already_reported` | Pickups at a skipped stop; a donation or bag reported again with the other status (`donation_ids`, `order_ids`) (§3.6) |
| 422 | `logistics.pickup_unknown` | A donation or bag that is not expected at this stop (`donation_ids`, `bags`) (§3.6) |
| 422 | `logistics.rider_invalid` · `logistics.vehicle_too_small` · `logistics.capacity_invalid` | Vehicles and route edits |
| 422 | `logistics.code_invalid` · `logistics.bag_mismatch` · `logistics.proof_required` · `logistics.photo_key_invalid` | Proof (§3.4) |
| 422 | `logistics.reason_invalid` · `logistics.refund_invalid` · `logistics.partial_refund_not_final` | Undelivered and triage (a refund below what is left needs `final: true`) |
| 422 | `logistics.date_out_of_range` | A date outside the allowed range |
| 409 | `logistics.rider_required` | Starting a tiffin route that has no rider (§3.7) |
| 409 | `logistics.nothing_ready` | Collect when nothing at the kitchen is ready and nothing was collected there yet (`not_ready`) (§3.7) |
| 409 | `logistics.no_redelivery` | `resolve` with `redeliver` on a tiffin (§3.7) |
| 422 | `logistics.not_a_kitchen_stop` · `logistics.pickup_code_invalid` · `logistics.pickup_wrong_kitchen` | Collect on a drop stop; a `GB1A:` code that is malformed, unknown or another hub's; a valid code of another kitchen than the stop's (§3.7) |
| 429 | `logistics.location_rate_limited` · `logistics.code_attempts_exceeded` · `logistics.too_many_streams` | Limits |
| 409 | `orders.illegal_transition` | The order is not in the status the step needs (from M4) |
| 403 | `auth.forbidden` | A role without the permission, or outside its scope |
| 400 / 422 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.invalid` | As in M0 |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `logistics.vehicles` | id (uuidv7), hub_id, label (unique per hub, case-insensitive), kind (cargo_3w, two_wheeler), registration null, capacity_kg, capacity_crates, capacity_tiffins, default_rider_user_id null, active, version, created_at |
| `logistics.route_plans` | id, hub_id, delivery_date, kind (box, tiffin), status, trigger (job, manual), source null (§3.7 adds `nearest`), distance_source null, model null, summary jsonb, seconds, error null, prediction_id null (`intel.predictions`), requested_by null, published_by null, published_at, created_at; one `published` plan per (hub_id, delivery_date, kind) (partial unique) |
| `logistics.routes` | id, plan_id → route_plans, hub_id, delivery_date, kind, trip, vehicle_id → vehicles, rider_user_id null, status (draft, published, in_progress, completed, cancelled), depart_time, return_by, planned_km, started_at, completed_at, empties_returned, version; RLS by hub (handbook 13 §13.4) |
| `logistics.route_stops` | id, route_id → routes, seq (unique per route), kind (drop, bank, kitchen), society_id null, slot_id null, assignment_id null and partner_id null (kitchen stops, §3.7), portions (tiffin stops, §3.7), window_start, window_end, eta, late_s, delay_s, status (pending, arrived, handed_over, skipped), rider_mode, handover_code, code_attempts, crates, bags, empties_expected, arrived_at, skip_reason null |
| `logistics.handovers` | id, stop_id (unique) → route_stops, society_id, by_actor (captain, rider, hub), user_id null, partner_id null, crates_expected, crates_counted, bags_expected, bags_counted, mismatch, proof (code, photo, rider_mode), photo_key null, distance_m null, note, client_at, at |
| `logistics.deliveries` | id, order_id, attempt (1, 2), hub_id, society_id, order_kind, delivery_date (the attempt's day), slot_id, stop_id null, status, reason null, hold_until null, proof (scan, otp, photo) null, photo_key null, kept_by_captain (the bag is in the captain's hands overnight), hub_ride (attempt 1 booked again after the hub left the bag behind), short_code null (tiffins, §3.7), delivery_code, code_attempts, by_actor null, user_id null, refund_paise null, client_at, at; unique (order_id, attempt) |
| `logistics.field_events` | id, route_id, stop_id null, order_id null, action (start, arrive, skip, handover, proof, undelivered, hold, resolve, complete, pickup, rider_mode, collect), user_id, payload jsonb, client_at null, at |
| `logistics.pickups` | (§3.6) id, stop_id → route_stops, route_id → routes, hub_id, delivery_date, society_id, kind (empties, donation, bag), ref_id null (the donation or the order; null for empties), household_id null (donation and bag rows, for the flat label; nulled by the erasure), items null (M9's summary of a donation), qty (expected: the open empties, or 1), collected_qty null (empties: the count reported; 1 or 0 otherwise), status (expected, collected, not_ready), by_user_id null, client_at null, created_at, reported_at null; unique (stop_id, kind, ref_id) with nulls not distinct; index (hub_id, delivery_date) and (society_id, kind, status) |
| `logistics.stop_orders` | (§3.7) id, stop_id → route_stops, order_id, short_code, portions; unique (stop_id, order_id); index on order_id. A tiffin is on two rows of one route: its kitchen stop and its drop |
| `logistics.gps_pings` | rider_user_id, route_id, at, location geography(Point, 4326), accuracy_m, speed_mps null, heading null; partitioned by month on `at`, BRIN on `at`, index on (route_id, at DESC) for a route's newest ping, unique (rider_user_id, at) |

Foreign keys stay inside `logistics`; hub, order, society, slot and user ids are plain UUIDs (ADR-0026 §5).

## 5. Events
**Published** (no names, phones, flats, codes or coordinates in any payload):
- `logistics.route_published { route_id, plan_id, hub_id, delivery_date, kind, trip, vehicle_id, rider_user_id, previous_rider_user_id, depart, stops: [ { seq, kind, society_id, slot_id, eta, bags, captain_user_id, partner_id } ] }` (`partner_id`: the kitchen on a tiffin route's kitchen stops, else null; §3.7) — M6 packing sequence, M11 (the rider: "Tomorrow: EV-1, 6 stops, leave 06:30"; each captain: "14 bags tomorrow, van about 07:20").
- `logistics.route_cancelled { route_id, plan_id, hub_id, delivery_date, rider_user_id, reason: "superseded" }` — M6 clears route references, M11 tells the rider.
- `logistics.plan_needs_review { plan_id, hub_id, delivery_date, kind, problems }` — M11 tells the hub's managers.
- `logistics.stop_arrived { stop_id, route_id, hub_id, society_id, captain_user_id, at }` — M11 tells the captain.
- `logistics.handover_completed { handover_id, stop_id, route_id, hub_id, society_id, by_actor, user_id, partner_id, crates, bags, order_ids, mismatch }` — M12. (Not for pay: a captain is paid ₹6 a bag on `orders.delivered` with actor `captain`, M8 §3.6, ADR-0027.)
- `logistics.delivery_held { order_id, household_id, society_id, hold_until }` — M11 tells the family to collect.
- `logistics.redelivery_scheduled { order_id, household_id, society_id, delivery_date, window }` — M11.
- `logistics.route_completed { route_id, hub_id, delivery_date, rider_user_id, vehicle_id, stops_done, stops_skipped, km, started_at, completed_at }` — M12. (A rider is paid per order on `orders.dispatched` with actor `rider`, M8 §3.6.)
- `logistics.pickup_completed { stop_id, route_id, hub_id, society_id, delivery_date, rider_user_id, empties, donations: [ { donation_id, status } ], bag_order_ids, at }` (§3.6) — M9 (donation outcomes), M6 (returned bags).
- `logistics.carry_over_pending { order_id, household_id, hub_id, society_id, lines: [ { product_id, steps, paid_paise } ] }` — the non-perishable lines of a box missed twice, kept unrefunded for the household's next delivery (§3.4). Nothing consumes it until M4's `carry_over` and M6's kept items exist; it is the record the next packing can read. The carry-over of the non-perishables (this event's consumer) will be built next as an M4 follow-up, contract first, before MS-B.
- Order events (`orders.dispatched`, `orders.at_society`, `orders.delivered`, `orders.delivery_failed` with `reason`, `billing.refund_issued`) are published by M4 and M5 when M7 calls their facades.

**Consumed:** none. M7 reads a society's donations through `community.api.pickups_for` when a route starts (§3.6), so `community.donation_scheduled` and `community.donation_cancelled` are not consumed: a donation cancelled after the start is skipped by M9 when it is reported.

**For M11's event map** (added when M7 lands; categories and push rules are M11's): `logistics.route_published` (rider, captain), `logistics.route_cancelled` (rider), `logistics.plan_needs_review` (hub managers), `logistics.stop_arrived` (captain), `logistics.delivery_held` (customer, category `delivery`, link `/orders`), `logistics.redelivery_scheduled` (customer, `delivery`, `/orders`). Not mapped: `handover_completed`, `route_completed`, `pickup_completed`.

## 6. Jobs
| Job | Trigger (IST) | Notes |
|---|---|---|
| `solve_routes` | T-1 23:15 (cron-job.org → `POST /v1/internal/jobs/solve_routes`) | Every active hub: solves a `box` plan for T (each hub in its own savepoint), and any manual plan still queued; auto-publish rule in §3.2. Re-solves on demand through `POST /v1/hubs/{id}/route-plans` (the old T 07:00 solve) |
| `solve_tiffin_routes` | T 09:35 (cron-job.org) | Every active hub: solves a `tiffin` plan for today from M8's assignments (§3.7); auto-publish rule as §3.2 |
| `persist_gps_pings` | Every minute in the morning window, plus 12:30 and 14:30 for the lunch run (cron-job.org) | Valkey stream → `logistics.gps_pings`; idempotent on (rider, at) |
| `hold_expiry` | T 20:30 (cron-job.org) | Still `at_society` → `undelivered` (actor `system`): `not_home` for held bags, `not_confirmed` for the rest (§3.4) |
| `schedule_redeliveries` | T 20:45 (cron-job.org) | First miss → `redelivery` for T+1; second miss → perishables `refunded` minus the fee, non-perishables carried to the next delivery (§3.4) |
| `close_routes` | T 16:00 (cron-job.org) | Completes routes still `in_progress` (actor `system` on the orders, reason `returned_to_hub`) and stops their location; tiffins still out go to triage instead (§3.7) |
| `purge_gps_pings` | Daily 03:43 (GitHub Actions) | Deletes pings older than 90 days; drops a month's partition once it is empty |

Each job is idempotent, takes its advisory lock and uses `scheduled_for` (the delivery day follows from it), not the run time (ADR-0021, ADR-0025).

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `vehicles:read` · `vehicles:write` | hub_manager, franchise_owner (hub), admin · hub_manager (hub), admin |
| `routes:read` | hub_manager, franchise_owner, packer (hub), admin; rider (own routes) |
| `route_plans:read` | hub_manager, franchise_owner (hub), admin: route plans (packers see routes, not plans) |
| `routes:write` | hub_manager (hub): solve, publish, edit routes and stops, start or complete for a rider |
| `routes:execute` | rider (own route): start, arrive, skip, complete, location, pickups, collect (§3.7) |
| `routes:live` | rider (own), hub_manager (hub), captain (her society's stop), customer (own order, until handover) |
| `handover:write` | captain (society, active in M9), rider (rider-mode stop of own route), hub_manager (hub) |
| `deliveries:read` | customer (own household), captain (her society), rider (rider-mode stop), hub staff (hub) |
| `deliveries:write` | captain (society), rider (rider-mode stops and tiffins on own route), hub_manager (hub) |
| `deliveries:resolve` | hub_manager (hub), admin |
| `pickups:read` | hub_manager (hub), admin: the hub's pickups list (§3.6); riders read their stops' pickups on the route |

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
- Door and exceptions: bag QR must match; rider needs otp or photo; hold keeps `at_society` until `hold_expiry`; `hold_expiry` reasons; first miss → one redelivery on T+1's plan, and a bag the captain kept overnight is counted neither in `redelivery_bags` nor at the handover yet moves to `at_society` with it; second miss → refund of perishables minus ₹20, never negative, and the non-perishable lines passed to `orders.api.carry_over`; damaged/missing refunded in full; tiffin failure refunded at once; a tiffin is picked up by its assignment's `GB1A:` QR and delivered with its `short_code` and `otp` or `photo`, by a captain too; triage refund bounded by what is left of the paid total.
- Tiffin routes (§3.7):
  - the solve: kitchens from `tiffin_pickups` ordered by `ready_by` then nearest neighbour, trips filled to `capacity_tiffins` in portions without splitting a kitchen, drops after their kitchens, a society on two trips gets two drops; an over-capacity kitchen, no two-wheeler, and an empty day; the check catches a tiffin on no drop or before its kitchen; auto-publish and `plan_needs_review`; `plan_incomplete` by order;
  - start moves nothing and needs a rider; collect by the rider only, on a kitchen stop of a route in progress; a wrong, foreign or other kitchen's code; `nothing_ready`; a partial Ready then a second scan; `already` on a repeat scan before M8's row moves; `not_on_route`; `orders.dispatched` with actor `rider`;
  - a captain's tiffin handover moves no order and makes the attempts `with_captain`; the count mismatch lists short codes; `short_code` and `otp` or `photo` by the captain and by the rider; `scan` refused; every undelivered tiffin refunded in full at once, no hold, no redelivery; complete refunds what the rider still holds and sends uncollected tiffins to triage; `close_routes` sends tiffins still out to triage and moves none; the hub's triage refund of a never-collected tiffin;
  - the customer's card and stream for a tiffin; the captain's day with tiffin rows and short codes; the kitchen's address hidden before start and after collect.
- Pickups (§3.6): route start makes the expected empties (handover crates minus collected, rider-mode handovers left out), donations (`pickups_for`) and bags (attempt 2 refunded in the captain's hands), each on one stop only; a report by the route's rider only (another rider 403, a hub manager 403, another hub 404), not before start, after completion or at a skipped stop; partial reports, empties replaced, a changed status 409, an unexpected donation or bag 422; the idempotent replay; `pickup_completed` and its consumers (M9 `picked_up`, `not_ready` rolled then cancelled; M6 kept items and returned lots); rows still expected become `not_ready` at complete; the hub list's scope (hub manager of the hub, admin; another hub 403); the solve sends the open empties.
- Privacy: captain views never contain names or phones and only her society; no event payload carries a code, phone, flat or coordinate; data export and erasure.
- Solver integration (with M10): golden routes from fixed seeds are feasible; the 26 s gateway timeout leads to the fallback, never a missing plan.
- E2E (MS-B): seeded hub → cut-off → 23:15 plan published → packing in route order → start → handover → every flat delivered, plus one held bag that becomes a redelivery and is delivered the next morning; on the simulator seed. The tiffin journey (`e2e/backend/test_journey_tiffin_partner.py`) replaces its stand-ins (`partners.api.pickup` and the hub's `orders.api` moves) with `solve_tiffin_routes`, start, collect and the door proof (§3.7).
- Device: background location on a low-end Android (Capacitor background geolocation plugin), pings stop at complete; offline handover and door scans replay once.

## 10. Dependencies
- **M4** (`orders.api`), changes from ADR-0026 made in the M6 packing PR: the field actors and `actor_user_id` on `transition`, the per-kind state machine, `kinds` on `orders_for_delivery`, `system` on `at_society → undelivered`, `kind` in fulfilment events. **Further M4 changes this spec needs** (in the M7 PR, reviewed by both owners; recorded in [ADR-0027](../adr/0027-operations-contract-amendments.md), which extends ADR-0026 §2's table): `system` on `undelivered → redelivery` (the automatic free redelivery); `dispatched → undelivered` for box and add-on orders by `rider`, `system` and `hub` (bags that ride back to the hub: a skipped stop, a count mismatch, `close_routes`); `rider` on `dispatched → at_society` and `at_society → delivered / undelivered` also where a hub manager switched the stop to rider mode for an absent captain (M7 checks it); `transition(…, "refunded")` takes `payload.refund_paise` (default: what is left of the paid total) and `payload.reason`; `orders.delivery_failed` carries `reason`; `orders_for_delivery` accepts `statuses=("locked", "packed")`; **`orders.api.carry_over(session, order_id, lines)`** (the owner's decision of 2026-10-09 on a box missed twice): M4 keeps the given non-perishable lines (product, steps) for the order's household and adds them to its next box or add-on order at no charge, with `carried_from_order_id` on the line: the household's next box or add-on order whose delivery date is after the missed attempt and that is not yet packed. Carried lines wait at most 14 days, then M4 refunds them at their paid price without the fee (ADR-0027 default, §3.4).
- **M9** captains PR (before M7): `community.captains` with one active captain per society, `community.api.captain_for_society`, the `captain_assigned`/`captain_ended` events. **Also needed from M9:** `community.api.society_points(society_ids) -> {id: (lat, lng)}` (a nullable gate point on societies, else a point inside the boundary) and tower labels (already needed by M6).
- **M6** (`hub_ops.api.society_loads`, `bag_for_order`; consumes `logistics.route_published` and `route_cancelled`; **follow-up:** a bag back after a second miss sends its perishables to the surplus cascade and keeps its non-perishables aside for the household, and packing adds a carried-over line from those kept items), **M1** (`identity.api.delivery_labels`, rider grants for a hub, the data export section `logistics`, the erase hook), **M0** (a new upload purpose `delivery_photo`: JPEG/WebP, 5 MB, 90 days after the delivery), **M2** (`catalog.api.grams_of` and shelf life per product), **M10** (`intel_gateway.api.solve_routes`, PR on `feat/M10-route-solve`), **M5** (refunds through M4), **M11** (the event map above), **M8** (tiffin assignments and the `GB1A:` pickup in §3.7; partner pay comes from M4's fulfilment events with `actor`, not from M7's events, M8 §3.6).
- **Missing, found building M7:** (1) ~~a `billing.api` facade that refunds an order's money to its **original payment source** (M5's refund-to-source works from the wallet balance and has no per-order entry point), which triage needs when the wallet is at its cap~~. Done by M5 §3.3 "Order refunds when the wallet is full" (2026-10-21): `refund_to_wallet` sends the refund to the payment source itself, so triage needs no change; (2) M4's `carry_over` and its 14-day expiry job, and M6's kept items, for the non-perishables of a box missed twice; (3) ~~§3.6 pickups (`bag` kind), which would bring a bag the captain holds back to the hub after a second miss~~. Specified in §3.6 (2026-10-25): the bag is a pickup at the society's next stop, and M6's consumer of `logistics.pickup_completed` unpacks it.
- **Docs to update with this spec:** handbook 07 §7.1 and W5 (solve at T-1 23:15 per hub, not T 07:00 per slot; crates by count and handover code, not crate QR; hold until 20:00, the held bag overnight with the captain, one redelivery, then the refund rule with the non-perishables carried to the next delivery; the fallback is the previous day's order or nearest neighbour), handbook 10 §10.4 (`solve_routes` T-1 23:15; the new M7 jobs) and §10.3 (the M7 events), handbook 05 §5.5 ERD for `logistics`, handbook 06 §6.2 (hold and redelivery triggers), M10 §3.4 (stop ids and the 23:15 trigger), M11 §3.1 event map (the six events above and a category for partner messages). **For §3.7:** handbook 07 (the 09:35 tiffin solve in the daily table, W6's scan and proof going to M7), handbook 10 §10.4 (`solve_tiffin_routes`), handbook 05 §5.5 (`stop_orders`), M8 §3.4 (`PickupRef` additions).

## 11. Open questions and decisions
- **Decided (ADR-0026, owner 2026-10-09):** captain from M9 (one per society), rider as the last-50-m fallback, crates counted not tracked, bag QR `GB1:<order_id>`, solve at T-1 23:15, hold until 20:00 with `hold_expiry` at 20:30, one free redelivery the next morning then the perishables refunded minus a fee, electric cargo three-wheeler (400 kg, 24 crates) for boxes and two-wheelers for tiffins.
- **Decided (this spec):** the fee is ₹20; perishable means catalog shelf life ≤ 96 h; ML down → previous day's order, else nearest neighbour; no live traffic in v1 ETAs.
- **Decided by the owner (2026-10-09):**
  - ~~Where does a held bag spend the night before its redelivery?~~ Decided: it stays overnight with the captain, and her society gets a stop on the next morning's route even if the van brings nothing else for it; ADR-0026's `redelivery → dispatched` path stays (§3.4).
  - ~~A box missed twice: should non-perishables (atta, oil) be refunded too, or ride on the family's next delivery?~~ Decided: the perishables are refunded minus the ₹20 fee as drafted; the non-perishables ride on the household's next delivery, neither surplus nor refunded. The bag stays at the hub, and its non-perishable lines are carried to the next order through a new `orders.api.carry_over` (M4) and packed from the kept items (M6) (§3.4, §10). This narrows ADR-0026's decided item 1 ("the box goes to the surplus cascade") to the perishables; [ADR-0027](../adr/0027-operations-contract-amendments.md) records the amendment.
- Pilot data (Rhythm Thakur, Manish Raje): real vehicles and their speed against OSRM (GPS traces), captains' real windows, orders per crate.
- **Defaults set by [ADR-0027](../adr/0027-operations-contract-amendments.md) (2026-10-09), which the owner may change:** carried-over non-perishables wait up to 14 days for the household's next delivery, then are refunded at their paid price without the fee (§3.4, §10); a captain earns nothing for tiffins she delivers (§3.7).

**Decisions for the owner (§3.6, 2026-10-25).** The outline left these open; each is the simplest choice consistent with ADR-0026 and ADR-0027, and §3.6 is built on it until the owner says otherwise.
1. **Expected pickups are a snapshot at route start**, like the crate counts, not made at publish and not kept up to date from `community.donation_scheduled` or `donation_cancelled` (M7 consumes neither; M9 §5's notes on them change to match). A pickup date closes at 22:00 the day before (M9 §3.4), so the snapshot misses no donation; one cancelled after the start is skipped by M9 when it is reported.
2. **Everything rides back to the hub.** No `bank` stop and no `donation_kg_tenths` in the solve: M9 §3.4 has the hub count and verify donations before a hand-off (M9 §3.5), and M10 refuses donation weight without a food bank. Donation weight is not in the van's kg check.
3. **A rider-mode handover leaves no empties** at the society: the rider does the doors and keeps his crates. Only captain and hub-manager handovers add to a society's open empties.
4. **Reports are partial and repeatable**, because the app taps empties and each donation separately through its outbox: `empties` replaces the stop's count until the route completes; a donation or bag is set once.
5. **Only the route's rider reports**, as the outline said; a hub manager cannot report for him yet.
6. **M6 unpacks a returned bag when the event arrives** (kept items and returned lots), not only when the hub scans it; the scan then answers the same result.
7. **No pickup-only stops.** A society without a delivery that day gets no stop for its pickups.
8. **A rider at a captain's stop sees no flat labels**, also for donations (§Privacy): he tells donations apart by their item summary. M9's `pickups_for` gives no label or code for a donation bag.
9. **Unreported pickups become `not_ready` at route complete**, without an event; M9's 22:30 sweep already handles unreported donations, and a bag or empties not collected are expected again at the society's next stop.
