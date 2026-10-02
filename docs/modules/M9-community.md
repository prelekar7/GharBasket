# M9 — Community & Kindness

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje (also NGO tie-ups and drive content)
**Phase / Sprint:** P5 / S9 (societies table needed from P2 / S1)
**Postgres schema:** `community` · **Python package:** `gharbasket.modules.community`

## 1. Purpose
The neighbourhood layer: societies (polygons, towers, captain), society onboarding and the **unlock** mechanic (free delivery when N households join), referrals, Kindness donation drives and donations, NGO hand-offs, and the impact counters shown to customers and in the console.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a hub manager, I want to draw a society polygon and set towers so that addresses map to the right society. | Polygon stored; addresses validated against it. |
| US-2 | As a customer, I want to see how many neighbours are needed to unlock free delivery so that I invite them. | Unlock bar with N and current count; threshold from route-cost model. |
| US-3 | As a customer, I want to share a referral link for my society so that neighbours join easily. | Link to `/s/<society>` landing page; reward on referee's first delivered box. |
| US-4 | As a customer, I want to schedule a donation pickup so that I can give away clothes, books or toys. | Donation `SCHEDULED`; attached to a return-leg pickup by M7. |
| US-5 | As a hub manager, I want to verify donations so that points are fair. | Verification awards points (drive multiplier) → `community.donation_verified`. |
| US-6 | As an NGO coordinator, I want to record hand-offs with photos so that impact is proven. | Hand-off record with items and photo. |
| US-7 | As a customer, I want to see my and my society's impact so that I feel part of something. | Impact view: items donated, waste avoided, partner earnings supported. |
| US-8 | As a resident, I want to request my society if it isn't listed so that GharBasket can come there. | Society request with location pin → hub review. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| GET | `/v1/societies?q=&near=` | Public/authenticated | — | Search, nearby |
| GET | `/v1/societies/{id}` | Authenticated | — | Towers, captain, status |
| GET | `/v1/societies/{id}/unlock` | Authenticated | — | Threshold, count |
| POST | `/v1/society-requests` | customer | Required | |
| POST / PATCH | `/v1/admin/societies[/{id}]` | hub_manager (hub) | Required (POST) | Polygon, towers, threshold |
| GET / POST | `/v1/referrals` | customer | Required (POST) | |
| GET | `/v1/drives` | Authenticated | — | Active drives |
| POST | `/v1/donations` | customer | Required | Items, photos, preferred slot |
| POST | `/v1/donations/{id}/verify` | hub_manager | Required | Points awarded |
| POST | `/v1/ngo-handoffs` | ngo_coordinator, hub_manager | Required | |
| GET | `/v1/impact?scope=me|society|hub` | Authenticated | — | |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `community.societies` | id, hub_id, name, boundary geography(Polygon), towers jsonb, flats, unlock_threshold, status (prospect, live) |
| `community.society_requests` | id, user_id, name, location, status |
| `community.captains` | society_id, partner_id, since |
| `community.referrals` | id, referrer_user_id, referee_user_id, society_id, status, reward_entry_id |
| `community.donation_drives` | id, code, starts_on, ends_on, multiplier |
| `community.donations` | id, user_id, items jsonb, photos[], status, pickup_id, points_awarded, ngo_handoff_id |
| `community.ngos` | id, name, contact, verified |
| `community.ngo_handoffs` | id, ngo_id, items jsonb, photo_key, at |

## 5. Events
**Published:** `community.society_live`, `community.donation_scheduled`, `community.donation_verified`, `community.referral_converted`.
**Consumed:** `logistics.pickup_completed`, `orders.delivered` (referral conversion), `partners.verified` (captain assignment).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `recompute_unlock_thresholds` | Weekly | From route-cost model (M10 slot pricing) |
| `refresh_impact_counters` | Hourly | Materialised views |

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `societies:write` | hub_manager (hub), admin |
| `donations:verify` | hub_manager (hub) |
| `ngo_handoffs:write` | ngo_coordinator (ngo), hub_manager |

## 8. Screens
**Mobile:** Society (unlock bar, captain card, referrals), Kindness (gullak, schedule donation, history), Impact. **Console:** Societies map editor (terra-draw), society requests, drives and NGOs, donation verification, impact dashboard. **Public (console app):** `/s/<society>` referral landing page.

## 9. Tests required
- Integration: address-in-polygon validation; donation flow end to end with M7 pickup and M5 points; referral reward only once.
- E2E: schedule donation → pickup → verify → points visible.

## 10. Dependencies
M1 (addresses), M7 (pickups), M5 (points via `billing.api`), M8 (captains), M10 (thresholds).

## 11. Open questions
- NGO partners for the pilot (Rhythm Thakur and Manish Raje to identify two).
- Points per item category and drive multipliers.
