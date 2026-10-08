# M8 — Partner Network

**Status:** Draft v2 (with [ADR-0026](../adr/0026-operations-contract.md)). When this spec is approved, §3.1 (applications, KYC and review), §3.2 (training), §3.3 (partners, roles, pause and suspend), §3.4 (kitchens, capacity and tiffin assignments) and §3.5 (know your cook and ratings) are frozen for S6. §3.6 (earnings) has a frozen shape, but the rates are refined at its PR. §3.7 (payouts and the income certificate) is an outline, refined after MS-B.
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P3 / S6 (the partner core in S5, see the build order)
**Postgres schema:** `partners` · **Python package:** `gharbasket.modules.partners`

## 1. Purpose
M8 handles the people GharBasket pays: home-kitchen partners, Society Captains, riders and packers. A customer applies from inside the app. The applicant uploads KYC documents (checked by simulation in the prototype; FSSAI registration for kitchens), passes short training modules, and a hub manager reviews the application. M8 then **verifies the person**: it publishes `partners.verified`, M1 grants the hub role, and for captains M9 appoints the society (ADR-0026 §4). After that, M8 holds kitchen capacity and skills and the nightly **tiffin assignment** (fair assignment by M10, with a greedy fallback). Kitchens move their tiffin orders through `orders.api`. M8 also owns ratings, the public "know your cook" card, the rate card that decides who earns what for which order event, and the partner's earnings view.

What M8 does **not** do:
- **Money.** It never holds money: every earning is a journal entry in M5's ledger, posted through `billing.api`, and the earnings view reads it back from there.
- **Captains.** It does not record which captain serves a society (M9 `community.captains`).
- **Rider pickups.** It does not route riders or move orders past `packed` (M7).

**Build order:**
- (a) **Partner core, in S5 with M9's captains and before M7:** `partners.partners`, `partners.api.get_partner` / `profile_card`, and seeded verified captain partners (synthetic), so M9 can appoint and M7 can hand over before applications exist.
- (b) **Applications, KYC, training, review and verification** (§3.1–§3.3).
- (c) **Kitchens, capacity and tiffin assignments** (§3.4).
- (d) **Ratings and the card** (§3.5).
- (e) **Earnings** (§3.6).

(b)–(e) are S6, for MS-B ("a tiffin day; a partner completes KYC"). (f) **Payouts and the income certificate** (§3.7) come after MS-B.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a customer, I want to apply to become a Society Captain, kitchen partner, rider or packer from the app so that I can earn. | One open application at a time; the society or hub is filled in from my household; status `applied`. |
| US-2 | As an applicant, I want to upload KYC documents securely so that I can be verified. | Private uploads (M0 `kyc_document`); numbers stored encrypted and shown masked; FSSAI number for kitchens; bank checked by a simulated penny drop; Aadhaar refused. |
| US-3 | As an applicant, I want short training modules with a quiz so that I learn hygiene and procedures. | Score and pass recorded per module; the answers are checked on the server. |
| US-4 | As a hub manager, I want to review applications so that only trained, verified people operate. | Approve, ask for a document again, or reject with a reason; audit-logged. Approval plus passed training gives `partners.verified`, and the role appears in the role switcher. |
| US-5 | As a kitchen partner, I want to set my capacity and menu skills so that I receive manageable orders. | A weekly default per meal, overridden per date until the cut-off; skills from a fixed list. |
| US-6 | As a kitchen partner, I want tomorrow's counts by 21:30 so that I can buy and plan. | Offer after the fair assignment; accept or decline by 22:30; declined and unanswered portions are reassigned. |
| US-7 | As a kitchen partner, I want to mark my tiffins as cooking and ready, with a hygiene checklist, so that the rider comes on time. | Start → orders `picking`; ready (checklist complete) → orders `packed`; a pickup QR for the rider. |
| US-8 | As a customer, I want to see who cooked my tiffin, with the FSSAI number, hygiene grade and rating, so that I trust it. | The card shows the display name, area, FSSAI number, hygiene grade and rating. It never shows an address or phone number. |
| US-9 | As a customer, I want to rate my tiffin so that good kitchens get more orders. | 1–5 stars for 48 h after delivery, once per order; it feeds the kitchen's rating and the rating floor. |
| US-10 | As a partner, I want to see what I earned this week so that my income is transparent. | Weekly view from the ledger by day and kind; payout status. |
| US-11 | As a hub manager, I want to suspend a partner so that quality and safety problems stop at once. | Role revoked (M1), captain appointment ended (M9), future assignments released and reassigned. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| POST | `/v1/partner-applications` | customer | Required | §3.1 |
| GET | `/v1/partner-applications/me` | Authenticated | — | §3.1; the open application, else the latest |
| GET | `/v1/partner-applications/{id}` | applicant (own), hub_manager (hub), admin | — | §3.1 |
| POST | `/v1/partner-applications/{id}/withdraw` | applicant (own) | Required | §3.1 |
| POST | `/v1/kyc-documents` | applicant (own application) | Required | §3.1; file keys from `POST /v1/files/presign` (M0 `kyc_document`) |
| GET | `/v1/hubs/{id}/partner-applications?status=&kind=` | hub_manager (hub), admin | — | §3.1 review queue; cursor |
| POST | `/v1/partner-applications/{id}/decision` | hub_manager (hub), admin | Required | §3.1 |
| POST | `/v1/kyc-documents/{id}/file-links` | hub_manager (hub), admin | — | §3.1; 5-minute links, every call audit-logged |
| GET | `/v1/trainings` | Authenticated | — | §3.2 |
| POST | `/v1/trainings/{code}/complete` | Authenticated (open application or partner) | Required | §3.2 |
| GET | `/v1/partners/me` | Authenticated | — | §3.3 |
| PATCH | `/v1/partners/{id}` | partner (own) | `If-Match` | §3.3 display name |
| POST | `/v1/partners/{id}/pause` · `/resume` | partner (own; kitchen, rider, packer) | Required | §3.3 |
| GET | `/v1/hubs/{id}/partners?kind=&status=` | hub_manager, franchise_owner (hub), admin | — | §3.3; cursor |
| GET | `/v1/partners/{id}` | hub_manager (hub), admin | — | §3.3 |
| POST | `/v1/partners/{id}/suspend` · `/reinstate` | hub_manager (hub), admin | Required | §3.3 |
| GET / PATCH | `/v1/kitchens/me` | kitchen_partner (own) | `If-Match` (PATCH) | §3.4 |
| POST | `/v1/kitchens/{partner_id}/inspection` | hub_manager (hub), admin | Required | §3.4 hygiene grade |
| GET / PUT | `/v1/kitchen-capacity?from=&to=` | kitchen_partner (own) | — | §3.4 |
| GET | `/v1/assignments?date=` | kitchen_partner (own) | — | §3.4 |
| POST | `/v1/assignments/{id}/accept` · `/decline` · `/start` · `/ready` | kitchen_partner (own) | Required | §3.4; offline-safe |
| GET | `/v1/hubs/{id}/tiffin-board?date=` | hub_manager, franchise_owner (hub), admin | — | §3.4 |
| POST | `/v1/hubs/{id}/tiffin-assignments/run` | hub_manager (hub), admin | Required | §3.4; one assignment round now |
| POST | `/v1/hubs/{id}/tiffin-assignments/manual` | hub_manager (hub), admin | Required | §3.4 |
| GET | `/v1/partners/{id}/card` | Authenticated | — | §3.5 know your cook (kitchens) |
| POST | `/v1/ratings` · GET `/v1/ratings?order_id=` | customer (own order) | Required (POST) | §3.5 |
| GET | `/v1/earnings?week=` | partner (own) | — | §3.6, from `billing.api` |
| GET / PUT | `/v1/hubs/{id}/partner-rates` | hub_manager, franchise_owner (hub, read), admin (write) | — | §3.6 |
| POST | `/v1/internal/jobs/assign_kitchens` · `assignment_acceptance_timeout` · `prompt_tiffin_ratings` | operator token | — | §6 |

The old `/v1/partners/{id}/public` becomes `/v1/partners/{id}/card`. `GET /v1/trainings` with `POST …/complete` and the kitchen endpoints replace the draft's rows.

