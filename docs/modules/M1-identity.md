# M1 — Identity & Household

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Yash Salunkhe · **Reviewer:** Prathamesh Relekar · **QA:** Rhythm Thakur, Manish Raje
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
| POST | `/v1/households` | customer | Required | One per owner (v1) |
| GET / PATCH | `/v1/households/{id}` | owner, hub_manager (read) | — | `If-Match` version |
| GET / POST / PATCH / DELETE | `/v1/households/{id}/members[/{mid}]` | owner | — | Age band, health tags (consent-gated) |
| GET / POST / PATCH | `/v1/addresses[/{id}]` | owner | — | Society, tower, flat, location |
| GET / POST | `/v1/consents` | Authenticated | — | Grant/revoke per purpose |
| GET | `/v1/me/data-export` | Authenticated | — | Async; link via notification |
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

## 4. Tables owned
| Table | Key columns |
|---|---|
| `identity.users` | id (uuidv7), phone_e164 (unique, E.164 check), name, locale, senior_mode, status, data_source, created_at, updated_at |
| `identity.user_roles` | id, user_id, role, scope_type, scope_id, status, verified_at; unique (user, role, scope) with NULLS NOT DISTINCT |
| `identity.sessions` | one per signed-in device or browser (the "devices" list): id, user_id, platform, push_token, created_at, last_seen_at, expires_at (30 days, never extended), revoked_at, revoke_reason |
| `identity.refresh_tokens` | id, session_id, token_hash (HMAC-SHA256 with a pepper; the token is never stored), parent_id (rotation chain), created_at, used_at, revoked_at |
| `identity.households` | id, owner_user_id, hub_id, society_id, size, diet, cuisine_tags[], weekday_cook_minutes, no_cook_days[], budget_paise, autopilot_enabled, auto_approve, data_source, version |
| `identity.household_members` | id, household_id, age_band, sex, health_tags[], fasting_rules jsonb |
| `identity.addresses` | id, household_id, society_id, tower, flat, location geography(Point), h3_r9 |
| `identity.consents` | id, user_id, purpose, version, granted_at, revoked_at |

## 5. Events
**Published:** `identity.household_created`, `identity.household_updated`, `identity.user_deleted`.
**Consumed:** `partners.verified` (activate partner role), `partners.suspended`.

## 6. Jobs
| Job | Trigger | Notes |
|---|---|---|
| `build_data_export` | On request | Writes JSON to private S3, notifies user |
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
- E2E: onboarding under 2 minutes on a mid-range device profile; role switch.
- Security: OTP exchange rate limits; refresh token theft simulation.

## 10. Dependencies
M0 (platform). Reads society existence via `community.api` (M9). Partner verification arrives via `partners.verified` events.

## 11. Open questions
- One household per user in v1, or allow multiple addresses/households (e.g. parents' home)?
- Minimum household fields required before the first plan vs progressive profiling.
