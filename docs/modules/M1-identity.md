# M1 — Identity & Household

**Status:** Draft v1.1. §3.1 (auth) and §3.2 (household, consents, address) are frozen; §3.3 (data export) frozen on 2026-10-08; the rest is refined at phase start (spec-first).
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P2 / S1
**Postgres schema:** `identity` · **Python package:** `gharbasket.modules.identity`

## 1. Purpose
Who the user is and what household they belong to: phone-OTP login exchange, sessions and devices, roles with scopes (customer, captain, kitchen partner, rider, packer, hub manager, franchise owner, NGO coordinator, admin), the household profile used by Autopilot (size, members, diet, fasting rules, consented health tags, budget), addresses linked to societies, and DPDP consent records. M1 does not verify partner KYC (M8) or manage societies (M9).

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a new customer, I want to sign in with my phone number and OTP so that I don't need a password. | Firebase ID token exchanged for access (15 min) + refresh (30 days) tokens; user created on first login. |
| US-2 | As a customer, I want to set up my household (size, diet, fasting/Jain rules, cooking time, no-cook days) so that Autopilot plans suitable meals. | Household saved; `identity.household_created` published; editable later. |
| US-3 | As a customer, I want to choose my society, tower and flat so that my orders join the society route. | Address links to `society_id`; location stored; unknown society creates a society request (M9). |
| US-4 | As a customer, I want to give or revoke consent for health personalisation so that I control sensitive data. | Consent versioned; health tags hidden from planning when revoked. |
| US-5 | As a person with several roles, I want to switch between Customer and Captain modes so that I use one app. | `/me/roles` lists verified roles; `X-Active-Role` honoured; unverified roles not selectable. |
| US-6 | As a customer, I want to see and sign out my devices so that my account stays secure. | Device list; revoking invalidates its refresh token. |
| US-7 | As a customer, I want to export or delete my data so that my DPDP rights are respected. | Export JSON delivered; delete removes PII and keeps anonymised aggregates. |
| US-8 | As an admin, I want to grant hub-manager roles so that hubs can be operated. | Role created with hub scope; audit-logged. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| POST | `/v1/auth/exchange` | Public (Firebase token) | Not required | Rate-limited per phone/IP |
| POST | `/v1/auth/refresh` | Refresh token | — | Rotation with reuse detection |
| POST | `/v1/auth/logout` | Authenticated | — | Revokes current device |
| GET / PATCH | `/v1/me` | Authenticated | — | Name, locale, senior mode |
| GET | `/v1/me/roles` | Authenticated | — | Roles with scope and status |
| GET / DELETE | `/v1/me/devices`, `/v1/me/devices/{id}` | Authenticated | — | |
| POST | `/v1/households` | customer | Required | Onboarding in one call: household, members, address and consents in one transaction (§3.2). One per owner (v1) |
| GET | `/v1/households/me` | customer | — | The caller's household, or 404 `identity.household_not_found` |
| GET / PATCH | `/v1/households/{id}` | owner, hub_manager (read) | — | `ETag` / `If-Match` on `version` |
| POST / PATCH / DELETE | `/v1/households/{id}/members[/{mid}]` | owner | — | Age band, fasting rules, health tags (consent-gated) |
| GET / PATCH | `/v1/addresses[/{id}]` | owner | — | Society, tower, flat, location. The first address is created by `POST /v1/households` |
| GET / POST | `/v1/consents` | Authenticated | — | Grant or revoke one purpose |
| POST | `/v1/me/data-export` | Authenticated | One per user per IST day | Ask for a copy of your data; 202, built in the background (§3.3) |
| GET | `/v1/me/data-export` | Authenticated | — | Status of the latest export and, when ready, a 5-minute download link; the notification comes after M11 |
| DELETE | `/v1/me` | Authenticated | — | Account deletion flow |
| POST | `/v1/admin/user-roles` | admin | Required | Grant staff roles |

### 3.1 Walking-skeleton contract (frozen for Sprint 0)
Both the backend (Prathamesh) and the mobile/console login screens (Yash) code against exactly these shapes. Changing them requires a PR reviewed by both.

