# Architecture Decision Records (ADRs)

Each ADR records one significant decision: the context, what we decided, the alternatives we rejected and why, and the consequences. All decisions below were agreed during planning on 2 October 2026.

**Rules**
- Never rewrite the decision of an accepted ADR. To change it, add a new ADR that supersedes it and mark the old one "Superseded by ADR-XXXX".
- Format: Title · Status · Date · Context · Decision · Alternatives considered · Consequences · Related.
- File name: `NNNN-kebab-case-title.md`, numbered in order.
- Every new ADR also adds a row to [Chapter 15 — Risks and Decisions](../handbook/15-risks-and-decisions.md).

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-headline-usps-autopilot-and-society-captain.md) | Headline USPs: Kitchen Autopilot + Society Captain | Accepted |
| [0002](0002-react-capacitor-mobile-nextjs-console.md) | One TypeScript/React frontend: Vite + Capacitor mobile app (field roles), Next.js console | Accepted |
| [0003](0003-python-fastapi-backend-and-ml-service.md) | Python-only backend: FastAPI modular monolith, ML service, Taskiq workers | Accepted |
| [0004](0004-closed-loop-wallet-and-upi-autopay.md) | Closed-loop wallet (default) + UPI Autopay; KYC only for payees | Accepted |
| [0005](0005-text-assistant-mvp-voice-stretch.md) | Assistant: text MVP, voice stretch | Accepted |
| [0006](0006-postgresql-centric-data-platform.md) | PostgreSQL-centric data platform; no MongoDB/Firebase DB/Elasticsearch | Accepted |
| [0007](0007-research-scope-four-algorithms.md) | One systems paper covering four algorithms | Accepted |
| [0008](0008-simulator-first-data-source-tagging.md) | Simulator first; `data_source` tagging | Accepted |
| [0009](0009-team-ownership-and-tracks.md) | Team ownership and tracks | Accepted |
| [0010](0010-timeline-april-2027-floating-buffer.md) | Timeline to April 2027 with floating buffer | Accepted |
| [0011](0011-hosting-on-aws-student-credits.md) | Hosting on AWS student credits | Accepted (refined by 0015) |
| [0012](0012-android-and-pwa-platforms.md) | Android + PWA; no iOS in v1 | Accepted |
| [0013](0013-assistant-model-chosen-by-eval.md) | Assistant model chosen after S8 evaluation | Accepted |
| [0014](0014-final-stack-and-service-based-architecture.md) | Final stack; service-based architecture, not microservices | Accepted |
| [0015](0015-aws-mumbai-topology-with-neon-postgres.md) | AWS Mumbai topology with Neon Postgres and S3 | Accepted |
| [0016](0016-ui-design-in-claude-design.md) | UI design entirely in Claude Design | Accepted |
| [0017](0017-design-before-code-in-phase-0.md) | Design before code in Phase 0; spec-first later | Accepted |
