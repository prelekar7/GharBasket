# ADR-0036 — A production environment for the pilot, apart from staging

- **Status:** Proposed (2026-11-14)
- **Date:** 2026-11-14
- **Decision ID:** D36
- **Relates to:** [ADR-0020](0020-free-tier-hosting-render-vercel-neon-r2.md) (free-tier hosting), [ADR-0021](0021-jobs-without-a-broker.md) (jobs), [ADR-0030](0030-ml-service-on-render-free.md) (ML on Render free; the shared 750 hours), [ADR-0032](0032-scheduled-jobs-from-the-oracle-vm.md) (the VM scheduler), [ADR-0033](0033-ops-alerts-healthchecks-and-uptimerobot.md) (ops alerts), [ADR-0035](0035-custom-domain-gharbasket-co-in.md) (the domain; "a production environment takes the names over"). Handbook 12 §12.1, §12.4 and "Open" (Q3).

## Context
Staging is the only deployed environment. It is built for testing, not for real customers:
- **It holds synthetic data and test users.** The demo loader (`just staging-demo`) and the simulator write to it, and it is rolled back to restore points.
- **It has no point-in-time restore.** It is a Neon child branch (handbook 12 §12.4), and the backup is a weekly dump.
- **Payments are test-only.** It runs Razorpay test keys and Firebase test phone numbers.
- **Its secrets have been handled during development.** They were used on laptops and in CI and GitHub, and the demo uses real team phone numbers.

The pilot (handbook 14, S10) takes real money from real households in one or two Panvel societies. It needs an environment where none of the above is true, and where a staging mistake cannot reach a customer.

Two facts limit the choices:
- **Render's free plan gives a workspace 750 instance-hours a month** (ADR-0030). The staging API, kept awake by UptimeRobot, uses nearly all of them. A free production API in the same workspace would leave one of the two asleep part of the month.
- **Vercel's free Hobby plan is for non-commercial use** (handbook 12, "Vercel projects"). That covers staging on test keys, not a pilot that takes payments.

## Decision
**Production is a second, complete copy of the deployment, sharing nothing that holds data or secrets with staging.**