```
POST /v1/auth/exchange        (public, rate-limited)
  request : { "firebase_id_token": "<string>",
              "device": { "platform": "android" | "web", "push_token": "<string> | null" } }
            dev-only (APP_ENV=dev): { "dev_phone": "+919800000001", "dev_name": "Priya",
                                      "device": { ... } }  instead of firebase_id_token
  200     : { "access_token": "<jwt>", "access_expires_in": 900, "refresh_token": "<opaque>",
              "is_new_user": true, "user": UserOut }

GET /v1/me                    (Authorization: Bearer <access_token>)
  200     : UserOut & { "roles": [ { "role": "customer", "scope_type": null,
                                     "scope_id": null, "status": "verified" } ] }

UserOut   : { "id": "<uuidv7>", "phone_e164": "+91…", "name": "<string|null>",
              "locale": "en-IN" | "hi-IN" | "mr-IN", "senior_mode": false }

Errors    : application/problem+json { "type", "title", "status", "detail", "code" }
            codes: "auth.invalid_token" (401), "auth.rate_limited" (429), "auth.dev_login_disabled" (403)
```

Additions (implemented in `feat/M1-auth-api`; additive, the shapes above are unchanged):

```
POST /v1/auth/refresh         body { "refresh_token": "<opaque>" }
  200     : { "access_token", "access_expires_in": 900, "refresh_token" }   (the old refresh token stops working)
POST /v1/auth/logout          (Bearer) 204: this device is signed out
POST /v1/auth/logout-all      (Bearer) 204: every device is signed out

More codes: "auth.refresh_reused" (401, a used refresh token was replayed: the session is ended),
            "auth.account_disabled" (403), "auth.provider_unavailable" (503, Firebase keys unreachable),
            "request.invalid" (422, e.g. both or neither of firebase_id_token / dev_phone).
dev_phone accepts Indian numbers with or without +91 and is normalised to E.164.

Browser (cookie) mode, implemented in `feat/M1-auth-web-cookies`:
  Browsers send the header  X-GB-Client: web  on exchange, refresh and every request that changes
  something. The API then sets httpOnly cookies instead of returning tokens: the JSON fields
  access_token and refresh_token are null.
    __Host-gb_at  access token   Secure; HttpOnly; SameSite=Lax; Path=/;          Max-Age=900
    gb_rt         refresh token  Secure; HttpOnly; SameSite=Lax; Path=/v1/auth;   Max-Age=rest of the session
  Refresh: POST /v1/auth/refresh with {} (the cookie carries the token). Logout clears both cookies.
  CSRF: a cookie-authenticated POST/PATCH/PUT/DELETE without X-GB-Client: web, or from an Origin not
  in WEB_ORIGINS, is 403 "auth.csrf_rejected". A Bearer header always wins over the cookie.
Instant sign-out: logout, logout-all, device sign-out and refresh reuse take effect at once.
Rate limits: exchange 30 per 10 min per address and 10 per 10 min per phone; refresh 300 per minute
  per address; over the limit: 429 "auth.rate_limited" with Retry-After.
```

### 3.2 Household contract (frozen for S1)
Onboarding (screens Hf04 to Hf15) and the backend both build against these shapes. Changing them needs a PR reviewed by both Prathamesh and Yash. Every endpoint below needs `Authorization` (Bearer or the web cookie from §3.1).

**Onboarding is a single call.** The app collects everything on the device and sends it once, with one `Idempotency-Key` per onboarding attempt. The server writes the household, its members, the address and the consents in one transaction, so a retry can never leave half a household or duplicate members. If the user edits the draft after a failed attempt, the app must use a new key; reusing the old key with a different body returns 422 `request.idempotency_mismatch`.

