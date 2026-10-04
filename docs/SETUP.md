# Developer setup

Everyone installs the same toolchain so `just up && just dev` works on every laptop. Rhythm Thakur and Manish Raje verify this checklist on their machines in Sprint 0 and keep it current.

## 1. Tools

| Tool | Version | macOS (Homebrew) | Windows |
|---|---|---|---|
| Git | latest | `brew install git` | Git for Windows |
| Node.js | 24 LTS (see `.nvmrc`) | `brew install fnm && fnm install 24 && fnm use 24` | `winget install Schniz.fnm` |
| pnpm | 12 (pinned in `package.json`) | `corepack enable` (ships with Node) | `corepack enable` |
| Python | 3.13 (see `.python-version`) | `uv python install 3.13` | `uv python install 3.13` |
| uv | latest | `brew install uv` | `winget install astral-sh.uv` |
| just | latest | `brew install just` | `winget install Casey.Just` |
| Docker Desktop | latest | Docker Desktop for Mac | Docker Desktop (WSL 2 backend) |
| Android Studio | latest | Android Studio + SDK + one emulator (API 34+) | same |
| gitleaks (optional locally; CI always runs it) | latest | `brew install gitleaks` | `winget install gitleaks` |
| GitHub CLI | latest | `brew install gh && gh auth login` | `winget install GitHub.cli` |

Windows users: clone inside WSL 2 for the backend, so the bash scripts and the `justfile` work unchanged.

## 2. First run
```bash
git clone https://github.com/prelekar7/GharBasket
cd GharBasket
just install          # pnpm install (also installs git hooks) + uv sync
just                  # lists every command
```
### Local infrastructure
```bash
cp .env.example .env     # required: just migrate and just dev read DATABASE_URL from it
just up                  # Postgres 18 (PostGIS, pgvector) + Valkey          ~400 MB RAM
just up-storage          # + SeaweedFS S3 with buckets gb-media, gb-lake, gb-mlflow
just up-all              # + MLflow tracking server and model registry       ~1.2 GB RAM in total
just infra-check         # verifies versions, extensions and services
just psql                # psql shell in the local database
just down                # stop (data is kept)      just reset   # stop and DELETE all local data
```

| Service | Host address | Notes |
|---|---|---|
| PostgreSQL 18 | `127.0.0.1:5432` | user/password/db `gharbasket`; also `gharbasket_test` and `mlflow` databases |
| Valkey | `127.0.0.1:6380` | 6380, not 6379, so it never clashes with a Redis installed on your Mac |
| S3 (SeaweedFS) | `http://127.0.0.1:8333` | key `gharbasket`, secret `gharbasket-local-secret`, region `ap-south-1` |
| MLflow | `http://127.0.0.1:5001` | 5001, not 5000, because macOS AirPlay Receiver uses 5000 |

Everything binds to `127.0.0.1` only. The Postgres image is built locally from the official multi-arch `postgres:18`, so it runs natively on Apple Silicon (the `postgis/postgis` image is amd64-only and would run under slow emulation). MinIO is not used because its community Docker images are no longer published; SeaweedFS speaks the same S3 API.

Docker Desktop settings that work well on an 8 GB laptop: Resources, Memory 3-4 GB, CPUs 4, Swap 1 GB; General, use the Virtualization framework and VirtioFS.

### Run the API
```bash
just migrate             # create or upgrade the tables
just seed                # synthetic people, one per role (safe to run again)
just dev                 # API on http://localhost:8000, with reload
```
Open **http://localhost:8000/v1/docs** (the docs live under `/v1`). Sign in with `POST /v1/auth/exchange` and
`{"dev_phone": "+919800001001", "device": {"platform": "web", "push_token": null}}`, copy `access_token`
into **Authorize**, then try `GET /v1/me`. The seeded phones and their roles are listed in `backend/api/README.md`.
`just test` runs the backend tests against the `gharbasket_test` database.

## 3. Editor
- VS Code or Cursor with: Biome, Python, Ruff, Pylance, Tailwind CSS IntelliSense, EditorConfig.
- Format on save is handled by Biome (JavaScript/JSON/CSS) and Ruff (Python).

## 4. Accounts (shared project owner; credentials in the team password manager, never in git)
Render (API and Key Value), Vercel (PWA and console), Neon (Postgres), Cloudflare R2 (files), Infisical (secrets), Firebase (Phone Auth + FCM), Razorpay (test mode), PostHog, UptimeRobot, Anthropic (from Sprint 8). All on free tiers until the pilot (ADR-0020). Shared secrets live in Infisical; local secrets go in `.env` (git-ignored), copied from `.env.example`. Tests and `just seed` only ever use the local database.

## 5. AI assistants
`AGENTS.md` is the single source of instructions for every AI tool. Edit only that file, then run `just sync-agent-docs`. The pre-commit hook and CI reject out-of-sync copies.
