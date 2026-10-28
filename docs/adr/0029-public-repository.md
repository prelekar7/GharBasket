# ADR-0029 — The repository is public

- **Status:** Accepted (2026-10-27, by the owner, Prathamesh)
- **Date:** 2026-10-28
- **Decision ID:** D29
- **Amends:** [ADR-0025](0025-ml-runtime-spaces-actions-r2.md), the alternative "Making the repository public for unlimited Actions minutes: declined by the team" and "The repository stays private"; ADR-0025 is otherwise unchanged

## Context
GitHub Actions stopped starting jobs on the private repository ("recent account payments have failed or your spending limit needs to be increased"). From early October every check ran on a laptop through `scripts/ci-local.sh`, and the Deploy staging workflow, which migrates Neon and deploys Render only after CI passes on `main`, never ran. When the staging cloud went live on 27 October that left migrations and deploys to manual steps.

## Decision
The repository `prelekar7/GharBasket` is public (owner, 27 October 2026). Actions minutes are free for public repositories, so CI runs on every pull request and push again, and every green CI run on `main` migrates Neon and deploys Render through `deploy-staging.yml`.

- Before it was made public, a full-history scan (`gitleaks git --redact`, 873 commits) found no secrets; its four hits were false positives in `docs/TDD.md` and handbook chapter 4.
- Secrets stay where they were: Infisical, the Render dashboard and GitHub Actions secrets. `.env` files are never committed, and gitleaks runs in CI on every change.
- cron-job.org keeps calling the minute-level jobs (ADR-0025). Moving them to Actions schedules is possible now but not needed.

## Alternatives considered
- **Fix the billing and stay private:** the team prefers no card on any account (ADR-0020); a private repository would also keep the 2,000-minute monthly cap.
- **Keep `scripts/ci-local.sh` as the only CI:** it ties every merge to one laptop and leaves the staging deploy manual.

## Consequences
- (+) CI and the staging deploy run for every change without a laptop; CI on `main` cancels superseded runs, so only the newest commit deploys.
- (+) Pull requests show real GitHub checks to the guide and the team.
- (−) Anyone can read the code, the specs and the handbook. Nothing secret or personal may ever be committed: synthetic seed data only, never real phone numbers or KYC data, and gitleaks must stay green.
- (−) GitHub issues and pull request comments are public too.

## Related
[ADR-0020](0020-free-tier-hosting-render-vercel-neon-r2.md) · [ADR-0025](0025-ml-runtime-spaces-actions-r2.md) · Handbook [Chapter 11](../handbook/11-engineering-process.md) and [Chapter 12](../handbook/12-deployment-and-operations.md)
