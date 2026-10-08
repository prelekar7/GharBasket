# M9 — Community & Kindness

**Status:** Draft v1.2. §3.1 (societies and society requests) is frozen for S1. §3.2 (captains, with [ADR-0026](../adr/0026-operations-contract.md)) is frozen for S5 once it is approved. The rest is refined at phase start (spec-first).
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje (also NGO tie-ups and drive content)
**Phase / Sprint:** P5 / S9. Societies, the unlock count and society requests (§3.1) are built in P2 / S1, and captains (§3.2) in P3 / S5, before M7's handover.
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
| GET | `/v1/societies/{id}/captains` | hub_manager (society's hub), admin | — | Appointments, newest first (§3.2) |
| POST | `/v1/societies/{id}/captains` | hub_manager (society's hub), admin | Required | Appoint (§3.2) |
| POST | `/v1/captains/{id}/end` | hub_manager (society's hub), admin | Required | End an appointment (§3.2) |
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
             has an active captain (appointments are §3.2; until then it is always null)

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

### 3.2 Captains contract (frozen for S5, with ADR-0026)
**Who serves which society.** M9 is the source of truth (ADR-0026 §4, decided by the owner on 2026-10-08). M8 verifies the person: application, KYC, training, and `partners.verified`. M9 records the **appointment**: which verified captain serves which society, and from when to when. M1 turns appointments into the `captain:<society>` role. M7 (handover), M3 (the cycle's captain card) and §3.1's `SocietyOut.captain` all read this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
Captain : { "id": "<uuid>", "society_id", "hub_id",
            "user_id", "partner_id",
            "display_name": "Meena tai", "photo_url": "<5-minute link>" | null,   from M8
                                                      (partners.api.profile_card)
            "status": "active" | "ended",
            "since": "2027-01-12T05:30:00Z", "until": "…" | null,
            "appointed_by": "auto" | "hub",           auto: on partners.verified (below)
            "end_reason": "stepped_down" | "replaced" | "hub_decision" | "suspended" | null }
            staff only; never the captain's flat or phone (hub staff look those up in M1)

GET  /v1/societies/{id}/captains?status=      hub_manager of the society's hub, admin
  200 : { "items": [ Captain ] }                newest first; the whole history unless status is given
POST /v1/societies/{id}/captains              hub_manager of the society's hub, admin; Idempotency-Key
  request : { "partner_id": "<uuid>", "replace": false }
  201 : Captain (active); publishes community.captain_assigned
        replace: true ends the current captain (end_reason "replaced") and appoints the new one
        in one transaction; publishes captain_ended, then captain_assigned
POST /v1/captains/{id}/end                    hub_manager of the society's hub, admin; Idempotency-Key
  request : { "reason": "stepped_down" | "hub_decision", "note": "…" | null }   note up to 300
  200 : Captain (ended, until = now); publishes community.captain_ended

CaptainCard (residents, in SocietyOut.captain, §3.1) : { "display_name", "photo_url" | null }
```

**Rules**
- **At most one active captain per society**, and a captain serves at most one society. Both are partial unique indexes where `status = 'active'`.
  - Appointing in a society with an active captain, without `replace`: 409 `community.captain_exists` (carries `captain_id`).
  - Appointing a partner who is already active elsewhere: 409 `community.already_captain`.
  - A backup captain may come later (owner, 2026-10-09).
- **Who can be appointed.** A partner of kind `captain` with status `active`, checked through `partners.api.get_partner`, whose `hub_id` is the society's hub. Otherwise: 422 `community.partner_not_eligible`.
  - Captains live in the society they serve (§3.1), so the partner's household must be in this society (`identity.api.household_for_user`). Otherwise: 422 `community.captain_not_resident`.
- **Automatic appointment** happens in the consumer of `partners.verified` with `kind: "captain"`. M9 appoints the partner to `requested_society_id` (`appointed_by: "auto"`) when three things hold:
  - the society has no active captain;
  - the partner is not active elsewhere;
  - the residency rule above holds.

  Otherwise nothing happens and the request waits for a hub manager (the M8 review queue shows whether the society is taken). Duplicate deliveries of the event appoint once.
- **Ending.**
  - **Suspension or departure.** In the consumer of `partners.suspended`, the partner's active appointment ends at once with `end_reason: "suspended"` (this includes `reason_code: "left"`).
  - **No automatic return.** `partners.reinstated` does not re-appoint; the hub manager decides.
  - **Ending twice.** Ending an ended appointment is 409 `community.captain_already_ended`.
- **Events and roles.**
  - **`community.captain_assigned { captain_id, society_id, hub_id, user_id, partner_id, since, appointed_by }`.** M1 grants `captain:<society>` (status `verified`); M11 tells the captain.
  - **`community.captain_ended { captain_id, society_id, hub_id, user_id, partner_id, until, reason }`.** M1 revokes `captain:<society>`.

  Neither event carries a name, flat or phone.
- **Privacy.**
  - **Never a flat or a phone.** No endpoint, event or facade returns a captain's flat number or phone to customers.
  - **Residents only.** The card `{ display_name, photo_url }` goes only to residents of that society (`SocietyOut.captain`, §3.1).
  - **M3.** M3's `CycleOut.captain` changes from `{ name, flat }` to the same card, read through `captain_card` (ADR-0026 §4). It is `null` today, so no client breaks.
  - **Photos.** `photo_url` is a 5-minute signed link minted by M8. It is null until M0 has a `partner_photo` purpose (M8 §11).
- **The seed** (S5, before M8's applications exist) appoints one seeded verified captain partner per live seeded society, so M7's handover can be built and tested (M8 build order (a)).

**Facade** (`gharbasket.modules.community.api`, in addition to §3.1):
- `captain_for_society(session, society_id) -> CaptainRef | None`. `CaptainRef` is `{ captain_id, user_id, partner_id, display_name }` (ADR-0026 §4). M7 uses it at handover.
- `captain_card(session, society_id) -> { display_name, photo_url } | None`, for M3 and `SocietyOut`.
- `is_active_captain(session, user_id, society_id) -> bool`, for M7's permission check (ADR-0026 §2: the calling module checks the actor).
- `export_for_user(session, user_id)`: the user's own appointments (society name, since, until), added to the M1 data export.

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `community.society_not_found` | Unknown society, or one outside the caller's hubs |
| 404 | `community.captain_not_found` | Unknown appointment, or one outside the caller's hubs |
| 409 | `community.captain_exists` | The society has an active captain and `replace` is false (`captain_id`) |
| 409 | `community.already_captain` | The partner is the active captain of another society |
| 409 | `community.captain_already_ended` | Ending an ended appointment |
| 422 | `community.partner_not_eligible` | Not an active `captain` partner of the society's hub |
| 422 | `community.captain_not_resident` | The partner's household is not in this society |
| 403 | `auth.forbidden` | Not a hub manager of the society's hub, or admin |
| 400 / 422 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.invalid` | As in M0 |

| Mock today (`feat/mobile-partner`) | Contract |
|---|---|
| `mocks/community.js`: `captain: { name, flat }` on the society | `captain: { display_name, photo_url }` (§3.1, unchanged); no flat |
| `mocks/subscriptions.js`: `captain: { name, flat }` on the cycle; the Society screen shows "with {flat}" (`onboarding.flat.capBody`) | `{ display_name, photo_url }` (M3 follow-up); drop the flat from the copy |
| `mocks/orders.js`: `captain: { name, phone_e164 }` on an order | No captain on `OrderOut` and never a phone; the order screen shows the society's captain card (`GET /v1/societies/{id}`) |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `community.societies` | id (uuidv7), hub_id (nullable, no FK until M6), slug (unique), name, area, boundary geography(Polygon, 4326) null, towers jsonb `[{id, label}]`, flats null, unlock_threshold, status (prospect, unlocking, live, paused), data_source, created_at, updated_at. GiST on boundary, trigram on name and area |
| `community.society_requests` | id, user_id, name, area null, location geography(Point, 4326) null, note null, status (received, accepted, rejected, duplicate), society_id null (set when matched), data_source, created_at |
| `community.captains` | (§3.2) id (uuidv7), society_id → societies, hub_id (copied from the society), user_id, partner_id, status (active, ended), since, until null, appointed_by (auto, hub), appointed_by_user_id null, end_reason null, ended_by_user_id null, note null, created_at; partial unique (society_id) and partial unique (partner_id) where status = 'active'; check: until is set exactly when status is ended. `user_id` and `partner_id` are plain UUIDs (no foreign key across schemas) |
| `community.referrals` | id, referrer_user_id, referee_user_id, society_id, status, reward_entry_id |
| `community.donation_drives` | id, code, starts_on, ends_on, multiplier |
| `community.donations` | id, user_id, items jsonb, photos[], status, pickup_id, points_awarded, ngo_handoff_id |
| `community.ngos` | id, name, contact, verified |
| `community.ngo_handoffs` | id, ngo_id, items jsonb, photo_key, at |

## 5. Events
**Published:** `community.society_live`, `community.donation_scheduled`, `community.donation_verified`, `community.referral_converted`, `community.captain_assigned` and `community.captain_ended` (payloads in §3.2).
**Consumed:**
- `logistics.pickup_completed`;
- `orders.delivered` (referral conversion);
- `partners.verified` (kind `captain`: automatic appointment, §3.2);
- `partners.suspended` (ends the appointment, §3.2).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `recompute_unlock_thresholds` | Weekly | From route-cost model (M10 slot pricing) |
| `refresh_impact_counters` | Hourly | Materialised views |

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `societies:write` | hub_manager (hub), admin |
| `captains:read` · `captains:write` | hub_manager (the society's hub), admin (§3.2) |
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
- Captains (§3.2):
  - A second active captain in a society, or a second society for one captain, is refused. Two concurrent appointments in the same society: one wins, the other gets 409.
  - `replace` ends and appoints in one transaction.
  - `partners.verified` (captain) appoints automatically only when the society is vacant, the partner is free and resident; a duplicate event appoints once; other kinds are ignored.
  - `partners.suspended` ends the appointment; `partners.reinstated` does not re-appoint.
  - Each appointment publishes `captain_assigned` once and each end `captain_ended` once, with no name, flat or phone. M1's consumer grants and revokes `captain:<society>`.
  - The card reaches residents only, and no response, event or facade result contains a captain's flat or phone.
- E2E: schedule donation → pickup → verify → points visible.

## 10. Dependencies
M1 (addresses; `household_for_user` for the captain residency rule), M7 (pickups), M5 (points via `billing.api`), M8 (verified captain partners via `partners.api.get_partner` / `profile_card`, and its `partners.verified` and `partners.suspended` events), M10 (thresholds).

## 11. Open questions
- NGO partners for the pilot (Rhythm Thakur and Manish Raje to identify two).
- Points per item category and drive multipliers.
