# ADR-0008 — Simulator first; real data added as it arrives; `data_source` tagging

- **Status:** Accepted
- **Date:** 2026-10-02
- **Decision ID:** D8

## Context
The AI needs weeks of household purchase history, but the app will not have real users until the pilot (around March 2027). Restarting a manual pilot immediately was considered; the team chose to start with simulated data and add real data whenever it becomes available. The accepted proposal also commits to synthetic or consented data for demonstrations.

## Decision
- Build a deterministic **household simulator** (`data/simulator`, seedable) in Sprint 0: hubs, societies, cohort-based households, latent per-item consumption rates (ground truth stored), meal choices, accept/edit behaviour, festivals, price shocks, churn, partners, donations.
- The simulator writes **through the module services** so ledger and state-machine rules hold.
- Every household and order carries **`data_source ∈ {synthetic, pilot, live}`**.
- Models train on the mix; metrics are always reported per `data_source`, and real-data accuracy is reported separately.
- Ground truth goes to Parquet files for honest evaluation.

## Alternatives considered
- **Manual pilot immediately** — deferred: would give earlier real data but competes with Phase 0 work; can start any time and simply flows in tagged as `pilot`.
- **Simulated data only** — rejected: viva claims would remain theoretical.

## Consequences
- (+) Development and demos never wait for real users; evaluation has known ground truth.
- (−) Simulator realism must be argued in the paper; real pilot results remain essential.

## Related
[ADR-0007](0007-research-scope-four-algorithms.md) · Handbook [Chapter 05](../handbook/05-data-architecture.md), [Chapter 08](../handbook/08-ai-and-ml.md)
