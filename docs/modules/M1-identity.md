# M1 — Identity & Household

**Status:** Draft v1.2. §3.1 (auth) and §3.2 (household, consents, address) are frozen; §3.3 (data export) frozen on 2026-10-08; §3.4 (shortage preference) and §3.5 (account deletion) drafted 2026-10-09, frozen on approval; §3.6 (staff lookup, revoke, the hub's staff, pending grants) drafted 2026-10-25, frozen on approval, built (as built, below); the rest is refined at phase start (spec-first).
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P2 / S1
**Postgres schema:** `identity` · **Python package:** `gharbasket.modules.identity`

## 1. Purpose
Who the user is and what household they belong to: phone-OTP login exchange, sessions and devices, roles with scopes (customer, captain, kitchen partner, rider, packer, hub manager, franchise owner, NGO coordinator, catalog editor, admin), the household profile used by Autopilot (size, members, diet, fasting rules, consented health tags, budget), addresses linked to societies, and DPDP consent records. M1 does not verify partner KYC (M8) or manage societies (M9).

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
| PUT | `/v1/me/push-token` | Authenticated | — | `{push_token: string \| null}` → 204: sets or clears the FCM token of the calling session; contract in M11 §3.1 |
| POST | `/v1/households` | customer | Required | Onboarding in one call: household, members, address and consents in one transaction (§3.2). One per owner (v1) |
| GET | `/v1/households/me` | customer | — | The caller's household, or 404 `identity.household_not_found` |
| GET / PATCH | `/v1/households/{id}` | owner, hub_manager (read) | — | `ETag` / `If-Match` on `version` |
| POST / PATCH / DELETE | `/v1/households/{id}/members[/{mid}]` | owner | — | Age band, fasting rules, health tags (consent-gated) |
| GET / PATCH | `/v1/addresses[/{id}]` | owner | — | Society, tower, flat, location. The first address is created by `POST /v1/households` |
| GET / POST | `/v1/consents` | Authenticated | — | Grant or revoke one purpose |
| POST | `/v1/me/data-export` | Authenticated | One per user per IST day | Ask for a copy of your data; 202, built in the background (§3.3) |
| GET | `/v1/me/data-export` | Authenticated | — | Status of the latest export and, when ready, a 5-minute download link; the notification comes after M11 |
| DELETE | `/v1/me` | Authenticated (not staff), OTP within the last 5 minutes | Naturally idempotent | Schedules the account's deletion after a 30-day grace period; signing in again cancels it once the app has asked "Keep my account?" (§3.5) |
| POST | `/v1/admin/user-roles` | admin | Required | Grant staff roles; a phone with no account gets a pending grant (§3.6) |
| GET | `/v1/admin/users?phone=\|q=\|staff=\|pending=` | admin | — | User lookup and the staff list; audit-logged (§3.6) |
| DELETE | `/v1/admin/user-roles/{id}` | admin | — | Revoke a staff grant or cancel a pending one; audit-logged (§3.6) |
| GET | `/v1/hubs/{id}/staff` | hub_manager, franchise_owner (hub), admin | — | The hub's role grants: names and masked phones (§3.6) |

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
  - `health_personalisation`, `marketing`, `research_anonymised` and `whatsapp` (updates on WhatsApp, M11 §3.1; added 2026-10-08) are optional opt-ins.
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
- `notifications` (M11): inbox messages with event type, category, English text, dates and delivery status per channel (no push tokens).
- Added by each module when it ships: assistant conversations (M13), donations and referrals (M9), the partner application with masked KYC references (M8).
- Not included: tokens and secrets, uploaded files, the audit log, other people's data (captains, riders), and derived ML data (`intel.predictions`).

**Delivery.** Until M11 ships, polling `GET` is the only path. After M11 the build job also publishes `identity.data_export_ready { export_id, user_id }`, which M11 turns into a "your data is ready" notification that opens Profile; the notification never carries the link. Every request and every download link minted is audit-logged. `DELETE /v1/me` deletes pending exports and their files.

| Status | Code | When |
|---|---|---|
| 404 | `identity.export_not_found` | `GET` before any export was asked for |
| 429 | `identity.export_rate_limited` | More than 5 `POST`s in a day (the same day's export is still answered by `GET`) |

| Mock today (`mocks/profile.js`) | Contract |
|---|---|
| `GET /v1/me/data-export` starts the export: 202 `{ status: "queued", ready_within_hours: 24 }` | `POST` starts it (202 `DataExport`, which keeps `status` and `ready_within_hours`); `GET` polls it and carries `download` when ready |

### 3.4 Shortage preference contract (drafted 2026-10-09, frozen on approval)
This is the owner's decision of 2026-10-09 (ADR-0026, decision 4): a short item is replaced by default with something similar, and a household can choose "never substitute, refund". The Settings screen, M6 packing and the backend build against this. It is an addition to §3.2, so the §3.2 shapes stay valid.

```
HouseholdOut  gains  "shortage_preference": "substitute" | "refund"     default "substitute"
POST /v1/households   optional "shortage_preference" (default "substitute")
PATCH /v1/households/{id}   If-Match as for every household field; { "shortage_preference": "refund" }
                            200 HouseholdOut, version + 1; identity.household_updated with
                            changed: ["shortage_preference"]

identity.api.shortage_preferences(session, household_ids) -> dict[household_id, "substitute" | "refund"]
                            batch read for M6's packing queue and M4's mark_shortage; an unknown id
                            reads as "substitute"
```

**Rules**
- **Where it is stored.** A column on `identity.households`: `shortage_preference text NOT NULL DEFAULT 'substitute'`, with a check that it is one of the two values. It is a household setting, like `auto_approve`, not a consent. It is not personal data that needs a purpose.
- **Who reads it.**
  - M6 does not offer the substitute picker for a household that chose `refund`. Its packing task carries `"shortage_preference"`, and the packer's shortage sheet then shows only "Refund".
  - M4's `mark_shortage` enforces the same rule as a second check. A substitute for such a household is refused with 422 `orders.substitute_invalid` and `reason: "refund_preferred"`, so a stale packer screen can never substitute.
- **Changing it.** A change applies to packing from the moment it is saved. Packing reads the preference when the shortage is recorded, not when the task is built. A change after a bag is packed changes nothing for that bag.
- **In the data export.** The field is part of the household in the `identity` section (§3.3).

**Screen (mobile, Settings → "If something is out of stock").**
- Two radio choices: "Send a similar item (same price or cheaper; we refund any difference)" and "Don't replace it, refund me".
- The app has no mock for this yet. It is a new field on the household mock and the `Household` typedef.

### 3.5 Account deletion contract (drafted 2026-10-09, frozen on approval)
This is the DPDP right to erasure (handbook 13 §13.6). The Profile screen's "Delete my account" and the backend build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
DELETE /v1/me              no body; no Idempotency-Key (DELETE is naturally idempotent)
  202 : { "status": "scheduled", "requested_at": "…", "purge_on": "2026-11-08",   IST date
          "wallet_refund": { "refund_id": "<uuid>", "amount_paise": 41000 } | null }
                           repeating it (e.g. a retried request) answers the same body
  409 "identity.staff_account"    the caller holds admin, hub_manager, franchise_owner,
                                  ngo_coordinator or catalog_editor: another admin removes
                                  those roles first
  403 "identity.fresh_otp_required"   the caller's session was not signed in with an OTP in the
                                  last 5 minutes (below); carries "max_age_s": 300

POST /v1/auth/exchange     (§3.1, additive) gains "keep_account": false (optional). For an account
                           whose deletion is scheduled:
                           without keep_account: true → 409 "identity.deletion_pending" with
                             "purge_on"; nothing is signed in, no tokens are issued; the app shows
                             "Keep my account?"
                           with keep_account: true (the same Firebase ID token, sent again after the
                             customer taps "Keep my account") → signs in, cancels the deletion and
                             answers "deletion_cancelled": true (the field is false otherwise); the
                             app then shows "Welcome back: your account is kept. Restart your
                             weekly box from Week."
```

**Fresh OTP for deletion** (owner, 2026-10-09). Asking for deletion needs a sign-in with an OTP within the last 5 minutes, not just a valid session. The server checks the **session's `auth_time`**: `identity.sessions` gains `auth_time`, set at `POST /v1/auth/exchange` from the Firebase ID token's `auth_time` claim (when the OTP was entered; the exchange already requires the claim), or the exchange time for the dev login. Refreshes keep it, so it never moves without a new OTP. `DELETE /v1/me` reads the session of the access token's `sid` claim and needs `now − auth_time ≤ 300 s`. The session's `created_at` is not used: the Firebase SDK can mint a new ID token without an OTP, so a new session does not prove one. The app's flow: "Delete my account" → the phone OTP again (Firebase) → `POST /v1/auth/exchange` → `DELETE /v1/me` with the new token. The deletion then signs out every session anyway (step 2 below).

**What happens when deletion is requested.** All of these happen in the `DELETE` transaction unless the step says otherwise:
1. **The account is marked.** `users.status` becomes `deleting`, and an `identity.deletions` row records `requested_at` and `purge_on`. The grace period is 30 days, so `purge_on` = the IST date of the request + 30 days. This matches the app copy and handbook 13 ("purged within 30 days" of the end of the grace period).
2. **Every session is signed out.** All sessions are revoked with `revoke_reason: "account_deleted"`, and access tokens go on the deny-list, as for logout-all. The calling device is signed out too. The app already returns to `/login`.
3. **Pending data exports are deleted.** Each export's file is deleted now (`delete_after` = now), and its row is deleted.
4. **Other modules react.** `identity.deletion_requested { user_id, household_id, purge_on }` is published, and each consumer does its part with no network calls:
   - **M3** ends the subscription (`status: ended`). It skips every cycle that is still before its cut-off, which cancels its `approved` order through M4 as a skip does. A `payment_failed` order is cancelled. Orders that are already paid (`locked` and later) are delivered as normal, because the address stays until the purge.
   - **M5** (called directly, not as a consumer) turns the whole refundable wallet balance into one refund to the original payment source (`reason: "account_deleted"`, M5 §3.3). The balance that cannot be refunded to a source is moved to `unclaimed_balances` (M5 §3.3). The `send_refunds` job makes the Razorpay calls. `wallet_refund` in the answer is that refund. The API creates it in the same transaction through `billing.api.close_wallet`, so the answer can show it.
   - **M8** marks every partner record of the user `left` and publishes `partners.suspended` with `reason_code: "left"` (M8 §3.3, once M8 ships): future tiffin offers are released, M1 suspends the hub roles, and **M9** ends a captain assignment (`community.captain_ended`), after which M1 removes `captain:<society>`. A partner who left is still paid what she earned (M5 §3.5).
   - **M11** sends nothing more to the user. It checks `identity.api.is_active(user_id)` at send time.
5. **It is audit-logged.** The action is `identity.deletion_requested`, with no before/after personal data.

**Cancelling** (owner, 2026-10-09: signing in cancels, after the app asks "Keep my account?"). Signing in again before the purge (`POST /v1/auth/exchange`, Firebase OTP, with `keep_account: true` after the question) sets `status: active` and `deletions.cancelled_at`, and publishes `identity.deletion_cancelled { user_id }`. If the customer does not confirm, the app stays on the login screen, nothing is signed in and the deletion stays scheduled. Nothing that already happened is undone:
- the subscription stays ended, and the customer restarts it;
- the refund to source goes ahead;
- the captain assignment stays ended.

There is no separate "cancel deletion" endpoint: signing in, confirmed on the "Keep my account?" screen, is the proof of the phone that the copy asks for.

**The purge** (`purge_deleted_users`, daily 03:33 IST; deletions whose `purge_on` ≤ today and that are not cancelled, oldest first, 50 per run, each in its own transaction):
1. **Preconditions.** All of these are read through each module's facade:
   - no order of the household is in a non-final status (`orders.api.has_open_orders`);
   - no order issue is `open`;
   - the wallet balance is 0 and no refund-to-source part is still `pending` or `sent` (`billing.api.wallet_closed`);
   - the partner payable balance is 0, which means the last weekly payout went out (M5 §3.5).
   A deletion that is not ready stays scheduled. The job records it as `blocked` with the reasons in its summary and tries again the next night. A deletion blocked for more than 15 days past `purge_on` is listed in the job's summary as `overdue` for an admin.
2. **Each module erases its own data.** It calls `delete_for_user(session, user_id, household_id)` and/or `anonymise_for_user(session, user_id, household_id)` from its `api.py`, in the fixed order below. Each returns `{ table: rows }` counts for the tombstone. They are registered like the export sections (`ERASERS` next to `SECTIONS` in `identity/jobs.py`); a module adds its own when it ships.
3. **M1 deletes its own rows last.** That is `users`, `user_roles`, `sessions`, `refresh_tokens`, `households` (with members and the address, by cascade), `consents` and `data_exports`. It then deletes every stored object under `*/<user_id>/` through `platform.files`.
4. **A tombstone is kept.** The `identity.deletions` row keeps `user_id`, `requested_at`, `purge_on`, `purged_at` and the counts, with no phone, name or household fields. The user id then refers to nobody: the money and order records that are kept carry an id that no longer leads to a person.
5. **The purge is announced.** `identity.user_deleted { user_id, household_id }` (already listed in §5) is published for consumers that keep caches: M12 deletes the household's `fact_households` and `fact_cycles` rows and keeps its order facts, as M4 keeps the orders (M12 §4).

Signing up again with the same phone after the purge creates a new user with a new id.

**As built (2026-10-10).** Read with the steps above:
- **The wallet.** `billing.api.close_wallet` and `wallet_closed` come with M5 §3.3 (PR #160). Until then `wallet_refund` is always `null`, and the purge's wallet precondition is a zero wallet balance (`billing.api.balance`), reported as `wallet_not_closed`. The partner payable check comes with M5 §3.5. Both are a few lines in `identity/jobs.py` once they merge.
- **One account at a time.** The job runs in one transaction that holds its advisory lock, so each account is purged inside its own savepoint. One account's failure rolls back only that account. The job's summary lists it under `failed` (with the error's type), and the next night tries again. `blocked` maps each user id to its reasons, and `overdue` lists the user ids blocked for more than 15 days.
- **Files.** The purge makes every upload under `*/<user_id>/` due at once (`platform.files.expire_for_user`). The same night's `sweep_uploads` (04:13 IST) deletes the objects and their rows, so the purge itself makes no storage call.
- **Erasers.** `ERASERS` in `identity/jobs.py` is an ordered list of `(module, eraser)`, in the matrix order below: M11, M10, M9, M6, M7, M3, M4, M5, the platform (idempotency keys and uploads) and M1. M13 and M12 hold no personal data yet. M7's eraser (`logistics.api.anonymise_for_user`) deletes the rider's GPS pings, makes the household's door photos due at once and clears their OTP records and notes, and nulls user ids on handovers, deliveries, field events, routes and vehicles; the export has a `logistics` section (the household's deliveries without photos or codes, and a rider's routes, handovers and pings of the last 90 days). M9's eraser deletes only the society requests for now; #276's fuller one replaces it. M8 (the applications chain) adds its own when it merges.
- **Which accounts a run takes.** `deletions.last_attempt_on` records the IST day of the last attempt. A run takes accounts not yet tried that day, never-tried ones first, so 50 blocked or failing accounts cannot starve a newer one. The day is that of `min(scheduled_for, now)`, so a run dated in the future never purges early.
- **Webhooks.** M11 deletes the WhatsApp status updates of the user's deliveries, which carry the phone. M5 keeps only an allowlist of fields on each entity of the user's stored Razorpay webhooks: ids, amounts, currency, status, method, refund fields and dates.
- **"Keep my account" against the purge.** A sign-in on a deleting account locks its deletion row. If the purge got there first, the sign-in gets 409 `identity.account_deleted`.
- **M6, not in the matrix.** Packing tasks copy the tower and flat from the address. `hub_ops.api.anonymise_for_user` clears them on the household's tasks and drops the user id where the user packed. It runs between M7 and M3. This is a gap in the matrix, flagged for review.
- **M3 on the request.** `subscriptions.api` consumes `identity.deletion_requested`. It ends the subscription and skips every `draft`, `awaiting_approval` or `approved` week before its cut-off, and every `payment_failed` week, through `orders.api.cancel_from_cycle`. It publishes no `box_skipped`, so nobody is told.

**Facade additions** (`identity/api.py`):
- `is_active(session, user_id) -> bool`: false while the account is `deleting` and after the purge. M11 checks it before sending.
- `contact_for_household(session, household_id) -> { name, phone_e164 } | None`: for M4's issue triage, where the hub calls the customer back (M4 §3.6). The caller audit-logs each read.
- `shortage_preferences` (§3.4).

**What each module does at the purge** (deleted = rows removed; anonymised = rows kept for accounting or aggregates with every personal field removed):

| Order | Module | Deleted | Kept, anonymised |
|---|---|---|---|
| 1 | M13 assistant | Conversations and messages | — |
| 2 | M11 notifications | Messages, deliveries, preferences, quiet hours | — |
| 3 | M10 intel (`intel_gateway`) | `predictions` and `feature_snapshots` whose subject is the household or the user | Model metrics (aggregates) |
| 4 | M9 community | Society requests (name, pin, note); donation photos | Donations and referrals: `user_id` kept, `items` note and pickup address details cleared (the counts feed the impact numbers) |
| 5 | M8 partners | Application, KYC documents and their files, training rows, ratings' comments | `partners.partners` row (payout history points at it) with `status: deleted` and every bank and KYC field cleared |
| 6 | M7 logistics | Delivery proofs (photos, OTP records) for the household; GPS pings of a rider | Route stops (no flat: they point at the order) |
| 7 | M3 subscriptions | Subscription, cycles with their lines and meal plans, Budget Locks, edit and pantry feedback | — (forecasting learns from orders, which are kept) |
| 8 | M4 orders | Issue photos (files) and the issue `note` | Orders, lines and `order_events` (they back ledger entries); issues with items and amounts; `order_events.actor_user_id` set to null where the user acted as a partner |
| 9 | M5 billing | — | The ledger is append-only: accounts, journal entries and postings stay as they are; top-ups (`payments`) stay for reconciliation. Kindness points still held are expired (`points_expired`). In stored Razorpay webhook payloads of the user's payments, `contact`, `email` and `vpa` are replaced by `"redacted"` |
| 10 | M0 platform | Idempotency keys of the user (stored responses); uploads under `*/<user_id>/` | Audit log rows (3-year retention; `actor_user_id` stays, and audit summaries never hold personal data) |
| 11 | M1 identity | Everything in step 3 above | The `identity.deletions` tombstone |

**Error codes**

| Status | Code | When |
|---|---|---|
| 409 | `identity.staff_account` | A staff role is still active; the problem carries `"roles"` |
| 401 | `auth.invalid_token` | Any call with a token from before the deletion request (§3.1) |
| 403 | `identity.fresh_otp_required` | `DELETE /v1/me` from a session whose OTP sign-in (`auth_time`) is more than 5 minutes old (`max_age_s`) |
| 409 | `identity.deletion_pending` | `POST /v1/auth/exchange` for an account whose deletion is scheduled, without `keep_account: true` (`purge_on`) |

**For the mobile mock** (`mocks/profile.js` on `feat/mobile-profile` and later → contract)

| Mock today | Contract |
|---|---|
| `DELETE /v1/me` → 202 `{ status: "scheduled", purge_on }` | Same, plus `requested_at` and `wallet_refund` (show "₹410 is on its way back to your UPI" when it is set) |
| `leave(false)` signs out this device only | The server has signed out every device; the app only clears its local state and goes to `/login` |
| Copy: "You have 30 days to change your mind by signing in again." | Matches: grace period of 30 days. Signing in during it gets 409 `identity.deletion_pending`: show "Keep my account?" (with `purge_on`), and on "Keep" repeat the exchange with `keep_account: true`; its `deletion_cancelled: true` shows the welcome-back note |
| "Delete my account" calls `DELETE /v1/me` with the current session | Ask for the phone OTP again first and exchange it, then call `DELETE /v1/me` within 5 minutes; a 403 `identity.fresh_otp_required` sends the customer back to the OTP step |
| Copy: "your wallet balance is refunded to your UPI" | True for the refundable part; balance that cannot go back to a source (older than Razorpay's refund window) is kept for the customer to claim through support (M5 §3.3) |

### 3.6 Staff lookup, revoke and the hub's staff (drafted 2026-10-25, frozen on approval)
The console's Users page (admin: find a person by number, list the staff, give and take away roles), its Delivery log (a number turned into a user id) and the franchise Staff page build against this. Today they call these paths as `@proposal`s (`mocks/identity.js`, `mocks/team.js`). Same sign-in rules as §3.1. These reads show personal data to staff, so each is limited to what the role needs: the admin lookup is audit-logged, and the hub list masks phones.

```
StaffGrant : { "id": "<uuid>",                         the user_roles row; DELETE takes it
               "role", "scope_type": "hub" | "society" | "ngo" | null, "scope_id",
               "scope_label": "Kharghar hub" | "Sunrise Towers" | null,
               "status": "applied" | "kyc_pending" | "verified" | "suspended",
               "verified_at": "…" | null }
AdminUser  : { "id", "name" | null, "phone_e164", "locale", "status": "active" | "blocked" | "deleting",
               "last_seen_at": "…" | null,              the latest session's
               "roles": [ StaffGrant ] }               customer included
PendingGrant : { "id", "phone_masked": "******1011", "role", "scope_type", "scope_id",
                 "scope_label", "status": "pending", "granted_at", "expires_at" }

GET /v1/admin/users?phone=  |  ?q=  |  ?staff=true  |  ?pending=true      admin (users:read)
    exactly one filter; cursor, limit ≤ 50 (default 20); Cache-Control: no-store
    phone    an Indian mobile typed any usual way (10 digits, +91, a leading 0): 0 or 1 item
    q        2 to 60 characters of a name (trigram), best match first
    staff    everyone holding a staff role (admin, hub_manager, franchise_owner, ngo_coordinator,
             catalog_editor), by name
    pending  Page[PendingGrant], newest first (staff roles given to a number that has not signed in)
  200 Page[AdminUser] (or Page[PendingGrant])
  every call is audit-logged (identity.users_looked_up: the admin, the filter kind and the user
  ids returned; never the typed number or name)

POST /v1/admin/user-roles     (§3, unchanged for a phone with an account: 201 RoleGrantOut)
    a phone with no account and a staff role other than admin: 202 PendingGrant; audit-logged
    (roles.granted, pending)
    admin, and a partner role (kitchen_partner, captain, rider, packer), still need the account:
    404 "identity.user_not_found" (partners come from M8's verification)
    403 "identity.not_admin"   the caller's admin role is gone from the database (token not expired)

DELETE /v1/admin/user-roles/{id}      admin (roles:revoke); no body
  204   the grant is deleted (user_roles has no revoked status; the audit log keeps roles.revoked
        with the admin), or the pending grant is cancelled
  409 "identity.role_not_removable"   customer, or a partner role (suspend the partner in M8, or
                                      end the captain's appointment in M9)
  409 "identity.cannot_remove_own_admin" · 409 "identity.last_admin"
  404 "identity.role_not_found"
  403 "identity.not_admin"   the caller's admin role is gone from the database (token not expired)

HubStaff : { "grant_id", "user_id" | null, "name": "Kavita More" | null,
             "phone_masked": "******1011",              the last 4 digits only
             "role", "works_as": "staff" | "partner",   partner: kitchen_partner, captain, rider, packer
             "society": { "id", "name" } | null,        captains: the society they serve
             "status": "applied" | "kyc_pending" | "verified" | "suspended" | "invited",
             "since": "…" | null }                      verified_at, or the pending grant's granted_at

GET /v1/hubs/{hub_id}/staff?role=&cursor=&limit=     hub_manager, franchise_owner (hub), admin
    (staff:read); limit ≤ 200, default 100; Cache-Control: no-store
  200 Page[HubStaff]   by role, then name
      the grants scoped to this hub, the captain grants of its societies
      (community.api.hub_society_ids), and the hub's pending grants (status invited, name null);
      admins and other unscoped grants are not listed
  403 "auth.forbidden"  a hub outside the caller's grants
```

- **Pending grants.** For a person who has never signed in. At the exchange that creates the user (§3.1, `is_new_user`), every unexpired pending grant for that phone becomes a `verified` row of `user_roles` in the same transaction, audit-logged as `roles.granted` by the admin who gave it. Then the pending row is deleted. A pending grant expires after 30 days (`expire_pending_grants`, §6). A second grant of the same role and scope to the number is 409 `identity.role_exists`.
- **Revoke takes effect at the next refresh.** Roles travel in the access token (§3.1). So a revoked role stops working within 15 minutes, at the next refresh, which reads `user_roles`. Signing the person out at once is the existing logout-all; the console offers no button for it in v1.
- **Never a phone in clear outside the admin lookup.** `HubStaff` masks it; hub managers who need to call a partner use M8's partner profile (`GET /v1/partners/{id}`), which already shows it to them.
- **Facades.** The scope labels come from `community.api.society_labels` and `hub_ops.api.get_hub`, which both exist; an NGO's label is null until M9 §3.5 ships an NGO facade. New in `identity.api`: `households_per_society(session, society_ids) -> dict[UUID, int]`, for M9's staff society list (M9 §3.9).
- **Permissions:** `users:read`, `roles:revoke` and `staff:read` are new (§7).

- **As built (2026-10-25).**
  - `expire_pending_grants` runs at 04:18 IST, not 04:13: 04:13 is `sweep_uploads`' slot in the Actions schedule, which maps one job to each cron line.
  - `GET /v1/admin/users`: `phone` also accepts spaces and a `91` prefix. A bad number, no filter, two filters, `staff=false` or `pending=false` is 422 `request.invalid`; a cursor from another filter is 422 `request.invalid_cursor`. `q` is trimmed and its inner spaces collapsed. It matches by pg_trgm word similarity (0.4) or by containing the typed text, ranks by word similarity, and pages by offset. `staff` pages by name then id, `pending` by newest then id.
  - The lookup audit entry is `identity.users_looked_up` with entity `user_lookup` and the admin's id; `after` holds the filter kind and the ids returned (`user_ids`, or `pending_grant_ids` for `pending`). A refused or invalid call writes none.
  - `?pending=true` lists only unexpired grants. A pending grant's audit entry has entity `pending_role_grant` and the grant id, never the number.
  - A number's grant and its first sign-in take the same Postgres advisory lock, so a grant given at the moment of sign-up is either claimed or made directly, never stranded. The operator command and the seed still need the account (`grant_role`).
  - **Owner's decisions (2026-10-26).**
    - Admin is never a pending grant: `POST /v1/admin/user-roles` with `admin` for a number with no account is 404 `identity.user_not_found`, like the partner roles, so a mistyped or recycled number cannot become an admin. `pending_role_grants.role` allows only hub_manager, franchise_owner, ngo_coordinator and catalog_editor.
    - Role changes check the database, not only the token. A grant, a revoke, and giving or cancelling a pending grant first confirm that the caller still holds a verified admin row (taken `FOR SHARE`, so a concurrent revoke of the caller waits or is seen); if not, 403 `identity.not_admin`. A revoked admin's token still reads (the lookup, the staff and pending lists, the hub's staff) for up to 15 minutes.
  - Revoking an admin grant takes every verified admin row `FOR UPDATE`, in id order and before the target row, so two admins removing each other wait for one another (no deadlock): the second finds its own admin row gone and gets 403 `identity.not_admin`. `last_admin` counts verified admin grants and stays as a backstop; with the database check a verified caller removing another verified admin always leaves at least one. A suspended admin grant can always be removed.
  - `GET /v1/hubs/{id}/staff`: an unknown hub gives an empty page for an admin. Pages by (role, name, grant id). `scope_label` for a hub is the hub's name ("Kharghar hub").

| Mock today (`mocks/identity.js`, `mocks/team.js`) | Contract |
|---|---|
| `GET /v1/admin/users?phone=` → `{ items: AdminUser[] }` (422 for a bad number) | Same; `AdminUser` gains `status`; `roles[]` are `StaffGrant` |
| `?staff=true` | Same; also `?q=` (by name) and `?pending=true` |
| `roles[].given_by` | Not in the contract: the audit log keeps who granted |
| `POST /v1/admin/user-roles` 404 `identity.user_not_found` for a new number | 202 `PendingGrant` for a staff role; 404 still for a partner role |
| `DELETE /v1/admin/user-roles/{id}` 204, 409 `identity.role_not_removable`, `identity.cannot_remove_own_admin` | Same, plus 409 `identity.last_admin`; it also cancels a pending grant |
| `GET /v1/hubs/{hub}/staff` → `{ id, name, role: "Packer", works_as, society?, today, last_week_paise, status }` | `HubStaff`: `role` is the role code (the console words it), `society { id, name }`, `phone_masked`; no `today` or `last_week_paise` (the tiffin board and M8 earnings have them); status `fix_bank_details` is M8's KYC state, read on the partner's profile |
| `POST /v1/hubs/{hub}/staff-invitations` | Dropped: staff roles through `POST /v1/admin/user-roles` (admin), riders and packers through M8's `POST /v1/hubs/{id}/partner-applications` (hub manager), kitchens and captains apply in the app |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `identity.users` | id (uuidv7), phone_e164 (unique, E.164 check), name, locale, senior_mode, status (active, blocked, deleting; the old `deleted` value is dropped: a purged user has no row), data_source, created_at, updated_at |
| `identity.user_roles` | id, user_id, role, scope_type, scope_id, status, verified_at; unique (user, role, scope) with NULLS NOT DISTINCT |
| `identity.pending_role_grants` | (§3.6) id (uuidv7), phone_e164, role (staff roles other than admin), scope_type, scope_id, granted_by, created_at, expires_at (30 days); unique (phone_e164, role, scope) with NULLS NOT DISTINCT; deleted when claimed or expired |
| `identity.sessions` | one per signed-in device or browser (the "devices" list): id, user_id, platform, push_token, created_at, auth_time (when the OTP behind the session was entered: the Firebase token's `auth_time`; §3.5), last_seen_at, expires_at (30 days, never extended), revoked_at, revoke_reason |
| `identity.refresh_tokens` | id, session_id, token_hash (HMAC-SHA256 with a pepper; the token is never stored), parent_id (rotation chain), created_at, used_at, revoked_at |
| `identity.households` | id (uuidv7), owner_user_id (unique in v1), hub_id (nullable, no FK until M6), society_id → community.societies, size, diet, cuisine_tags[], weekday_cook_minutes, weekend_cook_minutes, no_cook_days smallint[], budget_paise BIGINT null, autopilot_enabled, auto_approve, shortage_preference (substitute, refund; §3.4), data_source, version, created_at, updated_at |
| `identity.household_members` | id, household_id, age_band, sex null, health_tags[] (empty unless `health_personalisation` is granted), fasting_rules jsonb `{jain, fasts[], no_onion_garlic_on_fast_days}`, created_at |
| `identity.addresses` | id, household_id, society_id → community.societies, tower_id, flat, location geography(Point, 4326) null, h3_r9 null, created_at, updated_at |
| `identity.consents` | id, user_id, purpose, version, granted_at, revoked_at; at most one active row per (user, purpose) (partial unique index where revoked_at is null) |
| `identity.data_exports` | id, user_id, requested_on (IST date; unique with user_id), status (queued, building, ready, failed, expired), object_key (M0 `data_export`), size_bytes, attempts, error, requested_at, ready_at, expires_at |
| `identity.deletions` | user_id (primary key; no foreign key, it outlives the user), requested_at, purge_on (IST date), cancelled_at, purged_at, blocked_reasons text[], counts jsonb (rows erased per module and table); never holds phone, name or household fields (§3.5) |

## 5. Events
**Published:** `identity.household_created`, `identity.household_updated`, `identity.user_deleted`, `identity.deletion_requested` (`{user_id, household_id, purge_on}`), `identity.deletion_cancelled` (`{user_id}`), `identity.data_export_requested` (`{export_id, user_id}`; M1's `build_data_export` job takes the queued row), `identity.data_export_ready` (`{export_id, user_id}`, published by `build_data_export` when the file is written; M11 notifies).
- `identity.household_created` `{household_id, owner_user_id, society_id, hub_id, size, budget_paise, data_source}`. M3 uses `budget_paise` to open the first Budget Lock; M9 recounts the society's unlock bar.
- `identity.household_updated` `{household_id, version, changed: [field names]}`.
- `identity.user_deleted` `{user_id, household_id}`: published by `purge_deleted_users` once every module has erased its data (§3.5).
- No event carries health tags, fasting rules or flat numbers (DPDP: consumers read what they are allowed to through `identity.api`).
**Consumed:** `partners.verified` (the hub role for kitchens, riders and packers; nothing for captains), `partners.suspended`, `partners.reinstated`, `community.captain_assigned` and `community.captain_ended` (the `captain:<society>` role; ADR-0026 §4, M8 §10).
- **As built (S5, the captain role).** `community.captain_assigned` grants `captain:<society>` (status `verified`) and `community.captain_ended` deletes that row: `user_roles` has no `revoked` status, and the audit log keeps the history (`roles.granted` and `roles.revoked`, no actor). Each consumer first asks `community.api.is_active_captain`, so a late or redelivered event never grants an ended appointment's role or removes an active one. The hub-role consumers of `partners.verified`, `partners.suspended` and `partners.reinstated` come with M8's applications (S6). The data export has the `community` section (`captain_appointments`).

## 6. Jobs
| Job | Trigger | Notes |
|---|---|---|
| `build_data_export` | Every 10 minutes (cron-job.org); takes `queued` exports | Gathers each module's `export_for_user`, writes one JSON file to the private bucket (§3.3); 3 attempts, then `failed`; publishes `identity.data_export_ready` for M11. A job rather than the consumer of `identity.data_export_requested`, because outbox consumers get no object storage |
| `expire_data_exports` | Daily (04:23 IST, before `sweep_uploads` the next night) | Marks exports past `expires_at` (7 days) `expired`; M0's `sweep_uploads` deletes the file (`delete_after`) |
| `expire_pending_grants` | Daily 04:18 IST (GitHub Actions) | Deletes pending role grants past `expires_at` (§3.6): a phone number is personal data |
| `purge_deleted_users` | Daily 03:33 IST (GitHub Actions) | Deletions past `purge_on` (30 days after the request): checks the preconditions, calls each module's `delete_for_user` / `anonymise_for_user`, then deletes M1's rows and the user's files; not ready → `blocked`, retried nightly (§3.5) |

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `households:write` | customer (own) |
| `households:read` | customer (own), hub_manager (hub), admin |
| `roles:grant` | admin |
| `roles:revoke` | admin (§3.6) |
| `users:read` | admin (the user lookup, §3.6; every call audit-logged) |
| `staff:read` | hub_manager, franchise_owner (hub), admin (§3.6) |

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
- Shortage preference (§3.4): default `substitute`; PATCH needs If-Match and publishes `household_updated`; `shortage_preferences` reads unknown ids as `substitute`.
- Account deletion (§3.5): DELETE revokes every session at once (an old access token gets 401); a staff account gets 409; pending exports and their files are gone; `DELETE /v1/me` from a session whose `auth_time` is more than 5 minutes old gets 403 `identity.fresh_otp_required` and changes nothing, and a refresh does not move `auth_time`; signing in during the grace period without `keep_account` gets 409 `identity.deletion_pending` and issues no tokens, and with `keep_account: true` cancels and answers `deletion_cancelled: true`; the purge waits while an order is open, an issue is open or the wallet is not closed, and is idempotent (a rerun after a crash part-way finishes the job, and a purged user is not touched again); after the purge no table outside the kept records of the matrix has a row with the user or household id (a test walks every schema); ledger postings sum to zero before and after.

## 10. Dependencies
M0 (platform). Reads society existence via `community.api` (M9). Partner verification arrives via `partners.verified` events.

## 11. Open questions
- ~~One household per user in v1?~~ Decided: one household and one address per owner in v1 (§3.2). Multiple households (e.g. parents' home) stay open for later.
- ~~Minimum household fields before the first plan?~~ Decided: everything in `POST /v1/households` except `budget_paise`, `sex`, `health_tags` and the optional consents.
- Localised tower labels and fasting names: served by M2/M9 later, or kept as client copy?
- **Decided by the owner (2026-10-09):**
  - ~~Account deletion (§3.5): is signing in again the right way to cancel, or should the app ask "Keep my account?" first?~~ Decided: signing in again cancels a pending deletion, after the app shows a "Keep my account?" screen (`identity.deletion_pending`, then the exchange with `keep_account: true`).
  - ~~Should deletion also need a fresh OTP (step-up) rather than a valid session?~~ Decided: yes, a sign-in with an OTP within the last 5 minutes, checked on the session's `auth_time` (the Firebase token's `auth_time`, kept across refreshes); otherwise 403 `identity.fresh_otp_required` (§3.5).