```
POST /v1/households           (customer; Idempotency-Key required)
  request : {
    "society_id": "<uuid>",                     must exist in community.societies (any status)
    "address": { "tower_id": "a", "flat": "A-1203",
                 "location": { "lat": 19.04, "lng": 73.07 } | null },   null when no pin was placed
    "diet": "veg" | "egg" | "nonveg",
    "cuisine_tags": ["maharashtrian"],          up to 5 of: maharashtrian, gujarati, north_indian,
                                                south_indian, bengali
    "weekday_cook_minutes": 30,                 10 to 240
    "weekend_cook_minutes": 60,                 10 to 240
    "no_cook_days": [6],                        0 = Monday … 6 = Sunday (Python date.weekday()), unique
    "budget_paise": 300000 | null,              the monthly Budget Lock the user chose; null = off;
                                                at least 50000 (₹500) when set
    "members": [ MemberIn, … ],                 1 to 12; size is the number of members
    "consents": [ { "purpose": "service_delivery", "version": "v1", "granted": true }, … ]
  }
  201     : HouseholdOut   (+ header ETag: "<version>")

MemberIn  : { "age_band": "under_5" | "5_17" | "18_59" | "60_plus",
              "sex": "f" | "m" | "other" | null,                       optional, default null
              "fasting_rules": { "jain": false,
                                 "fasts": ["shravan_mondays", "ekadashi"],   codes from
                                       catalog.festival_calendar; v1 accepts shravan_mondays,
                                       ekadashi, navratri, tuesdays
                                 "no_onion_garlic_on_fast_days": false },
              "health_tags": ["diabetes"] }        only with health_personalisation granted (below);
                                       v1 codes: diabetes, high_bp, pcos, low_oil, high_protein,
                                       weight_loss

HouseholdOut : { "id", "version": 1, "society_id", "hub_id": "<uuid> | null",
                 "size", "diet", "cuisine_tags", "weekday_cook_minutes", "weekend_cook_minutes",
                 "no_cook_days", "budget_paise", "autopilot_enabled": true, "auto_approve": false,
                 "members": [ MemberOut ], "address": AddressOut, "created_at" }
MemberOut    : MemberIn & { "id" }     health_tags is [] while health_personalisation is not granted
AddressOut   : { "id", "household_id", "society_id", "tower_id", "flat", "location": {lat,lng} | null }

GET /v1/households/me         200 HouseholdOut (+ ETag) | 404 "identity.household_not_found"
GET /v1/households/{id}       owner only (hub_manager read arrives with RLS); 404 for anyone else
PATCH /v1/households/{id}     If-Match: "<version>" required. Any of: diet, cuisine_tags,
                              weekday_cook_minutes, weekend_cook_minutes, no_cook_days, budget_paise,
                              autopilot_enabled, auto_approve. 200 HouseholdOut with the new version.
POST   /v1/households/{id}/members          MemberIn → 201 MemberOut; at most 12 members
PATCH  /v1/households/{id}/members/{mid}    any MemberIn field → 200 MemberOut
                              (fasting_rules is replaced whole; sex: null clears it)
DELETE /v1/households/{id}/members/{mid}    204; the last member cannot be removed
                              Member changes bump the household version and update size; the
                              response carries the household's new ETag. Same rules as onboarding
                              (Jain needs veg, health tags need the consent). No If-Match needed.
GET   /v1/addresses           [AddressOut]   (bare list, at most one address in v1)
PATCH /v1/addresses/{id}      any of society_id, tower_id, flat, location (null removes the pin).
                              Moving to another society also moves the household (society_id, hub_id)
                              in the same transaction; the tower and the pin (the stored one if not
                              resent) must fit the new society. Bumps the household version (ETag).

GET  /v1/consents             [ConsentOut]: one entry per purpose, including never-given ones
POST /v1/consents             { "purpose", "version", "granted": true | false } → 200 ConsentOut
ConsentOut : { "purpose", "version", "granted": bool, "granted_at": "<ts> | null",
               "revoked_at": "<ts> | null" }
```

**Rules**
- **Server-derived values.** `hub_id` is copied from the society and stays null until M6 creates hubs. `size` is always the number of members. `flat` is trimmed and upper-cased, 1 to 20 characters of letters, digits, space, `/` and `-`.
- **Address location.** `location` must fall inside the society's boundary when the society has one. `h3_r9` is computed server-side and never returned.
- **Jain.** A Jain member (`fasting_rules.jain`) requires `diet = "veg"`.
- **Consent purposes.** These follow handbook 13 §DPDP:
  - `service_delivery` and `personalisation` are required for Autopilot; onboarding must grant both.
  - `health_personalisation`, `marketing` and `research_anonymised` are optional opt-ins.
  - The current notice version is `v1` for every purpose. A version other than the current one is refused, so the app has to show the new notice.
- **Granting and revoking.**
  - Granting inserts a row. Revoking sets `revoked_at` on the active row.
  - Revoking `health_personalisation` **erases** the `health_tags` of every member of the caller's household in the same transaction. This is stricter than hiding them, and it is audit-logged.
- **Concurrency.** `GET` responses carry `ETag: "<version>"`, and a `PATCH` on a household needs `If-Match` with that value. This is the same optimistic-locking convention as boxes (handbook 10).

**Error codes** (application/problem+json, §3.1 format)

