# GharBasket — Module Specifications

Each backend/product module has one specification file. Specs are written **before** a module is built (spec-first) and refined at the start of the phase that builds it. Architecture rules for all modules are in `docs/TDD.md` (Sections 2.3, 4, 5, 10.3).

**Status of all specs:** Draft v1 — refine at phase start (spec-first).

## Index

| ID | Module | File | Owner | Phase / Sprints |
|---|---|---|---|---|
| M0 | Platform | [M0-platform.md](M0-platform.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P1 / S0 |
| M1 | Identity & Household | [M1-identity.md](M1-identity.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P2 / S1 |
| M2 | Catalog & Pricing | [M2-catalog.md](M2-catalog.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P2 / S1 |
| M3 | Plans & Subscriptions | [M3-subscriptions.md](M3-subscriptions.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P2 / S3 |
| M4 | Orders & Checkout | [M4-orders.md](M4-orders.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P2 / S2 |
| M5 | Billing & Ledger | [M5-billing.md](M5-billing.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P2 / S2 |
| M6 | Hub Operations | [M6-hub-ops.md](M6-hub-ops.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P3 / S4 |
| M7 | Logistics | [M7-logistics.md](M7-logistics.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P3 / S5 |
| M8 | Partner Network | [M8-partners.md](M8-partners.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P3 / S6 |
| M9 | Community & Kindness | [M9-community.md](M9-community.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P5 / S9 |
| M10 | Intelligence (ML service) | [M10-intelligence.md](M10-intelligence.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P4 / S7–S8 (simulator from S0) |
| M11 | Notifications | [M11-notifications.md](M11-notifications.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P2 / S3 |
| M12 | Console & Analytics | [M12-console-analytics.md](M12-console-analytics.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P2–P5 |
| M13 | Assistant (LLM) | [M13-assistant.md](M13-assistant.md) | Prathamesh Relekar (backend), Yash Salunkhe (screens) | P4 / S8 |

Ownership (ADR-0023): Prathamesh Relekar owns the backend of every module and Yash Salunkhe its screens; each reviews the other's contract changes (§3). Testing and QA for every module: Rhythm Thakur and Manish Raje (E2E, test cases, QA checklist).

## Rules every module follows

1. Owns exactly one Postgres schema; writes only to it.
2. Exposes a Python facade `gharbasket.modules.<name>.api` — the only import other modules may use (enforced by import-linter).
3. Cross-module side effects go through outbox events (`<module>.<event>`), consumed idempotently by `event_id`.
4. Every POST that creates money movement or orders requires `Idempotency-Key`.
5. Money in integer paise; quantities `NUMERIC(10,3)` + unit.
6. Hub-scoped rows carry `hub_id`; household-derived rows carry `data_source`.
7. Permissions are declared in the module and enforced with `require("<module>:<action>", scope=…)`.

## Spec template

Copy this into a new file `Mx-<name>.md`.

```markdown
# Mx — <Module name>

**Status:** Draft v1 — refine at phase start (spec-first)
**Owner:** Prathamesh Relekar (backend), Yash Salunkhe (screens) · **QA:** Rhythm Thakur, Manish Raje
**Phase / Sprint:** <P# / S#>
**Postgres schema:** `<schema>` · **Python package:** `gharbasket.modules.<name>`

## 1. Purpose
One paragraph: what this module is responsible for and what it is not.

## 2. User stories
| # | Story | Acceptance criteria |
|---|---|---|
| US-1 | As a <role>, I want <capability> so that <benefit>. | Given/When/Then … |

## 3. API endpoints
| Method | Path | Permission / role | Idempotency | Notes |
|---|---|---|---|---|

## 4. Tables owned
| Table | Key columns | Notes |
|---|---|---|

## 5. Events
**Published:** … **Consumed:** …

## 6. Jobs
| Job | Schedule / trigger | Notes |
|---|---|---|

## 7. Permissions
| Permission | Roles (scope) |
|---|---|

## 8. Screens
**Mobile:** … **Console:** …

## 9. Tests required
Unit, integration, property, E2E, load — list the specific scenarios.

## 10. Dependencies (via `api.py` / events only)
…

## 11. Open questions
…
```
