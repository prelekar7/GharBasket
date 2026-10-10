# M9 — Community & Kindness

**Status:** Draft v1.3. §3.1 (societies and society requests) is frozen for S1. §3.2 (captains, with [ADR-0026](../adr/0026-operations-contract.md)) is frozen for S5 once it is approved. §3.3–§3.8 (referrals; point rates, drives and donations; NGOs, surplus offers, donated meals and hand-offs; society unlock; impact; facade, export and erasure) were drafted on 2026-10-09 and are frozen for S9 on approval.
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
| US-4 | As a customer, I want to schedule a donation pickup so that I can give away clothes, books or toys. | Donation `scheduled` for one of the society's next delivery days; collected at the door and brought back on the van's return leg (M7 §3.6). |
| US-5 | As a hub manager, I want to verify donations so that points are fair. | Verification awards points (drive multiplier) → `community.donation_verified`. |
| US-6 | As an NGO coordinator, I want to record hand-offs with photos so that impact is proven. | Hand-off record with items and photo. |
| US-7 | As a customer, I want to see my and my society's impact so that I feel part of something. | Impact view: items donated, meals donated, food saved and partner earnings at the hub; no number that describes fewer than 5 households or partners (§3.7). |
| US-9 | As an NGO coordinator, I want to see and claim the hub's surplus food so that it reaches people before it spoils. | Open offers from the 12:30 cascade; first claim wins; the hand-off records the kilograms (§3.5). |
| US-10 | As a customer, I want to turn my Kindness points into a meal for a child so that my points do good. | `POST /v1/kindness/donations` (M5 §3.6); the meal is counted until an NGO hand-off serves it (§3.5). |
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
| GET | `/v1/referrals` | customer | — | Code, link, rewards, own referrals (§3.3) |
| POST | `/v1/referrals` | customer | Required | The neighbour claims a code (§3.3) |
| GET | `/v1/point-rates` | Authenticated | — | Points per kg or piece (§3.4) |
| PUT | `/v1/admin/point-rates/{code}` | admin | — | §3.4 |
| GET | `/v1/drives?state=` | Authenticated | — | Running and upcoming drives for the caller; staff see every state (§3.4) |
| POST / PATCH | `/v1/admin/drives[/{id}]` | hub_manager (hub), admin | Required (POST); If-Match (PATCH) | §3.4 |
| GET | `/v1/donations` | customer | — | Own donations and the next pickup dates (§3.4) |
| POST | `/v1/donations` | customer | Required | Items, photos, drive, pickup date (§3.4) |
| POST | `/v1/donations/{id}/cancel` | customer | Required | While scheduled (§3.4) |
| GET | `/v1/admin/donations?hub_id=&status=` | hub_manager (hub), admin | — | Verification queue (§3.4) |
| POST | `/v1/donations/{id}/verify` · `/reject` | hub_manager (hub), admin | Required | Points awarded or reason (§3.4) |
| GET | `/v1/ngos` | hub_manager (hub), admin, ngo_coordinator (own) | — | §3.5 |
| POST / PATCH | `/v1/admin/ngos[/{id}]` | admin | Required (POST); If-Match (PATCH) | §3.5 |
| GET | `/v1/ngo/offers?hub_id=` | ngo_coordinator, hub_manager (hub), admin | — | Surplus offered to NGOs (§3.5) |
| POST | `/v1/ngo/offers/{offer_id}/claim` | ngo_coordinator (ngo) | Required | First claim wins (§3.5) |
| GET | `/v1/ngo-handoffs/ready?hub_id=` · `/v1/ngo-handoffs?hub_id=&ngo_id=&month=` | ngo_coordinator (ngo), hub_manager (hub), admin | — | §3.5 |
| POST | `/v1/ngo-handoffs` | ngo_coordinator (ngo), hub_manager (hub) | Required | Items, food or meals with photos (§3.5) |
| POST | `/v1/ngo-handoffs/{id}/confirm` | ngo_coordinator (ngo) | Required | §3.5 |
| GET | `/v1/impact?scope=me\|society` | customer; society: residents, its captain, hub staff, admin | — | §3.7; the hub, NGO and platform views are M12's |

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
            "display_name": "Meena tai", "photo_url": null,   from M8 (partners.api.profile_card);
                                                      null for now (below)
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
  - Captains live in the society they serve (§3.1), so the partner's household must be in this society (`identity.api.household_for_user`). Otherwise: 422 `community.captain_not_resident`. The household's society is the only check: the prototype asks for no proof of residence (owner, 2026-10-09; M8 §3.1).
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
  - **Photos.** `photo_url` is null for now (owner, 2026-10-09). A `partner_photo` upload purpose comes later; then it becomes a 5-minute signed link minted by M8 (M8 §11).
