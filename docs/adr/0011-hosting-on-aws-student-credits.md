# ADR-0011 — Hosting on AWS student credits

- **Status:** Accepted (refined by ADR-0015)
- **Date:** 2026-10-02
- **Decision ID:** D11

## Context
The stack needs one always-on Linux server for the api, workers, ML service, MLflow, OSRM, Valkey and the console. Options discussed: DigitalOcean (GitHub Student Pack credits), Hetzner, Oracle Cloud Free, AWS or Azure credits.

## Decision
Host on **AWS using student credits** (AWS Educate / AWS Academy through SIES, or GitHub Student Pack offers). Region and topology are defined in ADR-0015.

## Alternatives considered
- **DigitalOcean** — simple, Bangalore region; not chosen.
- **Hetzner** — cheapest, but no India region.
- **Oracle Cloud Free** — large free ARM VM in India, but signup is unreliable and idle VMs can be reclaimed.
- **Azure for Students** — comparable; the team chose AWS.

## Consequences
- (+) Mumbai region for low latency; industry-relevant experience.
- (−) Credits must be applied for and monitored; costs must stay inside the budget in Chapter 14.

## Related
[ADR-0015](0015-aws-mumbai-topology-with-neon-postgres.md) · Handbook [Chapter 12](../handbook/12-deployment-and-operations.md)
