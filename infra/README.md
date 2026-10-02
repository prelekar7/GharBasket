# infra

Owner: Prathamesh Relekar. Local and deployment infrastructure.

| Folder | What | Status |
|---|---|---|
| `postgres/` | PostgreSQL 18.6 image built from the official multi-arch `postgres` image, with PostGIS 3.6 and pgvector. `initdb/` creates the `gharbasket`, `gharbasket_test` and `mlflow` databases and their extensions on first start | done |
| `compose/` | Docker Compose for local development: `postgres` and `valkey` always; profile `storage` adds SeaweedFS (S3) and bucket creation; profile `ml` adds MLflow | done |
| `mlflow/` | MLflow 3.16 tracking server and model registry image (Postgres backend, S3 artifacts, job runner disabled to save RAM) | done |
| `osrm/` | Road-network routing data for the Mumbai Metropolitan Region | spike S-3 |
| `caddy/` | Reverse proxy and TLS for staging and production | `feat/M0-deploy-staging` |

Use the `justfile`, not raw `docker compose`: `just up`, `just up-storage`, `just up-all`, `just down`, `just reset`, `just infra-check`. Ports, credentials and RAM budget are in `docs/SETUP.md`.

See `docs/handbook/12-deployment-and-operations.md` for the staging and production topology.
