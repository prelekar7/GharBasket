# M9 — Community & Kindness

**Status:** Draft v1.1. §3.1 (societies and society requests) is frozen for S1; the rest is refined at phase start (spec-first).
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje (also NGO tie-ups and drive content)
**Phase / Sprint:** P5 / S9 (societies, unlock count and society requests (§3.1) are built in P2 / S1)
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
| GET | `/v1/societies?q=&near=` | Authenticated | — | Search and nearby. List items carry no captain data (§3.1) |
| GET | `/v1/societies/{id}` | Authenticated | — | Towers, status; captain card only for residents |
| GET | `/v1/societies/{id}/unlock` | Authenticated | — | Threshold, count |
| POST | `/v1/society-requests` | customer | Required | Location optional (no pin placed) |
| POST / PATCH | `/v1/admin/societies[/{id}]` | hub_manager (hub) | Required (POST) | Polygon, towers, threshold |
| GET / POST | `/v1/referrals` | customer | Required (POST) | |
| GET | `/v1/drives` | Authenticated | — | Active drives |
| POST | `/v1/donations` | customer | Required | Items, photos, preferred slot |
| POST | `/v1/donations/{id}/verify` | hub_manager | Required | Points awarded |
| POST | `/v1/ngo-handoffs` | ngo_coordinator, hub_manager | Required | |
| GET | `/v1/impact?scope=me|society|hub` | Authenticated | — | |

### 3.1 Society contract (frozen for S1)
Onboarding (Hf04 society picker, Hf05 request, Hf06 flat) and the Society screen build against these shapes. Changing them needs a PR reviewed by both Prathamesh and Yash. These are built in P2 / S1, ahead of the rest of M9 (P5).

**Captain privacy.** Captains are women who live in the society. No endpoint ever returns a captain's flat number or phone. Their display name appears only to residents of that society.

```
GET /v1/societies?q=<text>&near=<lat>,<lng>&cursor=&limit=     (authenticated; limit ≤ 50, default 20)
  200 : { "items": [ SocietySummary ], "next_cursor": "<opaque> | null" }     (platform Page)
        q    : matches name and area (pg_trgm + unaccent); best match first
        near : with q, ties are broken by distance; without q, nearest first
        neither: alphabetical
SocietySummary : { "id": "<uuid>", "slug": "sunrise-towers-kharghar", "name": "Sunrise Towers",
                   "area": "Sector 20, Kharghar",
                   "status": "prospect" | "unlocking" | "live" | "paused",
                   "distance_m": 400 | null }        null when near isn't given

GET /v1/societies/{id}        200 SocietyOut | 404 "community.society_not_found"
SocietyOut : SocietySummary & {
               "towers": [ { "id": "a", "label": "A wing" } ],     tower ids are unique per society
               "flats": 240 | null,
               "captain": { "display_name": "Meena tai", "photo_url": "<url> | null" } | null }
             captain is non-null only when the caller's household is in this society and the society
             has an active captain (captains arrive with M8; until then it is always null)

GET /v1/societies/{id}/unlock
  200 : { "society_id", "status", "joined": 14, "threshold": 20 }
        joined counts households whose society_id is this society (read via identity.api)

POST /v1/society-requests     (customer; Idempotency-Key required)
  request : { "name": "Panvel Heights",          2 to 120 characters
              "area": "New Panvel" | null,       up to 120 characters
              "location": { "lat": 18.99, "lng": 73.11 } | null,   null when no pin was placed;
                                                 never a default map centre
              "note": "<text> | null" }          up to 500 characters
  201 : { "id", "status": "received", "created_at" }
```

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `community.society_not_found` | Unknown society id |
| 422 | `community.near_invalid` | `near` isn't `lat,lng` within India's bounding box |
| 429 | `community.request_rate_limited` | More than 5 society requests per user per day |
| 400 / 422 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.invalid` | As in M0 |

**Facade for M1** (`gharbasket.modules.community.api`). These are the only things M1 calls:
- `get_society(id)` returns `{id, hub_id, status, tower_ids}` or None.
- `contains(society_id, lat, lng)` returns true when the society has no boundary yet.

## 4. Tables owned
| Table | Key columns |
|---|---|
| `community.societies` | id (uuidv7), hub_id (nullable, no FK until M6), slug (unique), name, area, boundary geography(Polygon, 4326) null, towers jsonb `[{id, label}]`, flats null, unlock_threshold, status (prospect, unlocking, live, paused), data_source, created_at, updated_at. GiST on boundary, trigram on name and area |
| `community.society_requests` | id, user_id, name, area null, location geography(Point, 4326) null, note null, status (received, accepted, rejected, duplicate), society_id null (set when matched), data_source, created_at |
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
- Society contract (§3.1):
  - List responses never contain captain fields.
  - The captain card goes only to residents.
  - Search ranks by trigram and then distance.
  - A society request replays with the same key, and stores a null location when no pin was placed.
  - The rate limit applies.
- E2E: schedule donation → pickup → verify → points visible.

## 10. Dependencies
M1 (addresses), M7 (pickups), M5 (points via `billing.api`), M8 (captains), M10 (thresholds).

## 11. Open questions
- NGO partners for the pilot (Rhythm Thakur and Manish Raje to identify two).
- Points per item category and drive multipliers.
