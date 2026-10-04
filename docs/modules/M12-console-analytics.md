# M12 — Console & Analytics

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** Shell in P1 / S0; screens grow with each phase (P2–P5)
**Deployables:** `frontend/console` (Next.js 16) + analytics endpoints in the API (`gharbasket.modules.analytics`, read-only)
**Postgres schema:** `analytics` (materialised views only)

## 1. Purpose
The web console for admins, hub managers, franchise owners and NGO coordinators, plus the public website and society referral pages. The analytics module provides read-only KPIs, unit economics, cohort/retention, impact and experiment results, computed from materialised views over other modules' data (read through their `api.py` or read-only views — never writes).

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a hub manager, I want a today board (orders, procurement, packing, routes) so that I run the morning in one place. | Live counts per stage; links to each workflow. |
| US-2 | As a franchise owner, I want P&L and unit economics so that I know if my hub is profitable. | Contribution per order = AOV × margin − (delivery + packing + captain commission + payment fee + CAC); assumptions editable. |
| US-3 | As an admin, I want KPIs (active households, acceptance rate, waste per 100 kg, on-time %, partner earnings) so that I monitor the platform. | KPI tiles with trend; filter by hub and date. |
| US-4 | As the team, I want cohort retention so that we can report in the paper. | Weekly cohorts by signup week and data_source. |
| US-5 | As an NGO coordinator, I want impact numbers so that we can report outcomes. | Donations, hand-offs, beneficiaries. |
| US-6 | As a visitor, I want a society landing page so that I can join with my neighbours. | `/s/<society>` server-rendered with unlock progress and join CTA. |
| US-7 | As an admin, I want a command palette so that I navigate quickly. | ⌘K finds pages, orders, households, partners. |

## 3. API endpoints
| Method | Path | Permission / role | Notes |
|---|---|---|---|
| GET | `/v1/analytics/kpis?hub_id=&from=&to=` | hub_manager (hub), admin | |
| GET | `/v1/analytics/unit-economics?hub_id=&period=` | franchise_owner (hub), admin | Accepts assumption overrides as query params |
| GET | `/v1/analytics/cohorts?data_source=` | admin | |
| GET | `/v1/analytics/impact?scope=` | ngo_coordinator, admin | |
| GET | `/v1/analytics/experiments/{key}` | admin | |
| GET | `/v1/public/societies/{slug}` | Public | For the SEO landing page |

## 4. Tables owned
Materialised views in `analytics`: `mv_daily_hub_kpis`, `mv_unit_economics`, `mv_cohorts`, `mv_impact`, `mv_partner_earnings_distribution`.

## 5. Events
**Published:** none. **Consumed:** none directly (scheduled refresh).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `refresh_materialised_views` | Every 15 min (KPIs), nightly (cohorts) | `REFRESH MATERIALIZED VIEW CONCURRENTLY` |

## 7. Permissions
`analytics:read` scoped by hub for hub_manager/franchise_owner; global for admin; impact for ngo_coordinator.

## 8. Screens
Console route groups: `(auth)`, `(public)`, `(admin)`, `(hub)`, `(franchise)`, `(analytics)` — full list in `docs/FRONTEND_DESIGN.md` Section 10. Charts: ECharts; maps: MapLibre + deck.gl; tables: TanStack Table.

## 9. Tests required
- Integration: KPI numbers match fixture expectations on simulator seed.
- E2E (Playwright): login, role-based redirect via `proxy.js`, today board, unit-economics calculator.
- Accessibility: axe on all console pages.

## 10. Dependencies
Read-only views over all module schemas (granted by a read-only DB role); M1 for auth; generated API client.

## 11. Open questions
- Whether franchise owners can see household-level data or only aggregates (proposed: aggregates only).
