# Architecture Decision Records (ADRs)

Each ADR records one significant decision: the context, what we decided, the alternatives we rejected and why, and the consequences. ADRs 0001–0017 were agreed during planning on 2 October 2026; later ones carry their own dates.

**Rules**
- Never rewrite the decision of an accepted ADR. To change it, add a new ADR that supersedes it and mark the old one "Superseded by ADR-XXXX".
- Format: Title · Status · Date · Context · Decision · Alternatives considered · Consequences · Related.
- File name: `NNNN-kebab-case-title.md`, numbered in order.
- Every new ADR also adds a row to [Chapter 15 — Risks and Decisions](../handbook/15-risks-and-decisions.md).

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-headline-usps-autopilot-and-society-captain.md) | Headline USPs: Kitchen Autopilot + Society Captain | Accepted |
| [0002](0002-react-capacitor-mobile-nextjs-console.md) | One React frontend: Vite + Capacitor mobile app (field roles), Next.js console | Accepted (language amended by 0018) |
| [0003](0003-python-fastapi-backend-and-ml-service.md) | Python-only backend: FastAPI modular monolith, ML service, Taskiq workers | Accepted (Taskiq deferred by 0021) |
| [0004](0004-closed-loop-wallet-and-upi-autopay.md) | Closed-loop wallet (default) + UPI Autopay; KYC only for payees | Accepted |
| [0005](0005-text-assistant-mvp-voice-stretch.md) | Assistant: text MVP, voice stretch | Accepted |
| [0006](0006-postgresql-centric-data-platform.md) | PostgreSQL-centric data platform; no MongoDB/Firebase DB/Elasticsearch | Accepted |
| [0007](0007-research-scope-four-algorithms.md) | One systems paper covering four algorithms | Accepted |
| [0008](0008-simulator-first-data-source-tagging.md) | Simulator first; `data_source` tagging | Accepted |
| [0009](0009-team-ownership-and-tracks.md) | Team ownership and tracks | Code ownership superseded by 0023 |
| [0010](0010-timeline-april-2027-floating-buffer.md) | Timeline to April 2027 with floating buffer | Accepted |
| [0011](0011-hosting-on-aws-student-credits.md) | Hosting on AWS student credits | Superseded by 0020 |
| [0012](0012-android-and-pwa-platforms.md) | Android + PWA; no iOS in v1 | Accepted |
| [0013](0013-assistant-model-chosen-by-eval.md) | Assistant model chosen after S8 evaluation | Accepted |
| [0014](0014-final-stack-and-service-based-architecture.md) | Final stack; service-based architecture, not microservices | Accepted (amended by 0018, 0020–0022) |
| [0015](0015-aws-mumbai-topology-with-neon-postgres.md) | AWS Mumbai topology with Neon Postgres and S3 | Superseded by 0020 |
| [0016](0016-ui-design-in-claude-design.md) | UI design entirely in Claude Design | Accepted |
| [0017](0017-design-before-code-in-phase-0.md) | Design before code in Phase 0; spec-first later | Accepted |
| [0018](0018-javascript-not-typescript-frontend.md) | Frontend in JavaScript, not TypeScript (JSDoc + generated types for editor hints) | Accepted |
| [0019](0019-repository-layout-frontend-backend.md) | Repository layout: `frontend/` and `backend/`, one folder per module | Accepted |
| [0020](0020-free-tier-hosting-render-vercel-neon-r2.md) | Hosting on free tiers: Render (API, Key Value), Vercel, Neon, Cloudflare R2, Infisical, PostHog | Accepted |
| [0021](0021-jobs-without-a-broker.md) | Jobs without a broker until the pilot: Postgres outbox + GitHub Actions schedule calling internal job endpoints | Accepted |
| [0022](0022-api-client-openapi-fetch.md) | Generated API client: openapi-typescript + openapi-fetch + openapi-react-query | Accepted |
| [0023](0023-ownership-backend-and-frontend.md) | Ownership: Prathamesh all backend, infra and ML; Yash all frontend | Accepted |
| [0024](0024-sign-in-firebase-otp-own-tokens.md) | Sign-in: Firebase phone OTP, then our own rotating tokens; cookies + CSRF for browsers | Accepted |
| [0025](0025-ml-runtime-spaces-actions-r2.md) | ML runtime: serve on Hugging Face Spaces, train in GitHub Actions, R2 lake and registry, cron-job.org for minute-level jobs | Accepted (amended by 0029) |
| [0026](0026-operations-contract.md) | Operations contract: units, order actors, tiffin states, Society Captains in M9, hubs in M6 | Accepted (amended by 0027) |
| [0027](0027-operations-contract-amendments.md) | Operations contract amendments: unassigned tiffins refunded, boxes missed twice (perishables refunded, the rest carried over), captain pay at `delivered`, M7's order moves, the preferred cook | Accepted |
| [0028](0028-m7-order-moves-by-hub-managers-and-orphan-redeliveries.md) | M7 order moves: a hub manager's tap for a rider moves the orders as `rider`; a redelivery no route picked up can be refunded (`redelivery → refunded`) | Accepted |
| [0029](0029-public-repository.md) | The repository is public: Actions minutes are free, so CI and the staging deploy run on every change; nothing secret or personal may be committed | Accepted |
