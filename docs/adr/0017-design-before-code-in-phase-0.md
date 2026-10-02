# ADR-0017 — Design before code: UI design in Phase 0, spec-first for later phases

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D17

## Context
Designing alongside code risks an inconsistent look; designing all ~40 screens upfront delays coding by two to three weeks and some designs would change. The team wants thorough planning and documentation before implementation.

## Decision
- **Phase 0 (now → 18 Oct 2026):** the Design System and the customer core screens — onboarding, Week (Thali), Box (Parchi), Substitute sheet, Wallet, Society unlock — are designed and reviewed before any product code.
- **Console screens** are designed at the start of P2/P3; **field-role screens** (captain, kitchen, rider, packer) at the start of P3.
- Each phase starts by writing its module specs in `docs/modules/` (spec-first).

## Alternatives considered
- **All screens upfront** — rejected: delays Sprint 0, and later screens would change.
- **Design alongside code** — rejected: higher risk of an inconsistent, generic look.

## Consequences
- (+) Core experience is settled before code; later screens benefit from real implementation learnings.
- (−) Phase 0 has a fixed design workload that must finish by 18 October.

## Related
[ADR-0016](0016-ui-design-in-claude-design.md) · [ADR-0010](0010-timeline-april-2027-floating-buffer.md) · Handbook [Chapter 14](../handbook/14-project-plan.md)
