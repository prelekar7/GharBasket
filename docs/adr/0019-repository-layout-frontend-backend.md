# ADR-0019 — Repository layout: `frontend/` and `backend/`, one folder per module

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D19
- **Amends:** the folder paths in [ADR-0002](0002-react-capacitor-mobile-nextjs-console.md), [ADR-0003](0003-python-fastapi-backend-and-ml-service.md) and [ADR-0014](0014-final-stack-and-service-based-architecture.md) (their decisions are unchanged)

## Context
The monorepo used the usual tool names: `apps/` and `packages/` for the JavaScript side and `services/` for Python.
For a four-person team, the project guide and the viva panel, those names do not say which part is the frontend and
which is the backend. The team asked for a layout where that is obvious, with the backend organised module by module.

## Decision
- Top-level folders by role:
  - `frontend/mobile` (was `apps/mobile`), `frontend/console` (was `apps/console`),
    `frontend/packages/*` (was `packages/*`: `ui`, `api-client`, `domain`);
  - `backend/api` (was `services/api`), `backend/ml` (was `services/ml`);
  - unchanged: `infra/`, `data/`, `e2e/`, `docs/`, `scripts/`, `spikes/`.
- Backend modules live in `backend/api/src/gharbasket/modules/<name>/`, one folder per module, and the folder name is
  also the Postgres schema and the event and permission prefix (`orders.locked`, `orders:read`):

  | Module | Folder and schema | Module | Folder and schema |
  |---|---|---|---|
  | M1 Identity | `identity` | M8 Partners | `partners` |
  | M2 Catalog | `catalog` | M9 Community | `community` |
  | M3 Subscriptions | `subscriptions` (was `subscr`) | M10 Intelligence gateway | `intel_gateway` (schema `intel`) |
  | M4 Orders | `orders` | M11 Notifications | `notifications` (was `notify`) |
  | M5 Billing | `billing` | M12 Analytics | `analytics` |
  | M6 Hub operations | `hub_ops` (was `ops`) | M13 Assistant | `assistant` (was `assist`) |
  | M7 Logistics | `logistics` | M0 Platform | `gharbasket.platform`, schema `platform` |

- The Python package (`gharbasket-api`, import name `gharbasket`) and the JS package names (`@gharbasket/*`) do not change.

## Alternatives considered
- **Keep `apps/` + `packages/` + `services/`** — rejected: the common monorepo convention, but unclear to the people who
  read and assess this project.
- **A separate top-level `shared/` for the JS packages** — rejected: they are used only by the frontend, so they belong
  under it.
- **Numbered module folders (`m4_orders`)** — rejected: longer import paths; the M-number is in each module's docstring.

## Consequences
- (+) Anyone can tell frontend from backend at a glance; module ownership maps to one folder and one schema.
- (+) Done before most code exists: one rename PR, git history follows the files (`git log --follow`).
- (−) Older ADRs and chat history mention the old paths; this ADR is the mapping.
- (−) Open branches had to rebase over the move (git's rename detection carries the changes).

## Related
Handbook [Chapter 11](../handbook/11-engineering-process.md) (repository layout) ·
[Chapter 04](../handbook/04-system-architecture.md) · [`docs/modules/`](../modules/README.md)
