# ADR-0022 — Generated API client: openapi-typescript + openapi-fetch + openapi-react-query

- **Status:** Accepted (replaces the Hey API choice in ADR-0014 and TDD §3.4)
- **Date:** 2026-10-04 (decided 2 October 2026)
- **Decision ID:** D22

## Context
The backend publishes its contract as `backend/api/openapi.json` (CI fails if it drifts from the code). The frontend needs a client generated from it, so request and response shapes cannot drift. The earlier plan named Hey API (`@hey-api/openapi-ts`), which generates a TypeScript SDK. The frontend is JavaScript (ADR-0018).

## Decision
- **`openapi-typescript`** turns `openapi.json` into one `schema.d.ts` file: types only, no runtime code. JavaScript files use it through JSDoc for editor hints.
- **`openapi-fetch`** is the runtime client: a small `fetch` wrapper typed by `schema.d.ts`. Paths and bodies are checked in the editor. Browser builds send `credentials: "include"` and `X-GB-Client: web` (ADR-0024).
- **`openapi-react-query`** adds TanStack Query hooks on top (`useQuery`, `useMutation`) for caching and retries.
- The package will live in `frontend/packages/api-client` (PR `feat/M1-api-client`). It is **generated only** (`just gen-api`) and never edited by hand. CI regenerates it and fails on any difference.

## Alternatives considered
- **Hey API:** rejected. It generates a TypeScript SDK and plugin code that a JavaScript codebase would carry without benefit.
- **Hand-written fetch calls:** rejected. This is how the mobile mocks drifted from the backend (missing `Idempotency-Key`, wrong field names).
- **orval:** viable, but it generates more code; `openapi-fetch` keeps the runtime tiny.

## Consequences
- (+) One source of truth; tiny runtime; editor hints in JavaScript.
- (−) Problem responses still need a small shared helper (reading `code`, retrying with a new `Idempotency-Key` only after an edit).
- The hand-written client and mocks in `frontend/mobile` move to the generated client after the `feat/M1-api-client` PR.

## Related
[ADR-0018](0018-javascript-not-typescript-frontend.md) · [ADR-0014](0014-final-stack-and-service-based-architecture.md) · Handbook [Chapter 10](../handbook/10-api-and-integration.md)
