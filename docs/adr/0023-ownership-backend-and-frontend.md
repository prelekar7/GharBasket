# ADR-0023 — Ownership: one backend owner, one frontend owner

- **Status:** Accepted (supersedes the code ownership in ADR-0009; the roles of Rhythm Thakur and Manish Raje are unchanged)
- **Date:** 2026-10-04 (decided 2 October 2026)
- **Decision ID:** D23

## Context
ADR-0009 split the code by tracks: Yash Salunkhe had the mobile app and the commerce backend (M1–M5, M11), while Prathamesh Relekar had operations (M6–M8), the console, infrastructure and intelligence. In practice the backend is one modular monolith with a shared platform layer, shared contracts and one migration chain, and it stays consistent more easily with one owner. The split also did not match skills: Prathamesh is stronger in Python and infrastructure, Yash in React and design.

## Decision
| Person | Owns (Accountable and Responsible) |
|---|---|
| **Prathamesh Relekar** | All of `backend/` (every module M0–M13, including M1–M5 and M11), `infra/`, `data/` (simulator), ML and the research results, CI/CD and root configuration |
| **Yash Salunkhe** | All of `frontend/`: the mobile app (every role), the console UI, `packages/ui` and `packages/domain`; design in Claude Design |
| **Rhythm Thakur, Manish Raje** | Unchanged: docs, QA, Playwright end-to-end tests, pilot operations, report, paper and presentation |

- Each owner reviews the other's contract changes: module specs §3 and the generated `api-client`. `CODEOWNERS` follows this table.
- Module spec headers name Prathamesh as backend owner and Yash as owner of the screens.

## Alternatives considered
- **Keep the ADR-0009 tracks:** rejected. Two people editing the same platform layer and migration chain conflicts more and stays less consistent.
- **Feature teams (each person end to end):** rejected for the same reason, and because of the skills split.

## Consequences
- (+) One consistent backend; contracts agreed between exactly two people; fewer merge conflicts.
- (−) Prathamesh's load is higher: backend, ML, simulator and research (risk R1). Mitigations: stretch items are cut first, Rhythm and Manish take all testing and content, and research spikes are scheduled in S0.
- Handbook 14 (RACI and per-phase table) and the M1–M5 and M11 spec headers are updated.

## Related
[ADR-0009](0009-team-ownership-and-tracks.md) · [ADR-0019](0019-repository-layout-frontend-backend.md) · Handbook [Chapter 14](../handbook/14-project-plan.md)