**For every section.** Every endpoint needs sign-in (M1 §3.1).
- **Partner kinds.** They are `kitchen`, `captain`, `rider` and `packer`, as the app already uses them. They map to the M1 roles `kitchen_partner`, `captain`, `rider` and `packer`.
- **Hub staff.** Hub staff act in one hub (`hub_manager:<hub_id>`, `X-Active-Role`); a request for another hub gets 403 `auth.forbidden`.
- **"Own".** A kitchen partner's "own" rows are the rows of the partner record whose `user_id` is the caller.
- **Times.** Dates are IST calendar dates, clock times IST `HH:MM`, and timestamps UTC.
- **Money** is integer paise.
- **Caching.** Reads send `Cache-Control: no-store`.
- **No clear numbers.** No response ever carries a KYC number in clear, a document key, or an applicant's address to anyone but hub staff of that hub.

### 3.1 Applications, KYC documents and review (frozen for S6)
The mobile "Become a partner" screens (`feat/mobile-partner`: role choice, next steps, apply; documents and training next), the console's Partners and KYC review queue, and the backend build against this. Changing it needs a PR reviewed by both Prathamesh and Yash.

```
Application : {
  "id", "kind": "kitchen" | "captain" | "rider" | "packer",
  "status": "applied" | "kyc_pending" | "training" | "verified" | "rejected" | "withdrawn",
  "scope_type": "society" | "hub",        captain: the society she asks to serve; others: the hub
  "scope_id": "<uuid>", "hub_id": "<uuid>",
  "display_name": "Meena tai",            what customers will see (2 to 40 characters)
  "requirements": {
    "documents": [ { "doc_type": "pan", "status": "missing" | "submitted" | "accepted" | "rejected",
                     "document_id": "<uuid>" | null, "reason": "…" | null } ],   required types only
    "trainings": [ { "code": "hygiene", "status": "todo" | "passed" | "failed" } ] },
  "next_step": "documents" | "review" | "training" | null,   null once verified, rejected or withdrawn
  "reason": "…" | null,                   why it was rejected (shown to the applicant)
  "partner_id": "<uuid>" | null,          set at verification
  "submitted_at", "decided_at": "…" | null, "verified_at": "…" | null, "version" }

POST /v1/partner-applications          customer; Idempotency-Key
  request : { "kind": "captain", "scope_id"?: "<uuid>", "display_name"?: "Meena tai" }
  201 : Application                      status applied; publishes partners.application_submitted
        scope_id: optional. Captain: the caller's household's society (sending another society
        is 422 partners.captain_not_resident). Others: the household's hub (sending another hub
        is 422 partners.scope_invalid).
        display_name defaults to the M1 name; 422 partners.display_name_invalid if neither exists.
GET  /v1/partner-applications/me       200 Application (the open one, else the latest)
                                       | 404 "partners.application_not_found"
GET  /v1/partner-applications/{id}     200 Application & { "documents": [ KycDocument ] } for the
                                       applicant; ApplicationReview for hub staff
POST /v1/partner-applications/{id}/withdraw     applicant; Idempotency-Key
  200 : Application (withdrawn)          only while applied, kyc_pending or training

KycDocumentIn : {
  "application_id": "<uuid>",
  "doc_type": "id_proof" | "pan" | "bank" | "fssai" | "driving_licence" | "kitchen_photos"
            | "society_proof",
  "id_kind": "voter_id" | "passport" | "driving_licence",       id_proof only; never Aadhaar
  "number": "ABCDE1234F",               id_proof, pan, bank (account number), fssai,
                                        driving_licence; absent for photos and society_proof
  "ifsc": "SBIN0001234", "account_holder": "Meena Patil",       bank only
  "valid_until": "2028-03-31",          fssai and driving_licence; must be in the future
  "vehicle_no": "MH46AB1234",           driving_licence, riders only
  "file_keys": [ "kyc_document/<user_id>/<uuidv7>.jpg" ] }       from POST /v1/files/presign
KycDocument : {
  "id", "application_id", "doc_type", "id_kind": … | null,
  "number_masked": "XXXXXX234F" | null, the last 4 characters only
  "fssai_reg_no": "11521999000123" | null,   fssai only: in full (it is printed on the card, §3.5)
  "details": { "ifsc": "SBIN0001234", "valid_until": "…", "vehicle_no": "MH46AB1234" },  as sent
  "file_count": 2,
  "check": { "status": "passed" | "failed",
             "code": "format_ok" | "penny_drop_ok" | "penny_drop_failed" | "duplicate", "at" },
  "review": { "status": "pending" | "accepted" | "rejected", "reason": "…" | null, "at": … | null },
  "created_at" }

POST /v1/kyc-documents                 applicant; Idempotency-Key
  request : KycDocumentIn
  201 : KycDocument                      a second document of the same type replaces the first
                                         (the old one is superseded and no longer listed)
```

**What each kind needs** (handbook 13 §13.8):

| Kind | Required documents | Required training |
|---|---|---|
| `kitchen` | `id_proof`, `pan`, `bank`, `fssai`, `kitchen_photos` | `app_basics`, `hygiene`, `kitchen_ops` |
| `captain` | `id_proof`, `pan`, `bank`, `society_proof` | `app_basics`, `captain_handover` |
| `rider` | `id_proof`, `driving_licence`, `bank` | `app_basics`, `rider_safety` |
| `packer` | `id_proof`, `bank` | `app_basics`, `hygiene`, `packing` |

A document type that is not on the kind's list gets 422 `partners.doc_type_invalid`.

**Files and numbers by type:**
- `kitchen_photos`: 2 to 4 files.
- `society_proof`: 1 or 2 files (a maintenance bill or a letter from the society office).
- Every other type: 0 to 2 files (for example, a photo of the card).

**Application status** (handbook 06 §6.6, in lower case):
- **`applied`.** Created. Documents and training can start at once.
- **`applied → kyc_pending`.** Automatic, when every required document type has a current document. The application then shows in the hub's review queue.
- **Decision.** The hub manager decides (below).
- **Ending.** The applicant may withdraw at any time before `verified`. A rejected or withdrawn application is final; the person may apply again with a new application.
- **One open application per person** (`applied`, `kyc_pending` or `training`), as the app's mock already assumes. A person who is already a partner of a kind (any status except `left`) cannot apply for that kind again: 409 `partners.already_applied`, which carries `application_id` or `partner_id`. Verified partners may apply for another kind.
- **Applicants need a household.** The society and hub come from it. Without one: 422 `partners.household_required`. A society with no hub yet: 422 `partners.no_hub`.
- **Captains apply for their own society.** Captains live in the society they serve (M9 §3.1), so the requested society is always the caller's own. It is a request, not an appointment (ADR-0026 §4). An application for a society that already has an active captain is still accepted, and the review queue shows that the society is taken.

**KYC rules** (handbook 13 §13.4 and §13.8):
- **No Aadhaar, ever.** `id_kind` cannot be Aadhaar, and any 12-digit number given as an ID or licence number is refused with 422 `partners.aadhaar_not_accepted`, before anything is stored. Hub staff are told not to accept an Aadhaar image as `society_proof`.
- **Formats** (422 `partners.number_invalid`, with `field`):
  - PAN: `^[A-Z]{5}[0-9]{4}[A-Z]$`.
  - Voter ID: `^[A-Z]{3}[0-9]{7}$`.
  - Passport: `^[A-Z][0-9]{7}$`.
  - Driving licence: 15–16 letters and digits.
  - FSSAI registration: 14 digits.
  - Bank account: 9–18 digits.
  - IFSC: `^[A-Z]{4}0[A-Z0-9]{6}$`.
  - Vehicle number: an Indian registration.

  Numbers are upper-cased with spaces and hyphens removed. A `valid_until` that is not in the future is 422 `partners.document_expired`.
- **Simulated verification.** No real verification API is called in the prototype.
  - Every number that passes the format check gets `check: passed / format_ok`.
  - `bank` runs a simulated ₹1 penny drop: `penny_drop_ok`, except that an account number ending in `0000` fails with `penny_drop_failed`. This is the test hook, in every environment, because no real money moves.
  - The same number (by its hash) on another person's open application or partner record gives `check: failed / duplicate`.

  A failed check does not block the upload. The hub manager sees it and decides.
- **Encryption.** Numbers are stored only as:
  - `number_enc = pgp_sym_encrypt(number, key)` (pgcrypto), with `KYC_ENCRYPTION_KEY` from Infisical, sent as a bind parameter, never logged, plus a `key_version` for rotation;
  - `number_masked`;
  - `number_hash` (HMAC-SHA256 with `KYC_HASH_PEPPER`, for duplicate checks).

  No endpoint decrypts. The only decryption is `partners.api.payout_account` for M5's payouts (§3.7), and each call is audit-logged. The FSSAI registration number is public by law, so it is stored in clear on the kitchen (§3.4).