| Status | Code | When |
|---|---|---|
| 404 | `identity.household_not_found` | `GET /v1/households/me` before onboarding, or a household that isn't the caller's |
| 409 | `identity.household_exists` | The owner already has a household (a different key). The problem carries `"household_id"` so the app can carry on |
| 422 | `identity.society_unknown` | `society_id` doesn't exist |
| 422 | `identity.tower_unknown` | `tower_id` isn't one of the society's towers |
| 422 | `identity.address_outside_society` | `location` is outside the society boundary |
| 422 | `identity.diet_conflict` | A Jain member with a diet other than `veg` |
| 422 | `identity.consent_required` | `service_delivery` or `personalisation` not granted at onboarding |
| 409 | `identity.consent_version_stale` | `version` isn't the current one; the problem carries `"current_version"` |
| 403 | `identity.health_consent_required` | `health_tags` sent while `health_personalisation` isn't granted (in this request or already) |
| 422 | `identity.household_needs_member` | Deleting the last member |
| 422 | `identity.household_full` | Adding a 13th member |
| 404 | `identity.member_not_found` | A member id that isn't in the caller's household |
| 404 | `identity.address_not_found` | An address id that isn't the caller's |
| 428 | `request.precondition_required` | `PATCH` without `If-Match` |
| 412 | `identity.version_conflict` | `If-Match` isn't the current version; the app reloads and retries |
| 400 | `request.idempotency_key_required` | `POST /v1/households` without `Idempotency-Key` (M0) |
| 422 | `request.idempotency_mismatch` | Same key, different body (M0) |
| 422 | `request.invalid` | Shape or range validation (M0) |

### 3.3 Data export contract (frozen on 2026-10-08)
The DPDP right of access (handbook 13 §13.6). The Profile screen's "Download my data" and the backend build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
DataExport : { "id", "status": "queued" | "building" | "ready" | "failed" | "expired",
               "requested_at", "ready_at": "…" | null, "expires_at": "…" | null,   file kept 7 days
               "ready_within_hours": 24,
               "download": { "url": "<signed GET>", "expires_at": "…", "size_bytes": 48211 } | null }
                                       only while ready; a fresh 5-minute link on every GET

POST /v1/me/data-export    no body; no Idempotency-Key: the day is the key
  202 DataExport           one export per user per IST day: asking again the same day answers that
                           day's export (a failed one is queued again); publishes
                           identity.data_export_requested { export_id, user_id } for the build job
GET  /v1/me/data-export    200 DataExport (the latest) | 404 "identity.export_not_found"
                           the app polls this (every 10 s while the screen is open)
