# ADR-0018 — Frontend in JavaScript, not TypeScript

- **Status:** Accepted (amends ADR-0002 and ADR-0014 on the language only)
- **Date:** 2026-10-04 (decided 2 October 2026; the number was reserved in ADR-0019)
- **Decision ID:** D18

## Context
ADR-0002 and ADR-0014 described one TypeScript/React frontend. Yash Salunkhe owns all frontend code (ADR-0023) and knows JavaScript much better than TypeScript. Writing every screen in TypeScript would slow the frontend track, which already carries the customer app, the partner roles and the console.

## Decision
- `frontend/mobile` (Vite + React → PWA + Capacitor 8), `frontend/console` (Next.js 16) and `frontend/packages/*` are written in **JavaScript** (`.js` / `.jsx`).
- Types are kept as editor help, not as a gate: JSDoc comments, `jsconfig.json` with `checkJs`, and the generated API types (`schema.d.ts`, ADR-0022) for request and response shapes. CI runs these checks as "JS editor hints (informational, never blocks)".
- Everything else from ADR-0002 is unchanged: React, Vite, Capacitor, Next.js, one design system.

## Alternatives considered
- **TypeScript everywhere** (the earlier plan): rejected. It is safer in theory, but the frontend owner would be slower and less confident.
- **TypeScript only in `packages/`:** rejected. Mixing the two languages makes the codebase harder to follow, for little gain.

## Consequences
- (+) Faster screen work by the person who writes it; less build configuration.
- (−) Shape mistakes are found at run time, not at compile time. This is mitigated by the generated API types, by Playwright end-to-end tests (Rhythm and Manish) and by contract-first module specs.
- Documentation that said "TypeScript", "`tsc`" or "`.ts`" for the frontend now reads JavaScript; the backend stays Python with pyright.

## Related
[ADR-0002](0002-react-capacitor-mobile-nextjs-console.md) · [ADR-0014](0014-final-stack-and-service-based-architecture.md) · [ADR-0022](0022-api-client-openapi-fetch.md) · [ADR-0023](0023-ownership-backend-and-frontend.md) · Handbook [Chapter 09](../handbook/09-frontend-and-design.md)