- **Files.**
  - **Claiming.** Each key must be the caller's own `kyc_document` upload with an object behind it (`platform.files.verify` and `claim` in the same transaction); otherwise 422 `partners.file_key_invalid`.
  - **Reading.** Hub managers of the application's hub and admins read files only through `POST /v1/kyc-documents/{id}/file-links`, which answers `{ "files": [ { "url", "content_type", "expires_at" } ] }` (5-minute signed GETs). Every call writes `platform.audit_log` (action `kyc.file_read`). The applicant never reads the files back.
  - **Retention** (M0 §3.1). `delete_after` is set to 30 days after the application is rejected or withdrawn, the document is superseded, or the partner leaves.

**Review** (`POST /v1/partner-applications/{id}/decision`, hub_manager of the application's hub or admin; Idempotency-Key; every decision goes to `platform.audit_log` with the document review states):

```
request : { "outcome": "approve" | "resubmit" | "reject",
            "reason": "…" | null,                 required for resubmit and reject; up to 300;
                                                  shown to the applicant
            "document_ids": [ "<uuid>" ],          resubmit: the documents to redo (at least one)
            "hygiene_grade": "A" | "B" | "C" }     approve of a kitchen: required (from the
                                                  kitchen photos; §3.4)
200 : ApplicationReview
ApplicationReview : Application & {
  "applicant": { "user_id", "name", "phone_e164" },   hub staff only
  "documents": [ KycDocument ],
  "society": { "id", "name", "has_active_captain": true } | null }   captain applications
```

- **`approve`.** Allowed only from `kyc_pending`, with every required document current (409 `partners.documents_missing`, with `doc_types`). Every current document becomes `accepted`. The application moves to `training`, or straight to `verified` when every required module is already passed.
- **`resubmit`.** The listed documents become `rejected` with the reason, and the application stays `kyc_pending` with `next_step: "documents"`. Uploading a replacement puts it back in the queue.
- **`reject`.** The application becomes `rejected` with the reason.
- **Notification.** Publishes `partners.application_decided`, which M11 turns into a notification to the applicant.
- **Other statuses.** Deciding on an application that is not `kyc_pending` is 409 `partners.not_reviewable`. The one exception: `reject` is also allowed in `training`.
- **Verification** happens in the same transaction as whichever comes last, the approval or the last required training pass (§3.2). It creates the partner record (§3.3), sets `partner_id` and `verified_at`, and publishes `partners.verified`.

**Review queue** (`GET /v1/hubs/{id}/partner-applications?status=kyc_pending&kind=&cursor=&limit=`): `Page[ApplicationReview]`, oldest submitted first. `status` defaults to `kyc_pending`; `limit` is at most 100 (default 20).

**Error codes**

| Status | Code | When |
|---|---|---|
| 404 | `partners.application_not_found` | No application yet (`/me`), an unknown id, or someone else's |
| 409 | `partners.already_applied` | An open application exists, or the caller is already this kind of partner (carries `application_id` or `partner_id`) |
| 422 | `partners.kind_invalid` | Not one of the four kinds |
| 422 | `partners.household_required` | The caller has no household |
| 422 | `partners.no_hub` | The household's society has no hub yet |
| 422 | `partners.captain_not_resident` | A captain `scope_id` that is not the caller's own society |
| 422 | `partners.scope_invalid` | Another kind's `scope_id` that is not the household's hub |
| 422 | `partners.display_name_invalid` | Not 2 to 40 characters, or missing with no M1 name |
| 409 | `partners.application_closed` | Documents, withdrawal or training on a verified, rejected or withdrawn application |
| 422 | `partners.doc_type_invalid` | A document type the kind does not need, or `id_kind` missing or unknown |
| 422 | `partners.number_invalid` | A number, IFSC or vehicle number in the wrong format (`field`) |
| 422 | `partners.aadhaar_not_accepted` | Aadhaar as `id_kind`, or a 12-digit ID or licence number |
| 422 | `partners.document_expired` | `valid_until` is not in the future |
| 422 | `partners.file_key_invalid` | Not the caller's `kyc_document` upload, nothing uploaded at the key, or the wrong file count |
| 409 | `partners.documents_missing` | `approve` with required documents missing (`doc_types`) |
| 409 | `partners.not_reviewable` | A decision on an application that is not `kyc_pending` |
| 422 | `partners.reason_required` | `resubmit` or `reject` without a reason, `resubmit` without `document_ids`, or a kitchen `approve` without `hygiene_grade` |
| 404 | `partners.document_not_found` | Unknown document, or one outside the caller's hub |
| 403 | `auth.forbidden` | A role without the permission, or hub staff of another hub |
| 400 / 422 | `request.idempotency_key_required`, `request.idempotency_mismatch`, `request.invalid` | As in M0 |

### 3.2 Training (frozen for S6)
```
TrainingModule : {
  "code": "hygiene", "version": 2,
  "title": "Kitchen hygiene", "summary": "…",         in Accept-Language (en / hi / mr → en)
  "minutes": 8, "video_url": "https://…" | null,      an unlisted video; null = read-only module
  "for_kinds": [ "kitchen", "packer" ],
  "questions": [ { "id": "q1", "text": "…", "options": [ "…", "…", "…" ] } ],   5 to 10;
                                                      the correct answers are never sent
  "pass_score": 0.8,
  "status": "todo" | "passed" | "failed", "best_score": 0.9 | null, "passed_at": "…" | null }

GET  /v1/trainings     200 { "items": [ TrainingModule ] }   the modules required for the kinds of
                       the caller's open application and partner records, required order first;
                       [] for anyone else
POST /v1/trainings/{code}/complete     Idempotency-Key
  request : { "version": 2, "answers": [ { "question_id": "q1", "option": 2 } ] }   every question
  200 : { "code", "score": 0.9, "correct": 9, "total": 10, "passed": true, "status": "passed",
          "application": { "id", "status", "next_step" } | null }
```

- **Scoring.** Scored on the server; `passed` when `score ≥ pass_score`. A pass is kept for good: later attempts never undo it. The best score is shown.
- **Attempts.** Up to 5 per module per IST day (429 `partners.training_attempts_exceeded`). A `version` other than the current one gets 409 `partners.training_version_stale`, and the app reloads the module.
- **When.** Training is open from `applied` onwards, so it can run in parallel with document review. A pass that completes the requirements of an application already approved by the hub manager verifies it in the same transaction (§3.1). Partners can retake modules at any time, for example after a new version.
- **Content.** Modules are seed data (`partners.training_modules`, code, version, texts per locale, answers). They are written by Rhythm Thakur and Manish Raje and reviewed by the owner. English is required; Hindi and Marathi texts fall back to English until translated.

| Status | Code | When |
|---|---|---|
| 404 | `partners.training_not_found` | Unknown code, or a module not required for the caller's kinds |
| 409 | `partners.training_version_stale` | `version` is not the current one |
| 422 | `partners.answers_invalid` | A question missing or repeated, or an option out of range |
| 429 | `partners.training_attempts_exceeded` | A sixth attempt on the same module in one IST day |

### 3.3 Partners: verification, roles, pause and suspend (frozen for S6)
```
Partner : { "id", "user_id", "kind", "hub_id",
            "display_name": "Meena tai", "photo_url": "<5-minute link>" | null,   null until M0 has
                                                       a partner_photo purpose (§11)
            "status": "active" | "paused" | "suspended" | "left",
            "rating": { "average": 4.6, "count": 23 } | null,   kitchens; null under 5 ratings
            "verified_at", "suspension": { "at", "reason_code", "note" } | null, "version" }

GET   /v1/partners/me              200 { "items": [ Partner ] }   one per kind the caller has
PATCH /v1/partners/{id}            own; If-Match; { "display_name" } → 200 Partner
POST  /v1/partners/{id}/pause      own; Idempotency-Key → 200 Partner (paused)
POST  /v1/partners/{id}/resume     own; Idempotency-Key → 200 Partner (active)
GET   /v1/hubs/{id}/partners?kind=&status=&cursor=&limit=   200 Page[PartnerStaff]  by name
GET   /v1/partners/{id}            200 PartnerStaff
PartnerStaff : Partner & { "name", "phone_e164", "application_id" | null,
                           "requested_society": { "id", "name" } | null }   captains
POST  /v1/partners/{id}/suspend    hub_manager (hub), admin; Idempotency-Key
  request : { "reason_code": "rating" | "hygiene" | "compliance" | "conduct" | "other",
              "note": "…" }        note required, up to 300; staff-only
  200 : PartnerStaff (suspended)
POST  /v1/partners/{id}/reinstate  hub_manager (hub), admin; Idempotency-Key; { "note" }
  200 : PartnerStaff (active)
```

- **The partner record** (`partners.partners`) is created at verification, one per (user, kind). Its `hub_id` is the application's hub; for a captain it is the requested society's hub. **It has no `society_id`** (ADR-0026 §4): which society a captain serves is M9's `community.captains`.
- **`partners.verified { partner_id, user_id, kind, hub_id, requested_society_id }`.** `requested_society_id` is set for captains only. What follows from it:
  - M1 grants `kitchen_partner:<hub>`, `rider:<hub>` or `packer:<hub>`.
  - For a captain, M1 grants nothing on this event. M9 appoints her when the requested society has no active captain, publishes `community.captain_assigned`, and M1 then grants `captain:<society>` (ADR-0026 §4, M9 §3.2).
- **Pause** is for kitchens, riders and packers (409 `partners.pause_not_allowed` for captains: a captain who steps back is ended in M9 by the hub manager).
  - A paused kitchen gets no new offers. Offers already made stay, and she may still decline them.
  - Pausing does not change roles.
- **Suspend** (reason and note go to `platform.audit_log`) takes effect at once:
  - M8 cancels the partner's assignments that are `offered` or `accepted` for today or later. Their orders are released and taken by the next assignment round (§3.4). Assignments already `preparing` or `ready` stay with the hub manager.
  - It publishes `partners.suspended { partner_id, user_id, kind, hub_id, reason_code }`.
  - M1 marks the hub role `suspended` (the access token's roles change at the next refresh).
  - M9 ends an active captain appointment, and M1 then removes `captain:<society>`.
  - Suspending twice is 409 `partners.already_suspended`.
- **Reinstate** sets `active` and publishes `partners.reinstated { partner_id, user_id, kind, hub_id }`. M1 marks the hub role `verified` again. A reinstated captain is **not** re-appointed automatically: the hub manager appoints her in M9.
- **Leaving.** On `identity.user_deleted`, every partner record of the user becomes `left`, and M8 publishes `partners.suspended` with `reason_code: "left"`, so M9 and M1 clean up the same way. KYC files get `delete_after` set to 30 days later.

| Status | Code | When |
|---|---|---|
| 404 | `partners.partner_not_found` | Unknown partner, another hub's, or someone else's (own endpoints) |
| 409 | `partners.pause_not_allowed` | Pausing a captain, or pausing or resuming a suspended partner |
| 409 | `partners.already_suspended` · `partners.not_suspended` | Suspend twice · reinstate an active partner |
| 412 / 428 | `request.version_mismatch` · `request.precondition_required` | `If-Match` wrong · missing |

### 3.4 Kitchens, capacity and tiffin assignments (frozen for S6)
The kitchen screens in the mobile app (Today's counts, Capacity, Prep and ready), the console's tiffin board and the backend build against this. Tiffin orders are M4 orders with `kind = 'tiffin'`, and they take the existing statuses (ADR-0026 §3):
- **`locked`**: the kitchen is being assigned, or has been assigned (this section);
- **`picking`**: "Being cooked";
- **`packed`**: "Ready at the kitchen";
- then the rider (M7) takes it on.

```
Kitchen : { "partner_id", "display_name", "status",
            "area": "Sector 20, Kharghar",                 public (the card)
            "address": "B-1103, Sunrise Towers, Sector 20", private: the kitchen, hub staff and the
                                                           rider at pickup only
            "location": { "lat", "lng" },                  private
            "fssai_reg_no": "11521999000123", "fssai_valid_until": "2028-03-31",
            "skills": [ "veg", "jain" ],                   from: veg, jain, no_onion_garlic,
                                                           diabetic, egg, nonveg
            "default_portions": { "lunch": [10, 10, 10, 10, 10, 8, 0],     Monday … Sunday
                                  "dinner": [0, 0, 0, 0, 0, 0, 0] },        0 to 100 each
            "hygiene": { "grade": "A" | "B" | "C", "inspected_on": "2026-12-20" } | null,
            "version" }
GET   /v1/kitchens/me      200 Kitchen
PATCH /v1/kitchens/me      If-Match; any of display_name, area, address, location, skills,
                           default_portions → 200 Kitchen
POST  /v1/kitchens/{partner_id}/inspection     hub_manager (hub), admin; Idempotency-Key
  request : { "grade": "A" | "B" | "C", "note": "…" | null }  → 200 Kitchen (audit-logged)

Capacity : { "date": "2027-01-15", "meal": "lunch" | "dinner",
             "max_portions": 12, "source": "default" | "set",
             "assigned_portions": 9,           in offered, accepted, preparing and ready assignments
             "locked": false }                 true from 21:00 IST the day before
GET /v1/kitchen-capacity?from=&to=     200 { "items": [ Capacity ] }   both meals per date;
                                       from today; at most 14 days; default today … today + 6
PUT /v1/kitchen-capacity               { "date", "meal", "max_portions": 0 … 100 } → 200 Capacity
                                       0 = no tiffins that meal; a set value overrides the default

Assignment : {
  "id", "date": "2027-01-15", "meal": "lunch", "hub_id",
  "slot": { "id", "label", "start": "12:30", "end": "13:30" },
  "partner": { "id", "display_name" },
  "status": "offered" | "accepted" | "preparing" | "ready" | "completed"
          | "declined" | "expired" | "cancelled",
  "round": 1, "source": "ml" | "fallback" | "manual",
  "portions": 12,
  "dishes": [ { "product_id", "name": "Diet thali", "portions": 8 } ],   in Accept-Language
  "orders": [ { "order_id", "short_code": "T-07", "portions": 2, "product": "Diet thali",
                "society": { "id", "name" },
                "status": "assigned" | "ready" | "picked_up" | "delivered" | "failed" | "released" } ],
  "respond_by": "2027-01-14T17:00:00Z",       22:30 IST for round 1 (below)
  "ready_by": "11:30",                        slot start − 60 min
  "pickup_qr": "GB1A:0199a…" | null,          once any order is ready; the rider scans it (M7)
  "checklist": { "handwash": true, "hair_cover": true, "clean_surface": true,
                 "sealed": true, "fresh_today": true } | null,
  "accepted_at", "ready_at", "version" }

GET  /v1/assignments?date=             kitchen (own); default tomorrow, from today − 7 to tomorrow
  200 { "items": [ Assignment ] }      all of the kitchen's assignments for the date
POST /v1/assignments/{id}/accept       Idempotency-Key → 200 Assignment (accepted)
POST /v1/assignments/{id}/decline      Idempotency-Key
  request : { "reason": "unwell" | "no_ingredients" | "family" | "other", "note"? }
  200 : Assignment (declined)
POST /v1/assignments/{id}/start        Idempotency-Key; { "client_at"? } → 200 Assignment (preparing)
POST /v1/assignments/{id}/ready        Idempotency-Key
  request : { "order_ids"?: [ … ],      default every assigned order not yet ready
              "checklist": { … all five true }, "client_at"? }
  200 : Assignment                     ready when every order is ready
```

**Rules**

- **Who cooks what.**
  - A tiffin order needs the skills of its tiffin products: the product tags that are skill codes, from `catalog.api`.
  - A kitchen can take an order when:
    - her skills include all of the order's skills;
    - her rating is at least the floor (3.8; kitchens with fewer than 5 ratings count as 4.5, the ML default);
    - she is within `max_km` (5 km) of the order's society point;
    - her capacity left for that date and meal covers the order's portions.
  - Portions are the order's tiffin line quantities (one step = one portion).
  - Meal: `lunch` for a tiffin slot starting before 16:00, `dinner` otherwise.
- **Capacity.**
  - The value for a date and meal is the `set` override, else `default_portions[weekday]`. New kitchens start at 0 everywhere, so nobody is assigned before she chooses.
  - A `PUT` for date T after T-1 21:00 IST gets 409 `partners.capacity_locked`. Lowering capacity never takes away portions already offered.
  - A paused kitchen counts as 0.
- **The 21:30 round** (`assign_kitchens`, T-1 21:30 IST, every active hub, both meals of T):
  1. **Orders.** `orders.api.orders_for_delivery(hub, T, statuses=("locked",), kinds=("tiffin",))`, less orders already in a live assignment (`assigned` or later). Live means one of `offered`, `accepted`, `preparing` or `ready`.
  2. **Kitchens.** The hub's `active` kitchens with capacity left. Each one's rolling earnings are her `tiffin_portion` earnings over the last 28 days, from `billing.api.partner_rolling_earnings` (§3.6).
  3. **One request per meal** to `intel_gateway.api.assign_kitchens` (M10 §3.5):
     - `orders[]`: `{id: order_id, lat, lng: the society point, portions, skills}`;
     - `kitchens[]`: `{id: partner_id, lat, lng, max_portions: capacity left, skills, rating, earnings_paise}`;
     - `fairness_lambda` from `PARTNERS_FAIRNESS_LAMBDA` (default 300, M10's knee);
     - `rating_floor: 3.8`, `max_km: 5`, `time_limit_s: 5`;
     - `paise_per_portion` from the hub's kitchen `tiffin_portion` rate (§3.6).
  4. **Greedy fallback** when the gateway returns `None` (the service is down, slow or not configured). M8 assigns the orders itself, largest portions first, then by order id. Each order goes to the eligible kitchen with the least `(km, rolling earnings, partner_id)`. Haversine distances; `source: fallback`, model `greedy-m8`. The result is deterministic, so tests pin it. ML's own greedy (`status: fallback` in the response) is still `source: ml`.
  5. **Offers.** Orders are grouped per kitchen into **one offer per (kitchen, date, meal, round)**, status `offered`, with `respond_by` = T-1 22:30 IST. Each offer publishes `partners.assignment_created`, and M11 pushes it ("12 portions of Diet thali for tomorrow lunch").
  6. **The run is recorded.** Each run is a `partners.assignment_runs` row: hub, date, meal, round, source, model, status, portions, the unassigned orders with reasons, and the `summary` (km per portion, Gini, Gini of eligible kitchens). This feeds the fairness dashboard and the eval harness.
  7. **Unassigned orders** are published once per run as `partners.tiffins_unassigned { hub_id, date, meal, order_ids, reasons }`. M11 tells the hub managers.

  A second run for the same (hub, date) only adds orders without a live assignment, so the job is idempotent.
- **Accept, decline, expire.** Only while `offered` and before `respond_by`; otherwise 409 `partners.assignment_closed`.
  - **Accept.** Calls `orders.api.set_kitchen(order_id, partner_id)` for each order (ADR-0026 §3), so the customer's order shows "Cooked by …" with the card (§3.5).
  - **Decline.** The orders are `released`.
  - **No answer.** Offers past `respond_by` become `expired`, and their orders are released.
  - **Cancelled after acceptance** (suspension, hub manager): `set_kitchen(order_id, None)`.
  - Every release publishes `partners.assignment_released { assignment_id, partner_id, hub_id, date, meal, order_ids, reason: "declined" | "expired" | "cancelled" }`.
- **Reassignment rounds** (`assignment_acceptance_timeout`, every 15 minutes from T-1 21:45 to T 00:30 IST). Each run, for every hub with tiffins on T:
  1. expires overdue offers;
  2. if any locked tiffin order has no live assignment (released, or locked late after a top-up in the payment grace), runs **round n + 1** the same way. Kitchens that declined or let an offer expire for that date and meal are left out, and capacity left counts their live assignments. A round-2+ offer has `respond_by` = 30 minutes after it was made.

  No new round starts after T 00:00, so the last offers expire at T 00:30. A hub manager can run a round at any time (`POST /v1/hubs/{id}/tiffin-assignments/run { "date" }` → `{ "run_id", "offers": 3, "unassigned": 1 }`).
- **Manual assignment** (`POST /v1/hubs/{id}/tiffin-assignments/manual { "date", "order_ids", "partner_id", "note" }`). The hub manager agreed with the kitchen by phone. It creates an `accepted` assignment (`source: manual`) and may exceed capacity (the answer carries `over_capacity: true`). It is allowed until the meal's `ready_by` minus 2 hours, and is audit-logged. Orders that are not locked tiffins of the hub and date, or that are already in a live assignment, get 409 `partners.order_not_assignable` (`order_ids`).
- **Cooking.**
  - **Start** (from `accepted`, from T 00:00 onwards): calls `orders.api.transition(order, "picking", actor="kitchen", actor_user_id=…)` for each order. The order shows "Being cooked".
  - **Ready** needs every checklist item true (422 `partners.checklist_incomplete`). For each listed order it moves `locked → picking` if start was skipped, then `picking → packed` (actor `kitchen`). M4 publishes `orders.packed` with `kind: tiffin`.
  - **Labels.** Each box is labelled with its `short_code` (for example "T-07", unique per hub, date and meal). The kitchen sees short codes, dishes and society names, never a flat or a customer name.
  - **Lateness.** Ready after `ready_by` is allowed and recorded (`late: true` in the assignment's events).
  - **Illegal moves.** An order already moved by the hub (actor `hub`) answers 409 `orders.illegal_transition`, and the assignment is not changed.
  - **Offline.** Each tap carries an `Idempotency-Key` made when tapped, as in M6 §3.2.
- **Pickup** is M7's. The rider scans `pickup_qr` (`GB1A:` + the assignment id without hyphens, shown on the kitchen's phone). M7 calls `partners.api.pickup(…)`, which returns the ready orders, then moves each `packed → dispatched` (actor `rider`). The order statuses come back to M8 as events and update `orders[].status`.
- **Completion.** When every order of an assignment is `delivered` or `failed` (from `orders.delivered` and `orders.delivery_failed`, kind `tiffin`), the assignment becomes `completed` and M8 publishes `partners.assignment_completed { assignment_id, partner_id, hub_id, date, meal, portions_delivered, orders_failed }`.
- **Tiffin board** (`GET /v1/hubs/{id}/tiffin-board?date=`):
  - per meal: `{ "orders", "portions", "assigned", "accepted", "preparing", "ready", "picked_up", "unassigned": [ { "order_id", "society", "portions", "reason" } ] }`;
  - the assignments, as `Assignment` with the partner;
  - the runs, as `{ "round", "source", "model", "status", "gini", "km_per_portion", "at" }`.

| Status | Code | When |
|---|---|---|
| 404 | `partners.kitchen_not_found` | The caller has no kitchen partner record, or the id is not a kitchen of the hub |
| 409 | `partners.capacity_locked` | Capacity for a date whose cut-off has passed |
| 422 | `partners.capacity_invalid` | `max_portions` outside 0 to 100, a date outside the window, or an unknown meal |
| 422 | `partners.skills_invalid` | A skill outside the list |
| 404 | `partners.assignment_not_found` | Unknown assignment, or another kitchen's |
| 409 | `partners.assignment_closed` | Accept or decline when not `offered` or after `respond_by` |
| 409 | `partners.assignment_not_accepted` | Start or ready before accepting, or start before T 00:00 |
| 422 | `partners.checklist_incomplete` | Ready without every checklist item true |
| 422 | `partners.order_not_in_assignment` | `order_ids` not in this assignment, or already ready |
| 409 | `partners.order_not_assignable` | Manual assignment of orders that are not free locked tiffins of the hub and date |
| 409 | `orders.illegal_transition` | The order is not in the status the step needs (from M4) |

### 3.5 Know your cook and ratings (frozen for S6)
```
KitchenCard : { "partner_id", "display_name": "Sunita tai", "photo_url": "<5-minute link>" | null,
                "area": "Sector 20, Kharghar", "since": "2026-12",       month of verification
                "fssai_reg_no": "11521999000123", "fssai_valid_until": "2028-03-31",
                "hygiene": { "grade": "A", "inspected_on": "2026-12-20" } | null,
                "rating": { "average": 4.6, "count": 23 } | null,      null under 5 ratings
                "skills": [ "veg", "jain" ],
                "portions_served": 340 }                                delivered, all time
GET /v1/partners/{id}/card     200 KitchenCard | 404 "partners.partner_not_found"
                               kitchens with status active or paused only; never an address,
                               phone, location or KYC detail

Rating : { "order_id", "partner_id", "score": 4, "comment": "…" | null, "created_at" }
POST /v1/ratings     customer; Idempotency-Key
  request : { "order_id", "score": 1 … 5, "comment"?: "…" }   comment up to 300; staff-only
  201 : Rating
GET  /v1/ratings?order_id=      200 Rating | 404 "partners.rating_not_found"   own orders only
```

- **The card.** The app reads `OrderOut.kitchen.partner_id` (M4, ADR-0026 §3) and fetches the card. `photo_url` is a 5-minute signed GET minted on each read.
- **Who can rate.** Only the household owner of a tiffin order that M8 has a kitchen for, from `delivered` until 48 hours after it (the same window as order issues). Outside it: 409 `partners.rating_window_closed`. A second rating for the same order is 409 `partners.rating_exists`. Not the caller's order, or not a tiffin: 404 `partners.rating_not_found`.
- **The kitchen's rating.** The mean of her last 50 ratings, recomputed in the same transaction. It is shown from 5 ratings on, and it feeds the rating floor (§3.4).
- **Prompt.** A rating prompt goes out at T 14:30 (`prompt_tiffin_ratings`), which publishes `partners.rating_requested { order_id, household_id, partner_id }` for M11.
- **Captains are not rated in v1.** A captain's public face is M9's card `{ display_name, photo_url }`, which M9 builds from `partners.api.profile_card`.

### 3.6 Earnings (view frozen for S6; rates refined at its PR)
Money stays in M5's ledger. M8 only decides **who earns what for which event**, from its rate card, and asks M5 to post it.

```
EarningsWeek : {
  "week": "2027-W02", "from": "2027-01-11", "to": "2027-01-17",       ISO week, Monday–Sunday, IST
  "total_paise": 72600,
  "by_kind": [ { "kind": "kitchen", "basis": "tiffin_portion", "units": 118, "amount_paise": 70800 },
               { "kind": "captain", "basis": "handover", "units": 30, "amount_paise": 1800 } ],
  "days": [ { "date": "2027-01-11", "amount_paise": 9600 } ],          7 entries, zeros included
  "entries": [ { "id", "kind", "basis", "units": 2, "amount_paise": 12000,
                 "ref": { "type": "order", "id", "delivery_date" }, "at" } ],   newest first
  "payout": { "status": "pending" | "scheduled" | "paid" | "held", "amount_paise",
              "paid_at": "…" | null, "held_reason": "kyc" | "suspended" | null } | null,  §3.7
  "generated_at" }
GET /v1/earnings?week=2027-W02     partner (own; every kind the caller has); default this week
  200 EarningsWeek | 422 "partners.week_invalid" (not YYYY-Www, or a week after the current one)

RateCard : { "hub_id", "rates": [ { "kind", "basis", "amount_paise", "valid_from" } ] }
GET /v1/hubs/{id}/partner-rates    200 RateCard (current rates)
PUT /v1/hubs/{id}/partner-rates    admin; { "rates": [ { "kind", "basis", "amount_paise",
                                   "valid_from" } ] }; valid_from today or later; audit-logged
```

**What earns** (one basis per kind in v1; amounts are the proposals to confirm, §11):

| Kind | Basis | Event (consumed by M8) | Units | Proposed rate |
|---|---|---|---|---|
| kitchen | `tiffin_portion` | `orders.delivered`, kind `tiffin`; the kitchen of the order's assignment | portions | ₹60 (the `paise_per_portion` M10 uses) |
| captain | `handover` | `orders.at_society` with `actor: captain`; the captain partner of `actor_user_id` | 1 per order | ₹6 (M5 §11 proposed ₹5–8) |
| rider | `dispatched_order` | `orders.dispatched` with `actor: rider`; the rider partner of `actor_user_id` | 1 per order | ₹8 |
| packer | `packed_order` | `orders.packed` with `actor: packer` | 1 per order | ₹4 |

- **Posting.** In the consumer's transaction, M8 calls `billing.api.accrue_partner_earning(session, partner_id, user_id, basis, ref_type="order", ref_id, units, amount_paise, on_date)`. It is keyed `earning:<partner>:<basis>:<order>`, so a redelivered event never pays twice. No partner record, or a `hub` or `system` actor, means no earning.
- **Rates.** The rate is the one valid on the order's delivery date at its hub. A later refund or issue on the order does not claw back the earning in v1.
- **Reading back.** The view comes from `billing.api.partner_earnings(session, user_id, from, to)` and `billing.api.payout_status(session, user_id, week)`. M8 keeps no earnings table: the draft's `partners.earnings` mirror is dropped.
- **Statements.** The weekly statement and payouts are M5's (§3.7). The draft's M8 job `weekly_earnings_statement` is dropped.

### 3.7 Payouts and the income certificate (outline; after MS-B)
- M5's `weekly_payouts` (Monday 10:00 IST, test mode) pays each partner's payable balance when `partners.api.payout_eligible(partner_id)` holds: status `active` or `paused`, a `bank` document `accepted` with `penny_drop_ok`, not suspended. Otherwise the payout is `held` with the reason.
- The account details come only from `partners.api.payout_account(partner_id)`, which decrypts and is audit-logged.
- Income certificate: `GET /v1/earnings/certificate?from=&to=` (PDF of the ledger totals per month, for loans). Refined with M5's statement PDF.

### Facade for other modules (`gharbasket.modules.partners.api`)
- `get_partner(session, partner_id) -> PartnerRef | None` — `{ id, user_id, kind, hub_id, status, display_name }` (M9 appointments, M7).
- `partner_for_user(session, user_id, kind) -> PartnerRef | None`.
- `profile_card(session, partner_id) -> { display_name, photo_url } | None` — M9's captain card.
- `tiffin_pickups(session, hub_id, date, meal) -> list[PickupRef]` — `{ assignment_id, partner_id, display_name, address, lat, lng, ready_by, status, order_ids }` for M7's tiffin routes.
- `pickup(session, pickup_qr, hub_id) -> PickupRef` — the orders ready at that kitchen; raises `partners.pickup_invalid` for an unknown or other hub's code.
- `payout_eligible(session, partner_id) -> Eligibility { ok, reason }` and `payout_account(session, partner_id) -> { account_number, ifsc, holder }` (M5, §3.7).
- `export_for_user(session, user_id) -> dict` — the M1 data export (§3.3 of M1): applications with masked KYC references and check and review states (no files, no hashes), training results, partner records, ratings given.

## 4. Tables owned
| Table | Key columns |
|---|---|
| `partners.applications` | id (uuidv7), user_id, kind, scope_type (society, hub), scope_id, hub_id, display_name, status (applied, kyc_pending, training, verified, rejected, withdrawn), docs_approved_at, decided_by, decided_at, reason, partner_id null, verified_at, data_source, version, created_at, updated_at; partial unique (user_id) where status in (applied, kyc_pending, training) |
| `partners.kyc_documents` | id, application_id → applications, user_id, doc_type, id_kind null, number_enc bytea null (`pgp_sym_encrypt`), key_version, number_masked null, number_hash bytea null (HMAC; indexed), fssai_reg_no null, details jsonb (ifsc, valid_until, vehicle_no; no numbers), file_keys text[], check_status, check_code, checked_at, review_status (pending, accepted, rejected), review_reason, reviewed_by, reviewed_at, superseded_by null, created_at |
| `partners.training_modules` | code, version, for_kinds text[], required, minutes, texts jsonb (per locale: title, summary, questions, options), video_url jsonb, answers jsonb (never returned), pass_score, active; primary key (code, version) |
| `partners.training_attempts` | id, user_id, module_code, module_version, score numeric(4,3), passed, answers jsonb, at; the pass of a user and module is the first passed attempt |
| `partners.partners` | id (uuidv7), user_id, kind, hub_id, application_id null (null for seeded partners), display_name, photo_key null, status (active, paused, suspended, left), rating_avg numeric(3,2) null, rating_count, verified_at, suspended_at, suspend_reason_code, suspend_note, data_source, version, created_at; unique (user_id, kind). **No society_id** (ADR-0026 §4) |
| `partners.kitchens` | partner_id (pk) → partners, area, address, location geography(Point, 4326), fssai_reg_no, fssai_valid_until, skills text[], default_portions jsonb, hygiene_grade null, inspected_on null, version |
| `partners.kitchen_capacity` | partner_id, date, meal (lunch, dinner), max_portions smallint (0–100), updated_at; primary key (partner_id, date, meal); only `set` overrides are stored |
| `partners.assignments` | id, partner_id, hub_id, date, meal, slot_id, round, run_id, source (ml, fallback, manual), status (offered, accepted, preparing, ready, completed, declined, expired, cancelled), portions, respond_by, responded_at, decline_reason, started_at, ready_at, checklist jsonb, late, version, created_at; index (hub_id, date, status) |
| `partners.assignment_orders` | assignment_id → assignments, order_id, portions, short_code, status (assigned, ready, picked_up, delivered, failed, released), km; partial unique (order_id) where status ≠ released |
| `partners.assignment_events` | id, assignment_id, action, user_id, payload jsonb, client_at null, at |
| `partners.assignment_runs` | id, hub_id, date, meal, round, source, model, status, portions, unassigned jsonb, summary jsonb, seconds, at |
| `partners.ratings` | id, order_id (unique), partner_id, household_id, user_id, score smallint (1–5), comment null, created_at |
| `partners.rate_cards` | id, hub_id, kind, basis, amount_paise BIGINT, valid_from, created_by; unique (hub_id, kind, basis, valid_from) |

Foreign keys stay inside `partners`. `user_id`, `hub_id`, `order_id`, `slot_id`, `household_id` and society ids are plain UUIDs (ADR-0026 §5). Row-level security on `partners`, `assignments` and `kyc_documents` by `hub_id` (handbook 13). The draft's `partners.trainings` (per partner) becomes `training_attempts` (per user, because training starts before the partner exists), and `partners.earnings` is dropped (§3.6).

## 5. Events
**Published:**
- `partners.application_submitted { application_id, user_id, kind, hub_id, scope_type, scope_id }` (§3.1)
- `partners.application_decided { application_id, user_id, kind, hub_id, outcome }`. `outcome` is `approved`, `resubmit` or `rejected`; the reason text is not in the event, and M11 reads it through the facade.
- `partners.verified { partner_id, user_id, kind, hub_id, requested_society_id }`. `requested_society_id` is set for captains and null otherwise (ADR-0026 §4).
- `partners.suspended { partner_id, user_id, kind, hub_id, reason_code }` (`reason_code: "left"` on account deletion) · `partners.reinstated { partner_id, user_id, kind, hub_id }` (§3.3)
- `partners.assignment_created { assignment_id, partner_id, user_id, hub_id, date, meal, portions, orders_count, respond_by, round }` (§3.4)
- `partners.assignment_released { assignment_id, partner_id, hub_id, date, meal, order_ids, reason }` · `partners.assignment_completed { assignment_id, partner_id, hub_id, date, meal, portions_delivered, orders_failed }`
- `partners.tiffins_unassigned { hub_id, date, meal, run_id, order_ids, reasons }`
- `partners.rating_requested { order_id, household_id, partner_id }` (§3.5)

No event carries KYC data, a number (masked or not), an address or a phone.

**Consumed:**
- `orders.delivered` (kind `tiffin` → assignment order status, completion, kitchen earning)
- `orders.delivery_failed` (kind `tiffin` → status, completion)
- `orders.dispatched` (kind `tiffin` → `picked_up`; actor `rider` → rider earning)
- `orders.at_society` (actor `captain` → captain earning)
- `orders.packed` (actor `packer` → packer earning)
- `identity.user_deleted` (§3.3)

The draft's `intel.assignment_ready` is dropped: the assignment is a synchronous gateway call inside M8's job. So is `orders.locked`: late locks are picked up by the next round.

## 6. Jobs
| Job | Trigger (IST) | Notes |
|---|---|---|
| `assign_kitchens` | T-1 21:30 (cron-job.org → `POST /v1/internal/jobs/assign_kitchens`) | Round 1 for every active hub, both meals of T; ML with greedy fallback; idempotent per order (§3.4) |
| `assignment_acceptance_timeout` | Every 15 min, T-1 21:45 → T 00:30 (cron-job.org) | Expires offers past `respond_by` (22:30 for round 1), then runs the next round for released and late-locked orders; no new round after T 00:00 |
| `prompt_tiffin_ratings` | T 14:30 (cron-job.org) | `partners.rating_requested` for lunch tiffins delivered today and not yet rated |

Each job is idempotent, takes its advisory lock and uses `scheduled_for` (the delivery day T follows from it), not the run time (ADR-0021, ADR-0025). The weekly statement and payouts are M5's jobs (§3.7).

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `partner_applications:write` | customer (own) |
| `partner_applications:review` | hub_manager (hub), admin |
| `kyc_files:read` | hub_manager (hub), admin (every read audit-logged) |
| `trainings:take` | any signed-in user with an open application or a partner record (own) |
| `partners:read` | hub_manager, franchise_owner (hub), admin |
| `partners:suspend` | hub_manager (hub), admin |
| `kitchens:write` | kitchen_partner (own) |
| `kitchens:inspect` | hub_manager (hub), admin |
| `assignments:read` · `assignments:write` | kitchen_partner (own), hub_manager (hub) · kitchen_partner (own) |
| `assignments:manage` | hub_manager (hub), admin (run a round, manual assignment) |
| `ratings:write` | customer (own orders) |
| `earnings:read` | kitchen_partner, captain, rider, packer (own) |
| `partner_rates:read` · `partner_rates:write` | hub_manager, franchise_owner (hub) · admin |

## 8. Screens
**Mobile:**
- Become a partner: role, steps, apply (done on `feat/mobile-partner`); then documents with the camera, training with the quiz, and status.
- Kitchen: Today's counts with accept or decline, Capacity, Prep and ready with the checklist and pickup QR, Ratings.
- Earnings, for every partner kind.
- Know-your-cook card on a tiffin order.
- Rate a tiffin.

**Console:** Partners and KYC review queue (documents with 5-minute links, checks, decision), partner profile with suspend and reinstate, kitchen inspection, tiffin board, fairness dashboard (from `assignment_runs`: Gini and km per portion over time), rate card.

**Mock today vs contract** (`feat/mobile-partner`: `mocks/partners.js`, `features/partners/queries.js`, `lib/session.js`, `mocks/orders.js`)

| Mock today | Contract |
|---|---|
| `status` in upper case (`APPLIED`, `KYC_PENDING`, `TRAINING`, `VERIFIED`, `ACTIVE`, `PAUSED`, `REJECTED`) | Lower case. `applied`, `kyc_pending`, `training`, `verified`, `rejected`, `withdrawn` on the application; `active` and `paused` are on the partner record (`GET /v1/partners/me`), not the application |
| 404 `partners.no_application` | 404 `partners.application_not_found` (the app's "no application" branch keys on the 404, so only the code changes) |
| 422 `partners.invalid_kind` | 422 `partners.kind_invalid` |
| 400 `platform.idempotency_key_required` | 400 `request.idempotency_key_required` (M0) |
| `scope_id` defaults to `"hub-kharghar"` | The server fills the household's society (captain) or hub (others); ids are UUIDs; a captain asking for another society gets 422 `partners.captain_not_resident` |
| Application `{ id, kind, scope_id, status, submitted_at, reason }` | Same fields plus `scope_type`, `hub_id`, `display_name`, `requirements`, `next_step`, `partner_id`, `decided_at`, `verified_at`, `version`; drive the steps from `next_step` and `requirements` |
| "One open application per person" | Same; a verified partner may apply for another kind |
| `lib/session.js` keeps grants whose `role` is in `customer, captain, kitchen, rider, packer` | M1's role is `kitchen_partner`: map `kitchen_partner` → the app's `kitchen` role (today a verified kitchen partner would never see her mode) |
| A tiffin order with `cook: { name }` before it is assigned, and `dish` | `OrderOut.kitchen: { partner_id } \| null` (null until a kitchen accepts); the card from `GET /v1/partners/{id}/card`; the dish is the order line's product name |
| Tiffin `status: "SCHEDULED"` | M4 statuses: `approved` (scheduled), `locked`, `picking` ("Being cooked"), `packed` ("Ready at the kitchen"), `dispatched`, `delivered` (ADR-0026 §3) |

## 9. Tests required
- **Applications (§3.1):**
  - household-derived scope;
  - a captain for another society is refused;
  - one open application per person, but another kind after verification;
  - the same key replays;
  - `applied → kyc_pending` when the last required type arrives;
  - a replacement supersedes;
  - withdrawal closes the application, and its file retention is set.
- **KYC:**
  - an Aadhaar kind or a 12-digit number is refused before any row is written;
  - each format rule;
  - the penny-drop hook (`…0000` fails);
  - duplicate detection across users by hash;
  - `number_enc` round-trips only with the key, and no response, log line or event contains a clear number (a test scans responses and the outbox);
  - file keys of another user or another purpose are refused;
  - every file-link call writes the audit log;
  - only hub staff of that hub get links.
- **Review:**
  - approve with documents missing gives 409;
  - resubmit, then re-upload, goes back to the queue;
  - reject is final;
  - approve with all training passed verifies at once;
  - the last training pass after approval verifies in the same transaction;
  - `partners.verified` is published once, with `requested_society_id` for captains only.
- **Training (§3.2):** answers are never returned; scoring; a pass is kept; the attempt limit; a stale version.
- **Roles** (integration with M1 and M9 consumers):
  - kitchen, rider and packer get their hub role on `verified`;
  - a captain gets `captain:<society>` only after M9's `captain_assigned`;
  - suspension suspends the role, ends the appointment and releases future offers;
  - reinstating does not re-appoint a captain.
- **Assignment (§3.4):**
  - the gateway is mocked to answer, to time out (greedy fallback, deterministic), and to answer `partial`;
  - eligibility: skills, rating floor (with the unrated default), distance, capacity;
  - one offer per kitchen and meal;
  - a re-run adds only orders without a live assignment;
  - decline and expiry release orders, and the next round excludes those kitchens;
  - no round starts after T 00:00;
  - a manual assignment over capacity;
  - accept calls `set_kitchen`, and a cancellation clears it;
  - start and ready move orders with actor `kitchen` and the user id;
  - an incomplete checklist is refused;
  - ready with start skipped moves both steps;
  - an order moved by the hub gives `orders.illegal_transition` and leaves the assignment unchanged;
  - completion from delivery events.
- **Capacity:** the default and override; locked after T-1 21:00; a paused kitchen counts as 0.
- **Card and ratings (§3.5):**
  - the card never contains address, location or phone;
  - the rating window, one per order, only the owner;
  - the average is shown from 5 ratings.
- **Earnings (§3.6):**
  - each basis posts once per order, even when the event is delivered twice;
  - no earning for `hub` or `system` actors;
  - the rate is the one valid on the delivery date;
  - the week view sums equal the ledger (against M5's facade).
- **E2E (MS-B):** apply as a kitchen → documents (simulated checks) → training → hub manager approves → the kitchen role appears → set capacity → cut-off → `assign_kitchens` → accept → start → ready → rider pickup → delivered → earning in the week view; on the simulator seed.

## 10. Dependencies
- **M1** (`identity.api`: household and society of a user, `household_location`, names and phones for hub staff; role grants on M8's and M9's events).
- **M9** (`community.api`: society names and points, `captain_for_society` for the review queue; appoints captains on `partners.verified`).
- **M4** (`orders.api`: `orders_for_delivery` with `kinds`, `transition` with actor `kitchen` and `actor_user_id`, `set_kitchen`, `get_order_ref` for ratings; fulfilment events with `kind`, `actor` and `actor_user_id`).
- **M10** (`intel_gateway.api.assign_kitchens`, M10 §3.5 on `feat/M10-assign-kitchens`).
- **M2** (`catalog.api`: tiffin products and their skill tags).
- **M5** (`billing.api`: `accrue_partner_earning`, `partner_earnings`, `partner_rolling_earnings`, `payout_status`).
- **M0** (`platform.files` for `kyc_document`, audit log, jobs).
- **M7** (rider pickup through `partners.api.pickup`).
- **M11** (notifications for the events above).

**Follow-ups in other specs.** Each is made in the PR that implements it and reviewed by both owners; this spec does not edit them.
- **M1.**
  - On `partners.verified`, grant `kitchen_partner:<hub>`, `rider:<hub>` or `packer:<hub>`, and nothing for captains.
  - On `partners.suspended`, mark that hub role `suspended`; on `partners.reinstated`, mark it `verified` again.
  - On `community.captain_assigned`, grant `captain:<society>`; on `community.captain_ended`, revoke it. This needs a `revoked` role status, or deleting the row.
  - `ROLE_STATUSES` drops `applied` and `kyc_pending`: application status lives in M8.
  - New facade `identity.api.household_location(user_id)` (the pin, else None) for a kitchen's default location; names and phones for hub staff.
  - The data export gains the `partners` section (`partners.api.export_for_user`) and M9's captain appointments.
  - Fix §5's "Consumed": the captain role comes from M9's events (ADR-0026 §4).
- **M3.** `CycleOut.captain` becomes `{ display_name, photo_url } | null`, read through `community.api.captain_card` (ADR-0026 §4).
- **M4.** As listed in ADR-0026: the actors, `kinds` on `orders_for_delivery`, `set_kitchen`, and `kitchen` on `OrderOut`. In addition:
  - fulfilment events carry `actor` and `actor_user_id` (internal; for earnings, §3.6) beside `kind`;
  - `orders_for_delivery` returns tiffin lines and `slot_id`;
  - `get_order_ref(order_id)` returns `{ household_id, owner_user_id, kind, status, delivered_at }` for ratings;
  - `POST /v1/orders` (direct tiffin orders, P3) must exist for a tiffin day;
  - the no-kitchen refund path (§11, question 1).
- **M5.** New facade functions:
  - `accrue_partner_earning(…)` (journal kind `earning`, partner expense → `partner_payable:<partner>`, keyed `earning:<partner>:<basis>:<order>`);
  - `partner_earnings(session, user_id, from, to)`;
  - `partner_rolling_earnings(session, partner_ids, basis, since)`;
  - `payout_status(session, user_id, week)`.

  Also: drop the consumption of `orders.delivered` for captain commission and of `partners.assignment_completed` for earnings (M8 posts them); `weekly_payouts` reads `partners.api.payout_eligible` and `payout_account`; the partner part of `/v1/statements`.
- **M0.** A `partner_photo` purpose if question 3 is approved; KYC file reads by the hub manager of the application's hub (question 4).
- **M2.** Tiffin products carry skill tags, and `catalog.tiffin_menus` is per hub rather than per partner: fair assignment chooses the kitchen, not the customer.
- **M7.** Tiffin pickups through `partners.api.tiffin_pickups` and `pickup` (the `GB1A:` QR); `logistics.handovers.captain_id` becomes the captain's `user_id` from `community.api.captain_for_society`.
- **M9** (outside §3.2). `community.api.society_points(society_ids)` (centroid, else the hub location) for assignment distances.
- **M11.** Event map:
  - `partners.application_decided` and `partners.verified` (to the applicant);
  - `partners.assignment_created` (to the kitchen);
  - `partners.rating_requested` (to the household);
  - `partners.tiffins_unassigned` (to the hub managers);
  - `community.captain_assigned` (to the captain).
- **M12.** The partners and KYC queue, and the fairness dashboard from `partners.assignment_runs`.
- **Handbook.**
  - 06 §6.6: lower-case statuses; training in parallel with review; VERIFIED and ACTIVE merged (the partner is `active` from verification).
  - 05 §5.5.8: the ERD as in §4 (no earnings mirror, trainings per user).
  - 07 W6: rounds every 15 min until T 00:30.
  - 07 W7: documents per kind as in §3.1.

## 11. Open questions and decisions
- **Decided (ADR-0026):**
  - M8 verifies the person; M9 says which captain serves a society; there is no `partners.society_id`;
  - `scope_id` is the requested society or hub;
  - kitchens move tiffins through `orders.api.transition(actor="kitchen")` and record themselves with `set_kitchen`;
  - tiffins use the existing M4 statuses.
- **Decided (this spec):**
  - training can run in parallel with document review;
  - approval plus passed training verifies, whichever comes last;
  - a whole offer is accepted or declined (no partial decline);
  - unanswered offers expire at 22:30 and are reassigned;
  - order locations are society points, never the customer's pin;
  - the FSSAI number is stored and shown in clear (public by law), while every other KYC number is encrypted and masked;
  - the earnings mirror table is dropped (the ledger is the only record).
- **Open, for the owner:**
  1. **Tiffins no kitchen takes.** Orders still unassigned after the last round (T 00:30), and not assigned by hand before the meal, have no path in ADR-0026: a `locked` tiffin can only go forward. Recommendation: an M4 move `locked → refunded` (actor `system`, tiffin only, `reason: no_kitchen`, full refund through `billing.api.refund_to_wallet`), run by a new M8 job at `ready_by` minus 2 h. This amends ADR-0026 §3 and M4.
  2. **Rates** (§3.6): kitchen ₹60 a portion, captain ₹6 a handover, rider ₹8 an order, packer ₹4 an order. Should riders and packers earn per event at all, or are they hub staff on a fixed wage? Should captains earn on `at_society` (as ADR-0026 says) or on `delivered`?
  3. **Partner photos.** The captain card and the cook card have `photo_url`, but M0 has no `partner_photo` purpose. Add one in S6 (images, 2 MB, kept until the partner leaves + 30 days), or ship `photo_url: null` until later?
  4. **Who sees KYC files.** M0 §3.1 says admins only. This spec lets the hub manager of the application's hub open them too, audit-logged, because W7 has the hub manager review documents. Confirm, and M0 §3.1 changes.
  5. **Applicants need a household.** Riders and packers who are not customers would have to onboard as a household first. Alternative: a hub manager creates the application for them in the console.
  6. **Captain requirements.** Residency in the requested society, plus `society_proof` as a required document. Keep both?
  7. **The partner core in S5** (build order (a)): seeded verified captain partners so that M9 and M7 work before applications exist. Confirm.
  8. Training content source and languages (videos in Hindi and Marathi).
  9. **The rating floor** (3.8, the ML default) and the 4.5 used for kitchens with fewer than 5 ratings.
