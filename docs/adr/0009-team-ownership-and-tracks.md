# ADR-0009 — Team ownership and tracks

- **Status:** Code ownership superseded by [ADR-0023](0023-ownership-backend-and-frontend.md); the roles of Rhythm Thakur and Manish Raje still apply
- **Date:** 2026-10-02
- **Decision ID:** D9

## Context
The project is built by four students collaborating on GitHub, and the team explicitly wants module-wise work so that pushes and pulls do not collide. Four tracks were defined: A (customer experience), B (commerce core), C (operations), D (intelligence).

## Decision
- **Yash Salunkhe:** Track A (mobile app for all field roles, `packages/ui` design system, Capacitor build) and Track B (backend modules M1–M5: identity, catalog, subscriptions, orders, billing/ledger) plus M11 notifications.
- **Prathamesh Relekar:** Track C (M6 hub operations, M7 logistics/routing, M8 partners/KYC, Next.js console, infrastructure) and Track D (M10 ML service, M13 assistant, simulator, M12 analytics, research results).
- **Rhythm Thakur and Manish Raje:** handbook and report, IEEE paper writing, UML, UX interviews and design review, Hinglish/Marathi copy and translations, testing/QA (Playwright E2E, k6 load, accessibility, device testing), assistant eval labelling, pilot operations, presentation.
- **CODEOWNERS** maps folders to owners; each owner must approve PRs in their area. Yash and Prathamesh cross-review each other's backend PRs.

## Alternatives considered
- **Four equal coding tracks** — not chosen by the team.
- **Pairing Rhythm and Manish under the code leads** — offered; the team preferred docs + testing roles.

## Consequences
- (+) Clear ownership; module boundaries match people.
- (−) Two people carry all code (risk R1): mitigated by strict MVP, stretch items cut first, and Rhythm and Manish owning all testing, content and pilot operations.
- (−) The viva may probe coding contributions of Rhythm and Manish; their E2E test suites, copy and evaluation work are their defensible artefacts.

## Related
[ADR-0010](0010-timeline-april-2027-floating-buffer.md) · Handbook [Chapter 14](../handbook/14-project-plan.md), [Chapter 11](../handbook/11-engineering-process.md)
