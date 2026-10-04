# M8 — Partner Network

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** P3 / S6
**Postgres schema:** `partners` · **Python package:** `gharbasket.modules.partners`

## 1. Purpose
The women-led partner network and other field partners: applications from inside the customer app, KYC (simulated in the prototype), FSSAI registration capture for kitchens, training modules, verification and activation of roles, kitchen capacity and menu skills, tiffin assignments (fair assignment computed by M10), ratings, earnings views and payout eligibility. Money itself lives in M5's ledger.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a customer, I want to apply to become a Society Captain or kitchen partner from the app so that I can earn. | Application with role and society/hub; status `APPLIED`. |
| US-2 | As an applicant, I want to upload KYC documents securely so that I can be verified. | Presigned private uploads; masked ID; FSSAI number for kitchens; bank via penny drop (simulated). |
| US-3 | As an applicant, I want short training modules with a quiz so that I learn hygiene and procedures. | Completion and score recorded per module. |
| US-4 | As a hub manager, I want to review and verify partners so that only trained, verified people operate. | Approve/reject with reason; `partners.verified` → role activated in M1. |
| US-5 | As a kitchen partner, I want to set daily capacity and menu skills so that I receive manageable orders. | Capacity per date/meal; skills list. |
| US-6 | As a kitchen partner, I want tomorrow's counts by 21:30 so that I can buy and plan. | Assignment published after fair assignment; accept/decline window. |
| US-7 | As a partner, I want to see earnings and download an income certificate so that I can prove income (e.g. for loans). | Weekly earnings view from ledger; PDF certificate. |
| US-8 | As a customer, I want to see who cooked my tiffin, with FSSAI number and hygiene score, so that I trust it. | "Know your cook" card data exposed. |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|
| POST | `/v1/partner-applications` | customer | Required | Role + scope |
| GET | `/v1/partner-applications/me` | applicant | — | Status |
| POST | `/v1/kyc-documents` | applicant | Required | File key from presign |
| GET | `/v1/trainings` · POST `/v1/trainings/{code}/complete` | applicant/partner | Required (POST) | |
| GET | `/v1/hubs/{id}/partner-applications` | hub_manager | — | Review queue |
| POST | `/v1/partner-applications/{id}/decision` | hub_manager | Required | Approve/reject |
| GET / PUT | `/v1/kitchen-capacity?date=` | kitchen_partner | Required (PUT) | |
| GET | `/v1/assignments?date=` | kitchen_partner (own), hub_manager | — | |
| POST | `/v1/assignments/{id}/accept` · `/decline` · `/ready` | kitchen_partner | Required | |
| GET | `/v1/earnings?week=` | partner | — | From ledger via `billing.api` |
| GET | `/v1/partners/{id}/public` | Authenticated | — | Know-your-cook card |
| POST | `/v1/partners/{id}/suspend` | hub_manager, admin | Required | |

## 4. Tables owned
| Table | Key columns |
|---|---|
| `partners.partners` | id, user_id, kind (kitchen, captain, rider, packer), hub_id, society_id, status, rating, fssai_reg_no, bank_ref_masked |
| `partners.applications` | id, user_id, kind, scope_id, status, submitted_at, decided_by, reason |
| `partners.kyc_documents` | id, partner_id, doc_type, file_key, status, reviewed_by (numbers encrypted with pgcrypto) |
| `partners.trainings` | partner_id, module_code, completed_at, score |
| `partners.kitchen_capacity` | partner_id, date, meal, max_portions, menu_skills[] |
| `partners.assignments` | id, partner_id, ref_type, ref_id, date, portions, status |
| `partners.ratings` | id, partner_id, order_id, score, comment |
| `partners.earnings` | id, partner_id, ref_type, ref_id, amount_paise, week (reporting mirror of the ledger) |

## 5. Events
**Published:** `partners.verified`, `partners.suspended`, `partners.assignment_created`, `partners.assignment_completed`.
**Consumed:** `orders.locked` (tiffin counts), `intel.assignment_ready`, `orders.delivered` (ratings prompt).

## 6. Jobs
| Job | Schedule | Notes |
|---|---|---|
| `assign_kitchens` trigger | T-1 21:30 | Calls M10 fair assignment; fallback greedy nearest |
| `assignment_acceptance_timeout` | T-1 22:30 | Reassign declined/unanswered |
| `weekly_earnings_statement` | Monday 09:00 | Hands to M5 payouts |

## 7. Permissions
| Permission | Roles (scope) |
|---|---|
| `applications:decide` | hub_manager (hub) |
| `capacity:write` | kitchen_partner (own) |
| `assignments:write` | kitchen_partner (own), hub_manager |
| `partners:suspend` | hub_manager, admin |

## 8. Screens
**Mobile:** Become a partner (application, documents, training), Kitchen — Today counts, Menu editor, Prep status, Training, Earnings; Captain/Rider/Packer earnings. **Console:** Partners and KYC review queue, partner profile, fairness dashboard (earnings distribution).

## 9. Tests required
- Unit: state machine `APPLIED → KYC_PENDING → TRAINING → VERIFIED → ACTIVE ⇄ PAUSED → SUSPENDED`.
- Integration: verification activates the role in M1 via event; KYC documents never exposed unmasked.
- E2E: apply → train → verify → role appears → accept assignment → ready.

## 10. Dependencies
M1 (roles), M5 (earnings/payout via `billing.api`), M10 (fair assignment), M2 (tiffin menus), M0 (presigned uploads).

## 11. Open questions
- Training content source and languages (videos in Hindi/Marathi).
- Rating floor for receiving assignments (proposed 3.8).
