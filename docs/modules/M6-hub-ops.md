# M6 — Hub Operations

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P3 / S4
**Postgres schema:** `hub_ops` · **Python package:** `gharbasket.modules.hub_ops`

## 1. Purpose
Everything inside the hub (franchise fulfilment centre): hubs and service areas, vendors and vendor scores, procurement plans (committed demand + forecast buffer), purchase orders, goods receipt and QC grading, inventory lots with expiry, packing tasks with bag QR codes, shortage handling, waste logging and the surplus cascade. Forecasting and newsvendor quantities come from M10; M6 turns them into operational plans.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a hub manager, I want a procurement plan right after cut-off so that I buy only what's needed. | At T-1 21:15 a plan lists committed qty, forecast q50/qτ, order qty and suggested vendor per item. |
| US-2 | As a hub manager, I want to adjust and send purchase orders so that vendors know what to deliver. | POs generated per vendor (PDF/WhatsApp text); edits audit-logged. |
| US-3 | As a hub manager, I want to record goods received with weight and grade so that inventory and vendor scores are accurate. | GRN creates lots with expiry; feeds weekly vendor scores. |
| US-4 | As a packer, I want a packing queue sorted by route stop so that crates are loaded in order. | 05:00 tasks generated; sorted by planned route sequence. |
| US-5 | As a packer, I want to scan a bag and pick lines with shortages flagged so that customers are treated fairly. | Scan-driven pick; shortage triggers substitution/partial refund through `orders.api`. |
| US-6 | As a hub manager, I want surplus routed through the cascade so that waste is minimised. | 12:30 job lists Imperfect Sabzi items, then kitchen credits, NGO, compost; each logged. |
| US-7 | As an analyst, I want waste per 100 kg sold so that the zero-waste claim is measurable. | Metric computed daily per hub. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/hubs/{id}/procurement-plans?date=` | hub_manager (hub) | — | |
| PATCH | `/v1/procurement-plans/{id}/lines/{product_id}` | hub_manager | — | Adjust order qty |
| POST | `/v1/procurement-plans/{id}/purchase-orders` | hub_manager | Required | Generate POs |
| POST | `/v1/hubs/{id}/grn` | hub_manager, packer (hub) | Required | Goods receipt + grade |
| GET | `/v1/hubs/{id}/packing-tasks?date=&status=` | packer, hub_manager | — | |
| POST | `/v1/packing-tasks/{id}/start` · `/pick` · `/complete` | packer (hub) | Required | Offline-safe (outbox on device) |
| POST | `/v1/packing-tasks/{id}/shortage` | packer | Required | |
| GET / POST | `/v1/hubs/{id}/waste-logs` | hub_manager, packer | Required (POST) | |
| GET / POST / PATCH | `/v1/hubs/{id}/vendors` | hub_manager | Required (POST) | |
| GET / POST | `/v1/admin/hubs` | admin | Required (POST) | Service area polygon |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `hub_ops.hubs` | id, name, location geography, service_area geography(Polygon), franchise_owner_id |
| `hub_ops.vendors` | id, hub_id, name, kind (apmc, fpo, farmer), phone |
| `hub_ops.vendor_scores` | vendor_id, week, freshness, price, reliability, defects, score |
| `hub_ops.procurement_plans` / `hub_ops.procurement_lines` | id, hub_id, for_date, status / plan_id, product_id, committed_qty, forecast_q50, forecast_qtau, order_qty, vendor_id, unit_cost_paise |
| `hub_ops.purchase_orders` | id, plan_id, vendor_id, lines jsonb, status, sent_at |
| `hub_ops.inventory_lots` | id, hub_id, product_id, qty, received_at, expires_at, cost_paise, grade |
| `hub_ops.packing_tasks` | id, order_id, packer_id, status, bag_qr, route_seq, started_at, done_at |
| `hub_ops.waste_logs` | id, hub_id, product_id, qty, reason (spoilage, damage, surplus), cascade_to (imperfect, kitchen, ngo, bin), at |

## 5. Events
**Published:** `hub_ops.procurement_plan_ready`, `hub_ops.shortage_detected`, `hub_ops.waste_logged`.
**Consumed:** `orders.locked`, `intel.forecast_ready`, `logistics.route_published` (packing order).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `build_procurement_plan` | T-1 21:10 (after forecast) | committed + Q_τ(uncommitted) |
| `build_packing_tasks` | T 05:00 | Sorted by route sequence |
| `surplus_cascade` | T 12:30 | Imperfect listing → kitchen → NGO → compost |
| `compute_vendor_scores` | Weekly | Freshness 40 %, price 20 %, reliability 20 %, defects 20 % |

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `procurement:write` | hub_manager (hub) |
| `packing:write` | packer (hub), hub_manager |
| `waste:write` | packer, hub_manager |
| `hubs:write` | admin |

## 8. Screens
**Mobile (packer):** Queue, Pick/Pack scanner, Shortages. **Console:** Ops board (today), Procurement, Receiving/QC, Packing kanban, Waste and cascade, Vendors, Hubs admin.

## 9. Tests required
- Unit: procurement quantity calculation (committed + quantile, shelf-life cap).
- Integration: shortage → `orders.api` substitution/refund flow; packing offline replay with idempotency keys.
- E2E: simulated hub morning (receive → pack → crates) on simulator seed.

## 10. Dependencies
M4 (orders via `orders.api` and events), M10 (forecasts via `intel_gateway.api`), M7 (route sequence), M8 (kitchen surplus credits), M9 (NGO hand-off).

## 11. Open questions
- Real vendor list and APMC Vashi timings for the pilot.
- Crate tracking granularity (per crate QR vs count only).
