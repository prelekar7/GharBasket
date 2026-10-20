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

### Real mode (frontends on the local backend)
Both apps normally run on mock data (MSW). Real mode runs them on your local API and database instead:
```bash
just up                   # once per boot (add `just up-storage` if you test uploads)
just dev-real             # migrates, seeds (--refresh), then runs the three processes below; Ctrl-C stops them
just real-check           # in another terminal: signs in as all 13 seed people and checks /v1/me against the seed
```
| Process | Address | What `just dev-real` sets |
|---|---|---|
| API | http://localhost:8000 | `just dev` with `APP_ENV=dev` and `FIREBASE_PROJECT_ID` empty (dev sign-in on) |
| Mobile (customers, captains, kitchen partners, riders, packers) | http://localhost:5173 | `VITE_API_MOCK=` (off), every `VITE_FIREBASE_*` empty, `VITE_API_PROXY_TARGET=http://localhost:8000` |
| Console (admin, hub manager, franchise owner, NGO coordinator) | http://localhost:3000 | `GB_API_MOCK=false GB_API_URL=http://localhost:8000` |

Sign in with a seed phone from the table `just dev-real` prints (`+91 98000 01001` to `01013`, one person per role; the
list is `PEOPLE` in `backend/api/src/gharbasket/tools/seed.py`) and the code `123456`. For example 01001 is a customer on
mobile, 01006 a rider on mobile, 01008 the hub manager and 01011 the admin on the console. To run the pieces by hand, use the
commands in the table's last column in three terminals.

Gotchas:
- **Firebase keys turn dev sign-in off.** The mobile app skips the code check and signs in with `dev_phone` only on
  `vite dev` while `VITE_FIREBASE_API_KEY` is empty. A `frontend/mobile/.env.local` with Firebase keys (from `pnpm env:pull`)
  breaks that when you start Vite yourself; `just dev-real` overrides them with empty values and leaves your file alone.
  `VITE_API_MOCK=true` in the same file is overridden too. Production builds never have dev sign-in.
- **Use Chrome, Chromium or Firefox, and `http://localhost`.** The API's cookie `__Host-gb_at` is `Secure`; those browsers
  accept it on `http://localhost`, Safari does not, and neither does a LAN address or `127.0.0.1`. Use a private window or
  clear cookies for `localhost` when switching between people on the same app.
- **Uploads need `just up-storage`.** Photos and documents go to SeaweedFS (S3 on :8333); without it
  `POST /v1/files/presign` answers 503 `platform.storage_unavailable`. `just dev-real` warns when it is not running.
- **Sign-in is rate limited**: 30 dev sign-ins per IP and 10 per phone in 10 minutes (`auth.rate_limited`, 429). `just real-check`
  uses 13 of them. Wait, or empty the limiter's Valkey database.
- **Some screens show an error screen on purpose.** Both apps were built against mocks first; calls to endpoints that have
  not shipped answer 404 (`http.not_found`). `just contract-gaps` lists every such call, who fixes it and when, in
  `docs/integration/contract-gaps.md`; the hand-kept classes are in `docs/integration/gaps.json`.
- **Payments** settle without Razorpay: a top-up creates a dev order, paid with `POST /v1/dev/razorpay/pay` (see
  `docs/modules/M5-billing.md`). Push notifications and WhatsApp are skipped without credentials; the in-app inbox still works.
- **Time** is the real clock. Cut-off, delivery days and the weekly job depend on the day; `just job <name>` runs a scheduled
  job by hand, and `just e2e-day` simulates a whole day without touching your database.

## 3. Editor
- VS Code or Cursor with: Biome, Python, Ruff, Pylance, Tailwind CSS IntelliSense, EditorConfig.
- Format on save is handled by Biome (JavaScript/JSON/CSS) and Ruff (Python).

## 4. Accounts (shared project owner; credentials in the team password manager, never in git)
Render (API and Key Value), Vercel (PWA and console), Neon (Postgres), Cloudflare R2 (files), Infisical (secrets), Firebase (Phone Auth + FCM), Razorpay (test mode), PostHog, UptimeRobot, Anthropic (from Sprint 8). All on free tiers until the pilot (ADR-0020). Shared secrets live in Infisical; local secrets go in `.env` (git-ignored), copied from `.env.example`. Tests and `just seed` only ever use the local database.

Staging (Render, Neon, R2) is set up once by the backend owner: see handbook 12, "How a change reaches staging". To check it from your machine: `infisical run --env=staging -- just cloud <staging URL>`.

## 5. AI assistants
`AGENTS.md` is the single source of instructions for every AI tool. Edit only that file, then run `just sync-agent-docs`. The pre-commit hook and CI reject out-of-sync copies.