- **The seed** (S5, before M8's applications exist; confirmed by the owner, 2026-10-09) appoints one seeded verified captain partner per live seeded society, so M7's handover can be built and tested (M8 build order (a)).

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

**As built (S5).**
- **Order of refusals** when appointing:
  - 404 `community.society_not_found`;
  - 422 `community.partner_not_eligible`, then `community.captain_not_resident`;
  - 409 `community.already_captain`, then `community.captain_exists`.

  Re-appointing the society's current captain, even with `replace`, is 409 `community.captain_exists`.
- **Concurrency.** Appointing locks the society row, so a second appointment in the same society waits and then gets 409. The partial unique indexes are the last guard, and a violation answers the same 409.
- **Duplicate `partners.verified`.** An appointment of the partner made since the event was published counts as that event's, so a redelivery never appoints again, even after a hub manager ended her.
- **`partners.suspended`** ends the appointment whatever the `reason_code`; `partners.reinstated` is not consumed.
- **Who can read and write.** A society without a hub is visible to admins only. The end `note` is stored (staff-only) but not returned.
- **Audit.** Every appointment and end is audit-logged (`community.captain_assigned`, `community.captain_ended`).
- **M1's role** follows the events through `is_active_captain` (M1 §5).
- **The export.** `export_for_user` returns `{ "captain_appointments": [ { "society_id", "society", "status", "since", "until" } ] }`.
- **The seed** appoints Meena (Sunrise Towers) and Savita (Krishna Kunj) automatically: `just seed` delivers its events once it has committed. Meena's `captain` role is no longer granted directly by the seed.

| Mock today (`feat/mobile-partner`) | Contract |
|---|---|
| `mocks/community.js`: `captain: { name, flat }` on the society | `captain: { display_name, photo_url }` (§3.1, unchanged); no flat |
| `mocks/subscriptions.js`: `captain: { name, flat }` on the cycle; the Society screen shows "with {flat}" (`onboarding.flat.capBody`) | `{ display_name, photo_url }` (M3 follow-up); drop the flat from the copy |
| `mocks/orders.js`: `captain: { name, phone_e164 }` on an order | No captain on `OrderOut` and never a phone; the order screen shows the society's captain card (`GET /v1/societies/{id}`) |

### 3.3 Referrals contract (drafted 2026-10-09, frozen on approval)
A customer shares a link for their society; a neighbour who joins with it and gets a first weekly box earns both of them Kindness points. The Society screen, the share sheet, onboarding and the public page (M12 §3.7) build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

**Rewards are points** (M5 §3.6, owner 2026-10-09): Kindness points have no cash value and are redeemed only as donated meals, so a referral reward is points, never wallet money or rupees off a box. The default pays 250 points to each side (half a donated meal each; two referrals make a meal), set by `REFERRAL_POINTS_REFERRER` and `REFERRAL_POINTS_REFEREE`.

```
Referrals : { "code": "K7Q2XD",                     one per user, made on first GET; 6 characters of
                                                    Crockford base32 (no I, L, O, U), case-insensitive,
                                                    random: never derived from a name or phone
              "link": "https://gharbasket.in/s/sunrise-towers-kharghar?ref=K7Q2XD",
                                                    PUBLIC_WEB_URL + the caller's society slug; null
                                                    while the caller has no household
              "share_text": "…",                    rendered in the request language; carries the link
              "rewards": { "referrer_points": 250, "referee_points": 250, "monthly_cap": 10 },
              "summary": { "joined": 3, "converted": 2, "points_earned": 500 },
              "referred_by": { "status": "joined" | "converted" | "void", "points": 250 | null } | null,
              "items": [ { "id", "status": "joined" | "converted" | "void",
                           "joined_on": "2027-02-24", "converted_on": "…" | null,
                           "points": 250 | null } ] }       newest first, at most 50
                    items never name the neighbour: no name, flat, tower or phone

GET  /v1/referrals              customer; Cache-Control: no-store
  200 Referrals
POST /v1/referrals              customer (the neighbour who was referred); Idempotency-Key
  request : { "code": "K7Q2XD" }
  201 : { "id", "status": "joined", "referee_points": 250,
          "converts_on": "first_delivered_box" }
```

**Rules**
- **Claiming.** The app keeps `ref` from the link (`/s/<slug>?ref=`, M12 §3.7, and the app's join link) and posts it once the household exists (onboarding's last step). A code can also be typed on the Society screen. A user is referred at most once.
  - The caller must have a household: otherwise 422 `community.household_required`.
  - Not the caller's own code, nor a code of anyone in the same household: 422 `community.referral_self`.
  - Within 14 days of the caller's sign-up, and before the household's first delivered box: otherwise 409 `community.referral_window_closed`.
  - A second claim: 409 `community.already_referred`. An unknown code: 404 `community.referral_code_unknown`.
- **Any society.** The neighbour may join a different society from the referrer's; the reward is the same. The unlock bar counts households, not referrals (§3.6).
- **Conversion.** In the consumer of `orders.delivered` with `kind: "box"` (add-ons and tiffins do not count), the referee household's first delivered box converts its `joined` referral, in one transaction:
  1. The referral becomes `converted` with `converted_on` and the order id.
  2. M9 calls `billing.api.credit_points(session, referee_user_id, 250, "referral_welcome", referral_id)`.
  3. It calls `credit_points(session, referrer_user_id, 250, "referral", referral_id)` unless the cap below applies. Calling the facade directly (not through an event) lets the row keep both entry ids; M5's keys `points:<source_type>:<referral id>` make a redelivered event credit once.
  4. It publishes `community.referral_converted { referral_id, referrer_user_id, referee_user_id, society_id, hub_id, referrer_points, referee_points }`. M5's `billing.points_credited` already notifies each side (M11 category `kindness`), so M11 maps nothing for this event.
- **Cap.** A referrer is rewarded for at most 10 conversions per IST calendar month. Later conversions still count in `summary` with `points: 0`, and the referee is still rewarded.
- **Void.** A referral becomes `void` when either account's deletion is requested (consumer of `identity.deletion_requested`), or when the referee's household is still unconverted 90 days after `joined_on` (`expire_referrals`). Void referrals pay nothing.
- **Captains.** A captain shares her own code like any customer and earns the same points. A cash referral bonus for captains (handbook 02 lever B11, handbook 09's "referral bonus" on the Earnings screen) is not in v1 (owner, 2026-10-09; §11): a captain already earns ₹6 for every bag in her society.
- **Synthetic data.** The simulator's households refer each other through the same endpoints, so the referral funnel has `data_source` like every household-derived row.

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `community.referral_code_unknown` | No user has this code |
| 409 | `community.already_referred` | The caller already claimed a code |
| 409 | `community.referral_window_closed` | More than 14 days after sign-up, or the household already had a delivered box |
| 422 | `community.referral_self` | Own code, or a code of the same household |
| 422 | `community.household_required` | The caller has no household yet |
| 400 / 422 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.invalid` | As in M0 |

| Mock today (`mocks/community.js`, `feat/mobile-sync-orders`, marked "@proposal") | Contract |
|---|---|
| `POST /v1/referrals { society_id, channel }` records an invite (`status: "sent"`) and bumps the society's `joined` | `POST` is the neighbour's claim `{ code }`. Shares are not recorded on the server (a PostHog event, no names, if wanted). `joined` moves only when a household is created (§3.6) |
| The link is built on the client: `${origin}/s/<slug>` with no code; `onboarding/done.jsx` passes the society id, not the slug | Use `Referrals.link` (it carries `?ref=`) and `share_text` from `GET /v1/referrals` |
| Mock slug `sunrise-towers`; console and public mocks use `sunrise-towers-kharghar` | The slug is §3.1's `SocietySummary.slug` |

### 3.4 Point rates, drives and donations contract (drafted 2026-10-09, frozen on approval)
A customer gives clothes, books, toys, school bags or utensils. Whoever brings the household's box collects them at the door on a delivery day, the van brings them back (M7 §3.6), the hub counts them, and the customer earns Kindness points (M5 §3.6). The mobile Kindness screen and the console's Drives, Point rates and Donations pages build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
PointRate : { "code": "clothes", "label": "Clothes",          label in the request language
              "unit": "kg" | "each", "points": 40,            points per kg or per piece
              "max_qty": 15 }                                 per donation
  seed (the console mock's rates; admin may change them):
    clothes 40 / kg (max 15) · utensils 30 / kg (max 10) · school_bag 50 / each (max 5) ·
    books 10 / each (max 30) · toys 15 / each (max 10)

Drive : { "id", "code": "diwali-2026", "name": "Diwali drive", "description": "…",   name and description
          "scope": "platform" | "hub" | "society", "hub_id" | null, "society_id" | null,   rendered in the
          "codes": [ "clothes", "toys" ],             the categories it multiplies        request language
          "multiplier": 2.0,                          1.0 to 3.0, one decimal
          "starts_on": "2026-10-01", "ends_on": "2026-10-31",    IST dates, inclusive
          "state": "upcoming" | "running" | "ended" | "cancelled",
          "ngo": { "id", "name" } | null,             who receives the items
          "goal_items": 200 | null,
          "progress": { "pledged": 40, "verified": 31 } }      items (pieces, and kilograms counted as
                                                               one item per kg); no households, no names

Donation : { "id", "status": "scheduled" | "picked_up" | "verified" | "rejected"
                           | "handed_over" | "cancelled",
             "items": [ { "code": "clothes", "unit": "kg", "qty": 3,
                          "counted": 2.5 | null,      set by the hub at verification
                          "points": 100 | null } ],
             "photos": 2,                              how many; customers never get links back
             "drive": { "id", "name", "multiplier" } | null,
             "pickup_on": "2026-10-13",
             "estimated_points": 240,                  from the declared items, before counting
             "points_awarded": 200 | null,
             "reject_reason": "unusable" | "not_accepted" | "unsafe" | "other" | null,
             "cancel_reason": "customer" | "not_ready" | null,
             "ngo": { "name" } | null,                 once handed over
             "timeline": [ { "status", "at" } ], "created_at" }

GET  /v1/point-rates                         authenticated
  200 { "items": [ PointRate ] }
PUT  /v1/admin/point-rates/{code}            admin; { "points", "max_qty", "label": { en, hi, mr } }
  200 PointRate                              applies to donations verified from now on; audit-logged

GET  /v1/drives?state=running                authenticated
  200 { "items": [ Drive ], "next_cursor" }  customers get running and upcoming drives of the platform,
                                             their hub and their society; staff of a hub (and admin)
                                             get every state with ?state= (default: running, upcoming)
POST /v1/admin/drives                        hub_manager (hub or society drive in their hub), admin (any);
                                             Idempotency-Key
  request : { "code", "name": { en, hi, mr }, "description": { en, hi, mr } | null,
              "scope", "hub_id" | null, "society_id" | null, "codes", "multiplier",
              "starts_on", "ends_on", "ngo_id" | null, "goal_items" | null }
  201 Drive
PATCH /v1/admin/drives/{id}                  same roles; If-Match; any field above, or
                                             { "state": "cancelled" }; dates of a running drive may
                                             only be extended; 200 Drive

GET  /v1/donations?cursor=&limit=            customer (own household)
  200 { "items": [ Donation ], "next_cursor",
        "pickup_dates": [ "2026-10-13", "2026-10-20" ] }   the next two dates this household can choose
POST /v1/donations                           customer; Idempotency-Key
  request : { "items": [ { "code": "clothes", "qty": 3 } ],   1 to 5 lines, distinct codes; qty in the
                                                              rate's unit: whole pieces, or kg in 0.5 steps
              "photo_keys": [ "donation_photo/<user_id>/<uuid>.jpg" ],   0 to 3 (M0 `donation_photo`)
              "drive_id": "<uuid>" | null,
              "pickup_on": "2026-10-13" }                     one of pickup_dates
  201 Donation (scheduled); publishes community.donation_scheduled
POST /v1/donations/{id}/cancel               customer; Idempotency-Key; only while scheduled
  200 Donation (cancelled); publishes community.donation_cancelled

GET  /v1/admin/donations?hub_id=&status=picked_up&cursor=     hub_manager (hub), admin
  200 Page[ Donation & { "household_id", "society": { "id", "name" }, "tower", "flat",
                         "picked_up_by": { "name" } | null, "picked_up_on",
                         "photo_urls": [ { "url", "expires_at" } ] } ]
        oldest first; staff only: tower and flat match the bag's label at the hub (M1 delivery_labels);
        each photo link is a 5-minute signed GET, and minting them is audit-logged
POST /v1/donations/{id}/verify               hub_manager of the donation's hub, admin; Idempotency-Key
  request : { "counted": { "clothes": 2.5, "toys": 0 },   codes the hub found, in each rate's unit;
                                                          codes not declared may be added
              "has_unsuitable": false,                    some items were set aside (they earn nothing)
              "note": "…" | null }                        up to 300
  200 Donation (verified); publishes community.donation_verified
POST /v1/donations/{id}/reject               same roles; Idempotency-Key
  request : { "reason": "unusable" | "not_accepted" | "unsafe" | "other", "note": "…" | null }
  200 Donation (rejected); publishes community.donation_rejected
```

**Rules**
- **States** (handbook 06 §6.7, simplified): `scheduled → picked_up → verified → handed_over`, `picked_up → rejected`, and `scheduled → cancelled`. The handbook's `PICKUP_ASSIGNED` and `AT_HUB` are not stored: a donation is assigned by its `pickup_on`, and it is at the hub once picked up (the van returns the same day). The handbook figure follows this spec (§10).
- **Pickup dates.** A donation is collected on a delivery day of the household's society (M3's society delivery days, through `subscriptions.api.delivery_dates(society_id, after, n)`). The household does not need a box that day; the captain (or the rider in rider mode) collects at the door during the round.
  - `pickup_dates` are the next two delivery dates whose route is not solved yet. A date is open until 22:00 IST the day before, ahead of M7's 23:15 solve. Another date gets 422 `community.pickup_date_invalid` with `dates`.
  - A society without delivery days gets `pickup_dates: []` and 422 `community.no_pickup_day`.
- **Limits.** At most 3 donations per household in `scheduled` or `picked_up`: otherwise 409 `community.too_many_open_donations`. A quantity over the rate's `max_qty`, or a kg quantity not in 0.5 steps, gets 422 `community.donation_qty_invalid`. An unknown code gets 422 `community.item_code_unknown`.
- **Drives.** A donation names at most one drive. The drive must be `running` on `pickup_on` and cover the household (platform, its hub, or its society): otherwise 422 `community.drive_not_open`.
  - The multiplier applies to lines whose code is in the drive's `codes`. It does not apply to other lines.
  - Drives may overlap, and the app suggests the running drive with the highest multiplier.
  - **Pledges.** A donation scheduled in a drive is that drive's pledge, so there is no separate pledge object. `progress.pledged` counts items in scheduled and picked-up donations, and `progress.verified` counts the items the hub counted.
  - `state` is computed from the dates in IST, except `cancelled`. Cancelling a drive keeps the multiplier for donations already scheduled with it.
- **Points** are computed at verification, from the rates in force then. `points = round_half_up(Σ counted × rate.points × (drive multiplier for that line, else 1))`.
  - At least one counted quantity must be positive: otherwise 422 `community.nothing_counted` (use reject).
  - A counted quantity follows the same unit and step rules as the request, and is at most twice the rate's `max_qty`.
  - Each line's `points` is its own rounded share, and the lines add up to `points_awarded`: the largest line takes the rounding remainder.
  - `estimated_points` uses the same formula on the declared items.
- **Verified once.** Verifying or rejecting a donation that is not `picked_up` is refused:
  - one already verified or rejected gets 409 `community.donation_already_verified`;
  - one still scheduled gets 409 `community.donation_not_picked_up`.
  `community.donation_verified { donation_id, user_id, household_id, hub_id, society_id, drive_id, points }` is published once, and M5's consumer credits the points (`credit_points(…, "donation", donation_id)`, M5 §3.6). A rejected donation earns nothing, and its items are not returned: the scheduling screen says that items the hub cannot use go to a recycler (§11).
- **Pickup outcomes** come from `logistics.pickup_completed` (M7 §3.6). M9 needs the outcome of each donation: `donations: [ { donation_id, status: "collected" | "not_ready" } ]` (an M7 follow-up; the outline's event carries only `donation_ids`).
  - `collected` makes the donation `picked_up`, with `picked_up_on` and the rider's user id.
  - `not_ready` moves `pickup_on` to the society's next delivery date once, and publishes `community.donation_scheduled` again with the new date. A second `not_ready` cancels it with `cancel_reason: "not_ready"`.
  - `sweep_missed_pickups` (22:30 IST) treats a donation whose `pickup_on` is today and that no stop reported as `not_ready`.
- **Photos.** These are M0's `donation_photo`, kept 90 days after the pickup. M9 claims each key when the donation is saved (only the caller's own uploads; otherwise 422 `community.photo_key_invalid`) and sets `delete_after` when the donation is picked up or cancelled. Photos show items, never people.
- **Notifications** (M11 event map, category `kindness`). `community.donation_verified` says what was counted and the points ("2.5 kg clothes, 100 points"), and `community.donation_rejected` gives the reason. M5's `billing.points_credited` for `source_type: "donation"` then sends nothing, so the customer gets one message (an M11 follow-up). `donation_scheduled` and `donation_cancelled` send nothing.

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `community.donation_not_found` | Unknown donation, or not the caller's (customer) or outside the caller's hubs (staff) |
| 404 | `community.drive_not_found` | Unknown drive, or outside the caller's hubs (staff) |
| 409 | `community.too_many_open_donations` | Three donations already scheduled or picked up |
| 409 | `community.donation_not_cancellable` | Cancelling a donation that is no longer `scheduled` |
| 409 | `community.donation_not_picked_up` | Verifying or rejecting a donation that is still `scheduled` |
| 409 | `community.donation_already_verified` | Verifying or rejecting twice (`status`) |
| 422 | `community.pickup_date_invalid` | `pickup_on` is not one of the household's `pickup_dates` (`dates`) |
| 422 | `community.no_pickup_day` | The society has no delivery days |
| 422 | `community.item_code_unknown` · `community.donation_qty_invalid` · `community.no_items` | Unknown code; quantity out of range or step; no lines |
| 422 | `community.drive_not_open` | The drive is not running on `pickup_on`, or does not cover the household |
| 422 | `community.nothing_counted` | Verification with every count 0 |
| 422 | `community.photo_key_invalid` | Not the caller's `donation_photo` upload |
| 422 | `community.drive_dates_invalid` | `ends_on` before `starts_on`, a past start, or shortening a running drive |
| 403 | `auth.forbidden` | A hub manager writing a drive or verifying outside their hub |
| 400 / 422 / 412 / 428 | `request.*` | As in M0 (idempotency, If-Match) |

| Mock today | Contract |
|---|---|
| Mobile `GET /v1/drives` → `{ id, code, starts_on, ends_on, multiplier }`, title from i18n `kindness.drives.<code>` | `Drive` adds `name`, `description`, `scope`, `codes`, `state`, `ngo`, `progress`; the app may keep its i18n key by `code` but should show `name` |
| Console `GET /v1/drives?include_ended=true` → `{ id: "diwali", name, description, multiplier, state: "upcoming" \| "running" \| "ended" }`, staff-only; `POST /v1/admin/drives { name, description, starts_on, ends_on, multiplier }` | One `Drive` shape for both apps; `id` is a UUID and `code` the slug; `?state=` replaces `include_ended`; the POST adds `code`, `scope`, `codes`, translations and needs an Idempotency-Key |
| Mobile item codes `school_bags`; console rates `school_bag` | `school_bag` |
| Console `GET /v1/admin/point-rates` → `{ key, label, unit: "kg" \| "each", points }` | `GET /v1/point-rates` (any signed-in user) with `code` instead of `key`, plus `max_qty`; the seed keeps the console's numbers |
| Mobile `GET /v1/donations` → `{ items, next_cursor, next_pickup_on }` | `pickup_dates` (two dates) replaces `next_pickup_on` |
| Mobile `Donation.items: [{ code, qty }]` or `[{ code, kg }]`; `photos: [keys]`; status UPPERCASE with `PICKUP_ASSIGNED`, `AT_HUB`, `HANDED_TO_NGO` | `items: [{ code, unit, qty, counted, points }]`; `photos` is a count; lower-case statuses `scheduled`, `picked_up`, `verified`, `rejected`, `handed_over`, `cancelled` |
| Mobile `POST /v1/donations { items: ["clothes"], photos, pickup_on }` (no quantities) | `items: [{ code, qty }]` (the hub needs an estimate to plan the van), `photo_keys` with M0 keys (`donation_photo/<user_id>/…`, not `donations/<uuid>`) |
| Console `GET /v1/admin/donations?status=to_verify` → `{ flat, society, lines: [{ key, label, unit, said, counted, rate }], photos: 2, picked_up_on, picked_up_by, drive, status: "to_verify" \| "verified" }` | `status=picked_up`; `items` in place of `lines` (`qty` is "said"); `tower` and `flat` separate; `photo_urls` with 5-minute links; `household_id` |
| Console `POST /v1/donations/{id}/verify { counted: { key: n }, has_unsuitable }` → `{ id, points_awarded }`; points = round(Σ counted × rate) × multiplier; 409 `community.already_verified` | Same request plus `note`; the answer is the whole `Donation`; the multiplier applies per line before rounding; `community.donation_already_verified`; rejecting is its own endpoint |

### 3.5 NGOs, surplus offers, donated meals and hand-offs contract (drafted 2026-10-09, frozen on approval)
GharBasket gives three things to NGO partners: **items** from verified donations, **food** from the hub's surplus cascade (M6 §3.5), and **meals** that customers donate with their points (M5 §3.6). Each gift is recorded as a **hand-off** with photo proof. The console's NGOs, Hand-offs and Donations pages, and the NGO coordinator's console, build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
Ngo : { "id", "name": "Annapurna Food Bank", "kind": "food_bank" | "shelter" | "school" | "other",
        "hub_ids": [ "<uuid>" ],                       hubs it collects from
        "accepts": [ "items", "food", "meals" ],
        "registration_ref": "DARPAN MH/2019/0123456" | null,
        "verified": true, "status": "active" | "paused",
        "note": "…" | null,
        "coordinator": { "name", "phone_e164" } | null }   staff and the NGO's own coordinators only
NgoCard : { "id", "name" }                                 what customers ever see

GET  /v1/ngos?hub_id=                    hub_manager (hub), admin; an ngo_coordinator gets their own
  200 { "items": [ Ngo ] }
POST /v1/admin/ngos                      admin; Idempotency-Key;  201 Ngo
PATCH /v1/admin/ngos/{id}                admin; If-Match;          200 Ngo
                                         coordinators are M1 roles ngo_coordinator:<ngo id>, granted
                                         by an admin through M1's roles:grant; `coordinator` shows the
                                         first active one (name and phone from identity.api)

NgoOffer : { "offer_id", "hub_id", "product": { "id", "name" }, "qty": 12.5, "unit": "kg",
             "kg": 12.5, "expires_at", "status": "open" | "claimed" | "expired",
             "claimed_by": NgoCard | null }
GET  /v1/ngo/offers?hub_id=&status=open           ngo_coordinator (an NGO that serves the hub and
                                                  accepts food), hub_manager (hub), admin
  200 { "items": [ NgoOffer ] }                   soonest expiry first
POST /v1/ngo/offers/{offer_id}/claim              ngo_coordinator; Idempotency-Key
  request : { "ngo_id" }                          one of the caller's NGOs
  200 NgoOffer (claimed)

Handoff : { "id", "ngo": NgoCard, "hub_id",
            "kind": "items" | "food" | "meals",
            "donation_ids": [ … ],                items: verified donations of this hub
            "offer_ids": [ … ],                   food: offers this NGO claimed
            "meals": 0,                           meals: donated meals this hand-off serves
            "summary": { "items": 12, "kg": 18.5, "meals": 0 },
            "photos": 2, "photo_urls": [ { "url", "expires_at" } ],   staff and the NGO only
            "people_reached": 40 | null,          as the NGO reports it
            "handed_at", "recorded_by": "hub" | "ngo",
            "confirmed_at": "…" | null,           when the NGO's coordinator confirmed receipt
            "note": "…" | null }

GET  /v1/ngo-handoffs/ready?hub_id=               hub_manager (hub), admin, ngo_coordinator (an NGO
                                                  serving the hub)
  200 { "donations": [ { "id", "items", "verified_on" } ],      verified, not yet handed over;
                                                                no household or flat
        "offers": [ NgoOffer ],                                 claimed, not yet handed over
        "meals_waiting": 6 }                                    donated meals of the hub not yet served
GET  /v1/ngo-handoffs?hub_id=&ngo_id=&month=2027-02&cursor=      same roles (an ngo_coordinator sees
  200 Page[ Handoff ] & { "report": { "handoffs", "items", "kg",        their own NGO only)
                                      "meals", "people_reached" } }
POST /v1/ngo-handoffs                             hub_manager (hub), ngo_coordinator (the NGO);
                                                  Idempotency-Key
  request : { "ngo_id", "hub_id", "kind", "donation_ids": [], "offer_ids": [], "meals": 0,
              "photo_keys": [ "handoff_photo/<user_id>/<uuid>.jpg" ],   1 to 3 (new M0 purpose)
              "people_reached": 40 | null, "handed_at": "…" | null,     default now
              "note": "…" | null }
  201 Handoff; publishes community.handoff_recorded
POST /v1/ngo-handoffs/{id}/confirm                ngo_coordinator of the hand-off's NGO; Idempotency-Key
  request : { "people_reached": 40 | null, "photo_keys": [] }
  200 Handoff (confirmed_at set)
```

**Rules**
- **Who records.** A hand-off recorded by the NGO's coordinator is confirmed at once. One recorded by the hub waits for the coordinator's confirmation, and the console shows it as "awaiting NGO". Impact counts every hand-off, and `confirmed_at` is shown as proof.
- **Items.** Each donation must be `verified`, of the same hub, and in no other hand-off: otherwise 409 `community.donation_not_ready` (`donation_ids`). The NGO must accept `items` (422 `community.ngo_does_not_accept`). Each donation becomes `handed_over` with the NGO's name, the household sees "Given to Annapurna Food Bank", and M11 sends nothing.
- **Food (surplus).**
  - **Offers.** In the consumer of `hub_ops.surplus_offered` with `offered_to: "ngo"` (M6 §3.5), M9 copies the offer into `community.ngo_offers` for the active, verified NGOs that serve the hub and accept food.
  - **Claiming.** The first claim wins, and a later one gets 409 `community.offer_taken`. In the same transaction M9 calls `hub_ops.api.settle_surplus(session, offer_id, "taken", taker={"type": "ngo", "id": ngo_id})`.
  - **Expiry.** An offer is expired after `expires_at`, or when M6 settles it otherwise (`hub_ops.surplus_settled`, an M6 follow-up). Claiming it then gets 409 `community.offer_expired`.
  - **The hand-off** names the claimed offers. `kg` comes from the offer's unit through `catalog.api.grams_of`.
  - **M6 dependency.** M6 §3.5 is being refined in a parallel PR, and this part follows whatever that PR freezes for `settle_surplus` and the offer events.
  - **Food safety.** Raw produce only, never cooked food in the prototype (handbook 13 §13.9).
- **Meals (donated points).**
  - **Pledges.** In the consumer of `billing.points_donated { entry_id, user_id, meals, points, code }`, M9 stores a `meal_pledges` row (unique by `entry_id`, so a redelivered event counts once). The row's hub is the user's household hub (`identity.api.household_for_user`).
  - **Serving.** A `meals` hand-off of `n` meals serves the hub's oldest unserved pledges first, splitting a pledge when needed. `n` may not exceed `meals_waiting`: otherwise 422 `community.meals_exceed_pledged` (`meals_waiting`). The NGO must accept meals.
  - **Who pays** (owner, 2026-10-09; §11). Points have no cash value, so a donated meal is GharBasket's cost.
    - **Prototype:** no money moves. A meal counts as served when a partner NGO records a `meals` hand-off.
    - **Before the pilot:** the owner and Prof. Kranti Bade set a sponsored cost per meal (about ₹25). M5 then books it as an INR expense at the `meals` hand-off, under a new `meal_sponsorship` entry kind (an M5 change made then).
- **Photos.** A new M0 upload purpose, `handoff_photo` (JPEG, PNG or WebP, 5 MB, kept 3 years as impact proof), is an M0 follow-up. Photos show goods and the hand-over, **never beneficiaries' faces**: they are often children, and the console's upload copy says so. Links are 5-minute signed GETs for the hub's staff, admins and the NGO's coordinators, never public.
- **NGO coordinators** never see a donor: hand-offs and `ready` show items, kilograms and dates, never a household, flat or name.
- **`community.handoff_recorded { handoff_id, ngo_id, hub_id, kind, items, kg_tenths, meals }`** feeds M12. It is not mapped in M11.

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `community.ngo_not_found` · `community.offer_not_found` · `community.handoff_not_found` | Unknown, or outside the caller's scope |
| 409 | `community.offer_taken` · `community.offer_expired` | Claimed by another NGO; past `expires_at` or settled by M6 |
| 409 | `community.donation_not_ready` | A donation that is not `verified`, of another hub, or already handed over (`donation_ids`) |
| 409 | `community.handoff_already_confirmed` | Confirming twice |
| 422 | `community.ngo_does_not_accept` | The NGO does not accept this kind, is paused or not verified, or does not serve the hub |
| 422 | `community.meals_exceed_pledged` | More meals than are waiting at the hub (`meals_waiting`) |
| 422 | `community.handoff_empty` | No donations, offers or meals for the kind; or ids given for another kind |
| 422 | `community.photo_key_invalid` | Not the caller's `handoff_photo` upload, or no photo |

| Mock today (console, `feat/console-sync-shapes-3`) | Contract |
|---|---|
| `GET /v1/ngos` → `{ id, name, note, coordinator, verified }`; `POST /v1/admin/ngos { name, coordinator }` | `Ngo` adds `kind`, `hub_ids`, `accepts`, `registration_ref`, `status`; coordinators are M1 role grants, not a field you POST |
| `GET /v1/ngo-handoffs?month=` → `{ items: [{ id, on, items: "School bag, 3 kg clothes", note, given_to, people_reached, proof: "photo" \| "photo_due" }], open_batches: [{ id, received_on, items, note }], report }` | `Page[Handoff]` with `report`; the item summary is structured (`summary`), not a sentence; `open_batches` becomes `GET /v1/ngo-handoffs/ready` (donations, offers, meals); a photo is required, so there is no `photo_due` |
| `POST /v1/ngo-handoffs { batch_id, given_to, people_reached, has_photo }` | `{ ngo_id, hub_id, kind, donation_ids \| offer_ids \| meals, photo_keys, people_reached }` with an Idempotency-Key |
| `GET /v1/ngo-handoffs/report?month=` → PDF | No server PDF in v1: the console prints the month's page (print stylesheet), and M12's impact CSV (M12 §3.6) covers the data |

### 3.6 Society unlock contract (drafted 2026-10-09, frozen on approval)
This adds to §3.1 how a society moves from `prospect` to `live` and where the threshold comes from (handbook 07 W10). The Society screen's unlock bar and celebration, the public page (M12 §3.7), M3's delivery days and M11 build against it.

```
GET /v1/societies/{id}/unlock      (§3.1, three fields added)
  200 : { "society_id", "status", "joined": 14, "threshold": 20,
          "live_at": "2027-02-27T06:12:00Z" | null,             set once, when it first went live
          "threshold_source": "model" | "hub" | "default",
          "box_fee_paise": 3000 }                               the delivery fee on each box until
                                                                live (the hub's setting); 0 once
                                                                `live_at` is set

PATCH /v1/admin/societies/{id}     (§3 table, hub_manager of the hub, admin) gains
          { "unlock_threshold": 25, "unlock_threshold_pinned": true }   pinned: the weekly job leaves it
          { "status": "unlocking" | "paused" | "live" }                 a hub decision, audit-logged
```

**Rules**
- **States.** A society moves through `prospect → unlocking → live`, and `paused` can come from any of them.
  - **`prospect → unlocking`** happens automatically when the society's first captain is appointed (§3.2, in the same transaction as `captain_assigned`), or when the hub sets it. A society in `prospect` shows no unlock bar.
  - **`unlocking → live`** happens automatically when `joined ≥ threshold`. It is checked in the consumer of `identity.household_created` (and `identity.household_updated` when `society_id` changed), and after any threshold change.
  - The transition sets `live_at` and publishes **`community.society_live { society_id, hub_id, joined, threshold, live_at }`**. Both happen once in the society's life: `live_at` is never cleared, and a society that is paused and resumed does not publish again.
- **Never back down on its own.** A live society stays live if households leave; only the hub can pause it.
- **What live changes: the delivery fee** (owner, 2026-10-09; §11). Live is free delivery, not the start of delivery.
  - **Boxes before live.** Households in a society that is not yet live (`prospect` or `unlocking`) can get boxes, with a delivery fee on each box until the society goes live.
    - The hub decides when to start serving such a society by assigning its delivery days (M3 §3.1, which does not depend on the status). From then on M3 drafts cycles as for any society.
    - Until the society has a captain, its stop is in rider mode (M7 §3.4).
  - **The fee.** It is a per-hub setting, `pre_live_box_fee_paise` on the hub (M6 follow-up, §11), set by an admin in the hub's settings.
    - **Default: 3,000 (a flat ₹30 a box).** This is a proposed default that the owner may change.
    - **Box orders only.** Add-ons ride on the box and pay no fee of their own. Tiffins, which have their own slots, pay none.
    - **Zero once live.** The fee is 0 when the society's `live_at` is set, and so always for live societies. It is `live_at` that decides, not `status`, so a live society that is paused and resumed never pays the fee again.
  - **How it is charged.** M4 adds the fee to the box order as `fees_paise`, and M3 shows it in the cycle total before approval. It is charged at cut-off with the box, and refunded with the box (M4 follow-up, §11). Shortage and issue refunds never touch it.
  - **At live.** The fee drops to 0 from the next cut-off, including on boxes already approved (M4 re-reads `live_at` at the lock).
    - M11 tells the society's households (category `kindness`, link `/society`): delivery is now free.
    - When the society has no delivery days yet, M11 also gives the hub's managers a console task, "set delivery days".
  - **The unlock bar** shows the fee beside the count (`box_fee_paise`), e.g. "₹30 delivery a box until 20 homes join, then free".
- **The threshold `N`** is "the number of households at which a stop becomes cheaper than the delivery fee" (W10). The formula below is kept as drafted (owner, 2026-10-09): the pre-live fee does not enter it.
  - **The weekly job.** `recompute_unlock_thresholds` (Monday 02:10 IST) asks M10, through `intel_gateway.api.society_stop_cost(society_id)` (an M10 follow-up on `/slots/price`), for the marginal cost of adding the society as a stop. It then sets `N = clamp(ceil(stop_cost_paise ÷ DELIVERY_ALLOWANCE_PAISE), 10, 60)`, where `DELIVERY_ALLOWANCE_PAISE` (default 4,000, ₹40, the console's delivery-per-box assumption) is what one household's box can carry towards a stop.
  - **When M10 is down.** The job keeps the current threshold (`threshold_source` unchanged). A society never priced keeps the default 20 (`default`).
  - **Pinned thresholds.** A pinned threshold is never changed by the job (`hub`).
  - **Live societies.** The job skips them: a threshold changes nothing once live.
  - **A lower threshold.** When a new threshold is at or below `joined`, the society goes live in the job's transaction.

| Mock today | Contract |
|---|---|
| Newest mobile mock (`feat/mobile-sync-orders`): `{ society_id, status, joined, threshold }`; the app celebrates when `status` turns `live` | Adds `live_at`, `threshold_source` and `box_fee_paise`; celebrate once per society when `live_at` is newer than the last one the app saw |
| Older mocks: `unlocked_at`, and the referral mock bumps `joined` and flips the society live | `live_at`; only a new household moves `joined` |

**As built (S9).**
- **The first captain.** `captains.appoint` moves a `prospect` society to `unlocking` in its own transaction (no consumer of `captain_assigned`), then checks the count, so a society that already has enough households goes live at once.
- **The hub's PATCH.** `PATCH /v1/admin/societies/{id}` takes only `unlock_threshold` (1 to 500; the source becomes `hub`), `unlock_threshold_pinned` and `status` for now; the polygon and towers come with the console's map editor. Hub managers of the society's hub and admins (`societies:write`); another hub's society is 404. It answers the unlock view plus `unlock_threshold_pinned`, is audit-logged (`community.society_updated`), and needs no Idempotency-Key or If-Match. `status: "live"` goes live like the automatic path (`live_at` and `society_live` the first time only).
- **M10 and M6 follow-ups.** `unlock.stop_cost` answers None until `intel_gateway.api.society_stop_cost` exists, so the job keeps every threshold; `box_fee_paise` is `unlock.PRE_LIVE_BOX_FEE_PAISE` (3,000) until the hub's `pre_live_box_fee_paise` exists.
- **The job** skips societies that are live or have a `live_at`, and pinned ones; it returns `{ priced, kept, skipped, went_live }`.
- **Societies already live** got `live_at = updated_at` in the migration, and the seed sets `live_at` when it seeds a society live.
- **Facade.** `get_society` gains `live_at`; `public_society(session, slug)` is in place for M12 §3.7.

### 3.7 Impact contract (drafted 2026-10-09, frozen on approval)
Customers' impact numbers: their own, and their society's (US-7). The hub, NGO and platform views are M12's (`GET /v1/analytics/impact`, M12 §3.6), so the old `scope=hub` moves there. The mobile Impact screen and the Society screen's impact card build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
GET /v1/impact?scope=me                            customer; Cache-Control: no-store
  200 { "scope": "me", "period": "all",
        "items_donated": 11,             accepted pieces, plus kilograms counted as one item per kg
        "kg_donated": 6.5,
        "donations_verified": 4,
        "points_earned": 1240,           all kindness points ever credited (donations, referrals)
        "meals_donated": 2,              from billing.points_donated
        "homes_invited": 2,              converted referrals
        "donated": [ { "code": "clothes", "unit": "kg", "qty": 6.5 } ] }   accepted totals per code

GET /v1/impact?scope=society&society_id=&period=all|2027-02
                                                   a resident of the society, its captain, a hub
                                                   manager of its hub, admin; others 403 auth.forbidden
  200 { "scope": "society", "society_id", "period": "all", "as_of",
        "homes_joined": 46 | null,                 null under 5 households
        "items_donated": 18 | null,  "kg_donated": 9.5 | null,
        "meals_donated": 7 | null,
        "contributors": "5+" | "under_5",
        "hub": { "food_saved_kg": 420.5,           surplus handed to NGOs by the society's hub
                 "meals_served": 31,               donated meals NGOs served from the hub
                 "partners_earned_paise": 1240000 | null,   the hub's partners this month; null
                                                            under 5 paid partners (M8 facade)
                 "partners": "5+" | "under_5" } }
```

**Rules**
- **Small numbers are not shown** (k = 5, handbook 13). A society's donation and meal numbers are `null` until at least 5 distinct households of the society have contributed: a verified donation, a converted referral or a donated meal (`contributors: "under_5"`). `homes_joined` is `null` under 5 households. Partner earnings are hub-wide and `null` under 5 paid partners.
  - These rules make a single family's giving, or a single captain's pay, impossible to read off a number.
  - The same rules hold on the public page (M12 §3.7), which reads `community.api.society_impact`.
- **No per-household lists.** No leaderboard (a stretch feature in handbook 03), and no names.
- **Numbers.**
  - The `me` scope is computed live from M9's tables and `billing.api.kindness_totals(user_id)` (an M5 follow-up: credited and donated totals).
  - The `society` scope reads `community.mv_society_impact`, which `refresh_impact_counters` refreshes hourly. `as_of` is its refresh time.
- **`food_saved_kg`** is hub-wide: surplus is offered and handed over per hub, not per society. It sits under `hub`, not as a society number.
- **Months.** `period` is `all` or an IST calendar month `YYYY-MM`, within the last 12 months.

| Mock today (`mocks/community.js`, `feat/mobile-sync-orders`, "@proposal") | Contract |
|---|---|
| `?scope=society&id=` → `{ period: "2026-10", food_saved_kg, items_donated, cooks_earned_paise, homes }` | `society_id=`; `period` defaults to `all`; `food_saved_kg` and the earnings move under `hub` (`partners_earned_paise`, the console's `earned_paise` is renamed too); numbers may be `null` under 5 |
| `?scope=me` → `{ food_saved_kg, items_donated, cooks_earned_paise, homes_invited, donated: [{ code, qty } \| { code, kg }], points_earned }` | No `food_saved_kg` or earnings for one household; adds `kg_donated`, `donations_verified`, `meals_donated`; `donated` lines carry `unit` and `qty` |
| The society card is fetched only when the society is live | Any status; numbers are `null` until there are enough contributors |

### 3.8 Facade, export and erasure additions (drafted 2026-10-09)
**Facade** (`gharbasket.modules.community.api`, in addition to §3.1 and §3.2):
- `get_society` (§3.1) gains `live_at`, so M4 can set the pre-live delivery fee (§3.6). The change is additive.
- `society_impact(session, society_id, period) -> SocietyImpact`: the `society` scope of §3.7 with the k rules applied, for M12's public page.
- `public_society(session, slug) -> PublicSociety | None`: `{ id, slug, name, area, status, joined, threshold, live_at }` for `unlocking` and `live` societies only; `None` for `prospect`, `paused` or unknown.
- `pickups_for(session, society_id, on) -> list[DonationPickup]`: `{ donation_id, household_id, items_summary }` for M7's return leg and the captain's flat checklist. M7 adds the flat through `identity.api.delivery_labels`.
- `impact_facts(session, *, date_from, date_to, hub_ids=None)`, `referral_facts(…)`, `handoff_facts(…)`: rows for M12's refresh (M12 §3.1). They carry no names, flats or phones.

**Data export** (M1 §3.3, section `community`): the user's donations (items, counts, points, statuses, dates; photos counted, not included), their referral code, the referrals they made (status, dates and points, never the neighbour), whether they were referred, their donated-meal pledges, and their captain appointments (§3.2).

**Erasure** (M1 §3.5, order 4, `anonymise_for_user`):
- **Deleted:** society requests (name, pin, note), donation photos, and the user's referral code (`referral_codes`).
- **Kept, without personal fields:** donations, referrals and meal pledges, so that the impact numbers stay true.
  - Donation and referral rows keep `user_id`: like M4's orders, it no longer leads to a person.
  - Item notes and the verifier's note on the user's donations are cleared.

NGO coordinators are staff, so M1 refuses their self-deletion (`identity.staff_account`).

## 4. Tables owned
| Table | Key columns |
|---|---|
| `community.societies` | id (uuidv7), hub_id (nullable, no FK until M6), slug (unique), name, area, boundary geography(Polygon, 4326) null, towers jsonb `[{id, label}]`, flats null, unlock_threshold, status (prospect, unlocking, live, paused), data_source, created_at, updated_at. GiST on boundary, trigram on name and area |
| `community.society_requests` | id, user_id, name, area null, location geography(Point, 4326) null, note null, status (received, accepted, rejected, duplicate), society_id null (set when matched), data_source, created_at |
| `community.captains` | (§3.2) id (uuidv7), society_id → societies, hub_id (copied from the society), user_id, partner_id, status (active, ended), since, until null, appointed_by (auto, hub), appointed_by_user_id null, end_reason null, ended_by_user_id null, note null, created_at; partial unique (society_id) and partial unique (partner_id) where status = 'active'; check: until is set exactly when status is ended. `user_id` and `partner_id` are plain UUIDs (no foreign key across schemas) |
| `community.referral_codes` | (§3.3) user_id (primary key), code (unique, upper case), created_at |
| `community.referrals` | (§3.3) id (uuidv7), code, referrer_user_id, referee_user_id (unique: a user is referred once), referee_household_id, society_id and hub_id (the referee's), status (joined, converted, void), joined_on, converted_on null, order_id null (the converting box), referrer_points, referee_points, referrer_entry_id null, referee_entry_id null (M5 journal entries), void_reason null, data_source, created_at; index (referrer_user_id, converted_on) for the monthly cap |
| `community.point_rates` | (§3.4) code (primary key), label jsonb `{en, hi, mr}`, unit (kg, each), points, max_qty `NUMERIC(10,3)`, active, updated_by, updated_at |
| `community.donation_drives` | (§3.4) id, code (unique), name and description jsonb `{en, hi, mr}`, scope (platform, hub, society), hub_id null, society_id null (check: matches scope), codes text[], multiplier `NUMERIC(2,1)` (1.0–3.0), starts_on, ends_on, ngo_id null → ngos, goal_items null, cancelled_at null, created_by, version, created_at |
| `community.donations` | (§3.4) id, user_id, household_id, hub_id, society_id, drive_id null, multiplier (copied at scheduling), items jsonb `[{code, unit, qty, counted, rate, points}]`, photo_keys text[], status (scheduled, picked_up, verified, rejected, handed_over, cancelled), pickup_on, pickup_misses, picked_up_on null, picked_up_by_user_id null, estimated_points, points_awarded null, has_unsuitable, reject_reason null, cancel_reason null, note null, verified_by_user_id null, verified_at null, handoff_id null → ngo_handoffs, data_source, version, created_at, updated_at; index (hub_id, status), (household_id, status), (society_id, pickup_on) where scheduled |
| `community.donation_events` | (§3.4) donation_id, status, by_user_id null, at: the timeline |
| `community.ngos` | (§3.5) id, name, kind, hub_ids uuid[], accepts text[], registration_ref null, verified, status (active, paused), note null, version, created_at |
| `community.ngo_offers` | (§3.5) offer_id (primary key: M6's surplus offer id), hub_id, product_id, qty, unit, kg_tenths, expires_at, status (open, claimed, expired), claimed_by_ngo_id null, claimed_by_user_id null, claimed_at null, handoff_id null |
| `community.meal_pledges` | (§3.5) id, entry_id (unique: M5's `points_donated` entry), user_id, hub_id, meals, served, pledged_at, data_source |
| `community.ngo_handoffs` | (§3.5) id, ngo_id, hub_id, kind (items, food, meals), items_count, kg_tenths, meals, photo_keys text[], people_reached null, handed_at, recorded_by (hub, ngo), recorded_by_user_id, confirmed_at null, confirmed_by_user_id null, note null, created_at. Donations and offers point at their hand-off (`handoff_id`) |
| `community.handoff_meals` | (§3.5) handoff_id, pledge_id, meals: which pledges a meals hand-off served (a pledge may be split) |
| `community.mv_society_impact` | (§3.7) materialised view: society_id, period (`all` or `YYYY-MM`), contributors, items, kg_tenths, meals, refreshed_at; unique (society_id, period) for `REFRESH … CONCURRENTLY` |

`community.societies` gains (§3.6): live_at null, unlock_threshold_pinned (default false), threshold_source (default, model, hub), threshold_computed_at null.

## 5. Events
**Published:**
- `community.captain_assigned`, `community.captain_ended` (§3.2).
- `community.society_live { society_id, hub_id, joined, threshold, live_at }` (§3.6): once per society. M11 tells its households and the hub's managers; M12 marks the date.
- `community.donation_scheduled { donation_id, user_id, household_id, hub_id, society_id, pickup_on, items: [{code, unit, qty}], est_kg_tenths }` (§3.4): M7 plans the pickup on the society's stop; published again when a pickup rolls to the next date.
- `community.donation_cancelled { donation_id, hub_id, society_id, pickup_on, reason }` (§3.4): M7 drops the expected pickup.
- `community.donation_verified { donation_id, user_id, household_id, hub_id, society_id, drive_id, points }` (§3.4): M5 credits the points; M11 notifies (category `kindness`).
- `community.donation_rejected { donation_id, user_id, hub_id, reason }` (§3.4): M11 notifies.
- `community.referral_converted { referral_id, referrer_user_id, referee_user_id, society_id, hub_id, referrer_points, referee_points }` (§3.3): for M12; M11 maps nothing (M5's `points_credited` already notifies).
- `community.handoff_recorded { handoff_id, ngo_id, hub_id, kind, items, kg_tenths, meals }` (§3.5): for M12.

No event carries a name, flat, phone or photo key.

**Consumed:**
- `identity.household_created`, and `identity.household_updated` when `society_id` changed: the unlock check (§3.6);
- `identity.deletion_requested`: open referrals of the user become `void` (§3.3);
- `orders.delivered` (kind `box`): referral conversion (§3.3);
- `logistics.pickup_completed`: donation pickups, with an outcome per donation (§3.4; M7 follow-up);
- `billing.points_donated`: meal pledges (§3.5);
- `hub_ops.surplus_offered` (`offered_to: "ngo"`) and `hub_ops.surplus_settled`: NGO offers (§3.5; follows M6 §3.5);
- `partners.verified` (kind `captain`: automatic appointment, §3.2);
- `partners.suspended` (ends the appointment, §3.2).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `recompute_unlock_thresholds` | Monday 02:10 IST (GitHub Actions) | §3.6: M10's stop cost, or the current threshold when M10 is down; skips pinned and live societies; may make a society live |
| `refresh_impact_counters` | Hourly at :25 (cron-job.org) | §3.7: `REFRESH MATERIALIZED VIEW CONCURRENTLY community.mv_society_impact` |
| `sweep_missed_pickups` | Daily 22:30 IST | §3.4: today's scheduled donations no stop reported count as `not_ready` |
| `expire_referrals` | Daily 03:10 IST | §3.3: `joined` referrals older than 90 days become `void` |

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `societies:write` | hub_manager (hub), admin |
| `captains:read` · `captains:write` | hub_manager (the society's hub), admin (§3.2) |
| `referrals:read` · `referrals:claim` | customer (own) (§3.3) |
| `point_rates:write` | admin (§3.4) |
| `drives:write` | hub_manager (hub and society drives of their hub), admin (any) (§3.4) |
| `donations:create` · `donations:read` · `donations:cancel` | customer (own household) (§3.4) |
| `donations:review` · `donations:verify` | hub_manager (hub), admin (§3.4) |
| `ngos:read` | hub_manager (hub), admin, ngo_coordinator (own NGO) (§3.5) |
| `ngos:write` | admin (§3.5) |
| `ngo_offers:read` | ngo_coordinator (an NGO serving the hub), hub_manager (hub), admin (§3.5) |
| `ngo_offers:claim` · `ngo_handoffs:confirm` | ngo_coordinator (ngo) (§3.5) |
| `ngo_handoffs:read` · `ngo_handoffs:write` | ngo_coordinator (ngo), hub_manager (hub), admin (read) (§3.5) |
| `impact:read` | customer (`me`); residents, the society's captain, hub_manager (hub), admin (`society`) (§3.7) |

Captains have no M9 permission beyond a resident's: their society's unlock, drives and impact. The donations they collect at the door reach them through M7's flat checklist (`community.api.pickups_for`, §3.8).

## 8. Screens
**Mobile:** Society (unlock bar and celebration, captain card, share with code, impact card), Kindness (gullak, drives, schedule donation, history, "donate a meal" from M5), Impact (me and society), onboarding's referral claim. **Console:** Societies map editor (terra-draw) with threshold pinning, society requests, Drives, Point rates, NGOs, Donation verification, NGO offers and Hand-offs (also the NGO coordinator's console), the hub impact page (M12). **Public (console app):** `/s/<society>` referral landing page (M12 §3.7).

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
- Referrals (§3.3):
  - Claiming refuses the caller's own code, a code of the same household, a second claim, and a claim after 14 days or after a delivered box.
  - A delivered box converts once: a redelivered `orders.delivered` credits no second time (both M5 keys), and an add-on or tiffin converts nothing.
  - The eleventh conversion in a month pays the referee and not the referrer.
  - A deletion request voids open referrals.
  - No response lists a neighbour's name, flat or phone.
- Donations (§3.4):
  - Only the next two open delivery dates are accepted, and they close at 22:00 the day before.
  - Limits apply: three open donations, `max_qty`, and 0.5 kg steps.
  - Drive multipliers apply only to the drive's codes; the line points add up to `points_awarded`; rounding is half up.
  - Verifying twice, or before pickup, is refused. `donation_verified` is published once, and the points reach M5 once.
  - `not_ready` rolls the pickup once and then cancels it; the sweep catches pickups that no stop reported.
  - Photo keys must be the caller's `donation_photo` uploads; customers never get photo links.
- NGOs and hand-offs (§3.5):
  - Two concurrent claims of one offer: one wins, and `settle_surplus` is called once.
  - An expired offer cannot be claimed.
  - A donation can be in only one hand-off.
  - Meals served never exceed meals pledged, oldest pledge first, and a redelivered `points_donated` counts once.
  - An NGO coordinator sees only their NGO and never a household, flat or name.
- Unlock (§3.6):
  - The household that reaches the threshold makes the society live once, and `society_live` is published once.
  - A live society stays live when households leave.
  - The job never changes a pinned threshold, keeps the threshold when M10 is down, and makes a society live when the new threshold is at or below `joined`.
  - The first captain moves a `prospect` society to `unlocking`.
  - `box_fee_paise` is the hub's `pre_live_box_fee_paise` while `live_at` is null, and 0 once it is set, also after a pause and resume.
- Impact (§3.7):
  - Every society number is `null` under 5 contributors, and partner earnings are `null` under 5 partners.
  - The `me` numbers equal the sum of the user's verified donations, credited points and donated meals.
  - A user from another society gets 403.
- Export and erasure (§3.8): the section holds donations, referrals without the neighbour, and pledges. After `anonymise_for_user`, the society requests, the photos and the code are gone, while the impact totals are unchanged.
- E2E (MS-D): schedule donation → pickup on the return leg → verify → points visible → donate a meal → NGO hand-off → impact updated; share link → neighbour claims → first box delivered → both get points; the 20th household makes the society live.

## 10. Dependencies
- **M1:** addresses; `household_for_user` for the captain residency rule and meal pledges; household and deletion events; `delivery_labels` for the verification queue; role grants for `ngo_coordinator:<ngo>`; `is_active`.
- **M3:** `subscriptions.api.delivery_dates(society_id, after, n)` for pickup dates. This is new and follows the existing `delivery_days`.
- **M4:** `orders.delivered` carries `kind` (ADR-0026 §3). The pre-live delivery fee on box orders (§3.6; the follow-up in §11).
- **M5:**
  - `credit_points` for referrals;
  - consumes `donation_verified`;
  - publishes `points_donated`;
  - `kindness_totals(user_id)` is new, for the `me` impact.
- **M6 (§3.5, being refined):** `surplus_offered`; `settle_surplus` with a taker; a `surplus_settled` event (new) so M9 can expire offers taken elsewhere. `hub_ops.api.get_hub` with the hub's `pre_live_box_fee_paise`, for the unlock bar (§3.6; the follow-up in §11).
- **M7 (§3.6):** consumes `donation_scheduled` and `donation_cancelled`. `pickup_completed` must carry each donation's outcome, which is a change. M7 reads `pickups_for` for the captain's flat checklist.
- **M8:** verified captain partners through `partners.api.get_partner` and `profile_card`, and its `partners.verified` and `partners.suspended` events. `partners.api.hub_earnings(hub_id, month) -> { paise, partners }` is new, for the impact card.
- **M10:** `intel_gateway.api.society_stop_cost(society_id)` is new, on `/slots/price`.
- **M0:** the `handoff_photo` upload purpose is new.
- **M11:** event map rows for `society_live`, `donation_verified` and `donation_rejected`. It must not send twice for `points_credited` with `source_type: "donation"`.
- **M12:** reads M9's facts through the §3.8 facade.

## 11. Open questions and decisions
No open questions remain for §3.3–§3.8.

- **Decided by the owner (2026-10-09)** (asked in this section; every recommendation accepted as written, except the boxes before live):
  - ~~Who pays for a donated meal, and who cooks it? (§3.5)~~ Decided as recommended: in the prototype no money moves, and a meal counts as served when a partner NGO records a `meals` hand-off. Before the pilot, the owner and Prof. Kranti Bade set a sponsored cost per meal (about ₹25); M5 then books it as an INR expense at the hand-off, under a new `meal_sponsorship` entry kind (an M5 change made then). The alternative (home kitchens cooking the meals as paid tiffins for the NGO) is not taken in v1.
  - ~~Can households in a society that is not yet live get boxes, and at what delivery fee? (§3.6; M4 §11 deferred this to S9)~~ **Decided, changing the recommendation:** yes. Households in a `prospect` or `unlocking` society can get boxes once the hub assigns delivery days, with a **per-box delivery fee until the society goes live**. The fee is per hub, set by an admin; the proposed default is a flat ₹30 a box, which the owner may change. It is 0 once the society is live, and always for live societies. The rules are in §3.6, the follow-ups below. The society-unlock threshold formula is kept as drafted (§3.6).
  - ~~Referral rewards (§3.3)?~~ Decided: 250 points to each side (two referrals make a meal), the neighbour included; at most 10 rewarded conversions a month; conversion on the first delivered box; a 14-day claim window and a 90-day expiry.
  - ~~A cash referral bonus for captains (handbook 02 lever B11; handbook 09's "referral bonus" on the captain's Earnings screen)?~~ Decided: not in v1, because a captain already earns ₹6 for every bag in her society (§3.3). If it is wanted later, it is a new M8 rate-card basis `household_referred` (e.g. ₹50 at conversion, through `billing.api.accrue_earning`): M9's conversion publishes the referrer's partner id, and M8 pays it. Handbook 09's "referral bonus" on the Earnings screen is dropped for v1.
  - ~~Point rates and multipliers?~~ Decided: the console mock's numbers are the seed: clothes 40/kg, school bag 50 each, books 10, toys 15, utensils 30/kg. Multipliers run from 1.0 to 3.0 (§3.4). Points are only worth something as meals (500 a meal), so a 3 kg bag of clothes (120 points) is about a quarter of a meal.
  - ~~Small-number threshold?~~ Decided: k = 5 for society and partner numbers, on the app and the public page (§3.7; M12 §3.6, §3.7).
  - ~~Rejected items (§3.4)?~~ Decided: not returned. The scheduling screen says that items the hub cannot use go to a recycler.
  - ~~Hand-off photo retention (§3.5)?~~ Decided: 3 years, the same as the audit log, because they are impact proof. Photos never show beneficiaries' faces.
  - ~~NGO partners for the pilot?~~ Decided: Rhythm Thakur and Manish Raje identify two: one food bank (food and meals), and one school or shelter (items).
- **Follow-ups from the boxes before live** (each made in the PR that needs it, reviewed by both owners):
  - **M4** (supersedes M4 §11's "the policy for non-unlocked societies is decided with M9 unlock thresholds in S9"):
    1. **The fee on the order.** A box order's `fees_paise` (the column and `OrderOut` field exist and are 0 today) is the pre-live fee. Add-on and tiffin orders keep 0. `total_paise = subtotal_paise + fees_paise − discount_paise`.
    2. **One rule, one place.** `orders.api.box_fee(session, hub_id, society_id) -> int` returns 0 when `community.api.get_society(…).live_at` is set (§3.8), else `hub_ops.api.get_hub(…).pre_live_box_fee_paise`.
    3. **When it is set.** `create_from_cycle` sets the fee at approval, as it freezes line prices. A later rise in the hub's fee changes only boxes approved after it. The cut-off re-reads `live_at` at the lock and sets the fee to 0 if the society went live in between. It never raises it.
    4. **Charged at cut-off, in the same `box_debit` entry.** `billing.api.debit_for_order(owner, order_id, total_paise, delivery_date)` already debits the order's `total_paise`, which now includes the fee. M5 does not change.
       - *Why not a new `delivery_fee` entry:* one entry keeps one idempotency key (the order id) and one balance check. A wallet that covers the food but not the fee then fails as one payment (`payment_failed`, one shortfall), never a half-paid box. A cancelled or undelivered box needs one refund, and the app's wallet list and M5's per-kind property tests need nothing new.
       - The split between food and fee is already on the order (`subtotal_paise`, `fees_paise`), which is where M12 reads it (`order_facts`, below). A second entry would only repeat it at the cost of a second key and a paid-box-unpaid-fee state.
       - The payment grace (`pay`) debits the same total through the same call.
    5. **Refunded with the box.** A box cancelled before cut-off was never charged, so nothing is refunded. A whole-box refund after the lock (`transition(…, "refunded")`, whose default `refund_paise` is what is left of the paid total) includes the fee.
       - A box missed twice (ADR-0027 §2) refunds the fee with its perishables, since it was never delivered.
       - Shortage refunds (`mark_shortage`) and issue credits refund lines and never touch the fee.
    6. **M3 shows it before approval.** The cycle (M3 §3.1 `CycleOut`) gains `fees_paise` (from `orders.api.box_fee`) and `payable_paise = total_paise + fees_paise`; `total_paise` stays the lines' sum, which is what Budget Lock checks (the fee is not food).
       - `subscriptions.box_ready` and `subscriptions.cutoff_reminder` gain `fees_paise`.
       - The Box screen shows "₹30 delivery until your society goes live" under the total.
       - M3 already calls M4 (`create_from_cycle`), so this adds no new dependency direction.
    7. **Tests.** A pre-live box is debited lines + fee once, and a live society's box is debited lines only. A society that goes live between approval and cut-off is charged no fee. A whole-box refund returns the fee, and a shortage refund does not.
  - **M6:** `hub_ops.hubs` gains `pre_live_box_fee_paise` (integer paise, default 3,000, 0 to 10,000), edited by an admin in the hub's settings (`PATCH /v1/admin/hubs/{id}` with If-Match, audit-logged) and returned by `hub_ops.api.get_hub`. The default is a proposal the owner may change.
  - **M11:** the `subscriptions.box_ready` message (the pre-debit notice) and the cut-off reminder mention the fee when `fees_paise > 0`, e.g. "Your box for Tue 3 Nov is ready: ₹690 + ₹30 delivery. Delivery is free once your society goes live." The `community.society_live` message says delivery is now free.
  - **M12:** the delivery fee is revenue. Unit economics gains a measured `delivery_fee_paise` per box, which adds to the contribution, and the P&L gains a `delivery_fee_revenue` line; both read `fees_paise` from `order_facts` (M12 §3.4, §10).
  - **Handbook:** 07 W10 (boxes before live with a fee; live means free delivery) and 02 §5 (the fee in the contribution formula).
- **Decided by the owner (2026-10-09)** (asked in M8 §11):
  - ~~Does a captain need proof of residence?~~ Decided: a captain must live in the society she serves; no proof upload in the prototype (§3.2).
  - ~~Partner photos on the captain card?~~ Decided: `photo_url` is null for now; an upload purpose comes later (§3.2).
  - ~~Captains before M8's applications exist?~~ Decided: M8's partner core table and seeded verified captains come in S5, so M9's captains and M7's handover work first (§3.2, the seed).
