# infra

Owner: Prathamesh Relekar. Local and deployment infrastructure.

| Folder | What | Arrives in |
|---|---|---|
| `postgres/` | PostgreSQL 18 image with PostGIS + pgvector | `chore/M0-infra-compose` |
| `compose/` | Docker Compose for local dev (postgres, valkey, minio, mlflow) and the AWS staging stack | `chore/M0-infra-compose`, `feat/M0-deploy-staging` |
| `osrm/` | Road-network routing data for the Mumbai Metropolitan Region | Spike S-3 |
| `caddy/` | Reverse proxy and TLS for staging/production | `feat/M0-deploy-staging` |

See `docs/handbook/12-deployment-and-operations.md`.