| Part | Production | Staging (unchanged unless said) |
|---|---|---|
| Secrets | Infisical `prod`, every value **generated fresh** (JWT signing key, refresh pepper, KYC key and pepper, `JOBS_TOKEN`, `ORIGIN_SECRET`, `ML_SERVICE_TOKEN`), never copied from staging | Infisical `staging` |
| Database | Its **own Neon project** (not a branch of staging's). It is a root branch, so it has instant restore (6 hours on the free plan) and its own 100 CU-hours. The region stays Q3: Neon Singapore, or a paid Postgres in Mumbai | Neon project `late-term-93583037`, child branch `staging` |
| API | A Render web service `gharbasket-api-prod`, `APP_ENV=prod` (no `/docs`). **The host plan is decided at pilot time** (owner, 14 Nov): it cannot be the free plan beside an awake staging API. The repo works on either | `gharbasket-api`, free |
| Valkey | Its own instance | Render Key Value, free |
| ML service | Its **own** service `gharbasket-ml-prod` (owner, 14 Nov), with its own token and its own model-registry prefix in R2. Promoted models are copied to production; nothing trained on synthetic data is promoted there unless the eval report says so | `gharbasket-ml` |
| Files | Its own private R2 buckets (uploads and KYC documents, backups `gb-prod-backups`) and its own R2 tokens | `gb-…` staging buckets |
| Sign-in | Its own Firebase project, on the paid plan that sends real SMS, with no test phone numbers | The current project, with test numbers |
| Payments | Razorpay **live** keys and their own webhook secret, once Razorpay's KYC is approved | Razorpay test mode |
| Messages | FCM and WhatsApp credentials of the production Firebase project and the verified WhatsApp sender | Test credentials |
| Scheduled jobs | The Oracle VM scheduler gets a second target (prod URL and prod `JOBS_TOKEN`, its own healthchecks checks). Minute-level windows as in `infra/jobs/schedule.yaml` | Unchanged |
| Alerts | Its own healthchecks.io checks (`prod-cutoff`, `prod-scheduler`, `prod-ops`), UptimeRobot on the prod `/v1/health`, Grafana service names `gharbasket-api-prod` and `gharbasket-ml-prod`, and the Neon usage check (`neon-usage.yml`) for the prod project | Unchanged |
| Backups | A **daily** `pg_dump` to `gb-prod-backups` (kept 30 days) on top of Neon's instant restore. The restore runbook gets a production section, and a drill is done before the first real order | Weekly dump, 8 kept |
| Frontends | The production PWA and console. **Their host is decided at pilot time with Yash Salunkhe** (owner, 14 Nov): Vercel Pro, or a host whose free plan allows commercial use | Vercel Hobby |
| Data | **Starts empty.** It gets only the real hub, the real societies, the catalog and price list, the festival calendar and staff role grants, through migrations and an explicit, reviewed load. No synthetic people, orders or demo weeks | Synthetic and test data |

**Domains (owner, 14 Nov).** Production takes the names of ADR-0035:
- `api.gharbasket.co.in` serves the production API.
- `app.gharbasket.co.in` serves the production PWA.
- `gharbasket.co.in` serves the production console.

Staging moves to `staging-api.gharbasket.co.in`, `staging-app.gharbasket.co.in` and `staging.gharbasket.co.in`. The change happens in one step, just before the pilot. Every place that names staging's address moves with it (handbook 12 §12.2): the VM scheduler, the mandi collector, the Capacitor staging build, UptimeRobot, the Razorpay test webhook, Firebase's authorised domains, `WEB_ORIGINS` and `STAGING_API_URL`.

**Deploying to production.**
- A workflow `deploy-prod.yml`, started by hand (`workflow_dispatch`), with the commit to deploy as its input.
- It runs in a GitHub environment `production` that needs the owner's approval, and it has its own secrets.
- It refuses a commit that is not on `main` or whose `Deploy staging` run did not succeed.
- Then it runs migrations on the production database (`GB_ALLOW_REMOTE_MIGRATIONS=1`), calls the production deploy hook, waits for `/v1/health` to report the commit, and runs the smoke test.
- Merging to `main` keeps deploying staging only.

**Guards in code** (built with this ADR, before production exists):
- `just seed`, `just staging-demo`, the simulator's `--allow-remote` path and the restore-point rollback refuse to run when the target is the production database. The refusal is by Neon project id and `APP_ENV=prod`, not only by the typed host confirmation.
- **Razorpay keys follow the environment.** Today `RAZORPAY_KEY_ID` must match `rzp_test_` everywhere, so no real money can move (M5). That rule changes in one place:
  - `APP_ENV=prod` requires live keys (`rzp_live_`) and refuses test keys;
  - every other environment keeps refusing live keys.

  It is the one code change that lets real money move, so it is reviewed as a money PR (Opus review, handbook 11 §11.3).
- The API already allows dev login only with `APP_ENV=dev`. The console's `GB_DEV_LOGIN` must not be set on the production console; the production setup checklist checks that.
- `PAYOUTS_PROVIDER` stays `simulated` in production until M5 builds Razorpay Route. How partners are actually paid during the pilot is an open question, settled with M5 §3.5 before the pilot.
- Firebase test phone numbers live in the Firebase console, not in code. The production Firebase project must have none; the setup checklist checks that.

## Alternatives considered
- **Use staging for the pilot.** Wipe it and switch it to live keys. It costs nothing, but it keeps the child-branch database without point-in-time restore. Its secrets have already been handled in development. And every test, demo load or experiment would run beside real money. Rejected.
- **Production as a branch of staging's Neon project.** That is cheap, but a branch of a branch has no instant restore. It would share staging's 100 CU-hours and 10-branch limit, and a project-level mistake would take out both. Rejected.
- **Share staging's ML service.** It is stateless serving, so it would cost nothing extra. But a staging experiment, a model promoted for the simulator, or staging's free-hour budget would reach real customers. Rejected by the owner.
- **Production on free Render next to staging.** The 750 workspace hours cannot keep both awake. Letting staging sleep would make its minute-level jobs miss their windows. Not chosen; the host plan is decided at pilot time.
- **Keep the main names on staging, and production on new names.** No change for the team, but customers would see odd names, and Razorpay and the Play listing want the main name. Rejected by the owner.

## Consequences
- (+) A staging mistake (a demo load, a rollback, a test key, a leaked development secret) cannot reach a customer's money or data.
- (+) Production has point-in-time restore and daily dumps. Staging keeps its cheap setup.
- (+) Production deploys are deliberate and approved, and they only take a commit staging has already run.
- (−) Production costs money. At least the API host (Render's free hours are used), the frontend host for commercial use, Firebase SMS, and likely the ML service. Prices are checked on the providers' own pages when the owner chooses, before the pilot.
- (−) Two of everything to keep in step. Secrets, alerts, the scheduler and runbooks each get a production entry. Handbook 12 lists them in one table.
- (−) The domain move touches every staging caller once. It is one checklist in handbook 12 §12.2, done in one sitting before the pilot.
- (−) Outside the code: Razorpay live mode needs business KYC, and WhatsApp needs Meta's business verification. Both need the legal and food registrations (GST, FSSAI/FoSCoS) that the team handles outside this repository.

## Order of work (each step a PR the owner approves)
1. This ADR.
2. Code guards (seed, demo, simulator and rollback refuse production), with tests. Then, separately, the Razorpay key rule (live keys only in `APP_ENV=prod`) as a money PR.
3. `deploy-prod.yml`, `backup-prod.yml`, and the production rows in `render.yaml` (the plan left as a value the owner sets). Inert until the production secrets exist.
4. Handbook 12: a "Production" section with the one-time setup steps for the owner, the secrets table and the domain-move checklist. Production sections in the restore, rollback and rotate-secrets runbooks.
5. Owner steps: Infisical `prod`, the Neon project, the Render services, R2 buckets, the Firebase project, Razorpay live, healthchecks checks. Then the first deploy, a restore drill, and the reviewed load of the real hub, societies and catalog.
6. The domain move, just before the pilot.
