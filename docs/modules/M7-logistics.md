# M7 — Logistics

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P3 / S5
**Postgres schema:** `logistics` · **Python package:** `gharbasket.modules.logistics`

## 1. Purpose
Getting crates from the hub to each society and each flat: vehicles, routes and stops (solved by M10's OR-Tools CVRPTW with pickups), rider execution, live location, **Society Captain handover**, last-50-metre delivery with proof, failed-delivery handling, and reverse pickups (crates and Kindness donations) on the return leg.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a hub manager, I want routes generated per slot so that vans leave on time. | 07:00 routes published with stops, ETAs, crates; solver stats stored. |
| US-2 | As a rider, I want my route on a map with stop details so that I can deliver without calling anyone. | Route view works offline after start; navigation link per stop. |
| US-3 | As a Society Captain, I want to receive crates by scanning so that handover is accountable. | Crate scan + bag count + OTP or photo → `orders.at_society`. |
| US-4 | As a Society Captain, I want a flat checklist and to scan each bag at the door so that deliveries are confirmed. | Scan → `DELIVERED`; customer notified. |
| US-5 | As a customer, I want to track the van live so that I know when to expect delivery. | SSE location updates every 15 s while en route. |
| US-6 | As a captain, I want to mark a flat unavailable so that the order is held or redelivered. | `UNDELIVERED` → redelivery next slot or refund. |
| US-7 | As a rider, I want donation and crate pickups on my return leg so that no extra trip is needed. | Pickup stops included by the solver; completion publishes `logistics.pickup_completed`. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/routes?hub_id=&date=&slot_id=` | hub_manager, rider (own) | — | |
| POST | `/v1/hubs/{id}/routes/solve` | hub_manager | Required | Triggers M10 solve; async |
| POST | `/v1/routes/{id}/publish` | hub_manager | Required | |
| POST | `/v1/routes/{id}/start` · `/complete` | rider (own) | Required | |
| GET | `/v1/routes/{id}/live` | customer (own order), hub staff | — | SSE |
| POST | `/v1/riders/me/location` | rider | — | Batched pings |
| POST | `/v1/stops/{id}/handover` | rider, captain (society) | Required | Crates, bag count, OTP/photo |
| POST | `/v1/deliveries/{order_id}/proof` | captain, rider | Required | Scan/OTP/photo key |
| POST | `/v1/deliveries/{order_id}/undelivered` | captain, rider | Required | Reason |
| GET / POST | `/v1/pickups` | hub_manager, rider | Required (POST) | Crates/donations |
| GET / POST | `/v1/hubs/{id}/vehicles` | hub_manager | Required (POST) | |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `logistics.vehicles` | id, hub_id, kind, capacity_kg, capacity_crates |
| `logistics.routes` | id, hub_id, date, slot_id, vehicle_id, rider_id, status, solver_stats jsonb |
| `logistics.route_stops` | route_id, seq, society_id, eta, kind (drop, pickup), payload jsonb |
| `logistics.handovers` | id, route_stop_id, captain_id, bags_count, otp_ok, photo_key, at |
| `logistics.deliveries` | order_id, status, proof_kind (otp, photo, captain_scan), proof_key, at |
| `logistics.reverse_pickups` | id, kind (crate, donation), ref_id, route_stop_id, status |
| `logistics.gps_pings` | rider_id, at, location — partitioned monthly |

## 5. Events
**Published:** `logistics.route_published`, `orders.dispatched` / `orders.at_society` / `orders.delivered` / `orders.delivery_failed` (applied via `orders.api`), `logistics.pickup_completed`.
**Consumed:** `orders.packed`, `community.donation_scheduled`.

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `solve_routes` trigger | T 07:00 per slot | Calls M10 `/route/solve`; fallback: previous day's sequence |
| `persist_gps_pings` | Every 60 s | From Valkey to partitioned table |
| `hold_expiry` | Evening | Held orders → redelivery/refund |

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `routes:write` | hub_manager (hub) |
| `routes:execute` | rider (own route) |
| `handover:write` | captain (society), rider |
| `deliveries:write` | captain (society), rider |

## 8. Screens
**Mobile:** Rider — Route map, Stop detail + proof, Pickups, Earnings. Captain — Today, Handover scan, Flat checklist, Exceptions, Earnings. Customer — live tracking (in M4 order detail). **Console:** Routes live map (deck.gl), Deliveries, Vehicles.

## 9. Tests required
- Integration: handover and proof idempotency under offline replay; SSE fan-out.
- Solver integration: golden-route fixtures from M10 (fixed seeds) produce feasible routes.
- E2E: simulated route from dispatch to all flats delivered; failed-delivery path.
- Device: background location on a low-end Android (Capawesome plugin).

## 10. Dependencies
M4 (`orders.api`), M10 (`intel_gateway.api` for solving), M9 (societies, captains, donations), M6 (crates/packing order), M11 (notifications).

## 11. Open questions
- Captain hold window and redelivery policy timings.
- Vehicle type for the pilot (e-rickshaw vs two-wheeler with crates).