```

**What the file holds** (contents, including leaving out ML predictions, confirmed by the owner on 2026-10-08). One JSON document, `{ "format": "gharbasket-export/v1", "generated_at", "user_id", "sections": { … } }`. Each module returns its own section from `export_for_user(session, user_id) -> dict` in its `api.py`, read-only; M1 only puts them together and stores the file (M0 purpose `data_export`, private bucket):
- `identity`: the user, roles, devices (platform and dates, no tokens), the household with members (diet, fasting rules, consented health tags), the address with its pin, the consent history.
- `subscriptions`: the subscription, every cycle with its meal plan and lines, Budget Locks, edit and pantry feedback.
- `orders`: orders with lines and timelines, and issues (the photos are counted, not included).
- `billing`: wallet entries and top-ups (Razorpay ids only; no card or UPI details ever reach us).
- Added by each module when it ships: notifications (M11), assistant conversations (M13), donations and referrals (M9), the partner application with masked KYC references (M8).
- Not included: tokens and secrets, uploaded files, the audit log, other people's data (captains, riders), and derived ML data (`intel.predictions`).

**Delivery.** Until M11 ships, polling `GET` is the only path. After M11 the build job also sends a "your data is ready" notification that opens this screen; the notification never carries the link. Every request and every download link minted is audit-logged. `DELETE /v1/me` deletes pending exports and their files.

| Status | Code | When |
|---|---|---|
| 404 | `identity.export_not_found` | `GET` before any export was asked for |
| 429 | `identity.export_rate_limited` | More than 5 `POST`s in a day (the same day's export is still answered by `GET`) |

| Mock today (`mocks/profile.js`) | Contract |
|---|---|
| `GET /v1/me/data-export` starts the export: 202 `{ status: "queued", ready_within_hours: 24 }` | `POST` starts it (202 `DataExport`, which keeps `status` and `ready_within_hours`); `GET` polls it and carries `download` when ready |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `identity.users` | id (uuidv7), phone_e164 (unique, E.164 check), name, locale, senior_mode, status, data_source, created_at, updated_at |
| `identity.user_roles` | id, user_id, role, scope_type, scope_id, status, verified_at; unique (user, role, scope) with NULLS NOT DISTINCT |
| `identity.sessions` | one per signed-in device or browser (the "devices" list): id, user_id, platform, push_token, created_at, last_seen_at, expires_at (30 days, never extended), revoked_at, revoke_reason |
| `identity.refresh_tokens` | id, session_id, token_hash (HMAC-SHA256 with a pepper; the token is never stored), parent_id (rotation chain), created_at, used_at, revoked_at |
| `identity.households` | id (uuidv7), owner_user_id (unique in v1), hub_id (nullable, no FK until M6), society_id → community.societies, size, diet, cuisine_tags[], weekday_cook_minutes, weekend_cook_minutes, no_cook_days smallint[], budget_paise BIGINT null, autopilot_enabled, auto_approve, data_source, version, created_at, updated_at |
| `identity.household_members` | id, household_id, age_band, sex null, health_tags[] (empty unless `health_personalisation` is granted), fasting_rules jsonb `{jain, fasts[], no_onion_garlic_on_fast_days}`, created_at |
| `identity.addresses` | id, household_id, society_id → community.societies, tower_id, flat, location geography(Point, 4326) null, h3_r9 null, created_at, updated_at |
| `identity.consents` | id, user_id, purpose, version, granted_at, revoked_at; at most one active row per (user, purpose) (partial unique index where revoked_at is null) |
| `identity.data_exports` | id, user_id, requested_on (IST date; unique with user_id), status (queued, building, ready, failed, expired), object_key (M0 `data_export`), size_bytes, attempts, error, requested_at, ready_at, expires_at |

## 5. Events
**Published:** `identity.household_created`, `identity.household_updated`, `identity.user_deleted`, `identity.data_export_requested` (`{export_id, user_id}`, consumed by M1's own build job; M11 later).
- `identity.household_created` `{household_id, owner_user_id, society_id, hub_id, size, budget_paise, data_source}`. M3 uses `budget_paise` to open the first Budget Lock; M9 recounts the society's unlock bar.
- `identity.household_updated` `{household_id, version, changed: [field names]}`.
- No event carries health tags, fasting rules or flat numbers (DPDP: consumers read what they are allowed to through `identity.api`).
**Consumed:** `partners.verified` (activate partner role), `partners.suspended`.

## 6. Jobs
| Job | Trigger | Notes |
|---|---|---|
| `build_data_export` | Outbox consumer of `identity.data_export_requested` | Gathers each module's `export_for_user`, writes one JSON file to the private bucket (§3.3); 3 attempts, then `failed`; notifies the user once M11 exists |
| `expire_data_exports` | Daily | Marks exports past `expires_at` (7 days) `expired`; M0's `sweep_uploads` deletes the file (`delete_after`) |
| `purge_deleted_users` | Daily | Completes deletion after grace period |

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `households:write` | customer (own) |
| `households:read` | customer (own), hub_manager (hub), admin |
| `roles:grant` | admin |

## 8. Screens
**Mobile:** Welcome/Language, Login OTP, Society picker, Household setup stepper, Consent, Profile/Settings, Devices, Data export/delete, Role switcher. **Console:** User lookup, staff role management.

## 9. Tests required
- Unit: token issue/rotation/reuse detection; permission checks; consent gating of health tags.
- Refresh rotation rules (implemented in `identity/service.py`): each refresh uses its token and issues a child; re-presenting a used token within 2 minutes while its child is unused (lost response, two tabs) revokes the child and issues a fresh one; any other reuse revokes the whole session (`auth.refresh_reused`).
- Integration: exchange with a mocked Firebase verifier; household create publishes outbox event; RLS on hub-scoped reads.
- Household contract (§3.2):
  - A failure part-way through `POST /v1/households` leaves no rows.
  - The same key replays; a different key gets 409 `identity.household_exists`.
  - Health tags without consent get 403.
  - Revoking `health_personalisation` erases tags on every member.
  - A stale `If-Match` gets 412; a missing one gets 428.
  - A Jain member with a non-veg diet gets 422.
  - An address outside the polygon gets 422.
- E2E: onboarding under 2 minutes on a mid-range device profile; role switch.
- Security: OTP exchange rate limits; refresh token theft simulation.

## 10. Dependencies
M0 (platform). Reads society existence via `community.api` (M9). Partner verification arrives via `partners.verified` events.

## 11. Open questions
- ~~One household per user in v1?~~ Decided: one household and one address per owner in v1 (§3.2). Multiple households (e.g. parents' home) stay open for later.
- ~~Minimum household fields before the first plan?~~ Decided: everything in `POST /v1/households` except `budget_paise`, `sex`, `health_tags` and the optional consents.
- Localised tower labels and fasting names: served by M2/M9 later, or kept as client copy?
